"""Small builders so each test states only the fields it cares about."""
from dataclasses import replace
from datetime import date, datetime, time, timedelta

from busbidder.models import Company, Destination, FareConditions, Offer, Preferences, TravelRequest

SR = Company("SR", "SolRuta", "low cost")
CE = Company("CE", "Camino Express", "fast")
COMPANIES = {"SR": SR, "CE": CE}
MALAGA = Destination("Malaga", "MAL", 530, True)
DESTINATIONS = {"Malaga": MALAGA, "Toledo": Destination("Toledo", "TOL", 75, False)}


def offer(dep="2026-07-12 09:00", minutes=400, price=30.0, company=SR, **kw) -> Offer:
    d = datetime.fromisoformat(dep)
    base = Offer(f"{company.code}-MAL-{d:%Y%m%d-%H%M}", company, MALAGA, d, d + timedelta(minutes=minutes),
                 price, 0, FareConditions(True, False, 20), 10, 4.0, 15.0)
    return replace(base, **kw)


def request(**kw) -> TravelRequest:
    return replace(TravelRequest("Malaga", date(2026, 7, 12), date(2026, 7, 14)), **kw)


WEIGHTS = {"price": 0.4, "duration": 0.25, "departure": 0.15, "flexibility": 0.2}


def prefs(weights=None, at="09:00") -> Preferences:
    return Preferences(time.fromisoformat(at), weights or dict(WEIGHTS))


def row(**kw) -> dict:
    """A valid offers.csv row as strings; override fields to make it invalid."""
    base = {"offer_id": "SR-MAL-20260712-0900", "company": "SR", "destination": "Malaga",
            "departure": "2026-07-12 09:00", "arrival": "2026-07-12 16:00", "price": "25.50",
            "transfers": "0", "luggage_kg": "20", "changeable": "yes", "refundable": "no",
            "seats": "12", "rating": "4.1", "co2_kg": "15.9"}
    return {**base, **kw}
