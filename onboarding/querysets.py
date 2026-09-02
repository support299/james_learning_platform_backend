from django.db.models import Prefetch

from .models import AgentCarrierRequirement, AgentChecklistItem, OnboardingAgent, RequirementTemplate


def agent_queryset():
    return OnboardingAgent.objects.select_related(
        'cohort', 'owner', 'last_updated_by'
    ).prefetch_related(
        Prefetch(
            'checklist_items',
            queryset=AgentChecklistItem.objects.select_related(
                'owner', 'completed_by', 'definition'
            ),
        ),
        Prefetch(
            'carrier_requirements',
            queryset=AgentCarrierRequirement.objects.select_related(
                'carrier', 'owner', 'completed_by'
            ),
        ),
    )


def template_queryset():
    return RequirementTemplate.objects.prefetch_related(
        'template_carriers__carrier',
        'template_items__definition',
    )
