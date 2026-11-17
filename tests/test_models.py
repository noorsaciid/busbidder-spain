"""The Bounded descriptor, tested with the cases the course metaprogramming lab requires:
boundary values accepted, values below/above the range rejected, wrong types rejected."""
import dataclasses
import unittest

from busbidder.models import Bounded, FareConditions, Offer, TravelRequest
from tests.helpers import offer, request


class BoundedDescriptorTest(unittest.TestCase):
    def test_boundaries_are_accepted(self):
        self.assertEqual(offer(rating=1.0).rating, 1.0)
        self.assertEqual(offer(rating=5.0).rating, 5.0)
        self.assertEqual(offer(price=0.0, seats=0).seats, 0)
        self.assertEqual(request(passengers=1, max_price=0).max_price, 0)

    def test_out_of_range_is_rejected(self):
        for kw in ({"price": -0.01}, {"seats": -1}, {"transfers": -1}, {"rating": 0.99}, {"rating": 5.01},
                   {"co2_kg": -1.0}):
            with self.assertRaises(ValueError, msg=kw):
                offer(**kw)
        with self.assertRaises(ValueError):
            request(passengers=0)

    def test_wrong_type_is_rejected(self):
        for kw in ({"price": "25"}, {"seats": 2.5}, {"seats": True}, {"price": None}):
            with self.assertRaises(TypeError, msg=kw):
                offer(**kw)
        with self.assertRaises(TypeError):
            request(max_price="50")
        with self.assertRaises(TypeError):
            FareConditions(True, False, "20")

    def test_optional_fields_accept_none_and_have_defaults(self):
        self.assertIsNone(offer(rating=None).rating)
        r = TravelRequest("Malaga", request().date_from, request().date_to)
        self.assertEqual((r.max_price, r.max_transfers, r.passengers), (None, None, 1))

    def test_one_descriptor_class_serves_several_models(self):
        fields = {(cls.__name__, name) for cls in (Offer, FareConditions, TravelRequest)
                  for name, attr in vars(cls).items() if isinstance(attr, Bounded)}
        self.assertIn(("Offer", "price"), fields)
        self.assertIn(("FareConditions", "luggage_kg"), fields)
        self.assertIn(("TravelRequest", "passengers"), fields)

    def test_name_discovered_and_value_stored_internally(self):
        o = offer(price=12.5)
        self.assertEqual(vars(Offer)["price"].name, "price")  # learned via __set_name__
        self.assertEqual(vars(o)["_price"], 12.5)

    def test_objects_stay_frozen_and_replace_revalidates(self):
        o = offer()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            o.price = 1.0
        with self.assertRaises(ValueError):
            dataclasses.replace(o, seats=-1)


if __name__ == "__main__":
    unittest.main()
