"""Command-line interface: python -m busbidder <command> ..."""
import argparse
import logging
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

from .booking import BookingStore
from .constraints import active_constraints, rejection_summary, split_offers
from .errors import BusBidderError, ConfigError
from .loader import load_companies, load_destinations, load_offers, load_profiles, parse_departure_time
from .models import Preferences
from .scoring import CRITERIA, explain, rank_offers, validate_weights
from .web import serve

ROOT = Path(__file__).resolve().parent.parent
DATA, CONFIG = ROOT / "data", ROOT / "config"
log = logging.getLogger("busbidder")


class App:
    """Loads everything once and wires the pieces together for one CLI run."""

    def __init__(self, output: Path):
        self.destinations = load_destinations(DATA / "destinations.csv")
        companies = load_companies(DATA / "companies.csv")
        offers, self.invalid = load_offers(DATA / "offers.csv", companies, self.destinations,
                                           review_path=output / "rejected_offers.csv")
        self.store = BookingStore(output / "bookings.json", offers)
        self.profiles = load_profiles(CONFIG / "profiles.json", self.destinations)

    def profile(self, name, args=None):
        if name not in self.profiles:
            raise ConfigError(f"unknown profile {name!r}; choose from {', '.join(self.profiles)}")
        p = self.profiles[name]
        if args is None:
            return p.request, p.preferences
        changes = {k: v for k, v in {"destination": args.destination, "date_from": args.date_from,
                                     "date_to": args.date_to, "max_price": args.budget,
                                     "passengers": args.passengers}.items() if v is not None}
        try:
            request = replace(p.request, **changes)
        except (TypeError, ValueError) as e:  # raised by the Bounded descriptors, e.g. --budget -5
            raise ConfigError(f"invalid request: {e}") from e
        if request.destination not in self.destinations:
            raise ConfigError(f"unknown destination {request.destination!r}")
        prefs = p.preferences
        if args.departure:
            prefs = replace(prefs, preferred_departure=parse_departure_time(args.departure))
        if args.weights:
            prefs = replace(prefs, weights=validate_weights(parse_weights(args.weights)))
        return request, prefs

    def rank(self, request, prefs):
        accepted, rejected = split_offers(self.store.current(), request)
        ranking = rank_offers(accepted, prefs)
        log.info("Ranked %d acceptable offers (%d rejected) for %s %s..%s",
                 len(accepted), len(rejected), request.destination, request.date_from, request.date_to)
        return ranking, rejected


def parse_weights(text: str) -> dict:
    """'price=40,duration=25,departure=15,flexibility=20' -> dict."""
    try:
        return {k.strip(): float(v) for k, v in (pair.split("=") for pair in text.split(","))}
    except ValueError:
        raise ConfigError("weights must look like price=40,duration=25,departure=15,flexibility=20")


def print_ranking(ranking, rejected, request, prefs, top):
    weights = {n: w for n, w in prefs.weights.items() if w > 0}
    print("Hard constraints (offers violating any are removed):")
    print("  " + "; ".join(active_constraints(request)))
    print("Soft preferences (weights used for ranking):")
    print(f"  preferred departure {prefs.preferred_departure:%H:%M}; "
          + ", ".join(f"{n} {w:.0%}" for n, w in weights.items()))
    summary = rejection_summary(rejected)
    if summary:
        print("Offers on this route and dates removed by: "
              + ", ".join(f"{name} ({n})" for name, n in summary.most_common()))
    print()
    if not ranking:
        print(explain(ranking, prefs))
        return
    crit = list(weights)
    head = f"{'#':>3} {'offer':<22}{'company':<15}{'depart':<12}{'dur':>6}{'EUR':>7}{'tr':>3} {'fare':<22}{'seats':>5}"
    print(head + "".join(f"{c[:8]:>9}" for c in crit) + f"{'TOTAL':>8}")
    for r in ranking[:top]:
        o = r.offer
        row = (f"{r.rank:>3} {o.offer_id:<22}{o.company.name:<15}{o.departure:%d/%m %H:%M} "
               f"{o.duration_min // 60:>3}h{o.duration_min % 60:02d}{o.price:>7.2f}{o.transfers:>3} "
               f"{o.fare.label:<22}{o.seats:>5}")
        print(row + "".join(f"{r.criterion_scores[c]:>9.1f}" for c in crit) + f"{r.total:>8.1f}")
    if len(ranking) > top:
        print(f"... {len(ranking) - top} more acceptable offers (use --top to show more)")
    print("\nWhy #1?\n" + explain(ranking, prefs))


def ask(prompt: str) -> str:
    """input() that treats a non-interactive or closed stdin as an empty answer."""
    if not sys.stdin.isatty():
        return ""
    try:
        return input(prompt).strip()
    except EOFError:  # on Windows, stdin redirected from NUL still claims to be a TTY
        return ""


def confirm(booking):
    f = booking.fare
    print(f"\nBooking confirmed: {booking.reference}\n"
          f"  {booking.traveller}, {booking.passengers} passenger(s)\n"
          f"  {booking.company}: Madrid -> {booking.destination}, departs {booking.departure}\n"
          f"  Total EUR {booking.total_price:.2f} (payment {booking.payment_status}, no card data collected)\n"
          f"  Fare: changeable={'yes' if f['changeable'] else 'no'}, refundable={'yes' if f['refundable'] else 'no'}, "
          f"luggage {f['luggage_kg']} kg")


