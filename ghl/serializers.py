from rest_framework import serializers

from .models import GhlToken, GhlUser


class GhlTokenSerializer(serializers.ModelSerializer):
    """Install metadata only. `access_token`, `refresh_token` and `raw` are
    deliberately absent so a token can never leak through the API."""

    is_expired = serializers.BooleanField(read_only=True)

    class Meta:
        model = GhlToken
        fields = [
            'id',
            'user_type',
            'location_id',
            'company_id',
            'scope',
            'expires_at',
            'is_expired',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class GhlUserSerializer(serializers.ModelSerializer):
    """A mirrored GHL user. `raw` stays out — it is a debugging aid, not part
    of the API contract."""

    class Meta:
        model = GhlUser
        fields = [
            'ghl_id',
            'name',
            'first_name',
            'last_name',
            'email',
            'phone',
            'role',
            'role_type',
            'location_id',
            'company_id',
            'student',
            'updated_at',
        ]
        read_only_fields = fields
