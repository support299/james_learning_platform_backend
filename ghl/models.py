import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class GhlToken(models.Model):
    """One row per GoHighLevel OAuth install.

    Agency (Company) connect stores one company token (location_id empty).
    Each sub-account then gets its own Location token via /oauth/locationToken.
    Location tokens have no refresh_token — they are reminted from the company
    token when they expire.

    `expires_at` is an absolute deadline. `raw` keeps the untouched token body.
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
    """A GHL user as seen in one sub-account.

    GHL's user id is unique in the agency, but the same person can belong to
    several locations. We keep one row per (ghl_id, location_id) so listing
    a location's users does not collapse them. Autologin still keys on ghl_id
    (`?logid={{user.id}}`).

    `user` is the platform login. Several location rows may point at the same
    account. SET_NULL keeps the GHL mirror if the account is deleted.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ghl_id = models.CharField(max_length=64)
    location_id = models.TextField(blank=True, default='')
    company_id = models.TextField(null=True, blank=True)
    name = models.CharField(max_length=200, blank=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    # roles.role ("admin"/"user") and roles.type ("account"/"agency").
    role = models.CharField(max_length=60, blank=True)
    role_type = models.CharField(max_length=60, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='ghl_users',
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
        constraints = [
            models.UniqueConstraint(
                fields=['ghl_id', 'location_id'],
                name='ghl_users_ghl_id_location_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['ghl_id'], name='ghl_users_ghl_id_idx'),
            models.Index(fields=['location_id']),
            models.Index(fields=['email']),
        ]

    def __str__(self):
        label = self.name or self.email or self.ghl_id
        if self.location_id:
            return f'{label} ({self.location_id})'
        return label
