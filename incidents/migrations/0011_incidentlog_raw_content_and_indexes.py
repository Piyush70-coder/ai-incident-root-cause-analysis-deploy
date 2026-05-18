from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("incidents", "0010_incident_dedup_fields_full_ai_report"),
    ]

    operations = [
        migrations.AddField(
            model_name="incidentlog",
            name="raw_content",
            field=models.TextField(blank=True),
        ),
        migrations.AddIndex(
            model_name="incident",
            index=models.Index(
                fields=["company", "-created_at"],
                name="inc_cmp_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="incident",
            index=models.Index(
                fields=["company", "-last_seen_at"],
                name="inc_cmp_seen_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="incident",
            index=models.Index(
                fields=["company", "status"],
                name="inc_cmp_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="incidentlog",
            index=models.Index(
                fields=["incident", "-uploaded_at"],
                name="inclog_inc_up_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="incidentlog",
            index=models.Index(
                fields=["incident", "processed"],
                name="inclog_inc_proc_idx",
            ),
        ),
    ]
