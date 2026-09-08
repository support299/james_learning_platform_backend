"""Login identity for a person.

One human is one User. LMS enrollments, GHL, and onboarding cases all hang
off that row. This module finds or creates the User; it does not create
onboarding or GHL rows.
"""

from django.contrib.auth import get_user_model
from django.utils.text import slugify

User = get_user_model()


class IdentityError(Exception):
    pass


def split_full_name(full_name):
    parts = (full_name or '').strip().split(None, 1)
    if not parts:
        return '', ''
    if len(parts) == 1:
        return parts[0], ''
    return parts[0], parts[1]


def unique_username(email, first_name='', last_name=''):
    base = (
        slugify(f'{first_name} {last_name}'.strip())
        or slugify((email or '').split('@')[0])
        or 'agent'
    )
    username = base[:150]
    n = 2
    while User.objects.filter(username=username).exists():
        suffix = f'-{n}'
        username = f'{base[: 150 - len(suffix)]}{suffix}'
        n += 1
    return username


def find_person_by_email(email):
    email = (email or '').strip()
    if not email:
        return None
    return User.objects.filter(email__iexact=email).order_by('pk').first()


def provision_person(
    *,
    email,
    full_name='',
    first_name='',
    last_name='',
    username=None,
    password=None,
):
    """Find or create the User for this email.

    Existing account is returned unchanged (password left alone). New account
    gets an unusable password unless one is passed. Staff accounts are
    returned as-is — callers decide whether that is allowed.
    """
    email = (email or '').strip()
    if not email:
        raise IdentityError('email is required to provision a login')

    user = find_person_by_email(email)
    if user:
        return user, False

    if not first_name and not last_name:
        first_name, last_name = split_full_name(full_name)
    user = User(
        username=username or unique_username(email, first_name, last_name),
        email=email,
        first_name=first_name,
        last_name=last_name,
        is_staff=False,
    )
    if password:
        user.set_password(password)
    else:
        user.set_unusable_password()
    user.save()
    return user, True
