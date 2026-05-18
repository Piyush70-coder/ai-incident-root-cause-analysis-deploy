from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("incidents", "0007_incidentanalysis_postmortem_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="incidentanalysis",
            name="feedback_notes",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="incidentanalysis",
            name="is_corrected",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="incidentanalysis",
            name="user_root_cause",
            field=models.TextField(blank=True),
        ),
    ]
