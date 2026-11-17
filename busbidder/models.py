"""Domain objects. Frozen dataclasses: an offer never changes after validation."""
from dataclasses import dataclass, field
from datetime import date, datetime, time

from .errors import ConfigError

_REQUIRED = object()  # marker: the field has no default value


class Bounded:
    """Reusable descriptor for a numeric field that must stay within [minimum, maximum].

    Offer, FareConditions and TravelRequest share this class, so each range rule is
    written once. Raises TypeError for a wrong type and ValueError for
    a value out of range; callers turn these into InvalidRecordError or ConfigError,
    because only they know which record or setting was being read.
    """

    def __init__(self, minimum=None, maximum=None, *, integer=False, default=_REQUIRED):
        self.minimum, self.maximum, self.integer, self.default = minimum, maximum, integer, default

    def __set_name__(self, owner, name):
        self.name = name
        self.storage = "_" + name

    def __get__(self, instance, owner):
        if instance is None:
            # Class access is how @dataclass asks for the field's default; AttributeError means "required".
            if self.default is _REQUIRED:
                raise AttributeError(self.name)
            return self.default
        return instance.__dict__[self.storage]

    def __set__(self, instance, value):
        if value is None and self.default is None:
            pass  # optional field left empty, e.g. no rating or no budget limit
        elif isinstance(value, bool) or not isinstance(value, int if self.integer else (int, float)):
            kind = "a whole number" if self.integer else "a number"
            raise TypeError(f"{self.name} must be {kind}, got {value!r}")
        elif self.minimum is not None and value < self.minimum:
            raise ValueError(f"{self.name} must be at least {self.minimum}, got {value}")
        elif self.maximum is not None and value > self.maximum:
            raise ValueError(f"{self.name} must be at most {self.maximum}, got {value}")
        # Write to __dict__ directly: a frozen dataclass blocks setattr, even on itself.
        instance.__dict__[self.storage] = value


@dataclass(frozen=True)
class Company:
    code: str
    name: str
    profile: str


@dataclass(frozen=True)
class Destination:
    name: str
    code: str
    distance_km: int
    coastal: bool


@dataclass(frozen=True)
class FareConditions:
    changeable: bool
    refundable: bool
    luggage_kg: int = Bounded(0, integer=True)

    @property
    def label(self) -> str:
        if self.changeable and self.refundable:
            return "changeable+refundable"
        if self.changeable:
            return "changeable"
        if self.refundable:
            return "refundable"
        return "fixed"


@dataclass(frozen=True)
class Offer:
    offer_id: str
    company: Company
    destination: Destination
    departure: datetime
    arrival: datetime
    price: float = Bounded(0)
    transfers: int = Bounded(0, integer=True)
    fare: FareConditions
    seats: int = Bounded(0, integer=True)  # still available: capacity minus bookings, see BookingStore.current
    rating: float | None = Bounded(1, 5, default=None)  # customer rating, optional
    co2_kg: float | None = Bounded(0, default=None)  # per passenger, optional

    @property
    def duration_min(self) -> int:
        # Computed from departure and arrival each time, so it always matches the timetable.
        return int((self.arrival - self.departure).total_seconds() // 60)

    @property
    def travel_date(self) -> date:
        return self.departure.date()


@dataclass(frozen=True)
class TravelRequest:
    """Hard constraints. An offer violating any of these is removed before ranking."""
    destination: str
    date_from: date
    date_to: date
    max_price: float | None = Bounded(0, default=None)
    max_transfers: int | None = Bounded(0, integer=True, default=None)
    max_duration_min: int | None = Bounded(0, integer=True, default=None)
    min_luggage_kg: int | None = Bounded(0, integer=True, default=None)
    changeable_required: bool = False
    refundable_required: bool = False
    passengers: int = Bounded(1, integer=True, default=1)

    def __post_init__(self):
        # Rules that involve two fields or non-numbers; numeric ranges are in the Bounded descriptors.
        # Runs on dataclasses.replace() too, so CLI overrides are checked.
        if self.date_from > self.date_to:
            raise ConfigError("travel window ends before it starts")
        for name in ("changeable_required", "refundable_required"):
            if not isinstance(getattr(self, name), bool):
                raise ConfigError(f"{name} must be true or false")


@dataclass(frozen=True)
class Preferences:
    """Soft preferences. They only change the order of acceptable offers."""
    preferred_departure: time
    weights: dict[str, float]


@dataclass(frozen=True)
class TravellerProfile:
    name: str
    description: str
    request: TravelRequest
    preferences: Preferences


@dataclass(frozen=True)
class RankedOffer:
    rank: int
    offer: Offer
    total: float
    criterion_scores: dict[str, float]
    missing: tuple[str, ...] = field(default=())  # criteria scored neutrally for lack of data


@dataclass(frozen=True)
class Booking:
    reference: str
    offer_id: str
    traveller: str
    passengers: int
    company: str
    destination: str
    departure: str
    unit_price: float
    total_price: float
    fare: dict  # snapshot of the fare conditions at booking time
    payment_status: str
    created_at: str
