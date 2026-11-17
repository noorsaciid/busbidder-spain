"""Weighted multi-criteria ranking.

Score(j) = sum over criteria c of  w_c * S_c(j),   S_c(j) in [0, 100],  sum(w_c) = 1

Every criterion is turned into a 0-100 score (higher = better) before weighting:
- relative criteria (price, duration, ...) use min-max over the acceptable offers,
  inverted when lower raw values are better;
- absolute criteria (departure, flexibility) already produce 0-100 points, because
  "3 hours from my preferred time" is bad even if every offer is that bad.
If all offers share a value, everyone gets 100: the criterion cannot separate them.
A missing value gets a neutral 50 and is reported, which puts the offer in the middle of
the range for that criterion.
"""
import logging
from dataclasses import dataclass
from datetime import time
from typing import Callable

from .errors import ConfigError
from .models import Offer, Preferences, RankedOffer

log = logging.getLogger(__name__)

NEUTRAL = 50.0
MIN_CRITERIA = 4
DOMINANCE_WARNING = 0.6  # a single weight above this is allowed but logged


@dataclass(frozen=True)
class Criterion:
    name: str
    raw: Callable[[Offer, Preferences], float | None]
    higher_is_better: bool
    relative: bool
    describe: Callable[[Offer], str]  # plain-language value used in explanations


CRITERIA: dict[str, Criterion] = {}


def criterion(name, *, higher_is_better, relative=True, describe):
    """Decorator registering a scoring criterion under `name`."""
    def register(fn):
        if name in CRITERIA:
            raise ValueError(f"criterion {name!r} is already registered")
        CRITERIA[name] = Criterion(name, fn, higher_is_better, relative, describe)
        return fn
    return register


def _minutes_apart(a: time, b: time) -> int:
    """Clock distance in minutes, wrapping midnight (23:30 and 00:30 are 60 apart)."""
    d = abs((a.hour * 60 + a.minute) - (b.hour * 60 + b.minute))
    return min(d, 24 * 60 - d)


@criterion("price", higher_is_better=False, describe=lambda o: f"price EUR {o.price:.2f}")
def _price(o, p):
    return o.price


@criterion("duration", higher_is_better=False,
           describe=lambda o: f"journey {o.duration_min // 60}h{o.duration_min % 60:02d}")
def _duration(o, p):
    return o.duration_min


@criterion("departure", higher_is_better=True, relative=False,
           describe=lambda o: f"departs {o.departure:%H:%M}")
def _departure(o, p):
    # 100 at the preferred time, minus 10 points per hour away, floor 0.
    return max(0.0, 100 - _minutes_apart(o.departure.time(), p.preferred_departure) / 6)


@criterion("flexibility", higher_is_better=True, relative=False, describe=lambda o: f"{o.fare.label} fare")
def _flexibility(o, p):
    return 50.0 * o.fare.changeable + 50.0 * o.fare.refundable


@criterion("transfers", higher_is_better=False,
           describe=lambda o: "direct route" if o.transfers == 0 else f"{o.transfers} transfer(s)")
def _transfers(o, p):
    return o.transfers


@criterion("rating", higher_is_better=True,
           describe=lambda o: f"rating {o.rating:.1f}/5" if o.rating is not None else "no rating")
def _rating(o, p):
    return o.rating


@criterion("co2", higher_is_better=False,
           describe=lambda o: f"{o.co2_kg:.1f} kg CO2" if o.co2_kg is not None else "no CO2 data")
def _co2(o, p):
    return o.co2_kg


