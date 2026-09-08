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

import logging
from datetime import timedelta
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.core import signing
from django.db.models import Q
from django.utils import timezone

from .models import GhlToken, GhlUser

logger = logging.getLogger(__name__)

GHL_MARKETPLACE_BASE = 'https://marketplace.gohighlevel.com/v2'
GHL_API_BASE = 'https://services.leadconnectorhq.com'
GHL_API_VERSION = 'v3'
# /oauth/locationToken is a v2 route; sending Version: v3 returns 400.
GHL_LOCATION_TOKEN_VERSION = '2021-07-28'

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


def exchange_code(code, redirect_uri=None, user_type=None):
    """Trade an authorization code for tokens and persist them.

    `user_type` picks which kind of token the exchange asks for — 'Location'
    (the default, for a single sub-account) or 'Company' (agency-wide, only
    granted when the app is installed at the agency level). The app's
    Distribution Type must be "Sub-account (Both Can Install)" in the GHL
    marketplace for a Company install to be possible at all.
    """
    payload = _post_token(
        {
            'client_id': settings.GHL_CLIENT_ID,
            'client_secret': settings.GHL_CLIENT_SECRET,
            'grant_type': 'authorization_code',
            'code': code,
            'user_type': user_type or USER_TYPE,
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


def ghl_app_id():
    """Marketplace app id is the GHL_CLIENT_ID prefix before the install suffix."""
    raw = settings.GHL_CLIENT_ID or ''
    return raw.split('-')[0] if raw else ''


def fetch_location_token(company_token, location_id, company_id=None):
    """Exchange an agency (Company) token for a sub-account token.

    POST /oauth/locationToken needs Version 2021-07-28 (and oauth.write).
    v3 renamed the path to /oauth/location-token; we try that if the v2
    route rejects the version header.
    """
    company_id = company_id or company_token.company_id
    if not company_id:
        raise GhlOAuthError('Cannot mint a location token without a companyId')

    attempts = (
        (
            f'{GHL_API_BASE}/oauth/locationToken',
            GHL_LOCATION_TOKEN_VERSION,
        ),
        (
            f'{GHL_API_BASE}/oauth/location-token',
            GHL_API_VERSION,
        ),
    )
    payload = None
    status_code = None
    for url, version in attempts:
        response = requests.post(
            url,
            data={'companyId': company_id, 'locationId': location_id},
            headers={
                'Accept': 'application/json',
                'Content-Type': 'application/x-www-form-urlencoded',
                'Version': version,
                'Authorization': f'Bearer {company_token.access_token}',
            },
            timeout=TIMEOUT,
        )
        payload = _json_or_text(response)
        status_code = response.status_code
        if response.ok:
            payload.setdefault('companyId', company_id)
            payload.setdefault('locationId', location_id)
            payload.setdefault('userType', GhlToken.UserType.LOCATION)
            return save_token(payload)

    raise GhlOAuthError(
        f'GHL location-token request failed ({status_code})',
        status_code=status_code,
        payload=payload,
    )


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


def get_valid_token(location_id=None, company_id=None, user_type=None):
    """Return a usable token for the given target, refreshing it if it is
    expired or about to be. Raises GhlToken.DoesNotExist if not installed.

    Location tokens minted from an agency install have no refresh_token;
    those are reminted from the company token instead.
    """
    queryset = GhlToken.objects.all()
    if location_id:
        queryset = queryset.filter(location_id=location_id)
    if company_id:
        queryset = queryset.filter(company_id=company_id)
    if user_type:
        queryset = queryset.filter(user_type=user_type)

    token = queryset.first()
    if token is None:
        raise GhlToken.DoesNotExist('No GHL token stored for that target')

    if token.expires_within(REFRESH_LEEWAY):
        token = _refresh_or_remint(token)
    return token


def _refresh_or_remint(token):
    if token.refresh_token:
        return refresh_token(token)
    if (
        token.user_type == GhlToken.UserType.LOCATION
        and token.location_id
        and token.company_id
    ):
        agency = (
            GhlToken.objects.filter(
                company_id=token.company_id,
                user_type=GhlToken.UserType.COMPANY,
            )
            .exclude(refresh_token='')
            .first()
        )
        if agency is None:
            raise GhlOAuthError(
                'GHL location token expired and no agency token is stored; '
                'reconnect the app'
            )
        if agency.expires_within(REFRESH_LEEWAY):
            if not agency.refresh_token:
                raise GhlOAuthError(
                    'GHL agency token expired and has no refresh token; '
                    'reconnect the app'
                )
            agency = refresh_token(agency)
        return fetch_location_token(
            agency, token.location_id, company_id=token.company_id
        )
    raise GhlOAuthError(
        'GHL token expired and has no refresh token; reconnect the app'
    )


def get_or_mint_location_token(location_id, company_id=None):
    """Location token for a sub-account, minted from the agency token if needed."""
    try:
        return get_valid_token(location_id=location_id)
    except GhlToken.DoesNotExist:
        agency = get_valid_token(
            company_id=company_id, user_type=GhlToken.UserType.COMPANY
        )
        return fetch_location_token(
            agency, location_id, company_id=agency.company_id
        )


# --- REST API -------------------------------------------------------------


def search_users(query=None, company_id=None):
    """GET /users/search — search the agency's users.

    `companyId` is required by GHL; it defaults to the one on the stored
    Company install, so callers only pass the search `query`. Returns the
    decoded GHL body ({"users": [...], "total": n}).
    """
    # /users/search requires a Company (agency) token, not a Location one —
    # look it up by user_type rather than location_id, since there is only
    # ever one Company token stored.
    token = get_valid_token(company_id=company_id, user_type=GhlToken.UserType.COMPANY)
    company = company_id or token.company_id
    if not company:
        raise GhlApiError(
            'The stored GHL install has no companyId; pass it explicitly or '
            'reconnect the app'
        )

    params = {'companyId': company}
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


def fetch_user(ghl_user_id, location_id=None, company_id=None):
    """GET /users/{userId} — one sub-account user by their GHL id.

    Raises GhlApiError (404) when GHL doesn't know the id, which is how a
    mistyped id surfaces to the caller.
    """
    token = get_valid_token(location_id=location_id, company_id=company_id)

    response = requests.get(
        f'{GHL_API_BASE}/users/{ghl_user_id}',
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
            f'GHL user lookup failed ({response.status_code})',
            status_code=response.status_code,
            payload=payload,
        )
    return payload


def _user_fields_from_payload(data):
    """Map a GHL user, contact, or webhook body onto `GhlUser` columns."""
    roles = data.get('roles') or {}
    if isinstance(roles, str):
        roles = {'role': roles}

    location_ids = (
        roles.get('locationIds')
        or data.get('locationIds')
        or data.get('locations')
        or []
    )
    if isinstance(location_ids, str):
        location_ids = [location_ids]

    first_name = data.get('firstName') or data.get('first_name') or ''
    last_name = data.get('lastName') or data.get('last_name') or ''
    name = data.get('name') or ' '.join(
        part for part in (first_name, last_name) if part
    )

    return {
        'name': name,
        'first_name': first_name,
        'last_name': last_name,
        'email': data.get('email') or '',
        'phone': data.get('phone') or '',
        'role': roles.get('role') or data.get('role') or '',
        'role_type': roles.get('type') or '',
        'location_id': (
            (location_ids[0] if location_ids else None)
            or data.get('locationId')
            or data.get('location_id')
            or ''
        ),
        'company_id': data.get('companyId') or data.get('company_id') or None,
        'raw': data,
    }


def save_user(payload, user=None, location_id=None):
    """Upsert a GHL user/contact payload into `ghl_users`.

    Keyed on (ghl_id, location_id) so the same GHL person can exist on more
    than one sub-account. Pass `location_id` when syncing a specific location
    so a user with many locationIds still lands on this location's row.

    `user` links the row to a platform account; leaving it None on an
    existing row keeps whatever link is already there.
    """
    data = payload.get('user') if isinstance(payload.get('user'), dict) else payload
    ghl_id = (
        data.get('id')
        or data.get('_id')
        or data.get('contactId')
        or data.get('userId')
    )
    if not ghl_id:
        raise GhlApiError('GHL user response carries no id', payload=payload)

    fields = _user_fields_from_payload(data)
    loc = location_id or fields.get('location_id') or ''
    fields['location_id'] = loc
    if user is not None:
        fields['user'] = user

    existing = GhlUser.objects.filter(ghl_id=str(ghl_id), location_id=loc).first()
    if existing is None and loc:
        unscoped = GhlUser.objects.filter(ghl_id=str(ghl_id), location_id='').first()
        if unscoped is not None:
            for key, value in fields.items():
                setattr(unscoped, key, value)
            unscoped.save()
            return unscoped

    ghl_user, _ = GhlUser.objects.update_or_create(
        ghl_id=str(ghl_id), location_id=loc, defaults=fields
    )
    return ghl_user


def link_ghl_to_user(payload, user):
    """Attach a fetched GHL user to a platform account.

    A GHL user id belongs to at most one User across every location row, so
    claiming one that another account already holds is refused here rather
    than silently stealing it. The reverse move is allowed: an account
    pointed at a different GHL user has those older rows released first.
    """
    data = payload.get('user') if isinstance(payload.get('user'), dict) else payload
    ghl_id = str(data.get('id') or '')

    if (
        GhlUser.objects.filter(ghl_id=ghl_id)
        .exclude(user=None)
        .exclude(user=user)
        .exists()
    ):
        raise GhlError(
            'That GoHighLevel user is already linked to another account'
        )

    GhlUser.objects.filter(user=user).exclude(ghl_id=ghl_id).update(user=None)
    save_user(payload, user=user)
    GhlUser.objects.filter(ghl_id=ghl_id).update(user=user)
    return GhlUser.objects.filter(ghl_id=ghl_id, user=user).first()


def link_mirrored_user(ghl_id, user):
    """Attach already-synced `GhlUser` rows (every location) to a login."""
    rows = GhlUser.objects.filter(ghl_id=str(ghl_id))
    if not rows.exists():
        raise GhlError('No synced GoHighLevel user with that id')

    if rows.exclude(user=None).exclude(user=user).exists():
        raise GhlError(
            'That GoHighLevel user is already linked to another account'
        )

    GhlUser.objects.filter(user=user).exclude(ghl_id=str(ghl_id)).update(user=None)
    rows.update(user=user)
    return rows.first()


def account_for_ghl_login(logid):
    """Django account for iframe autologin, keyed on GHL user id.

    `ghl_users.user_id` is often empty. Lookup is always `ghl_id == logid`.
    If an onboarding case already exists for that email, sign in as that
    case's User so /onboarding/me finds it.
    """
    from accounts.services import IdentityError, provision_person
    from onboarding.models import OnboardingAgent

    rows = (
        GhlUser.objects.select_related('user').filter(ghl_id=str(logid))
    )
    ghl_user = rows.first()
    if ghl_user is None:
        return None

    email = ghl_user.email or next(
        (row.email for row in rows if row.email), ''
    )
    agent = None
    if email:
        agent = (
            OnboardingAgent.objects.select_related('user')
            .filter(email__iexact=email)
            .order_by('pk')
            .first()
        )

    account = None
    if agent is not None and agent.user_id:
        account = agent.user
    if account is None:
        account = ghl_user.user or next(
            (row.user for row in rows if row.user_id), None
        )
    if account is None and email:
        try:
            account, _created = provision_person(
                email=email,
                full_name=ghl_user.name,
                first_name=ghl_user.first_name,
                last_name=ghl_user.last_name,
            )
        except IdentityError:
            return None
    if account is None:
        return None

    rows.filter(user=None).update(user=account)
    if agent is not None and agent.user_id is None:
        agent.user = account
        agent.save(update_fields=['user'])
    return account


# Older name kept so existing imports keep working.
link_user_to_student = link_ghl_to_user


def user_as_search_item(ghl_user):
    """Same shape the live GHL /users/search payload used, so the picker
    does not care that the source is now our DB."""
    return {
        'id': ghl_user.ghl_id,
        'name': ghl_user.name
        or ' '.join(
            part for part in (ghl_user.first_name, ghl_user.last_name) if part
        ),
        'firstName': ghl_user.first_name,
        'lastName': ghl_user.last_name,
        'email': ghl_user.email,
        'phone': ghl_user.phone,
        'locationId': ghl_user.location_id,
        'roles': {'role': ghl_user.role, 'type': ghl_user.role_type},
    }


def search_local_users(query='', limit=25):
    """Filter mirrored GHL users by name, email, or id."""
    qs = GhlUser.objects.all()
    term = (query or '').strip()
    if term:
        qs = qs.filter(
            Q(name__icontains=term)
            | Q(first_name__icontains=term)
            | Q(last_name__icontains=term)
            | Q(email__icontains=term)
            | Q(ghl_id__icontains=term)
        )
    rows = []
    seen = set()
    for row in qs:
        if row.ghl_id in seen:
            continue
        seen.add(row.ghl_id)
        rows.append(row)
        if len(rows) >= limit:
            break
    return {
        'users': [user_as_search_item(row) for row in rows],
        'total': len(rows),
    }


def list_agency_locations(company_token):
    """GET /locations/search — every sub-account under the agency.

    Agency token only. Paginates with skip/limit.
    """
    company_id = company_token.company_id
    if not company_id:
        raise GhlApiError('The stored GHL install has no companyId')

    skip = 0
    limit = 100
    found = []
    while True:
        response = requests.get(
            f'{GHL_API_BASE}/locations/search',
            params={
                'companyId': company_id,
                'limit': limit,
                'skip': skip,
            },
            headers={
                'Accept': 'application/json',
                'Version': GHL_API_VERSION,
                'Authorization': f'Bearer {company_token.access_token}',
            },
            timeout=TIMEOUT,
        )
        payload = _json_or_text(response)
        if not response.ok:
            raise GhlApiError(
                f'GHL list locations failed ({response.status_code})',
                status_code=response.status_code,
                payload=payload,
            )
        chunk = []
        if isinstance(payload, list):
            chunk = payload
        elif isinstance(payload, dict):
            chunk = (
                payload.get('locations')
                or payload.get('items')
                or []
            )
        if not isinstance(chunk, list):
            raise GhlApiError(
                'GHL list locations returned no locations list', payload=payload
            )
        found.extend(chunk)
        if len(chunk) < limit:
            break
        skip += limit
        if skip > 5000:
            break
    return found


def list_installed_locations(company_token):
    """GET /oauth/installed-locations — sub-accounts where this app is installed.

    Location tokens can only be minted for those. Needs oauth.readonly.
    """
    company_id = company_token.company_id
    app_id = ghl_app_id()
    if not company_id or not app_id:
        return []

    found = []
    page_token = ''
    while True:
        params = {
            'companyId': company_id,
            'appId': app_id,
            'isInstalled': 'true',
            'restrictToUserLocations': 'false',
            'pageSize': 100,
        }
        if page_token:
            params['pageToken'] = page_token
        response = requests.get(
            f'{GHL_API_BASE}/oauth/installed-locations',
            params=params,
            headers={
                'Accept': 'application/json',
                'Version': GHL_API_VERSION,
                'Authorization': f'Bearer {company_token.access_token}',
            },
            timeout=TIMEOUT,
        )
        payload = _json_or_text(response)
        if not response.ok:
            logger.warning(
                'GHL installed-locations failed (%s): %s',
                response.status_code,
                payload,
            )
            return []
        chunk = payload.get('items') or payload.get('locations') or []
        if not isinstance(chunk, list):
            return found
        found.extend(chunk)
        pagination = payload.get('pagination') or {}
        if not pagination.get('hasNextPage'):
            break
        page_token = pagination.get('nextPageToken') or ''
        if not page_token:
            break
    return found


def locations_for_onboard(company_token):
    """Prefer locations the app is installed on; otherwise every sub-account."""
    installed = list_installed_locations(company_token)
    if installed:
        return installed
    return list_agency_locations(company_token)


def list_location_users(location_id=None, company_id=None):
    """GET /users/?locationId= — every user on one sub-account."""
    loc = location_id
    if not loc:
        token = get_valid_token(company_id=company_id)
        loc = token.location_id
    if not loc:
        raise GhlApiError(
            'The stored GHL install has no locationId; reconnect the app'
        )

    token = None
    try:
        token = get_or_mint_location_token(loc, company_id=company_id)
    except GhlError as exc:
        logger.warning(
            'No location token for %s (%s); listing users with the agency token. %s',
            loc,
            exc,
            getattr(exc, 'payload', None),
        )
        token = get_valid_token(
            company_id=company_id, user_type=GhlToken.UserType.COMPANY
        )
    response = requests.get(
        f'{GHL_API_BASE}/users/',
        params={'locationId': loc},
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
            f'GHL list users failed ({response.status_code})',
            status_code=response.status_code,
            payload=payload,
        )
    if isinstance(payload, list):
        return payload
    users = payload.get('users')
    if isinstance(users, list):
        return users
    raise GhlApiError('GHL list users returned no users list', payload=payload)


def sync_location_users(location_id=None, company_id=None):
    """Pull every user on one location into `ghl_users`."""
    loc = location_id
    if not loc:
        token = get_valid_token(company_id=company_id)
        loc = token.location_id
    users = list_location_users(location_id=loc, company_id=company_id)
    saved = [save_user(item, location_id=loc) for item in users]
    logger.info('Synced %s GHL user(s) for location %s', len(saved), loc)
    return saved


def onboard_company(company_token):
    """After agency OAuth: mint a token per location, then sync that location's users.

    One location failing does not stop the rest.
    """
    company_token = get_valid_token(
        company_id=company_token.company_id,
        user_type=GhlToken.UserType.COMPANY,
    )
    locations = locations_for_onboard(company_token)
    location_count = 0
    user_count = 0
    errors = []
    for loc in locations:
        loc_id = str(loc.get('id') or loc.get('_id') or '')
        if not loc_id:
            continue
        try:
            try:
                fetch_location_token(
                    company_token, loc_id, company_id=company_token.company_id
                )
            except GhlError as exc:
                logger.warning(
                    'GHL location token for %s failed: %s %s',
                    loc_id,
                    exc,
                    getattr(exc, 'payload', None),
                )
            saved = sync_location_users(
                location_id=loc_id, company_id=company_token.company_id
            )
            location_count += 1
            user_count += len(saved)
        except GhlError as exc:
            logger.warning(
                'GHL onboard failed for location %s: %s %s',
                loc_id,
                exc,
                getattr(exc, 'payload', None),
            )
            errors.append(
                {
                    'location_id': loc_id,
                    'detail': str(exc),
                    'ghl_response': getattr(exc, 'payload', None),
                }
            )
    logger.info(
        'GHL agency onboard: %s location(s), %s user(s)',
        location_count,
        user_count,
    )
    return {
        'locations': location_count,
        'users': user_count,
        'errors': errors,
    }


def onboard_install(token):
    """Finish OAuth: agency → every location; sub-account → that location only."""
    is_agency = (
        token.user_type == GhlToken.UserType.COMPANY or not token.location_id
    )
    if is_agency:
        return onboard_company(token)
    saved = sync_location_users(
        location_id=token.location_id, company_id=token.company_id
    )
    return {'locations': 1, 'users': len(saved), 'errors': []}


WEBHOOK_UPSERT_TYPES = {
    'contactcreate',
    'contactupdate',
    'usercreate',
    'userupdate',
}
WEBHOOK_DELETE_TYPES = {
    'contactdelete',
    'userdelete',
}


def _webhook_event_type(body):
    return str(body.get('type') or body.get('event') or '').replace(' ', '').lower()


def _webhook_record(body):
    """GHL marketplace events are flat; some docs/tests wrap fields in `data`."""
    nested = body.get('data')
    if isinstance(nested, dict) and (
        nested.get('id')
        or nested.get('email')
        or nested.get('firstName')
        or nested.get('contactId')
        or nested.get('userId')
    ):
        record = dict(nested)
        record.setdefault('locationId', body.get('locationId'))
        record.setdefault('companyId', body.get('companyId'))
        return record
    return body


def apply_webhook(body):
    """Keep `ghl_users` in sync with a GHL Contact*/User* webhook payload.

    Create/update upsert by GHL id. Delete removes the mirrored row (the
    linked platform account stays). Unknown event types with an id + person
    fields still upsert, so a GHL workflow webhook with a custom `type` works.
    """
    if not isinstance(body, dict):
        raise GhlError('Webhook body must be a JSON object')

    event = _webhook_event_type(body)
    record = _webhook_record(body)
    ghl_id = str(
        record.get('id')
        or record.get('_id')
        or record.get('contactId')
        or record.get('userId')
        or ''
    )

    if event in WEBHOOK_DELETE_TYPES:
        qs = (
            GhlUser.objects.filter(ghl_id=ghl_id)
            if ghl_id
            else GhlUser.objects.none()
        )
        loc = record.get('locationId') or record.get('location_id')
        if loc:
            scoped = qs.filter(location_id=str(loc))
            qs = scoped if scoped.exists() else qs
        deleted, _ = qs.delete()
        return {'action': 'deleted', 'ghl_id': ghl_id, 'found': bool(deleted)}

    looks_like_person = bool(
        ghl_id
        and (
            record.get('email')
            or record.get('name')
            or record.get('firstName')
            or record.get('lastName')
            or event in WEBHOOK_UPSERT_TYPES
        )
    )
    if event in WEBHOOK_UPSERT_TYPES or looks_like_person:
        if not ghl_id:
            raise GhlError('Webhook payload carries no id')
        ghl_user = save_user(record)
        return {'action': 'upserted', 'ghl_id': ghl_user.ghl_id}

    return {'action': 'ignored', 'type': body.get('type') or ''}
