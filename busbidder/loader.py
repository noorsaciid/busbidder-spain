"""Read source files, convert types and validate every record.

Policy: an invalid offer is set aside in a review file with the reason, and logged.
Its values are left as they are, because guessing a missing price or date would show
travellers data the company never published.
"""
import csv
import json
import logging
import re
from datetime import date, datetime, time
from pathlib import Path

from .errors import ConfigError, DataFileError, InvalidRecordError
from .models import (Company, Destination, FareConditions, Offer, Preferences,
                     TravellerProfile, TravelRequest)
from .scoring import validate_weights

log = logging.getLogger(__name__)

# e.g. SR-MAL-20260712-0830: company code, destination code, departure date and time.
OFFER_ID_RE = re.compile(r"(?P<company>[A-Z]{2})-(?P<dest>[A-Z]{3})-(?P<date>\d{8})-(?P<time>\d{4})")
DATETIME_FMT = "%Y-%m-%d %H:%M"
OFFER_FIELDS = ["offer_id", "company", "destination", "departure", "arrival", "price",
                "transfers", "luggage_kg", "changeable", "refundable", "seats", "rating", "co2_kg"]
OPTIONAL_OFFER_FIELDS = {"rating", "co2_kg"}
NAMED_TIMES = {"morning": "09:00", "afternoon": "15:00", "evening": "19:00", "night": "23:00"}


def _read_csv(path, required: list[str]) -> list[dict]:
    path = Path(path)
    try:
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            missing = set(required) - set(reader.fieldnames or [])
            if missing:
                raise DataFileError(f"{path.name} is missing columns: {sorted(missing)}")
            return list(reader)
    except FileNotFoundError as e:
        raise DataFileError(f"missing data file: {path}") from e
    except PermissionError as e:
        raise DataFileError(f"permission denied reading {path}") from e
    except UnicodeDecodeError as e:
        raise DataFileError(f"{path.name} is not UTF-8 text (re-save it as CSV UTF-8): {e}") from e
    except csv.Error as e:
        raise DataFileError(f"{path.name} is not valid CSV: {e}") from e


def _flag(value: str) -> bool:
    v = value.strip().lower()
    if v in ("yes", "true", "1"):
        return True
    if v in ("no", "false", "0"):
        return False
    raise ValueError(f"expected yes/no, got {value!r}")


def load_companies(path) -> dict[str, Company]:
    rows = _read_csv(path, ["code", "name", "profile"])
    return {r["code"].strip(): Company(r["code"].strip(), r["name"].strip(), r["profile"].strip()) for r in rows}


def load_destinations(path) -> dict[str, Destination]:
    rows = _read_csv(path, ["name", "code", "distance_km", "coastal"])
    try:
        return {r["name"].strip(): Destination(r["name"].strip(), r["code"].strip(),
                                               int(r["distance_km"]), _flag(r["coastal"])) for r in rows}
    except ValueError as e:
        raise DataFileError(f"bad value in destinations file: {e}") from e


