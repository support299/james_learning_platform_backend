from django.conf import settings
from django.db import models


class RequirementTemplate(models.Model):
    """Named snapshot source for an agent's carriers + checklist.

    Optional role / state / agent_type are match hints, not a finished
    business-rules engine. Blank fields match any agent. A default template
    covers agents that don't match a more specific row.
    """

    name = models.CharField(max_length=200)
    role = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=80, blank=True)
    agent_type = models.CharField(max_length=80, blank=True)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Carrier(models.Model):
    """Configurable carrier catalog. Never hard-code the live list in the UI."""

    class Line(models.TextChoices):
        HEALTH = 'health', 'Health'
        LIFE = 'life', 'Life'

    name = models.CharField(max_length=200)
    code = models.SlugField(max_length=80, unique=True)
    line = models.CharField(max_length=20, choices=Line.choices, default=Line.LIFE)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['line', 'sort_order', 'name']

    def __str__(self):
        return f'{self.get_line_display()} / {self.name}'


class ChecklistItemDefinition(models.Model):
    """Configurable checklist catalog. Assigned onto agents via a template."""

    label = models.CharField(max_length=240)
    is_required = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'label']

    def __str__(self):
        return self.label


class TemplateCarrier(models.Model):
    template = models.ForeignKey(
        RequirementTemplate,
        related_name='template_carriers',
        on_delete=models.CASCADE,
    )
    carrier = models.ForeignKey(
        Carrier, related_name='template_links', on_delete=models.CASCADE
    )
    is_required = models.BooleanField(default=True)

    class Meta:
        unique_together = ('template', 'carrier')
        ordering = ['carrier__sort_order', 'carrier__name']


class TemplateChecklistItem(models.Model):
    template = models.ForeignKey(
        RequirementTemplate,
        related_name='template_items',
        on_delete=models.CASCADE,
    )
    definition = models.ForeignKey(
        ChecklistItemDefinition,
        related_name='template_links',
        on_delete=models.CASCADE,
    )
    is_required = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('template', 'definition')
        ordering = ['sort_order', 'id']


class Cohort(models.Model):
    name = models.CharField(max_length=200)
    start_date = models.DateField()
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-start_date', '-id']
        indexes = [
            models.Index(fields=['is_active', '-start_date']),
        ]

    def __str__(self):
        return f'{self.name} ({self.start_date})'


class OnboardingAgent(models.Model):
    """Onboarding case for one person.

    The person is `user` (same login as the academy student). `owner` is the
    staff member who manages the case — a different User.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        related_name='onboarding_agent',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    full_name = models.CharField(max_length=200)
    email = models.EmailField(blank=True, default='')
    cohort = models.ForeignKey(
        Cohort, related_name='agents', on_delete=models.CASCADE
    )
    start_date = models.DateField()
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='owned_onboarding_agents',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    role = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=80, blank=True)
    agent_type = models.CharField(max_length=80, blank=True)
    manually_at_risk = models.BooleanField(default=False)
    last_updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='updated_onboarding_agents',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    needs_sync = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['full_name', 'id']
        indexes = [
            models.Index(fields=['cohort', 'full_name']),
            models.Index(fields=['needs_sync']),
            models.Index(fields=['owner']),
            models.Index(fields=['start_date']),
            models.Index(fields=['email'], name='onboarding__email_8a3c1e_idx'),
        ]

    def __str__(self):
        return self.full_name


class AgentCarrierRequirement(models.Model):
    class Status(models.TextChoices):
        NOT_STARTED = 'not_started', 'Not Started'
        IN_PROGRESS = 'in_progress', 'In Progress'
        SUBMITTED = 'submitted', 'Submitted'
        APPROVED = 'approved', 'Approved'

    agent = models.ForeignKey(
        OnboardingAgent, related_name='carrier_requirements', on_delete=models.CASCADE
    )
    carrier = models.ForeignKey(
        Carrier, related_name='agent_requirements', on_delete=models.PROTECT
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.NOT_STARTED
    )
    is_required = models.BooleanField(default=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='owned_carrier_requirements',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    comment = models.TextField(blank=True)
    is_flagged = models.BooleanField(default=False)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='approved_carrier_requirements',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('agent', 'carrier')
        ordering = ['carrier__sort_order', 'carrier__name']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['is_flagged']),
        ]

    @property
    def is_complete(self):
        return self.status == self.Status.APPROVED

    def __str__(self):
        return f'{self.agent} / {self.carrier}'


class AgentChecklistItem(models.Model):
    agent = models.ForeignKey(
        OnboardingAgent, related_name='checklist_items', on_delete=models.CASCADE
    )
    definition = models.ForeignKey(
        ChecklistItemDefinition,
        related_name='agent_items',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    label = models.CharField(max_length=240)
    is_required = models.BooleanField(default=True)
    is_completed = models.BooleanField(default=False)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='completed_checklist_items',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='owned_checklist_items',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    comment = models.TextField(blank=True)
    is_flagged = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['agent', 'definition'],
                condition=models.Q(definition__isnull=False),
                name='uniq_agent_checklist_definition',
            ),
        ]
        indexes = [
            models.Index(fields=['is_completed']),
            models.Index(fields=['is_flagged']),
        ]

    def __str__(self):
        return f'{self.agent} / {self.label}'


class OnboardingEvent(models.Model):
    class Action(models.TextChoices):
        AGENT_CREATED = 'agent_created', 'Agent created'
        CHECKLIST_COMPLETED = 'checklist_completed', 'Checklist completed'
        CHECKLIST_UNCOMPLETED = 'checklist_uncompleted', 'Checklist uncompleted'
        CARRIER_STATUS = 'carrier_status', 'Carrier status'
        FLAGGED = 'flagged', 'Flagged'
        UNFLAGGED = 'unflagged', 'Unflagged'
        COMMENT = 'comment', 'Comment'
        OWNER_ASSIGNED = 'owner_assigned', 'Owner assigned'
        AT_RISK = 'at_risk', 'Marked at risk'

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='onboarding_events',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    agent = models.ForeignKey(
        OnboardingAgent, related_name='events', on_delete=models.CASCADE
    )
    action = models.CharField(max_length=40, choices=Action.choices)
    label = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
        ]

    def __str__(self):
        return f'{self.action} / {self.agent}'


class OnboardingSettings(models.Model):
    """Singleton (pk=1). Status day thresholds stay null until the client confirms them."""

    at_risk_after_days = models.PositiveIntegerField(null=True, blank=True)
    overdue_after_days = models.PositiveIntegerField(null=True, blank=True)
    default_template = models.ForeignKey(
        RequirementTemplate,
        related_name='+',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    sync_enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Onboarding settings'
        verbose_name_plural = 'Onboarding settings'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.select_related('default_template').get_or_create(pk=1)
        return obj

    def __str__(self):
        return 'Onboarding settings'


class SpreadsheetSyncState(models.Model):
    """Singleton (pk=1) for the last Google Sheets push."""

    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_synced_count = models.PositiveIntegerField(default=0)
    configured = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Spreadsheet sync state'
        verbose_name_plural = 'Spreadsheet sync state'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return 'Spreadsheet sync state'
