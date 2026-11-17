import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from busbidder import cli
from busbidder.booking import REFERENCE_RE, BookingStore
from busbidder.errors import BookingError, DataFileError
from tests.helpers import offer


class BookingStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "bookings.json"
        self.offer = offer(seats=3)
        self.store = BookingStore(self.path, [self.offer])

    def tearDown(self):
        self.tmp.cleanup()

    def test_booking_reduces_seats_and_persists(self):
        b = self.store.book(self.offer.offer_id, "Ana", 2)
        self.assertRegex(b.reference, REFERENCE_RE)
        self.assertEqual(self.store.seats_left(self.offer.offer_id), 1)
        self.assertEqual(self.store.current()[0].seats, 1)
        self.assertEqual(b.fare, {"changeable": True, "refundable": False, "luggage_kg": 20})
        reloaded = BookingStore(self.path, [self.offer])  # capacity derived again from disk
        self.assertEqual(reloaded.seats_left(self.offer.offer_id), 1)

    def test_cannot_overbook(self):
        self.store.book(self.offer.offer_id, "Ana", 3)
        with self.assertRaises(BookingError):
            self.store.book(self.offer.offer_id, "Ben", 1)

    def test_invalid_requests(self):
        for oid, name, n in (("XX-MAL-20260712-0900", "Ana", 1), (self.offer.offer_id, "Ana", 0),
                             (self.offer.offer_id, "  ", 1)):
            with self.assertRaises(BookingError):
                self.store.book(oid, name, n)
        self.assertFalse(self.path.exists())  # nothing saved for failed bookings

    def test_references_are_unique(self):
        big = offer(seats=200)
        store = BookingStore(self.path, [big])
        refs = {store.book(big.offer_id, "Ana").reference for _ in range(100)}
        self.assertEqual(len(refs), 100)
        self.assertIsNone(REFERENCE_RE.fullmatch("BB-ABCDEF\n"))  # near miss
        self.assertIsNone(REFERENCE_RE.fullmatch("BB-ABCDE0"))   # 0 is excluded from the alphabet

    def test_corrupt_file_is_not_overwritten(self):
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(DataFileError):
            BookingStore(self.path, [self.offer])
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{not json")

    def test_bookings_beyond_capacity_are_reported(self):
        self.store.book(self.offer.offer_id, "Ana", 3)
        with self.assertRaises(DataFileError):  # e.g. offers.csv capacity lowered after bookings were made
            BookingStore(self.path, [offer(seats=2)])

    def test_invalid_reference_in_file(self):
        self.store.book(self.offer.offer_id, "Ana")
        self.path.write_text(self.path.read_text(encoding="utf-8").replace("BB-", "XX-"), encoding="utf-8")
        with self.assertRaises(DataFileError):
            BookingStore(self.path, [self.offer])


class CliTest(unittest.TestCase):
    """End to end on the shipped data, with output in a temp folder."""

    def run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--output", self.tmp.name, *argv])
        return code, out.getvalue()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        import logging
        logging.shutdown()  # release the log file so Windows can delete the temp folder
        logging.getLogger().handlers.clear()
        self.tmp.cleanup()

    def test_rank_and_book(self):
        """Full pipeline: load -> validate -> filter -> rank -> book -> save, then check the state change."""
        code, text = self.run_cli("rank", "example", "--book", "1", "--name", "Test Traveller", "--passengers", "2")
        self.assertEqual(code, 0)
        self.assertIn("Why #1?", text)
        self.assertIn("Booking confirmed: BB-", text)
        self.assertTrue((Path(self.tmp.name) / "rejected_offers.csv").exists())
        saved = json.loads((Path(self.tmp.name) / "bookings.json").read_text(encoding="utf-8"))
        self.assertEqual(len(saved), 1)
        self.assertRegex(saved[0]["reference"], REFERENCE_RE)
        self.assertEqual(saved[0]["passengers"], 2)
        self.assertIn(saved[0]["reference"], text)
        app = cli.App(Path(self.tmp.name))  # a fresh run sees the reduced capacity
        oid = saved[0]["offer_id"]
        self.assertEqual(app.store.seats_left(oid), app.store.capacity[oid] - 2)
        self.assertIn("Booked", (Path(self.tmp.name) / "busbidder.log").read_text(encoding="utf-8"))

    def test_profiles_disagree(self):
        code, text = self.run_cli("compare")
        self.assertEqual(code, 0)
        winners = {line.split()[2] for line in text.splitlines()[1:] if line.strip()}
        self.assertGreaterEqual(len(winners), 3)

    def test_bad_input_returns_error_code(self):
        self.assertEqual(self.run_cli("rank", "nobody")[0], 1)
        self.assertEqual(self.run_cli("rank", "example", "--weights", "price=90,duration=90")[0], 1)
        self.assertEqual(self.run_cli("rank", "example", "--destination", "Paris")[0], 1)
        self.assertEqual(self.run_cli("rank", "example", "--budget", "-5")[0], 1)
        self.assertEqual(self.run_cli("rank", "example", "--from", "2026-07-14", "--to", "2026-07-12")[0], 1)


if __name__ == "__main__":
    unittest.main()
