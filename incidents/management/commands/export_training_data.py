import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from incidents.models import IncidentAnalysis, IncidentLog
from incidents.services.pii_masker import mask_pii


class Command(BaseCommand):
    help = "Export corrected AI records as JSONL for fine-tuning."

    def _get_masked_log_text(self, incident_id) -> str:
        """
        Fetch latest processed log text for an incident.
        Re-apply masking as a safety guard before export.
        """
        log_text = (
            IncidentLog.objects.filter(
                incident_id=incident_id,
                processed=True,
            )
            .exclude(processed_content="")
            .order_by("-uploaded_at")
            .values_list("processed_content", flat=True)
            .first()
        )
        if not log_text:
            return ""
        return mask_pii(log_text).strip()

    def handle(self, *args, **options):
        output_path = Path(settings.BASE_DIR) / "training_dataset.jsonl"
        exported_count = 0

        analyses = (
            IncidentAnalysis.objects.filter(is_corrected=True)
            .exclude(user_root_cause="")
            .select_related("incident")
            .iterator(chunk_size=200)
        )

        with output_path.open("w", encoding="utf-8") as dataset:
            for analysis in analyses:
                user_root_cause = (analysis.user_root_cause or "").strip()
                if not user_root_cause:
                    continue

                instruction = self._get_masked_log_text(analysis.incident_id)
                if not instruction:
                    # Skip records without usable masked log context.
                    continue

                record = {
                    "instruction": instruction,
                    "output": {
                        "root_cause": user_root_cause,
                        "notes": (analysis.feedback_notes or "").strip(),
                    },
                }

                dataset.write(json.dumps(record, ensure_ascii=False) + "\n")
                exported_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Export complete: {exported_count} records written to {output_path}"
            )
        )