def parse_offer(row: dict, companies: dict[str, Company], destinations: dict[str, Destination]) -> Offer:
    """Convert one CSV row to an Offer, or raise InvalidRecordError naming the problem."""
    oid = (row.get("offer_id") or "").strip() or "<missing id>"

    def fail(reason):
        raise InvalidRecordError(oid, reason)

    empty = [k for k in OFFER_FIELDS if k not in OPTIONAL_OFFER_FIELDS and not (row.get(k) or "").strip()]
    if empty:
        fail(f"missing value(s): {', '.join(empty)}")
    m = OFFER_ID_RE.fullmatch(oid)
    if not m:
        fail("offer_id does not match the pattern XX-XXX-YYYYMMDD-HHMM")
    company = companies.get(row["company"].strip())
    if company is None:
        fail(f"unknown company {row['company']!r}")
    dest = destinations.get(row["destination"].strip())
    if dest is None:
        fail(f"unknown destination {row['destination']!r}")
    try:
        dep = datetime.strptime(row["departure"].strip(), DATETIME_FMT)
        arr = datetime.strptime(row["arrival"].strip(), DATETIME_FMT)
    except ValueError:
        fail("invalid date/time (expected YYYY-MM-DD HH:MM)")
    if arr <= dep:
        fail("arrival is not after departure")
    if (m["company"], m["dest"], m["date"], m["time"]) != (company.code, dest.code, f"{dep:%Y%m%d}", f"{dep:%H%M}"):
        fail("offer_id is inconsistent with its company, destination or departure")
    try:  # stage 1: text -> Python types
        price = float(row["price"])
        transfers, luggage, seats = int(row["transfers"]), int(row["luggage_kg"]), int(row["seats"])
        changeable, refundable = _flag(row["changeable"]), _flag(row["refundable"])
        rating = float(row["rating"]) if (row.get("rating") or "").strip() else None
        co2 = float(row["co2_kg"]) if (row.get("co2_kg") or "").strip() else None
    except ValueError as e:
        fail(f"bad number or flag: {e}")
    try:  # stage 2: ranges, enforced by the Bounded descriptors on the model fields
        fare = FareConditions(changeable, refundable, luggage)
        return Offer(oid, company, dest, dep, arr, price, transfers, fare, seats, rating, co2)
    except (TypeError, ValueError) as e:
        fail(str(e))


def load_offers(path, companies, destinations, review_path=None) -> tuple[list[Offer], list[tuple[dict, str]]]:
    """Return (valid offers, rejected rows with reasons). Rejected rows go to review_path."""
    offers, rejected, seen = [], [], set()
    for row in _read_csv(path, OFFER_FIELDS):
        try:
            offer = parse_offer(row, companies, destinations)
            if offer.offer_id in seen:
                raise InvalidRecordError(offer.offer_id, "duplicate offer_id")
            seen.add(offer.offer_id)
            offers.append(offer)
        except InvalidRecordError as e:
            log.warning("Rejected offer %s", e)
            rejected.append((row, e.reason))
    if review_path and rejected:
        review_path = Path(review_path)
        review_path.parent.mkdir(parents=True, exist_ok=True)
        with review_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=OFFER_FIELDS + ["reason"], extrasaction="ignore")
            writer.writeheader()
            writer.writerows({**row, "reason": reason} for row, reason in rejected)
    log.info("Loaded %d valid offers, set aside %d for review", len(offers), len(rejected))
    return offers, rejected


def parse_departure_time(value: str) -> time:
    """Accept 'HH:MM' or a named part of the day such as 'morning'."""
    try:
        return time.fromisoformat(NAMED_TIMES.get(value.strip().lower(), value.strip()))
    except ValueError:
        raise ConfigError(f"invalid preferred departure {value!r}; use HH:MM or {', '.join(NAMED_TIMES)}")


def build_request(data: dict, destinations) -> TravelRequest:
    allowed = set(TravelRequest.__dataclass_fields__)
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"unknown request field(s): {sorted(unknown)}")
    try:
        req = TravelRequest(**{**data, "date_from": date.fromisoformat(str(data["date_from"])),
                               "date_to": date.fromisoformat(str(data["date_to"]))})
    except (KeyError, TypeError, ValueError) as e:
        raise ConfigError(f"invalid travel request: {e}") from e
    if req.destination not in destinations:
        raise ConfigError(f"unknown destination {req.destination!r}")
    return req


def load_profiles(path, destinations) -> dict[str, TravellerProfile]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise DataFileError(f"missing profile file: {path}") from e
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ConfigError(f"{path.name} is not valid UTF-8 JSON: {e}") from e
    profiles = {}
    for name, p in data.items():
        try:
            prefs = Preferences(parse_departure_time(p["preferred_departure"]), validate_weights(p["weights"]))
            profiles[name] = TravellerProfile(name, p.get("description", ""), build_request(p["request"], destinations), prefs)
        except KeyError as e:
            raise ConfigError(f"profile {name!r} lacks {e}") from e
        except ConfigError as e:
            raise ConfigError(f"profile {name!r}: {e}") from e
    return profiles
