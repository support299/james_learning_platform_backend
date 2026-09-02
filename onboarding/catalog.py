"""Aftermath Academy carrier catalog.

Kept as data (not UI hard-coding) so Settings can still add more later.
Seeded by migration 0002 and `seed_onboarding_carriers`.
"""

# (line, sort_order, code, name)
AFTERMATH_CARRIERS = [
    ('health', 10, 'united-healthcare', 'United Healthcare'),
    ('health', 20, 'allstate', 'Allstate'),
    ('health', 30, 'life-x', 'Life X'),
    ('health', 40, 'amerus', 'Amerus'),
    ('health', 50, 'manhattan-life', 'Manhattan Life'),
    ('health', 60, 'philadelphia-american-life', 'Philadelphia American Life'),
    ('life', 10, 'aig-corebridge', 'AIG/Corebridge'),
    ('life', 20, 'kansas-city-life', 'Kansas City Life'),
    ('life', 30, 'mutual-of-omaha', 'Mutual of Omaha'),
    ('life', 40, 'foresters', 'Foresters'),
    ('life', 50, 'transamerica', 'Transamerica'),
    ('life', 60, 'ethos', 'Ethos'),
]


def upsert_aftermath_carriers(Carrier):
    """Idempotent seed. `Carrier` is the model class (or historical model)."""
    for line, sort_order, code, name in AFTERMATH_CARRIERS:
        defaults = {
            'name': name,
            'line': line,
            'sort_order': sort_order,
            'is_active': True,
        }
        obj, created = Carrier.objects.get_or_create(code=code, defaults=defaults)
        if not created:
            obj.name = name
            obj.line = line
            obj.sort_order = sort_order
            obj.is_active = True
            obj.save(update_fields=['name', 'line', 'sort_order', 'is_active', 'updated_at'])
