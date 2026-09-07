import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('onboarding', '0002_carrier_line'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='onboardingagent',
            name='email',
            field=models.EmailField(blank=True, default='', max_length=254),
        ),
        migrations.AddField(
            model_name='onboardingagent',
            name='user',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='onboarding_agent',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddIndex(
            model_name='onboardingagent',
            index=models.Index(fields=['email'], name='onboarding__email_8a3c1e_idx'),
        ),
    ]
