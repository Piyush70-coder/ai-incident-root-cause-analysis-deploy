from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("incidents", "0008_incidentanalysis_feedback_loop_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="incidentanalysis",
            name="structured_output",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]

