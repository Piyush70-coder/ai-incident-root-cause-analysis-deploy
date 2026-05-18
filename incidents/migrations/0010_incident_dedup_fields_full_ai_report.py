from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('incidents', '0009_incidentanalysis_structured_output'),
    ]

    operations = [
        migrations.AddField(
            model_name='incident',
            name='error_signature',
            field=models.CharField(blank=True, db_index=True, max_length=512),
        ),
        migrations.AddField(
            model_name='incident',
            name='last_seen_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='incident',
            name='needs_follow_up',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='incident',
            name='occurrence_count',
            field=models.IntegerField(default=1),
        ),
        migrations.AddField(
            model_name='incidentanalysis',
            name='full_ai_report',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
