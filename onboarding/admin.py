from django.contrib import admin

from .models import (
    AgentCarrierRequirement,
    AgentChecklistItem,
    Carrier,
    ChecklistItemDefinition,
    Cohort,
    OnboardingAgent,
    OnboardingEvent,
    OnboardingSettings,
    RequirementTemplate,
    SpreadsheetSyncState,
    TemplateCarrier,
    TemplateChecklistItem,
)


class TemplateCarrierInline(admin.TabularInline):
    model = TemplateCarrier
    extra = 0


class TemplateChecklistInline(admin.TabularInline):
    model = TemplateChecklistItem
    extra = 0


@admin.register(RequirementTemplate)
class RequirementTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'role', 'state', 'agent_type', 'is_default', 'is_active']
    inlines = [TemplateCarrierInline, TemplateChecklistInline]


@admin.register(Carrier)
class CarrierAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'is_active', 'sort_order']
    prepopulated_fields = {'code': ('name',)}


@admin.register(ChecklistItemDefinition)
class ChecklistItemDefinitionAdmin(admin.ModelAdmin):
    list_display = ['label', 'is_required', 'is_active', 'sort_order']


@admin.register(Cohort)
class CohortAdmin(admin.ModelAdmin):
    list_display = ['name', 'start_date', 'is_active']


class AgentCarrierInline(admin.TabularInline):
    model = AgentCarrierRequirement
    extra = 0


class AgentChecklistInline(admin.TabularInline):
    model = AgentChecklistItem
    extra = 0


@admin.register(OnboardingAgent)
class OnboardingAgentAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'cohort', 'start_date', 'owner', 'needs_sync']
    list_filter = ['cohort', 'manually_at_risk']
    search_fields = ['full_name']
    raw_id_fields = ['owner', 'last_updated_by']
    inlines = [AgentCarrierInline, AgentChecklistInline]


@admin.register(OnboardingEvent)
class OnboardingEventAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'action', 'agent', 'actor', 'label']
    list_filter = ['action']


@admin.register(OnboardingSettings)
class OnboardingSettingsAdmin(admin.ModelAdmin):
    list_display = [
        'at_risk_after_days',
        'overdue_after_days',
        'default_template',
        'sync_enabled',
    ]


@admin.register(SpreadsheetSyncState)
class SpreadsheetSyncStateAdmin(admin.ModelAdmin):
    list_display = [
        'configured',
        'last_success_at',
        'last_error_at',
        'last_synced_count',
    ]
