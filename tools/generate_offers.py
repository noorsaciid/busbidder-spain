"""Generate data/offers.csv reproducibly (fixed seed) from the company profiles.

Run from the implementation folder:  python tools/generate_offers.py
Every company serves every destination (5 x 15 = 75 combinations) on each day of
the window, with 1-2 departures per day. A few deliberately invalid rows are
appended at the end so the validation step has something real to catch.
"""
import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
SEED = 4420
DAYS = [date(2026, 7, 10) + timedelta(days=i) for i in range(7)]  # 10-16 July 2026
MAJOR = {"Barcelona", "Valencia", "Seville", "Malaga", "Bilbao", "Zaragoza"}

# per_km price, speed km/h, departure hours, P(changeable), P(refundable), luggage, rating, kg CO2 per km
PROFILES = {
    "SR": dict(per_km=0.045, speed=68, hours=[7, 10, 14], change=0.25, refund=0.05, luggage=15, rating=3.6, co2=0.030),
    "CE": dict(per_km=0.085, speed=88, hours=[8, 12, 17], change=0.85, refund=0.45, luggage=20, rating=4.4, co2=0.028),
    "CL": dict(per_km=0.065, speed=72, hours=[9, 13, 18], change=0.60, refund=0.20, luggage=25, rating=4.0, co2=0.027),
    "VB": dict(per_km=0.075, speed=74, hours=[8, 11, 16], change=0.65, refund=0.30, luggage=20, rating=4.3, co2=0.012),
    "NV": dict(per_km=0.050, speed=60, hours=[20, 22, 23], change=0.50, refund=0.15, luggage=30, rating=3.9, co2=0.026),
}


def transfers_for(code, dest, coastal, rng):
    if code in ("CE", "NV"):
        return 0 if dest in MAJOR else 1
    if code == "CL":
        return 0 if coastal else rng.choice([1, 2])
    return rng.choice([0, 0, 1])


def generate():
    rng = random.Random(SEED)
    with (DATA / "destinations.csv").open(encoding="utf-8") as f:
        destinations = list(csv.DictReader(f))
    rows = []
    for code, p in PROFILES.items():
        for d in destinations:
            km, coastal = int(d["distance_km"]), d["coastal"] == "yes"
            for day in DAYS:
                for hour in sorted(rng.sample(p["hours"], rng.choice([1, 2]))):
                    dep = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=rng.choice([0, 15, 30, 45]))
                    tr = transfers_for(code, d["name"], coastal, rng)
                    minutes = km / p["speed"] * 60 + tr * 45 + rng.randint(0, 25) + (60 if code == "NV" else 0)
                    price = km * p["per_km"] * rng.uniform(0.85, 1.25) * (0.9 if code == "CL" and coastal else 1)
                    rows.append({
                        "offer_id": f"{code}-{d['code']}-{dep:%Y%m%d-%H%M}",
                        "company": code, "destination": d["name"],
                        "departure": f"{dep:%Y-%m-%d %H:%M}",
                        "arrival": f"{dep + timedelta(minutes=round(minutes)):%Y-%m-%d %H:%M}",
                        "price": f"{max(5.0, price):.2f}", "transfers": tr, "luggage_kg": p["luggage"],
                        "changeable": "yes" if rng.random() < p["change"] else "no",
                        "refundable": "yes" if rng.random() < p["refund"] else "no",
                        "seats": 0 if rng.random() < 0.05 else rng.randint(1, 40),
                        "rating": "" if rng.random() < 0.03 else f"{min(5, max(1, p['rating'] + rng.uniform(-0.3, 0.3))):.1f}",
                        "co2_kg": f"{km * p['co2']:.1f}",
                    })
    good = rows[0]
    invalid = [  # one row per validation rule
        {**good, "offer_id": "SR-MAL-20260712-0900", "destination": "Malaga", "departure": "2026-07-12 09:00",
         "arrival": "2026-07-12 17:00", "price": "-12.00"},                                   # negative price
        {**good, "offer_id": "CE-PAR-20260712-0800", "destination": "Paris"},                   # unknown destination
        {**good, "offer_id": "VB-MAL-20260732-0800", "destination": "Malaga",
         "departure": "2026-07-32 08:00", "arrival": "2026-07-32 15:00"},                       # invalid date
        {**good, "offer_id": "CL-VLC-20260713-1500", "destination": "Valencia",
         "departure": "2026-07-13 15:00", "arrival": "2026-07-13 11:00"},                       # arrival before departure
        {**good, "offer_id": "NV-SEV-20260713-2200", "company": "NV", "destination": "Seville",
         "departure": "2026-07-13 22:00", "arrival": "2026-07-14 06:30", "rating": "7.5"},      # rating outside 1-5
        {**good, "offer_id": "SR-TOL-20260714-0700", "destination": "Toledo",
         "departure": "2026-07-14 07:00", "arrival": "2026-07-14 08:15", "seats": "-3"},        # negative seats
        {**good, "offer_id": "SR-GRA-20260714-1000", "destination": "Granada",
         "departure": "2026-07-14 10:00", "arrival": "2026-07-14 16:30", "price": ""},          # missing price
        {**good, "offer_id": "SR_MAL_2026"},                                                    # malformed id
        {**good},                                                                               # duplicate id
        {**good, "offer_id": "CE" + good["offer_id"][2:]},                                      # id says CE, company is SR
    ]
    with (DATA / "offers.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows + invalid)
    print(f"Wrote {len(rows)} generated + {len(invalid)} deliberately invalid offers to {DATA / 'offers.csv'}")


if __name__ == "__main__":
    generate()
