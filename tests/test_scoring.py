import unittest
from datetime import time

from busbidder.errors import ConfigError
from busbidder.models import FareConditions
from busbidder.scoring import _minutes_apart, criterion_scores, explain, rank_offers, validate_weights
from tests.helpers import CE, offer, prefs


class WeightTest(unittest.TestCase):
    def test_fractions_and_percentages(self):
        self.assertAlmostEqual(sum(validate_weights({"price": 0.4, "duration": 0.25, "departure": 0.15, "flexibility": 0.2}).values()), 1)
        self.assertEqual(validate_weights({"price": 40, "duration": 25, "departure": 15, "flexibility": 20})["price"], 0.4)

    def test_invalid_weights(self):
        for bad in ({"price": 0.5, "duration": 0.25, "departure": 0.15, "flexibility": 0.2},   # sums to 1.1
                    {"price": -0.1, "duration": 0.5, "departure": 0.4, "flexibility": 0.2},    # negative
                    {"price": 0.4, "speed": 0.25, "departure": 0.15, "flexibility": 0.2},      # unknown
                    {"price": 0.7, "duration": 0.3, "departure": 0, "flexibility": 0},         # < 4 criteria used
                    {}, {"price": "a lot", "duration": 0.25, "departure": 0.15, "flexibility": 0.2}):
            with self.assertRaises(ConfigError, msg=bad):
                validate_weights(bad)


class NormalisationTest(unittest.TestCase):
    def test_lower_price_scores_higher(self):
        (cheap, _), (mid, _), (dear, _) = criterion_scores([offer(price=20), offer(price=30), offer(price=40)], prefs())
        self.assertEqual((cheap["price"], mid["price"], dear["price"]), (100, 50, 0))

    def test_identical_values_do_not_separate(self):
        scores = criterion_scores([offer(price=30), offer(price=30)], prefs())
        self.assertEqual([s["price"] for s, _ in scores], [100, 100])

    def test_missing_value_scores_neutral_and_is_flagged(self):
        w = {"price": 0.4, "duration": 0.2, "departure": 0.1, "flexibility": 0.1, "rating": 0.2}
        scores = criterion_scores([offer(rating=None), offer(rating=3.0), offer(rating=5.0)], prefs(w))
        self.assertEqual(scores[0][0]["rating"], 50)
        self.assertEqual(scores[0][1], ["rating"])

    def test_departure_is_absolute_and_wraps_midnight(self):
        self.assertEqual(_minutes_apart(time(23, 30), time(0, 30)), 60)
        (s, _), = criterion_scores([offer(dep="2026-07-12 00:30")], prefs(at="23:30"))
        self.assertEqual(s["departure"], 90)
        (far, _), = criterion_scores([offer(dep="2026-07-12 21:00")], prefs(at="09:00"))
        self.assertEqual(far["departure"], 0)  # floor, never negative

    def test_flexibility_categories(self):
        both, change, none = (offer(fare=FareConditions(c, r, 20)) for c, r in ((True, True), (True, False), (False, False)))
        scores = [s["flexibility"] for s, _ in criterion_scores([both, change, none], prefs())]
        self.assertEqual(scores, [100, 50, 0])

    def test_all_scores_within_0_100(self):
        offers = [offer(price=p, minutes=m, dep=d) for p, m, d in
                  ((5, 900, "2026-07-12 03:00"), (500, 60, "2026-07-12 09:00"), (25, 400, "2026-07-13 15:00"))]
        for r in rank_offers(offers, prefs()):
            self.assertTrue(0 <= r.total <= 100)
            self.assertTrue(all(0 <= v <= 100 for v in r.criterion_scores.values()))


class RankingTest(unittest.TestCase):
    def test_weights_change_the_winner(self):
        cheap_slow = offer(price=20, minutes=600)
        fast_dear = offer(price=45, minutes=300, company=CE)
        price_first = {"price": 0.7, "duration": 0.1, "departure": 0.1, "flexibility": 0.1}
        time_first = {"price": 0.1, "duration": 0.7, "departure": 0.1, "flexibility": 0.1}
        self.assertIs(rank_offers([cheap_slow, fast_dear], prefs(price_first))[0].offer, cheap_slow)
        self.assertIs(rank_offers([cheap_slow, fast_dear], prefs(time_first))[0].offer, fast_dear)

    def test_ties_are_broken_reproducibly(self):
        a, b = offer(dep="2026-07-12 09:00"), offer(dep="2026-07-13 09:00")  # same score
        self.assertEqual([r.offer for r in rank_offers([b, a], prefs())], [a, b])  # earlier departure first

    def test_empty_input(self):
        self.assertEqual(rank_offers([], prefs()), [])
        self.assertIn("No offer", explain([], prefs()))

    def test_explanation_names_strengths_weaknesses_and_caveat(self):
        ranking = rank_offers([offer(price=20, minutes=600), offer(price=45, minutes=300, company=CE)], prefs())
        text = explain(ranking, prefs())
        self.assertIn("Strengths", text)
        self.assertIn("Trade-offs", text)
        self.assertIn("ranks first only under the chosen constraints and weights", text)


if __name__ == "__main__":
    unittest.main()
