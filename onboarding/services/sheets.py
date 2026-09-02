"""Google Sheets sync for the onboarding tracker.

Credentials live in env (never in the repo). If they are missing, in-app
writes still succeed and this module records a visible failure on
SpreadsheetSyncState — it does not pretend the sheet was updated.

Excel/OneDrive is out of V1; keep this adapter as the only spreadsheet
backend so a second implementation can be swapped later.
"""

from __future__ import annotations

import json
import logging

import requests
from django.conf import settings
from django.utils import timezone

from urllib.parse import quote

from ..models import (
    Carrier,
    OnboardingAgent,
    OnboardingSettings,
    SpreadsheetSyncState,
)
from ..querysets import agent_queryset
from .progress import annotate_agent

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SHEETS_BASE = 'https://sheets.googleapis.com/v4/spreadsheets'


class SheetsNotConfigured(Exception):
    pass


class SheetsError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def credentials_configured():
    has_creds = bool(
        getattr(settings, 'GOOGLE_SERVICE_ACCOUNT_FILE', '')
        or getattr(settings, 'GOOGLE_SERVICE_ACCOUNT_JSON', '')
    )
    has_sheet = bool(getattr(settings, 'GOOGLE_SHEETS_SPREADSHEET_ID', ''))
    return has_creds and has_sheet


def sync_status_payload():
    state = SpreadsheetSyncState.load()
    configured = credentials_configured()
    return {
        'configured': configured,
        'enabled': bool(
            getattr(settings, 'ONBOARDING_SYNC_ENABLED', True)
            and OnboardingSettings.load().sync_enabled
        ),
        'spreadsheet_id': getattr(settings, 'GOOGLE_SHEETS_SPREADSHEET_ID', '') or '',
        'tab_name': getattr(settings, 'GOOGLE_SHEETS_TAB_NAME', 'Onboarding'),
        'last_success_at': state.last_success_at,
        'last_error': state.last_error,
        'last_error_at': state.last_error_at,
        'last_synced_count': state.last_synced_count,
    }


def _load_credentials():
    file_path = getattr(settings, 'GOOGLE_SERVICE_ACCOUNT_FILE', '') or ''
    raw_json = getattr(settings, 'GOOGLE_SERVICE_ACCOUNT_JSON', '') or ''
    if not file_path and not raw_json:
        raise SheetsNotConfigured(
            'Set GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_JSON.'
        )
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request
    except ImportError as exc:
        raise SheetsNotConfigured(
            'google-auth is not installed. pip install google-auth'
        ) from exc

    if file_path:
        creds = service_account.Credentials.from_service_account_file(
            file_path, scopes=SCOPES
        )
    else:
        info = json.loads(raw_json)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES
        )
    creds.refresh(Request())
    return creds


def _headers(creds):
    return {
        'Authorization': f'Bearer {creds.token}',
        'Content-Type': 'application/json',
    }


def _tab_name():
    return getattr(settings, 'GOOGLE_SHEETS_TAB_NAME', 'Onboarding') or 'Onboarding'


def _spreadsheet_id():
    sheet_id = getattr(settings, 'GOOGLE_SHEETS_SPREADSHEET_ID', '') or ''
    if not sheet_id:
        raise SheetsNotConfigured('Set GOOGLE_SHEETS_SPREADSHEET_ID.')
    return sheet_id


def _request(method, url, creds, **kwargs):
    response = requests.request(
        method, url, headers=_headers(creds), timeout=30, **kwargs
    )
    if response.status_code >= 400:
        raise SheetsError(
            f'Sheets API {response.status_code}: {response.text[:500]}',
            status_code=response.status_code,
        )
    if response.content:
        return response.json()
    return {}


def _ensure_tab(creds, spreadsheet_id, tab):
    meta = _request('GET', f'{SHEETS_BASE}/{spreadsheet_id}?fields=sheets.properties', creds)
    titles = [
        sheet.get('properties', {}).get('title')
        for sheet in meta.get('sheets', [])
    ]
    if tab in titles:
        return
    _request(
        'POST',
        f'{SHEETS_BASE}/{spreadsheet_id}:batchUpdate',
        creds,
        json={
            'requests': [
                {'addSheet': {'properties': {'title': tab}}},
            ]
        },
    )


def _headers_row(carriers):
    base = [
        'agent_id',
        'agent',
        'cohort',
        'start_date',
        'owner',
        'completion_pct',
        'status',
        'outstanding',
        'last_updated',
    ]
    return base + [f'carrier:{c.code}' for c in carriers]


def _owner_name(user):
    if user is None:
        return ''
    name = f'{user.first_name} {user.last_name}'.strip()
    return name or user.username


def _agent_row(agent, extra, carriers):
    by_code = {req.carrier.code: req.get_status_display() for req in extra['carriers']}
    return [
        str(agent.id),
        agent.full_name,
        agent.cohort.name,
        agent.start_date.isoformat(),
        _owner_name(agent.owner),
        extra['completion_percent'],
        extra['status'],
        '; '.join(extra['outstanding']),
        agent.updated_at.isoformat() if agent.updated_at else '',
        *[by_code.get(c.code, '') for c in carriers],
    ]


