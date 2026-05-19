import logging

from celery import shared_task
from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)


def is_render_deployment():
    """Detect if we are running on Render."""
    return getattr(settings, "IS_RENDER", False)


def dispatch_task(task, *args, **kwargs):
    """
    Dispatch a task: synchronously if on Render, otherwise asynchronously via Celery.
    """
    if is_render_deployment():
        logger.info("Executing task %s synchronously (Render mode)", task.__name__)
        return task.apply(args=args, kwargs=kwargs)
    return task.delay(*args, **kwargs)


@shared_task(ignore_result=True)
def generate_incident_embedding(incident_id):
    if not getattr(settings, "ENABLE_SEMANTIC_RETRIEVAL", True):
        return

    from incidents.models import Incident
    from incidents.services.embedding_service import save_incident_embedding

    try:
        incident = Incident.objects.get(id=incident_id)
    except Incident.DoesNotExist:
        return

    try:
        save_incident_embedding(incident, incident.title)
    except Exception:
        logger.exception("Embedding generation failed for incident %s", incident_id)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=10,
    retry_kwargs={"max_retries": 3},
    ignore_result=True,
)
def generate_root_cause_analysis(self, incident_id):
    from incidents.models import Incident, IncidentAnalysis
    from incidents.services import ai_parser
    from incidents.services.context_builder_simple import (
        build_incident_context_with_similarity,
    )
    from incidents.services.log_processor import extract_smart_context
    from incidents.services.text_generation import generate_root_cause

    try:
        incident = Incident.objects.get(id=incident_id)
    except Incident.DoesNotExist:
        return

    analysis, _ = IncidentAnalysis.objects.get_or_create(
        incident=incident,
        defaults={"ai_status": "pending"},
    )

    try:
        logger.info("Starting AI analysis for incident %s", incident_id)
        context = build_incident_context_with_similarity(incident)
        smart_context = extract_smart_context(context)
        logger.info("Context built for incident %s (%d chars)", incident_id, len(smart_context))

        ai_result = generate_root_cause(smart_context)
        raw_output = ai_result.get("raw", "")
        llm_error = ai_result.get("error")
        if llm_error:
            logger.error("LLM Error for incident %s: %s", incident_id, llm_error)
            raise RuntimeError(llm_error)

        logger.info("AI generation complete for incident %s, parsing results...", incident_id)
        parsed = ai_parser.parse_sre_structured_output(
            raw_output or "{}",
            extra_context=smart_context,
        )
        payload = parsed["payload"]
        final_confidence = float(parsed["confidence_score"])
        severity_label = parsed.get("severity") or ""

        with transaction.atomic():
            if parsed.get("is_error_response"):
                analysis.root_cause = ""
                analysis.explanation = str(parsed.get("explanation") or "")
                analysis.confidence_score = 0.0
                analysis.severity = ""
                analysis.structured_output = payload
                analysis.full_ai_report = payload
                analysis.ai_status = "failed"
                analysis.error_message = str(parsed.get("explanation") or "")[:500]
                analysis.save()
                return

            if not parsed.get("root_cause"):
                raise ValueError("AI response missing primary root cause")

            analysis.root_cause = str(parsed["root_cause"])
            analysis.explanation = str(parsed.get("explanation") or "")
            analysis.confidence_score = final_confidence
            analysis.severity = severity_label
            analysis.structured_output = payload
            analysis.full_ai_report = payload

            resolutions = payload.get("prioritized_resolutions", {})
            imm = resolutions.get("P0_immediate") or payload.get("immediate_fixes")
            if isinstance(imm, list):
                analysis.mitigation_steps = "\n".join(
                    f"- {step}" for step in imm if str(step).strip()
                )

            tactical = resolutions.get("P1_tactical", []) or payload.get("root_fixes", [])
            prev = resolutions.get("P2_prevention", []) or payload.get(
                "prevention_steps",
                [],
            )
            cmds = payload.get("safe_commands", [])
            fix_parts = []
            if isinstance(tactical, list) and tactical:
                fix_parts.append(
                    "Priority P1 (Tactical Fixes):\n"
                    + "\n".join(f"- {step}" for step in tactical)
                )
            if isinstance(prev, list) and prev:
                fix_parts.append(
                    "Priority P2 (Prevention):\n"
                    + "\n".join(f"- {step}" for step in prev)
                )
            if isinstance(cmds, list) and cmds:
                fix_parts.append(
                    "Safe Commands:\n" + "\n".join(f"  {command}" for command in cmds)
                )
            analysis.fix_steps = "\n\n".join(fix_parts)

            pm_parts = [
                f"Summary: {payload.get('analysis_summary', '')}",
                f"Failure Chain: {payload.get('failure_chain', '')}",
                f"Root Cause: {parsed['root_cause']}",
            ]
            depth = payload.get("root_cause_depth", [])
            if depth:
                pm_parts.append(
                    "Technical Depth Analysis:\n"
                    + "\n".join(
                        f"- [{item.get('component')}] {item.get('failure')}: {item.get('why')}"
                        for item in depth
                    )
                )
            if imm:
                pm_parts.append(
                    "P0 Immediate Actions:\n"
                    + "\n".join(f"- {step}" for step in imm)
                )
            if tactical:
                pm_parts.append(
                    "P1 Root Fixes:\n" + "\n".join(f"- {step}" for step in tactical)
                )
            analysis.postmortem = "\n\n".join(part for part in pm_parts if part.strip())
            analysis.ai_status = "completed"
            analysis.error_message = ""
            analysis.save()
            logger.info("Successfully completed AI analysis for incident %s", incident_id)

        dispatch_task(generate_postmortem_report, str(incident.id))
    except Exception as exc:
        analysis.ai_status = "failed"
        analysis.error_message = str(exc)[:500]
        analysis.save(update_fields=["ai_status", "error_message", "updated_at"])
        logger.exception("Root cause analysis failed for incident %s", incident_id)
        raise


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=10,
    retry_kwargs={"max_retries": 3},
    ignore_result=True,
)
def generate_postmortem_report(self, incident_id):
    from incidents.models import Incident, IncidentAnalysis
    from incidents.services.postmortem_service import generate_postmortem

    try:
        incident = Incident.objects.get(id=incident_id)
    except Incident.DoesNotExist:
        return

    analysis, _ = IncidentAnalysis.objects.get_or_create(incident=incident)
    if not analysis.root_cause and not analysis.explanation:
        return

    try:
        report = generate_postmortem(
            analysis.full_ai_report or analysis.structured_output or "",
            analysis.root_cause,
            analysis.explanation,
        )
        analysis.postmortem = report
        analysis.save(update_fields=["postmortem", "updated_at"])
    except Exception:
        logger.exception("Postmortem generation failed for incident %s", incident_id)
        raise


@shared_task(ignore_result=True)
def process_incident_logs(incident_id, trigger_ai=True):
    from incidents.models import IncidentLog
    from incidents.services.log_processor import process_log_file

    try:
        logs = IncidentLog.objects.filter(incident_id=incident_id, processed=False)
        for log in logs:
            try:
                processed_content = process_log_file(log)
                if processed_content:
                    log.processed_content = processed_content
                    log.processed = True
                    log.raw_content = ""
                    log.save(
                        update_fields=["processed_content", "processed", "raw_content"]
                    )
            except Exception:
                logger.exception("Log processing failed for log %s", log.id)
    except Exception:
        logger.exception("Error in process_incident_logs task for incident %s", incident_id)
        return

    if trigger_ai:
        dispatch_task(generate_root_cause_analysis, incident_id)
