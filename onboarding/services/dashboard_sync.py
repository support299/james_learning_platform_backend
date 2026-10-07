"""Apply a full team/agent snapshot pushed from the dashboard.

The dashboard owns team membership. This replaces linked cohorts and moves
linked agents. Checklist rows, carrier rows, and academy-only cohorts stay.
"""

import uuid
from datetime import date

from django.db import transaction
from django.utils import timezone

from ..models import Cohort, OnboardingAgent
from .assign import assign_requirements
from .person import PersonLinkError, apply_person_link


class DashboardSyncError(Exception):
    pass


def _as_uuid(value, field):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise DashboardSyncError(f'Invalid {field}.') from exc


def _as_date(value):
    if isinstance(value, date):
        return value
    text = str(value or '').strip()
    if not text:
        return timezone.localdate()
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise DashboardSyncError('Invalid team created_at.') from exc


def _clip(value, limit):
    return (value or '').strip()[:limit]


def apply_dashboard_snapshot(payload):
    if not isinstance(payload, dict):
        raise DashboardSyncError('Snapshot must be an object.')
    teams = payload.get('teams')
    agents = payload.get('agents')
    if not isinstance(teams, list) or not isinstance(agents, list):
        raise DashboardSyncError('Snapshot needs teams and agents lists.')

    team_rows = []
    seen_teams = set()
    for row in teams:
        if not isinstance(row, dict):
            raise DashboardSyncError('Each team must be an object.')
        team_id = _as_uuid(row.get('id'), 'team id')
        if team_id in seen_teams:
            raise DashboardSyncError('Duplicate team id in snapshot.')
        seen_teams.add(team_id)
        name = _clip(row.get('name'), 200)
        if not name:
            raise DashboardSyncError('Each team needs a name.')
        team_rows.append(
            {
                'id': team_id,
                'name': name,
                'active': bool(row.get('active', True)),
                'start_date': _as_date(row.get('created_at')),
            }
        )

    agent_rows = []
    seen_agents = set()
    for row in agents:
        if not isinstance(row, dict):
            raise DashboardSyncError('Each agent must be an object.')
        user_id = _as_uuid(row.get('user_id'), 'user id')
        if user_id in seen_agents:
            raise DashboardSyncError('Duplicate user id in snapshot.')
        seen_agents.add(user_id)
        team_id = row.get('team_id') or None
        if team_id is not None:
            team_id = _as_uuid(team_id, 'agent team id')
            if team_id not in seen_teams:
                raise DashboardSyncError('Agent team is not in this snapshot.')
        agent_rows.append(
            {
                'user_id': user_id,
                'name': _clip(row.get('name'), 200),
                'email': _clip(row.get('email'), 254),
                'team_id': team_id,
            }
        )

    with transaction.atomic():
        cohorts_by_team = _upsert_teams(team_rows)
        archived = _archive_missing_teams(seen_teams)
        created, updated, unassigned, conflicts = _upsert_agents(
            agent_rows, cohorts_by_team, seen_agents
        )

    return {
        'teams_upserted': len(team_rows),
        'teams_archived': archived,
        'agents_created': created,
        'agents_updated': updated,
        'agents_unassigned': unassigned,
        'conflicts': conflicts,
    }


def _upsert_teams(team_rows):
    cohorts_by_team = {}
    for row in team_rows:
        cohort = Cohort.objects.filter(dashboard_team_id=row['id']).first()
        if cohort is None:
            named = list(
                Cohort.objects.filter(
                    dashboard_team_id__isnull=True, name__iexact=row['name']
                )[:2]
            )
            if len(named) == 1:
                cohort = named[0]
        if cohort is None:
            cohort = Cohort.objects.create(
                name=row['name'],
                start_date=row['start_date'],
                is_active=row['active'],
                dashboard_team_id=row['id'],
            )
        else:
            cohort.dashboard_team_id = row['id']
            cohort.name = row['name']
            cohort.is_active = row['active']
            cohort.save(
                update_fields=[
                    'dashboard_team_id',
                    'name',
                    'is_active',
                    'updated_at',
                ]
            )
        cohorts_by_team[row['id']] = cohort
    return cohorts_by_team


def _archive_missing_teams(seen_teams):
    stale = Cohort.objects.filter(dashboard_team_id__isnull=False).exclude(
        dashboard_team_id__in=seen_teams
    )
    return stale.update(is_active=False)


def _upsert_agents(agent_rows, cohorts_by_team, seen_agents):
    created = 0
    updated = 0
    conflicts = []
    for row in agent_rows:
        agent = OnboardingAgent.objects.filter(dashboard_user_id=row['user_id']).first()
        if agent is None and row['email']:
            agent = (
                OnboardingAgent.objects.filter(
                    dashboard_user_id__isnull=True, email__iexact=row['email']
                )
                .order_by('pk')
                .first()
            )
            if (
                agent is None
                and OnboardingAgent.objects.filter(email__iexact=row['email'])
                .exclude(dashboard_user_id=row['user_id'])
                .exists()
            ):
                conflicts.append(
                    {
                        'user_id': str(row['user_id']),
                        'email': row['email'],
                        'detail': 'Email is already linked to another dashboard user.',
                    }
                )
                continue
        if row['team_id'] is None:
            if agent is None:
                continue
            agent.dashboard_user_id = row['user_id']
            agent.dashboard_unassigned = True
            if row['name']:
                agent.full_name = row['name']
            if row['email']:
                agent.email = row['email']
            agent.save(
                update_fields=[
                    'dashboard_user_id',
                    'dashboard_unassigned',
                    'full_name',
                    'email',
                    'updated_at',
                ]
            )
            _link_person(agent)
            updated += 1
            continue
        cohort = cohorts_by_team[row['team_id']]
        if agent is None:
            if not row['name']:
                conflicts.append(
                    {
                        'user_id': str(row['user_id']),
                        'email': row['email'],
                        'detail': 'Agent has no name.',
                    }
                )
                continue
            agent = OnboardingAgent.objects.create(
                full_name=row['name'],
                email=row['email'],
                cohort=cohort,
                start_date=cohort.start_date,
                dashboard_user_id=row['user_id'],
                dashboard_unassigned=False,
            )
            _link_person(agent)
            assign_requirements(agent)
            created += 1
            continue
        agent.dashboard_user_id = row['user_id']
        agent.cohort = cohort
        agent.dashboard_unassigned = False
        if row['name']:
            agent.full_name = row['name']
        if row['email']:
            agent.email = row['email']
        agent.save(
            update_fields=[
                'dashboard_user_id',
                'cohort',
                'dashboard_unassigned',
                'full_name',
                'email',
                'updated_at',
            ]
        )
        _link_person(agent)
        updated += 1

    unassigned = (
        OnboardingAgent.objects.filter(dashboard_user_id__isnull=False)
        .exclude(dashboard_user_id__in=seen_agents)
        .exclude(dashboard_unassigned=True)
        .update(dashboard_unassigned=True)
    )
    return created, updated, unassigned, conflicts


def _link_person(agent):
    """Attach a login when this case does not have one yet."""
    if not agent.email or agent.user_id:
        return
    try:
        apply_person_link(agent, email=agent.email)
    except PersonLinkError:
        return
