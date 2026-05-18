"""
Shared log ingestion: optional incident targeting, deduplication by error signature, Celery hooks.
"""
from datetime import timedelta
from uuid import uuid4

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from incidents.models import Incident, IncidentAnalysis, IncidentLog
from incidents.services.log_processor import extract_log_content
from incidents.services.log_signature import extract_error_signature

DEDUP_WINDOW_HOURS = 2
TERMINAL_STATUSES = frozenset({"resolved", "closed"})


def _queue_incident_processing(incident_id: str, trigger_ai: bool) -> None:
    from incidents.tasks import generate_incident_embedding, process_incident_logs

    process_incident_logs.delay(incident_id, trigger_ai)
    if trigger_ai:
        generate_incident_embedding.delay(incident_id)


def _incident_title_from_signature(signature: str, raw_content: str) -> str:
    if signature and not signature.startswith("hash:"):
        t = signature.strip()
        return (t[:247] + "…") if len(t) > 250 else t
    first_line = (raw_content or "").strip().split("\n")[0].strip()
    if first_line:
        return (first_line[:247] + "…") if len(first_line) > 250 else first_line
    return f"Ingested incident {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}"


def _open_incident_for_signature(company, signature: str):
    if not (signature and str(signature).strip()):
        return None
    window_start = timezone.now() - timedelta(hours=DEDUP_WINDOW_HOURS)
    return (
        Incident.objects.filter(
            company=company,
            error_signature=signature,
            last_seen_at__gte=window_start,
        )
        .exclude(status__in=TERMINAL_STATUSES)
        .order_by("-last_seen_at")
        .first()
    )


def _build_file_payload(uploaded_file, log_text):
    """Returns (file_obj, file_name, file_size, file_type, raw_content)."""
    if uploaded_file:
        extracted_text, _ = extract_log_content(uploaded_file)
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(0)
        if not extracted_text or extracted_text.startswith("Error reading log"):
            return None
        file_obj = uploaded_file
        file_name = uploaded_file.name
        file_size = uploaded_file.size
        ext = uploaded_file.name.rsplit(".", 1)[-1].lower() if "." in uploaded_file.name else "log"
        file_type = ext
        raw_content = extracted_text
        return file_obj, file_name, file_size, file_type, raw_content

    raw_text = str(log_text).strip()
    if not raw_text:
        return None
    file_name = f"raw_log_{uuid4().hex}.log"
    raw_bytes = raw_text.encode("utf-8")
    file_obj = ContentFile(raw_bytes, name=file_name)
    file_size = len(raw_bytes)
    file_type = "log"
    raw_content = raw_text
    return file_obj, file_name, file_size, file_type, raw_content


def handle_log_ingestion(*, company, user, incident_id, log_text, uploaded_file):
    """
    Persist log and return (incident, log_entry, meta).

    meta keys: appended_to_existing (bool), new_incident (bool), deduplicated (bool), trigger_ai (bool)
    """
    payload = _build_file_payload(uploaded_file, log_text)
    if not payload:
        raise ValueError("Provide non-empty log_text or a valid file.")

    file_obj, file_name, file_size, file_type, raw_content = payload
    signature = extract_error_signature(raw_content)
    now = timezone.now()

    with transaction.atomic():
        if incident_id:
            incident = Incident.objects.filter(id=incident_id, company=company).select_for_update().first()
            if not incident:
                raise LookupError("incident_not_found")
            Incident.objects.filter(pk=incident.pk).update(
                occurrence_count=F("occurrence_count") + 1,
                last_seen_at=now,
            )
            incident.refresh_from_db()
            log_entry = IncidentLog(
                incident=incident,
                file_name=file_name,
                file_size=file_size,
                file_type=file_type,
                processed=False,
                raw_content=raw_content,
                processed_content="",
            )
            log_entry.file.save(file_name, file_obj, save=False)
            log_entry.save()

            meta = {
                "appended_to_existing": True,
                "new_incident": False,
                "deduplicated": False,
                "trigger_ai": False,
            }
            _iid = str(incident.id)
            transaction.on_commit(lambda iid=_iid: _queue_incident_processing(iid, False))
            return incident, log_entry, meta

        existing = _open_incident_for_signature(company, signature)
        if existing:
            Incident.objects.filter(pk=existing.pk).update(
                occurrence_count=F("occurrence_count") + 1,
                last_seen_at=now,
            )
            existing.refresh_from_db()
            log_entry = IncidentLog(
                incident=existing,
                file_name=file_name,
                file_size=file_size,
                file_type=file_type,
                processed=False,
                raw_content=raw_content,
                processed_content="",
            )
            log_entry.file.save(file_name, file_obj, save=False)
            log_entry.save()

            meta = {
                "appended_to_existing": True,
                "new_incident": False,
                "deduplicated": True,
                "trigger_ai": False,
            }
            _iid = str(existing.id)
            transaction.on_commit(lambda iid=_iid: _queue_incident_processing(iid, False))
            return existing, log_entry, meta

        title = _incident_title_from_signature(signature, raw_content)
        description = (raw_content or "")[:2000]
        incident = Incident.objects.create(
            company=company,
            created_by=user,
            title=title,
            description=description or "Incident created from log ingestion.",
            error_signature=signature or "",
            occurrence_count=1,
            last_seen_at=now,
        )
        log_entry = IncidentLog(
            incident=incident,
            file_name=file_name,
            file_size=file_size,
            file_type=file_type,
            processed=False,
            raw_content=raw_content,
            processed_content="",
        )
        log_entry.file.save(file_name, file_obj, save=False)
        log_entry.save()

        IncidentAnalysis.objects.get_or_create(
            incident=incident,
            defaults={"ai_status": "pending"},
        )

        meta = {
            "appended_to_existing": False,
            "new_incident": True,
            "deduplicated": False,
            "trigger_ai": True,
        }

        def _after_new():
            _queue_incident_processing(str(incident.id), True)

        transaction.on_commit(_after_new)
        return incident, log_entry, meta
