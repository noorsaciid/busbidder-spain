"""Independent check of BusBidder results, used as testing evidence in the report.

Re-implements validation, filtering and scoring from the written rules, reading only the raw
files, then compares with what the program produces. Run from the implementation folder:

    python tools/verify_independent.py
"""
import csv
import json
import statistics
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
FMT = "%Y-%m-%d %H:%M"
ok = True


def check(cond, msg):
    global ok
    print(("PASS " if cond else "FAIL ") + msg)
    ok &= bool(cond)


# ---------- 1. raw data, own validation ----------
dests = {r["name"]: r for r in csv.DictReader(open(ROOT / "data/destinations.csv", encoding="utf-8"))}
companies = {r["code"]: r["name"] for r in csv.DictReader(open(ROOT / "data/companies.csv", encoding="utf-8"))}
rows = list(csv.DictReader(open(ROOT / "data/offers.csv", encoding="utf-8")))
valid, bad, seen = [], [], set()
for r in rows:
    try:
        dep, arr = datetime.strptime(r["departure"], FMT), datetime.strptime(r["arrival"], FMT)
        price, seats, tr = float(r["price"]), int(r["seats"]), int(r["transfers"])
        rating = float(r["rating"]) if r["rating"] else None
        good = (r["company"] in companies and r["destination"] in dests and arr > dep and price >= 0 and seats >= 0
                and (rating is None or 1 <= rating <= 5) and r["offer_id"] not in seen
                and r["offer_id"] == f"{r['company']}-{dests[r['destination']]['code']}-{dep:%Y%m%d-%H%M}")
    except (ValueError, KeyError):
        good = False
    if good:
        seen.add(r["offer_id"])
        valid.append(dict(r, dep=dep, arr=arr, price=price, seats=seats, transfers=tr, rating=rating,
                          co2=float(r["co2_kg"]) if r["co2_kg"] else None,
                          dur=int((arr - dep).total_seconds() // 60),
                          ch=r["changeable"] == "yes", rf=r["refundable"] == "yes", lug=int(r["luggage_kg"])))
    else:
        bad.append(r["offer_id"])
print(f"Independent validation: {len(valid)} valid, {len(bad)} invalid")

from busbidder.cli import App  # noqa: E402  (program under test)
out = Path(tempfile.mkdtemp())
app = App(out)
check(len(app.store.capacity) == len(valid) and set(app.store.capacity) == {v["offer_id"] for v in valid},
      "program accepts exactly the same offer IDs as the independent validator")
check(len(app.invalid) == len(bad) == 10, f"10 invalid rows set aside (program {len(app.invalid)}, independent {len(bad)})")
check(len({(v["company"], v["destination"]) for v in valid}) == 75, "all 75 company-destination pairs present")
review = list(csv.DictReader(open(out / "rejected_offers.csv", encoding="utf-8")))
check(len(review) == 10 and all(r["reason"] for r in review), "review file lists all 10 with a reason")

# ---------- 2. dataset matches the company profiles ----------
print("\nCompany profile check (all destinations, all dates):")
by = defaultdict(list)
for v in valid:
    by[v["company"]].append(v)
stats = {}
for c, vs in by.items():
    km = [int(dests[v["destination"]]["distance_km"]) for v in vs]
    stats[c] = dict(
        eur_km=statistics.mean(v["price"] / k for v, k in zip(vs, km)),
        kmh=statistics.mean(k / (v["dur"] / 60) for v, k in zip(vs, km)),
        co2_km=statistics.mean(v["co2"] / k for v, k in zip(vs, km)),
        late=statistics.mean(v["dep"].hour >= 20 for v in vs),
        coastal_direct=statistics.mean(v["transfers"] == 0 for v in vs if dests[v["destination"]]["coastal"] == "yes"),
        change=statistics.mean(v["ch"] for v in vs), rating=statistics.mean(v["rating"] for v in vs if v["rating"]))
    s = stats[c]
    print(f"  {companies[c]:<15} EUR/km {s['eur_km']:.3f}  km/h {s['kmh']:5.1f}  CO2/km {s['co2_km']:.3f}  "
          f"late {s['late']:.0%}  coastal direct {s['coastal_direct']:.0%}  changeable {s['change']:.0%}  rating {s['rating']:.2f}")
check(min(stats, key=lambda c: stats[c]["eur_km"]) == "SR", "SolRuta is the cheapest per km (low-cost)")
check(max(stats, key=lambda c: stats[c]["kmh"]) == "CE", "Camino Express is the fastest")
check(min(stats, key=lambda c: stats[c]["co2_km"]) == "VB", "VerdeBus has the lowest CO2")
check(stats["NV"]["late"] == 1 and all(stats[c]["late"] == 0 for c in stats if c != "NV"), "only NocheVia runs late evening")
check(stats["CL"]["coastal_direct"] == 1, "CostaLink is always direct to coastal destinations")


# ---------- 3. filtering and scoring per profile ----------
def mins_apart(a, b):
    d = abs(a.hour * 60 + a.minute - (b.hour * 60 + b.minute))
    return min(d, 1440 - d)


RAW = {"price": lambda v, p: v["price"], "duration": lambda v, p: v["dur"], "transfers": lambda v, p: v["transfers"],
       "rating": lambda v, p: v["rating"], "co2": lambda v, p: v["co2"],
       "departure": lambda v, p: max(0, 100 - mins_apart(v["dep"].time(), p) / 6),
       "flexibility": lambda v, p: 50 * v["ch"] + 50 * v["rf"]}
LOWER_BETTER = {"price", "duration", "transfers", "co2"}
ABSOLUTE = {"departure", "flexibility"}

print()
winners = {}
for name, prof in app.profiles.items():
    req, pref = prof.request, prof.preferences
    seats_left = {s.offer_id: s.seats for s in app.store.current()}
    acc = [v for v in valid if v["destination"] == req.destination
           and req.date_from <= v["dep"].date() <= req.date_to and seats_left[v["offer_id"]] >= req.passengers
           and (req.max_price is None or v["price"] <= req.max_price)
           and (req.max_transfers is None or v["transfers"] <= req.max_transfers)
           and (req.max_duration_min is None or v["dur"] <= req.max_duration_min)
           and (req.min_luggage_kg is None or v["lug"] >= req.min_luggage_kg)
           and (not req.changeable_required or v["ch"]) and (not req.refundable_required or v["rf"])]
    scores = {v["offer_id"]: {} for v in acc}
    for c in pref.weights:
        raw = {v["offer_id"]: RAW[c](v, pref.preferred_departure) for v in acc}
        known = [x for x in raw.values() if x is not None]
        lo, hi = min(known), max(known)
        for oid, x in raw.items():
            if x is None:
                s = 50
            elif c in ABSOLUTE:
                s = x
            elif hi == lo:
                s = 100
            else:
                s = 100 * ((hi - x) / (hi - lo) if c in LOWER_BETTER else (x - lo) / (hi - lo))
            scores[oid][c] = s
    total = {oid: sum(pref.weights[c] * s[c] for c in pref.weights) for oid, s in scores.items()}
    by_id = {v["offer_id"]: v for v in acc}
    mine = sorted(total, key=lambda o: (-round(total[o], 6), by_id[o]["price"], by_id[o]["dep"], o))

    ranking, rejected = app.rank(req, pref)
    theirs = [r.offer.offer_id for r in ranking]
    same_order = mine == theirs
    same_totals = all(abs(r.total - total[r.offer.offer_id]) < 1e-5 for r in ranking)
    same_parts = all(abs(r.criterion_scores[c] - scores[r.offer.offer_id][c]) < 1e-9 for r in ranking for c in pref.weights)
    check(same_order and same_totals and same_parts,
          f"{name:<17} {len(acc):>2} acceptable; order, totals and per-criterion scores match independent calculation")
    # every rejected offer on this route/window really violates something, with the right count
    check(len(ranking) + len(rejected) == len(valid), f"{name:<17} accepted + rejected = all valid offers")
    winners[name] = by_id[mine[0]]
    w = winners[name]
    print(f"     #1 {w['offer_id']} {companies[w['company']]}, EUR {w['price']:.2f}, {w['dur'] // 60}h{w['dur'] % 60:02d}, "
          f"dep {w['dep']:%H:%M}, {w['transfers']} tr, ch={w['ch']} rf={w['rf']}, CO2 {w['co2']}, score {total[mine[0]]:.2f}"
          + (f"; #2 {mine[1]} {total[mine[1]]:.2f}" if len(mine) > 1 else ""))

# ---------- 4. are the winners reasonable for each profile? ----------
print()
pool = {n: [v for v in valid if v["destination"] == "Malaga" and date(2026, 7, 12) <= v["dep"].date() <= date(2026, 7, 14)]
        for n in winners}
check(winners["budget"]["price"] <= min(v["price"] for v in pool["budget"] if v["price"] <= 40 and v["transfers"] <= 2) + 3,
      "budget winner is within EUR 3 of the cheapest acceptable fare")
check(winners["business"]["transfers"] == 0 and winners["business"]["ch"], "business winner is direct and changeable")
check(winners["flexible_tourist"]["ch"] and winners["flexible_tourist"]["rf"], "flexible tourist winner is changeable and refundable")
check(winners["eco"]["company"] == "VB", "eco winner is VerdeBus (lowest CO2)")
check(len({w["offer_id"] for w in winners.values()}) >= 3, "at least three different winners across profiles")

# ---------- 5. booking capacity cannot go negative ----------
from busbidder.errors import BookingError  # noqa: E402
oid = winners["business"]["offer_id"]
left = app.store.seats_left(oid)
for _ in range(left):
    app.store.book(oid, "Load Test")
try:
    app.store.book(oid, "One Too Many")
    check(False, "overbooking refused")
except BookingError:
    check(app.store.seats_left(oid) == 0, f"booked all {left} seats, the next booking is refused, 0 left")
app2 = App(out)
check(app2.store.seats_left(oid) == 0 and len(app2.store.bookings) == left, "capacity and bookings survive a restart")
check(oid not in [r.offer.offer_id for r in app2.rank(app.profiles["business"].request, app.profiles["business"].preferences)[0]],
      "sold-out offer disappears from the business ranking")
print("\nALL CHECKS PASSED" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)
