"""Stamp last editor, mark the row dirty, and queue a spreadsheet push."""
import logging

from django.db import transaction

from .models import OnboardingAgent, OnboardingEvent

logger = logging.getLogger(__name__)


def log_event(actor, agent, action, label=''):
    return OnboardingEvent.objects.create(
        actor=actor if getattr(actor, 'is_authenticated', False) else None,
        agent=agent,
        action=action,
        label=label or '',
    )


def touch_agent(agent, user, enqueue=True):
    agent.last_updated_by = user if getattr(user, 'is_authenticated', False) else None
    agent.needs_sync = True
    agent.save(update_fields=['last_updated_by', 'needs_sync', 'updated_at'])
    if not enqueue:
        return agent

    agent_id = agent.id

    def _enqueue():
        try:
            from .tasks import sync_agent

            sync_agent.delay(agent_id)
        except Exception as exc:
            logger.warning('Could not queue onboarding sheet sync: %s', exc)

    transaction.on_commit(_enqueue)
    return agent

