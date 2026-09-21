from rest_framework.permissions import BasePermission


class IsSuperUser(BasePermission):
    """DRF's IsAdminUser only checks is_staff; this one checks is_superuser."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_superuser)
