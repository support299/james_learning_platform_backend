"""Overall onboarding status.

TODO: the client has not confirmed At Risk / Overdue thresholds.

Until `OnboardingSettings.at_risk_after_days` and `overdue_after_days` are
set, those auto-rules never fire. Status is then `on_track` unless the agent
is manually marked at risk or has a flagged outstanding item.

Do not copy these rules into React — the API is the source of truth.
"""

from datetime import timedelta

from django.utils import timezone

STATUS_ON_TRACK = 'on_track'
STATUS_AT_RISK = 'at_risk'
STATUS_OVERDUE = 'overdue'


def compute_status(agent, progress, settings):
    """Return on_track / at_risk / overdue for one agent.

    `progress` is the dict from `progress_for`. `settings` is the singleton.
    """
    outstanding = progress['outstanding']
    if not outstanding:
        return STATUS_ON_TRACK

    today = timezone.localdate()
    start = agent.start_date
    overdue_days = settings.overdue_after_days
    at_risk_days = settings.at_risk_after_days

    if overdue_days is not None and today >= start + timedelta(days=overdue_days):
        return STATUS_OVERDUE
    if at_risk_days is not None and today >= start + timedelta(days=at_risk_days):
        return STATUS_AT_RISK
    if agent.manually_at_risk or progress['flagged']:
        return STATUS_AT_RISK
    return STATUS_ON_TRACK
