from django.db import transaction

from ..models import (
    AgentCarrierRequirement,
    AgentChecklistItem,
    OnboardingAgent,
    OnboardingSettings,
    RequirementTemplate,
    TemplateCarrier,
    TemplateChecklistItem,
)


def _specificity(template):
    return (
        int(bool(template.role))
        + int(bool(template.state))
        + int(bool(template.agent_type))
    )


def _matches(template, agent):
    if template.role and template.role.lower() != (agent.role or '').lower():
        return False
    if template.state and template.state.lower() != (agent.state or '').lower():
        return False
    if template.agent_type and template.agent_type.lower() != (
        agent.agent_type or ''
    ).lower():
        return False
    return True


def match_template(agent, settings=None):
    """Pick the most specific active template that matches this agent.

    Falls back to settings.default_template, then any is_default template.
    Returns None when catalogs are still empty — the agent is created with
    no requirements until staff configure them.
    """
    settings = settings or OnboardingSettings.load()
    templates = list(
        RequirementTemplate.objects.filter(is_active=True).prefetch_related(
            'template_carriers__carrier',
            'template_items__definition',
        )
    )
    matches = [t for t in templates if _matches(t, agent) and _specificity(t) > 0]
    if matches:
        matches.sort(key=_specificity, reverse=True)
        return matches[0]
    if settings.default_template_id:
        for t in templates:
            if t.id == settings.default_template_id:
                return t
        fallback = (
            RequirementTemplate.objects.filter(pk=settings.default_template_id)
            .prefetch_related(
                'template_carriers__carrier',
                'template_items__definition',
            )
            .first()
        )
        if fallback:
            return fallback
    defaults = [t for t in templates if t.is_default]
    return defaults[0] if defaults else None


@transaction.atomic
def assign_requirements(agent, template=None, settings=None):
    """Snapshot template carriers/items onto the agent. Idempotent per agent.

    Existing rows are left alone so a later template edit does not wipe
    in-progress checklists. Missing rows are created.
    """
    settings = settings or OnboardingSettings.load()
    template = template or match_template(agent, settings)
    if template is None:
        return agent

    existing_carriers = set(
        agent.carrier_requirements.values_list('carrier_id', flat=True)
    )
    for link in template.template_carriers.select_related('carrier').all():
        if not link.carrier.is_active:
            continue
        if link.carrier_id in existing_carriers:
            continue
        AgentCarrierRequirement.objects.create(
            agent=agent,
            carrier=link.carrier,
            is_required=link.is_required,
            owner=agent.owner,
        )

    existing_defs = set(
        agent.checklist_items.exclude(definition_id=None).values_list(
            'definition_id', flat=True
        )
    )
    for link in template.template_items.select_related('definition').all():
        if not link.definition.is_active:
            continue
        if link.definition_id in existing_defs:
            continue
        AgentChecklistItem.objects.create(
            agent=agent,
            definition=link.definition,
            label=link.definition.label,
            is_required=link.is_required,
            sort_order=link.sort_order,
            owner=agent.owner,
        )
    return agent


def default_template():
    """The template new agents snapshot. Creates one if the catalog has none."""
    settings = OnboardingSettings.load()
    template = None
    if settings.default_template_id:
        template = RequirementTemplate.objects.filter(
            pk=settings.default_template_id
        ).first()
    if template is None:
        template = (
            RequirementTemplate.objects.filter(is_default=True).order_by('pk').first()
        )
    if template is None:
        template = RequirementTemplate.objects.create(
            name='Default', is_default=True, is_active=True
        )
    if settings.default_template_id != template.pk:
        settings.default_template = template
        settings.save(update_fields=['default_template', 'updated_at'])
    return template


def publish_catalog_item(*, carrier=None, definition=None, apply_to='new'):
    """Put a new carrier or checklist item on the default template.

    `apply_to='existing'` also adds a not-started row on every current agent.
    Rows already on an agent are left alone.
    """
    if apply_to not in ('new', 'existing'):
        raise ValueError('apply_to must be new or existing')
    template = default_template()
    if carrier is not None:
        TemplateCarrier.objects.get_or_create(
            template=template,
            carrier=carrier,
            defaults={'is_required': True},
        )
    if definition is not None:
        TemplateChecklistItem.objects.get_or_create(
            template=template,
            definition=definition,
            defaults={
                'is_required': definition.is_required,
                'sort_order': definition.sort_order,
            },
        )
    if apply_to != 'existing':
        return 0
    return _backfill_agents(carrier=carrier, definition=definition)


def _backfill_agents(*, carrier=None, definition=None):
    agents = list(OnboardingAgent.objects.all().only('id', 'owner_id'))
    if carrier is not None:
        have = set(
            AgentCarrierRequirement.objects.filter(carrier=carrier).values_list(
                'agent_id', flat=True
            )
        )
        rows = [
            AgentCarrierRequirement(
                agent_id=agent.id,
                carrier=carrier,
                is_required=True,
                owner_id=agent.owner_id,
            )
            for agent in agents
            if agent.id not in have
        ]
        AgentCarrierRequirement.objects.bulk_create(rows)
        return len(rows)
    if definition is None:
        return 0
    have = set(
        AgentChecklistItem.objects.filter(definition=definition).values_list(
            'agent_id', flat=True
        )
    )
    rows = [
        AgentChecklistItem(
            agent_id=agent.id,
            definition=definition,
            label=definition.label,
            is_required=definition.is_required,
            sort_order=definition.sort_order,
            owner_id=agent.owner_id,
        )
        for agent in agents
        if agent.id not in have
    ]
    AgentChecklistItem.objects.bulk_create(rows)
    return len(rows)