def cmd_rank(app, args):
    request, prefs = app.profile(args.profile, args)
    ranking, rejected = app.rank(request, prefs)
    print(f"Profile: {args.profile} - {app.profiles[args.profile].description}")
    print_ranking(ranking, rejected, request, prefs, args.top)
    choice = args.book
    if choice is None and ranking:
        answer = ask("\nEnter a rank number to book, or press Enter to skip: ")
        choice = int(answer) if answer.isdigit() else None
    if choice is not None:
        if not 1 <= choice <= len(ranking):
            raise ConfigError(f"rank must be between 1 and {len(ranking)}")
        name = args.name or ask("Traveller name: ")
        confirm(app.store.book(ranking[choice - 1].offer.offer_id, name, request.passengers))


def cmd_compare(app, args):
    """Same offers, every profile: shows how weights and constraints change the winner."""
    print(f"{'profile':<18}{'acceptable':>11}  {'#1 offer':<22}{'company':<15}{'EUR':>7}{'dur':>7}  {'depart':<7}{'fare':<22}{'score':>6}")
    for name in app.profiles:
        request, prefs = app.profile(name, args)
        ranking, _ = app.rank(request, prefs)
        if not ranking:
            print(f"{name:<18}{0:>11}  (no acceptable offer)")
            continue
        o = ranking[0].offer
        print(f"{name:<18}{len(ranking):>11}  {o.offer_id:<22}{o.company.name:<15}{o.price:>7.2f}"
              f"{o.duration_min // 60:>4}h{o.duration_min % 60:02d}  {o.departure:%H:%M} {o.fare.label:<22}{ranking[0].total:>6.1f}")


def cmd_profiles(app, args):
    for name, p in app.profiles.items():
        print(f"{name}: {p.description}")
        print("  hard: " + "; ".join(active_constraints(p.request)))
        print(f"  soft: departure {p.preferences.preferred_departure:%H:%M}, "
              + ", ".join(f"{n} {w:.0%}" for n, w in p.preferences.weights.items()))
    print(f"\nAvailable criteria: {', '.join(CRITERIA)}")


def cmd_bookings(app, args):
    if not app.store.bookings:
        print("No bookings yet.")
    for b in app.store.bookings:
        print(f"{b.reference}  {b.traveller:<20} {b.offer_id:<22} x{b.passengers}  EUR {b.total_price:.2f}  {b.payment_status}")


def cmd_validate(app, args):
    print(f"Valid offers: {len(app.store.capacity)}; set aside for review: {len(app.invalid)}")
    for row, reason in app.invalid:
        print(f"  {row.get('offer_id') or '<missing id>'}: {reason}")


def build_parser():
    parser = argparse.ArgumentParser(prog="busbidder", description="Filter and rank bus offers from Madrid.")
    parser.add_argument("--output", type=Path, default=ROOT / "output",
                        help="folder for bookings, log and review files (default: output/)")
    sub = parser.add_subparsers(dest="command", required=True)

    def request_options(p):
        p.add_argument("--destination", help="override the profile destination, e.g. Malaga")
        p.add_argument("--from", dest="date_from", type=date.fromisoformat, help="first travel date YYYY-MM-DD")
        p.add_argument("--to", dest="date_to", type=date.fromisoformat, help="last travel date YYYY-MM-DD")
        p.add_argument("--budget", type=float, help="maximum price in EUR (hard constraint)")
        p.add_argument("--passengers", type=int, help="number of seats needed")
        p.add_argument("--departure", help="preferred departure HH:MM or morning/afternoon/evening/night")
        p.add_argument("--weights", help="e.g. price=40,duration=25,departure=15,flexibility=20")

    rank = sub.add_parser("rank", help="rank offers for a traveller profile and optionally book one")
    rank.add_argument("profile")
    request_options(rank)
    rank.add_argument("--top", type=int, default=10, help="how many offers to show")
    rank.add_argument("--book", type=int, metavar="RANK", help="book the offer at this rank")
    rank.add_argument("--name", help="traveller name for the booking")
    rank.set_defaults(func=cmd_rank)

    compare = sub.add_parser("compare", help="top offer for every profile on the same data")
    request_options(compare)
    compare.set_defaults(func=cmd_compare)

    sub.add_parser("profiles", help="list traveller profiles").set_defaults(func=cmd_profiles)
    sub.add_parser("bookings", help="list saved bookings").set_defaults(func=cmd_bookings)
    sub.add_parser("validate", help="show offers set aside by data validation").set_defaults(func=cmd_validate)

    web = sub.add_parser("web", help="start the web front end on http://127.0.0.1:PORT")
    web.add_argument("--port", type=int, default=8000)
    web.set_defaults(func=lambda app, args: serve(app, args.port))
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=args.output / "busbidder.log", level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True)
    try:
        args.func(App(args.output), args)
    except BusBidderError as e:
        log.error("%s", e)
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0
