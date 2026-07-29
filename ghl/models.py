import uuid
from datetime import timedelta

from django.conf import settings
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


class GhlUser(models.Model):
    """A user of a GHL sub-account, mirrored locally.

    GHL's own user id is the primary key: it is what an admin pastes into the
    student form, and keying on it makes re-linking the same user an update
    rather than a duplicate row.

    `student` is the bridge to this platform's accounts. It lives here because
    students are plain `auth.User` rows, which can't carry an extra column —
    and it is a OneToOne so a GHL user maps to at most one student, readable
    from either side (`student.ghl_user` / `ghl_user.student`). SET_NULL keeps
    the mirrored GHL record when a student account is deleted.

    `raw` keeps the untouched GHL body; the typed columns below are only the
    fields we actually display.
    """

    ghl_id = models.CharField(primary_key=True, max_length=64)
    location_id = models.TextField(null=True, blank=True)
    company_id = models.TextField(null=True, blank=True)
    name = models.CharField(max_length=200, blank=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    # roles.role ("admin"/"user") and roles.type ("account"/"agency").
    role = models.CharField(max_length=60, blank=True)
    role_type = models.CharField(max_length=60, blank=True)
    student = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        related_name='ghl_user',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    raw = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ghl_users'
        ordering = ['name', 'ghl_id']
        indexes = [
            models.Index(fields=['location_id']),
            models.Index(fields=['email']),
        ]

    def __str__(self):
        return self.name or self.email or self.ghl_id
