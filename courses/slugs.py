from django.utils.text import slugify


def unique_slug(base, existing):
    """Return `base` (or `base-2`, `base-3`, …) not present in `existing`.

    Mirrors the frontend's uniqueSlug() so ids generated server-side follow
    the same convention.
    """
    slug = slugify(base) or 'untitled'
    if slug not in existing:
        return slug
    i = 2
    while f'{slug}-{i}' in existing:
        i += 1
    return f'{slug}-{i}'
