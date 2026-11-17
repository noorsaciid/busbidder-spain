import json
import logging
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

from busbidder import cli, web


class WebTest(unittest.TestCase):
    """Real HTTP round trip on a free port: page, rank, book, bookings, bad input."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        web.Handler.app = cli.App(Path(cls.tmp.name))
        cls.server = HTTPServer(("127.0.0.1", 0), web.Handler)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        logging.shutdown()
        cls.tmp.cleanup()

    def call(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data, {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_page_and_meta(self):
        status, page = self.call("/")
        self.assertEqual(status, 200)
        self.assertIn(b"BusBidder Spain", page)
        meta = json.loads(self.call("/api/meta")[1])
        self.assertIn("example", meta["profiles"])
        self.assertEqual(len(meta["destinations"]), 15)

    def test_rank_book_and_list(self):
        status, body = self.call("/api/rank", {"profile": "business", "passengers": "2"})
        r = json.loads(body)
        self.assertEqual(status, 200)
        top = r["offers"][0]
        self.assertIn("ranks first only under", r["explanation"])
        status, body = self.call("/api/book", {"offer_id": top["offer_id"], "name": "Web Test", "passengers": 2})
        self.assertEqual(status, 200)
        self.assertRegex(json.loads(body)["reference"], r"^BB-")
        again = json.loads(self.call("/api/rank", {"profile": "business", "passengers": "2"})[1])
        self.assertEqual(next(o for o in again["offers"] if o["offer_id"] == top["offer_id"])["seats"], top["seats"] - 2)
        self.assertEqual(len(json.loads(self.call("/api/bookings")[1])), 1)

    def test_bad_input_is_a_400_with_a_message(self):
        for body in ({"profile": "nobody"}, {"profile": "example", "budget": "-5"}, {"profile": "example", "date_from": "July"},
                     {"profile": "example", "weights": "price=90"}, {"offer_id": "XX", "name": "A"}):
            path = "/api/book" if "offer_id" in body else "/api/rank"
            status, data = self.call(path, body)
            self.assertEqual(status, 400, body)
            self.assertTrue(json.loads(data)["error"])
        self.assertEqual(self.call("/nope")[0], 404)


if __name__ == "__main__":
    unittest.main()
