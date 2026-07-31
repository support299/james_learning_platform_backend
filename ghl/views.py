"""GoHighLevel OAuth endpoints.

`connect` and `callback` are plain Django views: they are top-level browser
navigations, so no Authorization header can be attached — and a
marketplace-initiated install hits the callback with no session at all. The
JSON endpoints that expose stored tokens are admin-only.
"""

from urllib.parse import urlencode

from django.conf import settings
from django.core import signing
from django.http import JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.serializers import UserSerializer
from accounts.views import tokens_for

from . import services
from .models import GhlToken, GhlUser
from .serializers import GhlTokenSerializer


@require_GET
def connect(request):
    """Kick off the OAuth flow: redirect the browser to GHL's consent screen.

    `?next=` is where we send the browser once the install lands; it rides
    along inside the signed state so it can't be tampered with.

    `?target=company` asks GHL for an agency-wide (Company) token instead of
    the default Location one — only honored if the app is actually installed
    at the agency level; a sub-account install ignores it. Also carried
    inside the signed state.
    """
    target = 'Company' if request.GET.get('target') == 'company' else ''
    try:
        state = services.make_state(
            {'next': request.GET.get('next') or '', 'target': target}
        )
        url = services.build_authorize_url(state)
    except services.GhlOAuthError as exc:
        return JsonResponse({'detail': str(exc)}, status=500)

    return redirect(url)


@require_GET
def callback(request):
    """GHL redirects here with `?code=…` after the user approves.

    State is verified when present. Installs started from the GHL marketplace
    listing arrive without one, so a missing state is allowed — but a state
    that *is* present must validate, which is what blocks a forged callback.
    """
    error = request.GET.get('error')
    if error:
        return _finish(
            request,
            None,
            {
                'detail': 'Authorization was denied',
                'error': error,
                'error_description': request.GET.get('error_description', ''),
            },
            status_code=400,
        )
    print(f"request.GET: {request.GET}")
    code = request.GET.get('code')
    print(f"code: {code}")
    if not code:
        return _finish(
            request, None, {'detail': 'Missing ?code'}, status_code=400
        )

    state = request.GET.get('state')
    next_url = ''
    target = ''
    if state:
        try:
            state_data = services.read_state(state)
            next_url = state_data.get('next') or ''
            target = state_data.get('target') or ''
        except signing.BadSignature:
            return _finish(
                request,
                None,
                {'detail': 'Invalid or expired state'},
                status_code=400,
            )

    try:
        token = services.exchange_code(code, user_type=target or None)
    except services.GhlOAuthError as exc:
        return _finish(
            request,
            next_url,
            {'detail': str(exc), 'ghl_response': exc.payload},
            status_code=exc.status_code or 502,
        )

    return _finish(
        request,
        next_url,
        {
            'detail': 'GoHighLevel connected',
            'id': str(token.id),
            'user_type': token.user_type,
            'location_id': token.location_id,
            'company_id': token.company_id,
            'expires_at': token.expires_at.isoformat(),
        },
    )


def _finish(request, next_url, payload, status_code=200):
    """Send the browser back to the frontend when a destination is known,
    otherwise return JSON so the flow is debuggable straight from the URL bar.
    Tokens themselves are never put in the redirect."""
    target = next_url or settings.GHL_OAUTH_SUCCESS_REDIRECT
    if target:
        summary = {
            'ghl': 'error' if status_code >= 400 else 'connected',
            'detail': payload.get('detail', ''),
        }
        if payload.get('location_id'):
            summary['location_id'] = payload['location_id']
        return redirect(f'{target}?{urlencode(summary)}')
    return JsonResponse(payload, status=status_code)


class GhlTokenListView(APIView):
    """Which GHL accounts are currently connected. Never returns the
    token values — only the install metadata."""

    permission_classes = [IsAdminUser]

    def get(self, request):
        tokens = GhlToken.objects.all()
        location_id = request.query_params.get('location_id')
        if location_id:
            tokens = tokens.filter(location_id=location_id)
        return Response(GhlTokenSerializer(tokens, many=True).data)


