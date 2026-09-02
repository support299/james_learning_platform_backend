from django.core.management.base import BaseCommand

from onboarding.catalog import upsert_aftermath_carriers
from onboarding.models import Carrier


class Command(BaseCommand):
    help = 'Upsert the Aftermath Health and Life carrier lists into Settings.'

    def handle(self, *args, **options):
        upsert_aftermath_carriers(Carrier)
        health = Carrier.objects.filter(line='health', is_active=True).count()
        life = Carrier.objects.filter(line='life', is_active=True).count()
        self.stdout.write(
            self.style.SUCCESS(f'Seeded carriers: {health} health, {life} life.')
        )
