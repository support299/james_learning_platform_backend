from .status import compute_status


def _carrier_label(req):
    return req.carrier.name


def progress_for(agent):
    """Derived completion, outstanding labels, and flags. Not stored."""
    items = list(agent.checklist_items.all())
    carriers = list(agent.carrier_requirements.all())

    required_items = [i for i in items if i.is_required]
    required_carriers = [c for c in carriers if c.is_required]

    total = len(required_items) + len(required_carriers)
    done = sum(1 for i in required_items if i.is_completed) + sum(
        1 for c in required_carriers if c.is_complete
    )
    percent = 100 if total == 0 else round(100 * done / total)

    outstanding = []
    for item in required_items:
        if not item.is_completed:
            outstanding.append(item.label)
    for req in required_carriers:
        if not req.is_complete:
            outstanding.append(_carrier_label(req))

    flagged = (
        any(i.is_flagged and not i.is_completed for i in required_items)
        or any(c.is_flagged and not c.is_complete for c in required_carriers)
        or any(i.is_flagged for i in items)
        or any(c.is_flagged for c in carriers)
    )

    return {
        'total_required': total,
        'completed_required': done,
        'completion_percent': percent,
        'outstanding': outstanding,
        'flagged': flagged,
        'items': items,
        'carriers': carriers,
    }


def annotate_agent(agent, settings):
    progress = progress_for(agent)
    return {
        **progress,
        'status': compute_status(agent, progress, settings),
    }
