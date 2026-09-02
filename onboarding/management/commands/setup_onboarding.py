from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from onboarding.constants import ONBOARDING_GROUPS
from onboarding.models import OnboardingSettings, SpreadsheetSyncState


class Command(BaseCommand):
    help = (
        'Create Onboarding Assistant / Recruiter / Leadership groups and '
        'the settings/sync singleton rows. Does not invent carriers or '
        'checklist items — add those in the onboarding settings UI.'
    )

    def handle(self, *args, **options):
        for name in ONBOARDING_GROUPS:
            group, created = Group.objects.get_or_create(name=name)
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created group "{name}"'))
            else:
                self.stdout.write(f'Group "{name}" already exists')
        OnboardingSettings.load()
        SpreadsheetSyncState.load()
        self.stdout.write(self.style.SUCCESS('Onboarding settings ready.'))
        self.stdout.write(
            'Assign staff users to an onboarding group in Django admin '
            '(/admin/auth/group/). Superusers already have assistant access.'
        )
