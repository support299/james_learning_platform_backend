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
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import GhlToken
from .serializers import GhlTokenSerializer


@require_GET
def connect(request):
    """Kick off the OAuth flow: redirect the browser to GHL's consent screen.

    `?next=` is where we send the browser once the install lands; it rides
    along inside the signed state so it can't be tampered with.
    """
    try:
        state = services.make_state({'next': request.GET.get('next') or ''})
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

    code = request.GET.get('code')
    if not code:
        return _finish(
            request, None, {'detail': 'Missing ?code'}, status_code=400
        )

    state = request.GET.get('state')
    next_url = ''
    if state:
        try:
            next_url = services.read_state(state).get('next') or ''
        except signing.BadSignature:
            return _finish(
                request,
                None,
                {'detail': 'Invalid or expired state'},
                status_code=400,
            )

    try:
        token = services.exchange_code(code)
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
