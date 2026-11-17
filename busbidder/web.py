"""Small web front end, standard library only: python -m busbidder web, then open http://127.0.0.1:8000

It reuses cli.App, so filtering, ranking, explanations and bookings behave exactly as on the command line.
"""
import json
import logging
from dataclasses import asdict
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

from .constraints import active_constraints, rejection_summary
from .errors import BusBidderError, ConfigError
from .scoring import explain

PAGE = Path(__file__).with_name("web.html")
MAX_BODY = 10_000
log = logging.getLogger(__name__)


def _field(data, key, convert):
    value = data.get(key)
    if value is None or value == "":
        return None  # empty form field: keep the profile's own value
    try:
        return convert(value)
    except (TypeError, ValueError):
        raise ConfigError(f"invalid {key}: {value!r}")


def rank(app, data):
    args = SimpleNamespace(  # same shape argparse gives App.profile
        destination=_field(data, "destination", str), date_from=_field(data, "date_from", date.fromisoformat),
        date_to=_field(data, "date_to", date.fromisoformat), budget=_field(data, "budget", float),
        passengers=_field(data, "passengers", int), departure=_field(data, "departure", str),
        weights=_field(data, "weights", str))
    request, prefs = app.profile(str(data.get("profile", "")), args)
    ranking, rejected = app.rank(request, prefs)
    criteria = [c for c, w in prefs.weights.items() if w > 0]
    return {
        "hard": active_constraints(request),
        "soft": [f"preferred departure {prefs.preferred_departure:%H:%M}"]
                + [f"{c} {prefs.weights[c]:.0%}" for c in criteria],
        "removed": [f"{name} ({n})" for name, n in rejection_summary(rejected).most_common()],
        "criteria": criteria,
        "passengers": request.passengers,
        "offers": [{"rank": r.rank, "offer_id": r.offer.offer_id, "company": r.offer.company.name,
                    "departure": f"{r.offer.departure:%d/%m %H:%M}",
                    "duration": f"{r.offer.duration_min // 60}h{r.offer.duration_min % 60:02d}",
                    "price": f"{r.offer.price:.2f}", "transfers": r.offer.transfers, "fare": r.offer.fare.label,
                    "seats": r.offer.seats, "scores": [round(r.criterion_scores[c], 1) for c in criteria],
                    "total": round(r.total, 1)} for r in ranking],
        "explanation": explain(ranking, prefs),
    }


def book(app, data):
    passengers = _field(data, "passengers", int) or 1
    return asdict(app.store.book(str(data.get("offer_id", "")), str(data.get("name", "")), passengers))


def meta(app):
    return {"profiles": {n: {"description": p.description, "destination": p.request.destination,
                             "date_from": str(p.request.date_from), "date_to": str(p.request.date_to),
                             "budget": p.request.max_price, "passengers": p.request.passengers,
                             "departure": f"{p.preferences.preferred_departure:%H:%M}",
                             "weights": ",".join(f"{c}={round(w * 100)}" for c, w in p.preferences.weights.items())}
                         for n, p in app.profiles.items()},
            "destinations": sorted(app.destinations)}


def respond(app, path, data=None):
    """Route one API call; data is None for GET. Used by the HTTP server and by the browser (Pyodide) build."""
    get = {"/api/meta": meta, "/api/bookings": lambda a: [asdict(b) for b in a.store.bookings]}
    post = {"/api/rank": rank, "/api/book": book}
    try:
        if data is None and path in get:
            return 200, get[path](app)
        if data is not None and path in post:
            if not isinstance(data, dict):
                raise ConfigError("expected a JSON object")
            return 200, post[path](app, data)
        return 404, {"error": "not found"}
    except (BusBidderError, ValueError) as e:
        log.error("web: %s", e)
        return 400, {"error": str(e)}


class Handler(BaseHTTPRequestHandler):
    app = None  # set by serve()

    def _send(self, status, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            self._send(200, PAGE.read_bytes(), "text/html")
        else:
            self._send(*respond(self.app, self.path))

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 <= length <= MAX_BODY:
                raise ConfigError("request body too large")
            data = json.loads(self.rfile.read(length) or b"{}")
        except (BusBidderError, ValueError) as e:  # ValueError covers bad JSON and a bad Content-Length
            log.error("web: %s", e)
            return self._send(400, {"error": str(e)})
        self._send(*respond(self.app, self.path, data))

    def log_message(self, fmt, *args):
        log.info("web %s", fmt % args)


def serve(app, port=8000):
    Handler.app = app
    # Localhost only: there is no login, and one thread at a time keeps the seat check race-free.
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"BusBidder is running at http://127.0.0.1:{server.server_port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
