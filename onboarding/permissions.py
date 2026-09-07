from rest_framework.permissions import BasePermission

from .constants import (
    GROUP_ASSISTANT,
    GROUP_LEADERSHIP,
    GROUP_RECRUITER,
    ROLE_ASSISTANT,
    ROLE_LEADERSHIP,
    ROLE_RECRUITER,
)


def onboarding_role(user):
    """Return assistant/recruiter/leadership, or None.

    Superusers count as assistants so the first staff account can bootstrap
    the module. Students (is_staff=False) never get a role.
    """
    if not getattr(user, 'is_authenticated', False):
        return None
    if not user.is_staff:
        return None
    if user.is_superuser:
        return ROLE_ASSISTANT
    names = set(user.groups.values_list('name', flat=True))
    if GROUP_ASSISTANT in names:
        return ROLE_ASSISTANT
    if GROUP_RECRUITER in names:
        return ROLE_RECRUITER
    if GROUP_LEADERSHIP in names:
        return ROLE_LEADERSHIP
    return None


def is_assistant(user):
    return onboarding_role(user) == ROLE_ASSISTANT


def recruiter_owns_agent(user, agent):
    return agent.owner_id == user.id


def recruiter_can_edit_item(user, agent, item):
    return item.owner_id == user.id or recruiter_owns_agent(user, agent)


def is_onboarding_person(user, agent):
    """True when this login is the person on the case, not staff managing it."""
    return bool(
        user
        and getattr(user, 'is_authenticated', False)
        and agent is not None
        and agent.user_id == user.id
    )


class HasOnboardingAccess(BasePermission):
    """Any onboarding group (or superuser). Students and ungrouped staff: 403."""

    def has_permission(self, request, view):
        return onboarding_role(request.user) is not None


class IsOnboardingAssistant(BasePermission):
    def has_permission(self, request, view):
        return is_assistant(request.user)


class OnboardingWritePermission(BasePermission):
    """GET: any onboarding role. Unsafe: assistant, recruiter, or the agent."""

    def has_permission(self, request, view):
        role = onboarding_role(request.user)
        if role is not None:
            if request.method in ('GET', 'HEAD', 'OPTIONS'):
                return True
            return role in (ROLE_ASSISTANT, ROLE_RECRUITER)
        if not getattr(request.user, 'is_authenticated', False):
            return False
        if request.user.is_staff:
            return False
        from .models import OnboardingAgent

        return OnboardingAgent.objects.filter(user=request.user).exists()

    def has_object_permission(self, request, view, obj):
        role = onboarding_role(request.user)
        if role == ROLE_ASSISTANT:
            return True
        agent = getattr(obj, 'agent', obj)
        if is_onboarding_person(request.user, agent):
            return True
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return role is not None
        if role != ROLE_RECRUITER:
            return False
        item_owner = getattr(obj, 'owner_id', None)
        if item_owner is not None and obj is not agent:
            return recruiter_can_edit_item(request.user, agent, obj)
        return recruiter_owns_agent(request.user, agent)
