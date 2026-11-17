import csv
import tempfile
import unittest
from pathlib import Path

from busbidder import loader
from busbidder.errors import ConfigError, DataFileError, InvalidRecordError
from tests.helpers import COMPANIES, DESTINATIONS, row

DATA = Path(__file__).resolve().parent.parent / "data"


class ParseOfferTest(unittest.TestCase):
    def parse(self, **kw):
        return loader.parse_offer(row(**kw), COMPANIES, DESTINATIONS)

    def assertRejected(self, fragment, **kw):
        with self.assertRaises(InvalidRecordError) as ctx:
            self.parse(**kw)
        self.assertIn(fragment, ctx.exception.reason)

    def test_valid_row_is_converted(self):
        o = self.parse()
        self.assertEqual(o.price, 25.5)
        self.assertEqual(o.duration_min, 420)
        self.assertTrue(o.fare.changeable)
        self.assertFalse(o.fare.refundable)

    def test_invalid_values(self):
        self.assertRejected("price must be at least 0", price="-1")
        self.assertRejected("unknown destination", destination="Paris")
        self.assertRejected("invalid date", departure="2026-07-32 09:00")
        self.assertRejected("arrival is not after departure", arrival="2026-07-12 08:00")
        self.assertRejected("arrival is not after departure", arrival="2026-07-12 09:00")  # zero-length boundary
        self.assertRejected("rating must be at most 5", rating="5.1")
        self.assertRejected("rating must be at least 1", rating="0.9")
        self.assertRejected("seats must be at least 0", seats="-1")
        self.assertRejected("luggage_kg must be at least 0", luggage_kg="-20")
        self.assertRejected("co2_kg must be at least 0", co2_kg="-0.1")
        self.assertRejected("bad number", price="cheap")
        self.assertRejected("expected yes/no", changeable="maybe")
        self.assertRejected("unknown company", company="XX")

    def test_missing_values(self):
        self.assertRejected("missing value(s): price", price="")
        self.assertRejected("missing value(s)", offer_id="")

    def test_optional_fields_may_be_blank(self):
        o = self.parse(rating="", co2_kg="")
        self.assertIsNone(o.rating)
        self.assertIsNone(o.co2_kg)

    def test_rating_scale_boundaries_accepted(self):
        self.assertEqual(self.parse(rating="1").rating, 1.0)
        self.assertEqual(self.parse(rating="5").rating, 5.0)

    def test_offer_id_regex_and_consistency(self):
        self.assertRejected("pattern", offer_id="SR_MAL_2026")
        self.assertIsNone(loader.OFFER_ID_RE.fullmatch("SR-MAL-20260712-0900\n"))  # near miss: trailing newline
        self.assertRejected("inconsistent", offer_id="SR-MAL-20260712-1000")  # time disagrees
        self.assertRejected("inconsistent", offer_id="CE-MAL-20260712-0900")  # company disagrees

    def test_zero_seats_is_valid_data(self):
        # Sold out is a valid state. The seats hard constraint removes such offers when filtering.
        self.assertEqual(self.parse(seats="0").seats, 0)


class LoadFilesTest(unittest.TestCase):
    def test_missing_file(self):
        with self.assertRaises(DataFileError):
            loader.load_companies("does/not/exist.csv")

    def test_missing_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "companies.csv"
            p.write_text("code,name\nSR,SolRuta\n", encoding="utf-8")
            with self.assertRaises(DataFileError):
                loader.load_companies(p)

    def test_non_utf8_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "companies.csv"
            p.write_bytes("code,name,profile\nSR,Sol Ruta Málaga,x\n".encode("cp1252"))  # as saved by Excel
            with self.assertRaises(DataFileError):
                loader.load_companies(p)

    def test_duplicates_rejected_and_review_file_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, review = Path(tmp) / "offers.csv", Path(tmp) / "review.csv"
            with src.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=loader.OFFER_FIELDS)
                w.writeheader()
                w.writerows([row(), row(), row(price="-5", offer_id="SR-MAL-20260712-0900")])
            offers, rejected = loader.load_offers(src, COMPANIES, DESTINATIONS, review)
            self.assertEqual(len(offers), 1)
            self.assertEqual([r for _, r in rejected], ["duplicate offer_id", "price must be at least 0, got -5.0"])
            self.assertIn("reason", review.read_text(encoding="utf-8").splitlines()[0])

    def test_shipped_dataset(self):
        companies = loader.load_companies(DATA / "companies.csv")
        destinations = loader.load_destinations(DATA / "destinations.csv")
        offers, rejected = loader.load_offers(DATA / "offers.csv", companies, destinations)
        self.assertEqual((len(companies), len(destinations)), (5, 15))
        self.assertEqual(len({(o.company.code, o.destination.name) for o in offers}), 75)
        self.assertEqual(len(rejected), 10)  # the deliberately invalid rows


class ProfileTest(unittest.TestCase):
    def test_named_departure_times(self):
        self.assertEqual(loader.parse_departure_time("Morning").hour, 9)
        self.assertEqual(loader.parse_departure_time("07:30").minute, 30)
        with self.assertRaises(ConfigError):
            loader.parse_departure_time("brunch")

    def test_bad_request(self):
        base = {"destination": "Malaga", "date_from": "2026-07-12", "date_to": "2026-07-14"}
        with self.assertRaises(ConfigError):
            loader.build_request({**base, "date_to": "2026-07-01"}, DESTINATIONS)
        with self.assertRaises(ConfigError):
            loader.build_request({**base, "destination": "Paris"}, DESTINATIONS)
        with self.assertRaises(ConfigError):
            loader.build_request({**base, "max_prise": 50}, DESTINATIONS)  # a misspelt field name raises an error
        for field, bad in (("max_price", -5), ("max_price", "50"), ("max_transfers", -1),
                           ("passengers", 0), ("passengers", True), ("changeable_required", "yes")):
            with self.assertRaises(ConfigError, msg=(field, bad)):
                loader.build_request({**base, field: bad}, DESTINATIONS)
        self.assertEqual(loader.build_request({**base, "max_price": 0}, DESTINATIONS).max_price, 0)  # boundary

    def test_shipped_profiles_load(self):
        destinations = loader.load_destinations(DATA / "destinations.csv")
        profiles = loader.load_profiles(Path(__file__).resolve().parent.parent / "config" / "profiles.json", destinations)
        self.assertGreaterEqual(len(profiles), 3)


if __name__ == "__main__":
    unittest.main()
