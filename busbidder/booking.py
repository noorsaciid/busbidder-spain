"""Simulated bookings, saved as JSON.

Seat capacity lives in offers.csv; sold seats live in bookings.json. Remaining seats
are always computed as capacity - sold, so the two files cannot drift apart.
No payment data is collected: payment_status is simulated.
"""
import json
import logging
import os
import re
import secrets
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from .errors import BookingError, DataFileError
from .models import Booking, Offer

log = logging.getLogger(__name__)

# BB- plus 6 characters, without I, O, 0 and 1 so a reference read aloud is unambiguous.
REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
REFERENCE_RE = re.compile(rf"BB-[{REF_ALPHABET}]{{6}}")  # always used with fullmatch


class BookingStore:
    def __init__(self, path, offers: list[Offer]):
        self.path = Path(path)
        self.capacity = {o.offer_id: o.seats for o in offers}
        self._offers = {o.offer_id: o for o in offers}
        self.bookings = self._load()
        oversold = sorted(oid for oid, n in self._sold().items() if oid in self.capacity and n > self.capacity[oid])
        if oversold:
            raise DataFileError(f"{self.path} has more seats booked than offers.csv provides on: {oversold}")

    def _sold(self) -> Counter:
        sold = Counter()
        for b in self.bookings:
            sold[b.offer_id] += b.passengers
        return sold

    def _load(self) -> list[Booking]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            bookings = [Booking(**b) for b in data]
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as e:
            # Stop here: saving over a damaged file would lose the bookings still in it.
            raise DataFileError(f"{self.path} is corrupt, fix or move it first: {e}") from e
        except OSError as e:
            raise DataFileError(f"cannot read {self.path}: {e}") from e
        bad = [b.reference for b in bookings if not isinstance(b.reference, str) or not REFERENCE_RE.fullmatch(b.reference)]
        if bad:
            raise DataFileError(f"{self.path} holds invalid booking references: {bad}")
        return bookings

    def seats_left(self, offer_id: str) -> int:
        return self.capacity[offer_id] - self._sold()[offer_id]

    def current(self) -> list[Offer]:
        """Offers with seats reduced by existing bookings."""
        sold = self._sold()
        return [replace(o, seats=o.seats - sold[oid]) for oid, o in self._offers.items()]

    def _new_reference(self) -> str:
        taken = {b.reference for b in self.bookings}
        while True:
            ref = "BB-" + "".join(secrets.choice(REF_ALPHABET) for _ in range(6))
            if ref not in taken:
                return ref

    def book(self, offer_id: str, traveller: str, passengers: int = 1) -> Booking:
        if offer_id not in self._offers:
            raise BookingError(f"unknown offer {offer_id}")
        if passengers < 1:
            raise BookingError("passengers must be at least 1")
        if not traveller.strip():
            raise BookingError("traveller name is required")
        left = self.seats_left(offer_id)  # checked again here: the ranking the user saw may be out of date
        if left < passengers:
            raise BookingError(f"only {left} seat(s) left on {offer_id}, {passengers} requested")
        o = self._offers[offer_id]
        booking = Booking(
            reference=self._new_reference(), offer_id=offer_id, traveller=traveller.strip(),
            passengers=passengers, company=o.company.name, destination=o.destination.name,
            departure=f"{o.departure:%Y-%m-%d %H:%M}", unit_price=o.price,
            total_price=round(o.price * passengers, 2),
            fare={"changeable": o.fare.changeable, "refundable": o.fare.refundable, "luggage_kg": o.fare.luggage_kg},
            payment_status="SIMULATED-PAID", created_at=datetime.now().isoformat(timespec="seconds"))
        self.bookings.append(booking)
        try:
            self._save()
        except OSError:
            self.bookings.pop()  # keep memory consistent with disk
            raise
        log.info("Booked %s: %s x%d on %s", booking.reference, traveller, passengers, offer_id)
        return booking

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(b) for b in self.bookings], indent=2), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic: a crash never leaves a half-written bookings file
