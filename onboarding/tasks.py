"""Background jobs for onboarding spreadsheet sync."""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    name='onboarding.sync_agent',
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def sync_agent(self, agent_id):
    from .services.sheets import SheetsError, credentials_configured, push_agents

    if not credentials_configured():
        push_agents(agent_ids=[agent_id])
        return {'agent_id': agent_id, 'status': 'not_configured'}
    try:
        result = push_agents(agent_ids=[agent_id])
    except SheetsError as exc:
        if exc.status_code and 400 <= exc.status_code < 500:
            logger.error('Sheets rejected sync for agent %s: %s', agent_id, exc)
            return {'agent_id': agent_id, 'status': 'rejected'}
        raise self.retry(exc=exc)
    except Exception as exc:
        logger.warning('Sheets sync errored for agent %s: %s', agent_id, exc)
        raise self.retry(exc=exc)
    return {'agent_id': agent_id, **result}


@shared_task(name='onboarding.sync_dirty_agents')
def sync_dirty_agents():
    from .models import OnboardingAgent
    from .services.sheets import push_agents

    count = OnboardingAgent.objects.filter(needs_sync=True).count()
    result = push_agents(force=False)
    logger.info('Onboarding dirty sync: %s pending, result=%s', count, result)
    return result
