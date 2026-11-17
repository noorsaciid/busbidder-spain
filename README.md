# BusBidder Spain

Filters and ranks competing bus offers from Madrid for an individual traveller, explains
the top result and makes simulated bookings. ACIT4420 final project.

## Requirements

- Python 3.10 or newer (tested with 3.12 and 3.14)
- No third-party libraries: only the standard library. Dependency information is in `pyproject.toml`
  (`dependencies = []`); `requirements.txt` is intentionally empty.
- With uv, prefix any command below with `uv run`, e.g. `uv run python -m busbidder rank example`.

## Run

All commands are run from this `implementation/` folder.

```bash
python -m busbidder profiles                      # list traveller profiles (hard vs soft)
python -m busbidder rank example                  # rank offers, explain #1, then offer to book
python -m busbidder rank budget --top 5
python -m busbidder rank example --book 1 --name "Ana Lopez"     # book rank 1 non-interactively
python -m busbidder compare                       # same offers, winner per profile
python -m busbidder bookings                      # list saved bookings
python -m busbidder validate                      # records set aside by data validation
python -m busbidder web                           # web front end on http://127.0.0.1:8000 (Ctrl+C to stop)
```

Override the profile for one run:

```bash
python -m busbidder rank example --destination Seville --from 2026-07-10 --to 2026-07-11 --budget 35 --departure evening --weights price=30,duration=30,departure=20,flexibility=20
```

## Tests

```bash
python -m unittest discover -s tests -t .
```

## Files

| Path | Purpose |
|------|---------|
| `busbidder/models.py` | Dataclasses: Company, Destination, Offer, FareConditions, TravelRequest, Preferences, Booking; `Bounded` descriptor enforcing numeric ranges on their fields |
| `busbidder/loader.py` | Reads CSV/JSON, converts types, validates records (regex for offer IDs) |
| `busbidder/constraints.py` | Hard constraints, registered with the `@hard_constraint` decorator |
| `busbidder/scoring.py` | Criteria registered with `@criterion`, normalisation, weighted ranking, explanation |
| `busbidder/booking.py` | Booking store: seat checks, unique `BB-XXXXXX` references, atomic JSON save |
| `busbidder/cli.py` | Command-line interface and logging setup |
| `busbidder/web.py`, `busbidder/web.html` | Web front end (standard library `http.server`, localhost only) using the same `App` as the CLI |
| `busbidder/errors.py` | Custom exceptions |
| `data/companies.csv`, `data/destinations.csv` | 5 companies, 15 destinations |
| `data/offers.csv` | 792 generated offers (all 75 company-destination pairs, 10-16 July 2026) plus 10 deliberately invalid rows |
| `config/profiles.json` | Traveller profiles: hard constraints (`request`) and soft preferences (`weights`, `preferred_departure`) |
| `pyproject.toml` | Project metadata: name, version, Python version, (no) dependencies |
| `tools/generate_offers.py` | Regenerates `data/offers.csv` with a fixed seed: `python tools/generate_offers.py` |
| `tools/verify_independent.py` | Recalculates validation, filtering and scoring from the raw files and compares with the program: `python tools/verify_independent.py` |
| `tools/experiments.py` | Weight sensitivity, rank reversal, price scale and company exposure used in the report: `python tools/experiments.py` |
| `examples/` | Saved outputs used as evidence in the report (rankings, validation, booking, log, tests, experiments) |
| `output/` | Created at run time: `bookings.json`, `busbidder.log`, `rejected_offers.csv` |

## Scoring in one paragraph

Every criterion becomes a 0-100 score (higher is better). Price, duration, transfers, rating and
CO2 use min-max over the acceptable offers, inverted where lower is better; departure loses 10
points per hour away from the preferred time; flexibility is 50 for changeable plus 50 for
refundable. Missing values get a neutral 50 and are reported. The total is the weighted sum;
weights must sum to 1 (or 100%) and at least four criteria must be used. Ties are broken by lower price,
earlier departure, then offer ID, so results are reproducible.
