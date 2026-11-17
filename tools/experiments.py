"""Experiments used as evidence in the report. Run from the implementation folder:

    python tools/experiments.py

1. Weight sensitivity: how the example traveller's winner changes with the price weight.
2. Rank reversal: whether removing one offer changes the order of two others (min-max effect).
3. Price stretch: how many euros one price point is worth for each profile.
4. Company exposure: which companies reach the top 3 for each profile.
"""
import logging
import sys
import tempfile
from dataclasses import replace
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from busbidder.cli import App  # noqa: E402
from busbidder.scoring import rank_offers  # noqa: E402

logging.disable(logging.WARNING)  # the dominance warnings for price >= 0.65 are expected here
app = App(Path(tempfile.mkdtemp()))


def winner(request, prefs):
    ranking, _ = app.rank(request, prefs)
    return ranking[0] if ranking else None


print("1. Weight sensitivity (example profile, other weights keep their 25:15:20 ratio)")
p = app.profiles["example"]
rest = {"duration": 25, "departure": 15, "flexibility": 20}


def weights_for(w):
    return {"price": w, **{k: (1 - w) * v / 60 for k, v in rest.items()}}


def totals(w):
    ranking, _ = app.rank(p.request, replace(p.preferences, weights=weights_for(w)))
    return ranking[0], {r.offer.offer_id: r.total for r in ranking}


A, B = "SR-MAL-20260714-1045", "CE-MAL-20260713-0845"  # #1 and #2 under the example weights
print(f"   {'price':>5}  {'winner':<22}{'company':<16}{A:>22}{B:>22}")
previous = None
for step in range(2, 17):  # price weight 0.10 .. 0.80
    w = step * 0.05
    top, t = totals(w)
    marker = "  <- winner changes" if previous and top.offer.offer_id != previous else ""
    print(f"   {w:5.2f}  {top.offer.offer_id:<22}{top.offer.company.name:<16}{t[A]:22.1f}{t[B]:22.1f}{marker}")
    previous = top.offer.offer_id
tipping = next(k / 1000 for k in range(300, 451) if totals(k / 1000)[0].offer.offer_id == A)
print(f"   tipping point: {A} first wins at price weight {tipping:.3f} (the example profile uses 0.400)")

print("\n2. Rank reversal (example profile)")
accepted, _ = app.rank(p.request, p.preferences)
offers = [r.offer for r in accepted]
full = [o.offer_id for o in offers]
reversals = []
for removed in offers:
    rest_ids = [r.offer.offer_id for r in rank_offers([o for o in offers if o is not removed], p.preferences)]
    for a, b in combinations([i for i in full if i != removed.offer_id], 2):
        if (full.index(a) < full.index(b)) != (rest_ids.index(a) < rest_ids.index(b)):
            reversals.append((removed.offer_id, a, b))
print(f"   {len(offers)} acceptable offers; removing one offer at a time gives {len(reversals)} reversed pairs")
for removed, a, b in reversals[:5]:
    print(f"   removing {removed} swaps {a} and {b}")

print("\n3. Price stretch (EUR per price point among acceptable offers)")
for name, prof in app.profiles.items():
    ranking, _ = app.rank(prof.request, prof.preferences)
    prices = [r.offer.price for r in ranking]
    lo, hi = min(prices), max(prices)
    print(f"   {name:<17} {len(prices):>2} offers, EUR {lo:6.2f} - {hi:6.2f}, 1 point = EUR {(hi - lo) / 100:.3f}")

print("\n4. Company exposure in the top 3")
for name, prof in app.profiles.items():
    ranking, _ = app.rank(prof.request, prof.preferences)
    print(f"   {name:<17} " + ", ".join(f"#{r.rank} {r.offer.company.name}" for r in ranking[:3]))
