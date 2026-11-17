import unittest
from dataclasses import replace

from busbidder.constraints import hard_constraint, split_offers
from busbidder.models import FareConditions
from tests.helpers import offer, request


def rejected_by(o, req):
    _, rejected = split_offers([o], req)
    return [r.split(":")[0] for r in rejected[0][1]] if rejected else []


class HardConstraintTest(unittest.TestCase):
    def test_acceptable_offer_passes(self):
        self.assertEqual(rejected_by(offer(), request(max_price=50, changeable_required=True)), [])

    def test_budget_boundary(self):
        self.assertEqual(rejected_by(offer(price=50.0), request(max_price=50)), [])
        self.assertEqual(rejected_by(offer(price=50.01), request(max_price=50)), ["budget"])

    def test_travel_window_is_inclusive(self):
        self.assertEqual(rejected_by(offer(dep="2026-07-12 00:00"), request()), [])
        self.assertEqual(rejected_by(offer(dep="2026-07-14 23:59"), request()), [])
        self.assertEqual(rejected_by(offer(dep="2026-07-15 00:00"), request()), ["travel window"])

    def test_seats_and_passengers(self):
        self.assertEqual(rejected_by(offer(seats=0), request()), ["seats"])
        self.assertEqual(rejected_by(offer(seats=2), request(passengers=2)), [])
        self.assertEqual(rejected_by(offer(seats=2), request(passengers=3)), ["seats"])

    def test_fare_and_route_constraints(self):
        fixed = FareConditions(False, False, 10)
        o = offer(fare=fixed, transfers=2, minutes=700)
        req = request(changeable_required=True, refundable_required=True, max_transfers=1,
                      max_duration_min=600, min_luggage_kg=20)
        self.assertEqual(sorted(rejected_by(o, req)),
                         sorted(["changeable ticket", "refundable ticket", "max transfers", "max duration", "luggage"]))

    def test_duplicate_registration_is_refused(self):
        with self.assertRaises(ValueError):
            hard_constraint("budget")(lambda o, r: None)  # would otherwise silently disable the budget check
        self.assertEqual(rejected_by(offer(price=99), request(max_price=50)), ["budget"])

    def test_every_violation_is_reported(self):
        o = replace(offer(price=99), seats=0)
        self.assertEqual(rejected_by(o, request(max_price=50)), ["seats", "budget"])


if __name__ == "__main__":
    unittest.main()
