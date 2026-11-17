"""Hard constraints. Each is registered by a decorator, so adding a constraint is one
function and the CLI lists them automatically.

A check returns a short reason when the offer violates the constraint, else None.
"""
from collections import Counter

from .models import Offer, TravelRequest

HARD_CONSTRAINTS = {}


def hard_constraint(name: str):
    def register(check):
        if name in HARD_CONSTRAINTS:
            raise ValueError(f"hard constraint {name!r} is already registered")
        HARD_CONSTRAINTS[name] = check
        return check
    return register


@hard_constraint("destination")
def _destination(o: Offer, r: TravelRequest):
    if o.destination.name != r.destination:
        return f"goes to {o.destination.name}"


@hard_constraint("travel window")
def _window(o, r):
    if not r.date_from <= o.travel_date <= r.date_to:
        return f"departs {o.travel_date}, outside {r.date_from}..{r.date_to}"


@hard_constraint("seats")
def _seats(o, r):
    if o.seats < r.passengers:
        return f"{o.seats} seat(s) left, {r.passengers} needed"


@hard_constraint("budget")
def _budget(o, r):
    if r.max_price is not None and o.price > r.max_price:
        return f"EUR {o.price:.2f} > EUR {r.max_price:.2f}"


@hard_constraint("max transfers")
def _transfers(o, r):
    if r.max_transfers is not None and o.transfers > r.max_transfers:
        return f"{o.transfers} transfers > {r.max_transfers}"


@hard_constraint("max duration")
def _duration(o, r):
    if r.max_duration_min is not None and o.duration_min > r.max_duration_min:
        return f"{o.duration_min} min > {r.max_duration_min} min"


@hard_constraint("luggage")
def _luggage(o, r):
    if r.min_luggage_kg is not None and o.fare.luggage_kg < r.min_luggage_kg:
        return f"{o.fare.luggage_kg} kg < {r.min_luggage_kg} kg"


@hard_constraint("changeable ticket")
def _changeable(o, r):
    if r.changeable_required and not o.fare.changeable:
        return "ticket not changeable"


@hard_constraint("refundable ticket")
def _refundable(o, r):
    if r.refundable_required and not o.fare.refundable:
        return "ticket not refundable"


def active_constraints(r: TravelRequest) -> list[str]:
    """Human-readable list of the hard constraints this request actually sets."""
    parts = [f"destination = {r.destination}", f"date {r.date_from}..{r.date_to}",
             f"seats >= {r.passengers}"]
    if r.max_price is not None:
        parts.append(f"price <= EUR {r.max_price:g}")
    if r.max_transfers is not None:
        parts.append(f"transfers <= {r.max_transfers}")
    if r.max_duration_min is not None:
        parts.append(f"duration <= {r.max_duration_min} min")
    if r.min_luggage_kg is not None:
        parts.append(f"luggage >= {r.min_luggage_kg} kg")
    if r.changeable_required:
        parts.append("changeable required")
    if r.refundable_required:
        parts.append("refundable required")
    return parts


def split_offers(offers: list[Offer], request: TravelRequest):
    """Return (acceptable offers, [(offer, [reasons])] for every rejected offer)."""
    accepted, rejected = [], []
    for o in offers:
        reasons = [f"{name}: {why}" for name, check in HARD_CONSTRAINTS.items() if (why := check(o, request))]
        if reasons:
            rejected.append((o, reasons))
        else:
            accepted.append(o)
    return accepted, rejected


def rejection_summary(rejected) -> Counter:
    """Count violated constraints among offers on the requested route and dates.
    Offers to other destinations or dates are left out: there are hundreds of them and
    they say nothing about why this traveller's options were removed."""
    counts = Counter()
    for _, reasons in rejected:
        names = [r.split(":")[0] for r in reasons]
        if "destination" not in names and "travel window" not in names:
            counts.update(names)
    return counts
