"""Background jobs for the GHL integration.

The only scheduled job is the token refresh: GHL access tokens live 24h, so
beat refreshes them well inside that window (see CELERY_BEAT_SCHEDULE) and
requests never have to discover an expired token via a 401.
"""

import logging

from celery import shared_task

from .models import GhlToken
from .services import GhlOAuthError, refresh_token

logger = logging.getLogger(__name__)


@shared_task(
    name='ghl.refresh_token',
    bind=True,
    max_retries=3,
    default_retry_delay=300,
)
def refresh_ghl_token(self, token_id):
    """Refresh one install. Retries on transport/5xx errors; a 4xx means the
    refresh token itself is dead, so retrying can't help — the app has to be
    reconnected."""
    try:
        token = GhlToken.objects.get(pk=token_id)
    except GhlToken.DoesNotExist:
        logger.warning('GHL token %s no longer exists; skipping', token_id)
        return {'token_id': str(token_id), 'status': 'missing'}

    try:
        refreshed = refresh_token(token)
    except GhlOAuthError as exc:
        if exc.status_code and 400 <= exc.status_code < 500:
            logger.error(
                'GHL refresh rejected for %s (%s): %s — reconnect the app',
                token,
                exc.status_code,
                exc.payload,
            )
            return {'token_id': str(token_id), 'status': 'rejected'}
        logger.warning('GHL refresh failed for %s, retrying: %s', token, exc)
        raise self.retry(exc=exc)
    except Exception as exc:  # network error, DNS, timeout…
        logger.warning('GHL refresh errored for %s, retrying: %s', token, exc)
        raise self.retry(exc=exc)

    logger.info('Refreshed GHL token %s (expires %s)', refreshed, refreshed.expires_at)
    return {
        'token_id': str(token_id),
        'status': 'refreshed',
        'expires_at': refreshed.expires_at.isoformat(),
    }


@shared_task(name='ghl.refresh_all_tokens')
def refresh_all_ghl_tokens():
    """Beat's entrypoint: fan out one refresh task per install so a single
    broken install can't stop the others from being refreshed."""
    token_ids = list(
        GhlToken.objects.exclude(refresh_token='')
        .exclude(refresh_token__isnull=True)
        .values_list('id', flat=True)
    )

    for token_id in token_ids:
        refresh_ghl_token.delay(str(token_id))

    logger.info('Queued GHL token refresh for %d install(s)', len(token_ids))
    return {'queued': len(token_ids)}
