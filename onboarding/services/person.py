"""Attach a login User to an onboarding case."""

from accounts.services import IdentityError, provision_person

from ..models import OnboardingAgent


class PersonLinkError(Exception):
    def __init__(self, message, field='email'):
        super().__init__(message)
        self.field = field


def ensure_user_available(user, agent):
    if user is None:
        return
    if user.is_staff:
        raise PersonLinkError(
            'That email belongs to a staff account.',
            field='email',
        )
    taken = OnboardingAgent.objects.filter(user=user)
    if agent.pk:
        taken = taken.exclude(pk=agent.pk)
    if taken.exists():
        raise PersonLinkError(
            'That account already has an onboarding case.',
            field='email',
        )


def apply_person_link(agent, *, email='', user=None, user_in_payload=False):
    """Set agent.user / agent.email.

    Explicit `user_id` wins. Otherwise a non-empty email on an unlinked case
    finds or creates the User.
    """
    email = (email or '').strip()

    if user_in_payload:
        if user is not None:
            ensure_user_available(user, agent)
            if not email:
                email = user.email or ''
        agent.user = user
        agent.email = email
        agent.save(update_fields=['user', 'email'])
        return agent

    agent.email = email
    if email and agent.user_id is None:
        try:
            person, _created = provision_person(
                email=email, full_name=agent.full_name
            )
        except IdentityError as exc:
            raise PersonLinkError(str(exc), field='email') from exc
        ensure_user_available(person, agent)
        agent.user = person
    agent.save(update_fields=['user', 'email'])
    return agent