class LocationTokenView(APIView):
    """Mint a sub-account token from a stored agency token.

    POST {"location_id": "...", "company_id": "..."} — company_id is optional
    when exactly one agency install exists. The resulting token is saved and
    returned as metadata; fetch the secret from the DB, not from here.
    """

    permission_classes = [IsAdminUser]

    def post(self, request):
        location_id = request.data.get('location_id')
        if not location_id:
            return Response(
                {'detail': 'location_id is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        company_id = request.data.get('company_id')
        try:
            agency_token = services.get_valid_token(company_id=company_id)
        except GhlToken.DoesNotExist:
            return Response(
                {'detail': 'No agency (Company) token stored; connect first'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except services.GhlOAuthError as exc:
            return Response(
                {'detail': str(exc), 'ghl_response': exc.payload},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        if agency_token.user_type != GhlToken.UserType.COMPANY:
            return Response(
                {
                    'detail': (
                        'Stored token is a Location token; /oauth/locationToken '
                        'requires a Company (agency) token'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            token = services.fetch_location_token(
                agency_token, location_id, company_id=company_id
            )
        except services.GhlOAuthError as exc:
            return Response(
                {'detail': str(exc), 'ghl_response': exc.payload},
                status=exc.status_code or status.HTTP_502_BAD_GATEWAY,
            )

        return Response(GhlTokenSerializer(token).data)


class AutoLoginView(APIView):
    """POST {"logid": "<GHL user id>"} — trade a GHL user id for a session.

    Backs the one-click academy link sent from GoHighLevel, which carries the
    id as `?logid={{user.id}}`. The id is the same value stored as
    `GhlUser.ghl_id`, so the linked student is a single lookup away.

    Note this treats the GHL user id as a bearer credential: it is not secret
    and does not expire, so anyone holding one can sign in as that student.
    The intended replacement is a random per-user token stored on the GHL side
    — when that lands, only the lookup below changes.
    """

    permission_classes = [AllowAny]
    # Deliberately no authentication: the caller is signed out by definition,
    # and an expired token left over from a previous session shouldn't 401 the
    # request that is meant to replace it.
    authentication_classes = []

    def post(self, request):
        logid = str(request.data.get('logid') or '').strip()
        if not logid:
            return Response(
                {'detail': 'logid is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ghl_user = (
            GhlUser.objects.select_related('student')
            .filter(ghl_id=logid)
            .first()
        )
        # Unknown id and known-but-unlinked are the same answer to the caller:
        # there is no student to sign in.
        student = ghl_user.student if ghl_user else None
        if student is None:
            return Response(
                {'detail': 'No student account is linked to that GHL user.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not student.is_active:
            return Response(
                {'detail': 'This account has been deactivated.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Same shape as /auth/login/ and /auth/register/, so the client stores
        # the session exactly as it does after a password login.
        return Response(
            {'user': UserSerializer(student).data, **tokens_for(student)}
        )


class UserSearchView(APIView):
    """GET /api/ghl/users/search/?query=… — search the agency's users.

    Authorization is the stored Company (agency) install's token (refreshed
    on the way out if needed), so the caller never handles a GHL bearer.
    `company_id` is only needed once more than one Company install exists;
    otherwise it comes from the stored token.
    """

    permission_classes = [IsAdminUser]

    def get(self, request):
        try:
            payload = services.search_users(
                query=request.query_params.get('query'),
                company_id=request.query_params.get('company_id'),
            )
        except GhlToken.DoesNotExist:
            return Response(
                {'detail': 'GoHighLevel is not connected; install the app first'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except services.GhlError as exc:
            return Response(
                {'detail': str(exc), 'ghl_response': exc.payload},
                status=exc.status_code or status.HTTP_502_BAD_GATEWAY,
            )

        return Response(payload)