def validate_weights(weights: dict) -> dict[str, float]:
    """Check names and values; accept fractions summing to 1 or percentages summing to 100."""
    if not isinstance(weights, dict) or not weights:
        raise ConfigError("weights must be a non-empty mapping of criterion -> weight")
    unknown = set(weights) - set(CRITERIA)
    if unknown:
        raise ConfigError(f"unknown criteria {sorted(unknown)}; choose from {sorted(CRITERIA)}")
    try:
        weights = {k: float(v) for k, v in weights.items()}
    except (TypeError, ValueError):
        raise ConfigError("weights must be numbers")
    if any(w < 0 for w in weights.values()):
        raise ConfigError("weights cannot be negative")
    total = sum(weights.values())
    if abs(total - 100) < 1e-6:
        weights, total = {k: w / 100 for k, w in weights.items()}, 1.0
    if abs(total - 1) > 1e-6:
        raise ConfigError(f"weights sum to {total:g}; they must sum to 1.0 (or 100%)")
    if sum(w > 0 for w in weights.values()) < MIN_CRITERIA:
        raise ConfigError(f"at least {MIN_CRITERIA} criteria need a weight above 0")
    for k, w in weights.items():
        if w > DOMINANCE_WARNING:
            log.warning("Criterion %s carries %.0f%% of the score and may dominate the ranking", k, w * 100)
    return weights


def criterion_scores(offers: list[Offer], prefs: Preferences):
    """Return one ({criterion: 0-100 score}, missing criteria) pair per offer."""
    scores = [{} for _ in offers]
    missing = [[] for _ in offers]
    for name in prefs.weights:
        c = CRITERIA[name]
        raws = [c.raw(o, prefs) for o in offers]
        known = [r for r in raws if r is not None]
        lo, hi = (min(known), max(known)) if known else (0.0, 0.0)
        for i, r in enumerate(raws):
            if r is None:
                scores[i][name] = NEUTRAL
                missing[i].append(name)
            elif not c.relative:
                scores[i][name] = float(r)
            elif hi == lo:
                scores[i][name] = 100.0
            else:
                frac = (r - lo) / (hi - lo)
                scores[i][name] = 100.0 * (frac if c.higher_is_better else 1 - frac)
    return list(zip(scores, missing))


def rank_offers(offers: list[Offer], prefs: Preferences) -> list[RankedOffer]:
    """Rank acceptable offers. Ties break on lower price, earlier departure, then offer id,
    so the same input always yields the same order."""
    weights = validate_weights(prefs.weights)
    scored = criterion_scores(offers, prefs)
    totals = [round(sum(weights[n] * s[n] for n in weights), 6) for s, _ in scored]
    order = sorted(range(len(offers)),
                   key=lambda i: (-totals[i], offers[i].price, offers[i].departure, offers[i].offer_id))
    return [RankedOffer(rank, offers[i], totals[i], scored[i][0], tuple(scored[i][1]))
            for rank, i in enumerate(order, 1)]


def explain(ranking: list[RankedOffer], prefs: Preferences) -> str:
    """Short data-driven explanation of the top offer, its trade-offs and the caveat."""
    if not ranking:
        return "No offer satisfies every hard constraint; relax a constraint and try again."
    top = ranking[0]
    o = top.offer
    used = [n for n, w in prefs.weights.items() if w > 0]
    strengths = [CRITERIA[n].describe(o) for n in used if top.criterion_scores[n] >= 75 and n not in top.missing]
    weaknesses = [CRITERIA[n].describe(o) for n in used if top.criterion_scores[n] < 50 and n not in top.missing]
    heaviest = max(prefs.weights, key=prefs.weights.get)
    lines = [f"{o.company.name} {o.offer_id} ranks first with {top.total:.1f}/100."]
    if strengths:
        lines.append("Strengths: " + ", ".join(strengths) + ".")
    if weaknesses:
        lines.append("Trade-offs: " + ", ".join(weaknesses) + ".")
    if top.missing:
        lines.append(f"Scored neutrally for missing data: {', '.join(top.missing)}.")
    if len(ranking) > 1:
        gap = top.total - ranking[1].total
        if gap < 2:
            lines.append(f"Only {gap:.1f} points ahead of #2 ({ranking[1].offer.offer_id}): "
                         "effectively a tie under these weights.")
    lines.append(f"It ranks first only under the chosen constraints and weights "
                 f"({heaviest} weighted most at {prefs.weights[heaviest]:.0%}).")
    return "\n".join(lines)