def _a1(tab, start_row, start_col, end_row, end_col):
    def col(n):
        letters = ''
        while n:
            n, rem = divmod(n - 1, 26)
            letters = chr(65 + rem) + letters
        return letters

    return f"'{tab}'!{col(start_col)}{start_row}:{col(end_col)}{end_row}"


def push_agents(agent_ids=None, force=False):
    """Push dirty (or all) agents to Google Sheets. Returns a status dict."""
    state = SpreadsheetSyncState.load()
    app_settings = OnboardingSettings.load()
    enabled = bool(
        getattr(settings, 'ONBOARDING_SYNC_ENABLED', True) and app_settings.sync_enabled
    )
    configured = credentials_configured()
    state.configured = configured
    if not enabled:
        state.save(update_fields=['configured', 'updated_at'])
        return {
            'ok': False,
            'configured': configured,
            'error': 'Spreadsheet sync is disabled.',
            'synced': 0,
        }
    if not configured:
        message = (
            'Google Sheets is not configured. Set GOOGLE_SERVICE_ACCOUNT_FILE '
            'or GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SHEETS_SPREADSHEET_ID. '
            'Share the spreadsheet with the service-account email.'
        )
        state.last_error = message
        state.last_error_at = timezone.now()
        state.save()
        logger.warning(message)
        return {'ok': False, 'configured': False, 'error': message, 'synced': 0}

    qs = agent_queryset()
    if agent_ids:
        qs = qs.filter(id__in=agent_ids)
    elif not force:
        qs = qs.filter(needs_sync=True)
    agents = list(qs)
    if not agents:
        return {'ok': True, 'configured': True, 'synced': 0, 'error': ''}

    try:
        creds = _load_credentials()
        spreadsheet_id = _spreadsheet_id()
        tab = _tab_name()
        _ensure_tab(creds, spreadsheet_id, tab)
        carriers = list(
            Carrier.objects.filter(is_active=True).order_by('line', 'sort_order', 'name')
        )
        header = _headers_row(carriers)
        existing = _request(
            'GET',
            f'{SHEETS_BASE}/{spreadsheet_id}/values/{quote(tab)}',
            creds,
        )
        values = existing.get('values') or []
        if not values:
            _request(
                'PUT',
                (
                    f'{SHEETS_BASE}/{spreadsheet_id}/values/'
                    f'{quote(_a1(tab, 1, 1, 1, len(header)))}'
                    '?valueInputOption=RAW'
                ),
                creds,
                json={'values': [header]},
            )
            values = [header]
            id_to_row = {}
        else:
            # Rebuild header if carrier columns changed.
            if values[0] != header:
                _request(
                    'PUT',
                    (
                        f'{SHEETS_BASE}/{spreadsheet_id}/values/'
                        f'{quote(_a1(tab, 1, 1, 1, len(header)))}'
                        '?valueInputOption=RAW'
                    ),
                    creds,
                    json={'values': [header]},
                )
            id_to_row = {}
            for index, row in enumerate(values[1:], start=2):
                if row:
                    id_to_row[str(row[0])] = index

        updates = []
        appends = []
        onb_settings = app_settings
        for agent in agents:
            extra = annotate_agent(agent, onb_settings)
            row_values = _agent_row(agent, extra, carriers)
            row_num = id_to_row.get(str(agent.id))
            if row_num:
                updates.append((row_num, row_values))
            else:
                appends.append(row_values)

        data = []
        for row_num, row_values in updates:
            data.append(
                {
                    'range': _a1(tab, row_num, 1, row_num, len(header)),
                    'values': [row_values],
                }
            )
        if data:
            _request(
                'POST',
                f'{SHEETS_BASE}/{spreadsheet_id}/values:batchUpdate',
                creds,
                json={'valueInputOption': 'RAW', 'data': data},
            )
        if appends:
            _request(
                'POST',
                (
                    f'{SHEETS_BASE}/{spreadsheet_id}/values/'
                    f'{quote(tab)}:append?valueInputOption=RAW'
                    '&insertDataOption=INSERT_ROWS'
                ),
                creds,
                json={'values': appends},
            )

        OnboardingAgent.objects.filter(id__in=[a.id for a in agents]).update(
            needs_sync=False
        )
        state.last_success_at = timezone.now()
        state.last_error = ''
        state.last_synced_count = len(agents)
        state.save()
        return {'ok': True, 'configured': True, 'synced': len(agents), 'error': ''}
    except SheetsNotConfigured as exc:
        state.last_error = str(exc)
        state.last_error_at = timezone.now()
        state.save()
        return {'ok': False, 'configured': False, 'error': str(exc), 'synced': 0}
    except (SheetsError, requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        message = str(exc)
        logger.exception('Onboarding sheet sync failed')
        state.last_error = message
        state.last_error_at = timezone.now()
        state.save()
        return {'ok': False, 'configured': True, 'error': message, 'synced': 0}
