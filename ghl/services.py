"""GoHighLevel OAuth 2.0 client and REST calls.

Flow (marketplace.gohighlevel.com/docs/ghl/oauth):
  1. Send the user to `/oauth/chooselocation` on the marketplace host.
  2. GHL redirects back to our callback with `?code=…`.
  3. POST that code to `/oauth/token` (form-encoded) for an access +
     refresh token pair.
  4. An agency (Company) token can be traded for a sub-account token at
     `/oauth/locationToken`, which additionally needs the `Version` header.

Everything below the OAuth section calls the v2 REST API with a stored token,
refreshing it first when it is close to expiry.

Credentials come from settings (i.e. .env). The hosts and API versions are
fixed by GHL, so they live here as constants.
"""

from datetime import timedelta
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.core import signing
from django.utils import timezone

from .models import GhlToken

GHL_MARKETPLACE_BASE = 'https://marketplace.gohighlevel.com'
GHL_API_BASE = 'https://services.leadconnectorhq.com'
GHL_API_VERSION = 'v3'

# This app installs at sub-account level only; the code exchange always asks
# for a Location-scoped token.
USER_TYPE = GhlToken.UserType.LOCATION

# Refresh this many seconds before the real deadline so a token can't expire
# between the freshness check and the API call it is used for.
REFRESH_LEEWAY = 300

STATE_SALT = 'ghl.oauth.state'
STATE_MAX_AGE = 600  # 10 minutes to get through the consent screen.

TIMEOUT = 30


