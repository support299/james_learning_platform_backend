import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone


class GhlToken(models.Model):
    """One row per GoHighLevel OAuth install.

    `expires_at` is stored as an absolute deadline (computed from the
    `expires_in` seconds GHL returns) so freshness is a single comparison.
    `raw` keeps the untouched token response — GHL adds fields over time
    (userId, planId, approvedLocations…) and we don't want to lose them.
    """

    class UserType(models.TextChoices):
        COMPANY = 'Company', 'Company (agency)'
        LOCATION = 'Location', 'Location (sub-account)'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    access_token = models.TextField()
    refresh_token = models.TextField()
    expires_at = models.DateTimeField()
    location_id = models.TextField(null=True, blank=True)
    company_id = models.TextField(null=True, blank=True)
    user_type = models.TextField(null=True, blank=True)
    scope = models.TextField(null=True, blank=True)
    raw = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ghl_tokens'
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['location_id']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        target = self.location_id or self.company_id or self.id
        return f'{self.user_type or "?"} {target}'

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    def expires_within(self, seconds):
        """True if the token dies inside `seconds` — lets us refresh early
        instead of discovering it via a 401 mid-request."""
        return timezone.now() + timedelta(seconds=seconds) >= self.expires_at
