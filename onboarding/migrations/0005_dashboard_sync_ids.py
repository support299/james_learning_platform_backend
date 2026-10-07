from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('onboarding', '0004_agentcarrierrequirement_writing_number_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='cohort',
            name='dashboard_team_id',
            field=models.UUIDField(blank=True, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='onboardingagent',
            name='dashboard_user_id',
            field=models.UUIDField(blank=True, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='onboardingagent',
            name='dashboard_unassigned',
            field=models.BooleanField(default=False),
        ),
    ]