class GhlError(Exception):
    """A GHL call failed. `status_code` / `payload` mirror the GHL response
    when there was one, so views can pass the real reason through."""

    def __init__(self, message, status_code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class GhlOAuthError(GhlError):
    """A GHL OAuth endpoint returned a non-2xx response."""


class GhlApiError(GhlError):
    """A GHL REST endpoint returned a non-2xx response."""


def _require_credentials():
    if not settings.GHL_CLIENT_ID or not settings.GHL_CLIENT_SECRET:
        raise GhlOAuthError(
            'GHL_CLIENT_ID / GHL_CLIENT_SECRET are not set in .env'
        )


def make_state(payload=None):
    """Sign an opaque, timestamped `state` value for CSRF protection."""
    return signing.dumps(payload or {}, salt=STATE_SALT)


def read_state(state):
    """Return the signed payload, or raise `signing.BadSignature` if the
    state was forged or is older than STATE_MAX_AGE."""
    return signing.loads(state, salt=STATE_SALT, max_age=STATE_MAX_AGE)


def build_authorize_url(state, scopes=None, redirect_uri=None):
    """The consent URL the browser is sent to."""
    _require_credentials()
    params = {
        'response_type': 'code',
        'redirect_uri': redirect_uri or settings.GHL_REDIRECT_URI,
        'client_id': settings.GHL_CLIENT_ID,
        'scope': scopes or settings.GHL_SCOPES,
        'state': state,
        "version_id": "6a68d13d9c220c30cf6dd86d",
    }
    return f'{GHL_MARKETPLACE_BASE}/oauth/chooselocation?{urlencode(params)}'


def _json_or_text(response):
    try:
        return response.json()
    except ValueError:
        return {'raw_body': response.text}


def _post_token(data):
    """POST to /oauth/token. GHL requires form encoding here — sending JSON
    yields a 401 with an unhelpful body."""
    _require_credentials()
    response = requests.post(
        f'{GHL_API_BASE}/oauth/token',
        data=data,
        headers={
            'Accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        timeout=TIMEOUT,
    )
    payload = _json_or_text(response)
    if not response.ok:
        raise GhlOAuthError(
            f'GHL token request failed ({response.status_code})',
            status_code=response.status_code,
            payload=payload,
        )
    return payload


def exchange_code(code, redirect_uri=None):
    """Trade an authorization code for tokens and persist them."""
    payload = _post_token(
        {
            'client_id': settings.GHL_CLIENT_ID,
            'client_secret': settings.GHL_CLIENT_SECRET,
            'grant_type': 'authorization_code',
            'code': code,
            'user_type': USER_TYPE,
            'redirect_uri': redirect_uri or settings.GHL_REDIRECT_URI,
        }
    )
    return save_token(payload)


def refresh_token(token):
    """Refresh in place. GHL rotates the refresh token, so the response's
    refresh_token replaces the old one on the same row."""
    payload = _post_token(
        {
            'client_id': settings.GHL_CLIENT_ID,
            'client_secret': settings.GHL_CLIENT_SECRET,
            'grant_type': 'refresh_token',
            'refresh_token': token.refresh_token,
            'user_type': token.user_type or USER_TYPE,
        }
    )
    return save_token(payload, instance=token)


def fetch_location_token(company_token, location_id, company_id=None):
    """Exchange an agency (Company) token for a sub-account token.

    POST /oauth/locationToken — form-encoded, authorized with the *agency*
    bearer token, and it requires the `Version` header the v2 API uses. The
    response carries no refresh token; re-run this call against the agency
    token when it expires.
    """
    company_id = company_id or company_token.company_id
    if not company_id:
        raise GhlOAuthError('Cannot mint a location token without a companyId')

    response = requests.post(
        f'{GHL_API_BASE}/oauth/locationToken',
        data={'companyId': company_id, 'locationId': location_id},
        headers={
            'Accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Version': GHL_API_VERSION,
            'Authorization': f'Bearer {company_token.access_token}',
        },
        timeout=TIMEOUT,
    )
    payload = _json_or_text(response)
    if not response.ok:
        raise GhlOAuthError(
            f'GHL location-token request failed ({response.status_code})',
            status_code=response.status_code,
            payload=payload,
        )

    payload.setdefault('companyId', company_id)
    payload.setdefault('locationId', location_id)
    payload.setdefault('userType', GhlToken.UserType.LOCATION)
    return save_token(payload)


def save_token(payload, instance=None):
    """Upsert a token response into `ghl_tokens`.

    Identity is (company_id, location_id): re-installing the same sub-account
    updates the existing row instead of leaving a dead token behind.
    """
    expires_in = int(payload.get('expires_in') or 86400)
    location_id = payload.get('locationId') or None
    company_id = payload.get('companyId') or None

    fields = {
        'access_token': payload.get('access_token', ''),
        # A locationToken response has no refresh_token; keep whatever we
        # already hold rather than blanking a still-valid one.
        'refresh_token': payload.get('refresh_token')
        or (instance.refresh_token if instance else ''),
        'expires_at': timezone.now() + timedelta(seconds=expires_in),
        'location_id': location_id,
        'company_id': company_id,
        'user_type': payload.get('userType')
        or (
            GhlToken.UserType.LOCATION
            if location_id
            else GhlToken.UserType.COMPANY
        ),
        'scope': payload.get('scope') or '',
        'raw': payload,
    }

    if instance is not None:
        for key, value in fields.items():
            setattr(instance, key, value)
        instance.save()
        return instance

    token, _ = GhlToken.objects.update_or_create(
        company_id=company_id, location_id=location_id, defaults=fields
    )
    return token


def get_valid_token(location_id=None, company_id=None):
    """Return a usable token for the given target, refreshing it if it is
    expired or about to be. Raises GhlToken.DoesNotExist if not installed."""
    queryset = GhlToken.objects.all()
    if location_id:
        queryset = queryset.filter(location_id=location_id)
    if company_id:
        queryset = queryset.filter(company_id=company_id)

    token = queryset.first()
    if token is None:
        raise GhlToken.DoesNotExist('No GHL token stored for that target')

    if token.expires_within(REFRESH_LEEWAY):
        if not token.refresh_token:
            raise GhlOAuthError(
                'GHL token expired and has no refresh token; reconnect the app'
            )
        token = refresh_token(token)
    return token


# --- REST API -------------------------------------------------------------


def search_users(query=None, location_id=None, company_id=None):
    """GET /users/search — search the users of a sub-account.

    `companyId` and `locationId` are required by GHL; they default to the ones
    on the stored install, so callers only pass the search `query`. Returns the
    decoded GHL body ({"users": [...], "total": n}).
    """
    token = get_valid_token(location_id=location_id, company_id=company_id)

    company = company_id or token.company_id
    location = location_id or token.location_id
    if not company or not location:
        raise GhlApiError(
            'The stored GHL install has no companyId/locationId; pass them '
            'explicitly or reconnect the app'
        )

    params = {'companyId': company, 'locationId': location}
    # Send `query` only when there is one: GHL matches an empty query against
    # the empty string and returns nothing, while omitting it lists all users.
    if query:
        params['query'] = query

    response = requests.get(
        f'{GHL_API_BASE}/users/search',
        params=params,
        headers={
            'Accept': 'application/json',
            'Version': GHL_API_VERSION,
            'Authorization': f'Bearer {token.access_token}',
        },
        timeout=TIMEOUT,
    )
    payload = _json_or_text(response)
    if not response.ok:
        raise GhlApiError(
            f'GHL user search failed ({response.status_code})',
            status_code=response.status_code,
            payload=payload,
        )
    return payload
