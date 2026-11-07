#!/usr/bin/env python3
"""Tests for the MobMaker REST API and static file serving.

Run via ``make test`` (unittest discover) or directly:
    python3 -m unittest server_test
"""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import mob_file
import server


def make_figure(fid, name, description="", interesting=False, not_interesting=False):
    return mob_file.Figure(id=fid, name=name, description=description,
                           interesting=interesting, not_interesting=not_interesting)


def canonical_mob():
    mob = mob_file.Mob(mob_name="test_mob", current_theme="initial theme")
    mob.figures = [
        make_figure("a1", "alice", "A description for alice."),
        make_figure("b2", "bob", "Bob's description.", not_interesting=True),
        make_figure("c3", "carlos", "Carlos's description.", interesting=True),
    ]
    return mob


class ServerApiTest(unittest.TestCase):
    """Exercises every REST endpoint against a temp mob directory."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.mob_dir = Path(cls._tmp.name)
        server.MOB_DIR = str(cls.mob_dir)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.MobMakerHandler)
        cls.port = cls.httpd.server_address[1]
        cls._thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls._tmp.cleanup()

    def setUp(self):
        # Each test starts from the same on-disk mob (every request reloads
        # from disk, so rewriting the file is enough to isolate tests).
        mob_file.save_mob(canonical_mob(), self.mob_dir)

    # -- helpers ---------------------------------------------------- #
    def api(self, method, path, body=None):
        """Send a request; return (status, raw_body_bytes).

        ``body`` may be a dict/list (sent as JSON) or raw bytes/None.
        """
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        headers = {}
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        else:
            data = body  # bytes or None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def api_json(self, method, path, body=None):
        status, raw = self.api(method, path, body)
        return status, json.loads(raw or b"{}")

    def reloaded(self):
        return mob_file.load_mob(self.mob_dir)

    # -- mob ----------------------------------------------------------- #
    def test_get_mob_shape(self):
        status, data = self.api_json("GET", "/api/mob")
        self.assertEqual(200, status)
        self.assertEqual("test_mob", data["mob_name"])
        self.assertEqual("initial theme", data["current_theme"])
        self.assertEqual(["a1", "b2", "c3"], [f["id"] for f in data["figures"]])
        self.assertEqual(["alice", "bob", "carlos"], [f["name"] for f in data["figures"]])
        for f in data["figures"]:
            self.assertEqual({"id", "name", "description", "interesting",
                              "not_interesting", "image_file", "stl_file"},
                             set(f.keys()))

    def test_patch_mob_theme_persists(self):
        status, data = self.api_json("PATCH", "/api/mob", {"current_theme": "new theme"})
        self.assertEqual(200, status)
        self.assertEqual("new theme", data["current_theme"])
        self.assertEqual("new theme", self.reloaded().current_theme)

    def test_patch_mob_bad_json_400(self):
        status, _ = self.api("PATCH", "/api/mob", b"{not json")
        self.assertEqual(400, status)

    # -- figures -------------------------------------------------------- #
    def test_patch_figure_name_and_description(self):
        status, data = self.api_json(
            "PATCH", "/api/figures/a1",
            {"name": "alice smith", "description": "Updated."})
        self.assertEqual(200, status)
        a1 = next(f for f in data["figures"] if f["id"] == "a1")
        self.assertEqual("alice smith", a1["name"])
        self.assertEqual("Updated.", a1["description"])
        self.assertEqual("alice smith", self.reloaded().figures[0].name)

    def test_interesting_clears_not_interesting(self):
        # b2 has not_interesting=True; marking it interesting should clear that.
        status, data = self.api_json("PATCH", "/api/figures/b2", {"interesting": True})
        self.assertEqual(200, status)
        b2 = next(f for f in data["figures"] if f["id"] == "b2")
        self.assertTrue(b2["interesting"])
        self.assertFalse(b2["not_interesting"])

    def test_not_interesting_clears_interesting(self):
        # c3 has interesting=True; marking it less-so should clear that.
        status, data = self.api_json("PATCH", "/api/figures/c3", {"not_interesting": True})
        self.assertEqual(200, status)
        c3 = next(f for f in data["figures"] if f["id"] == "c3")
        self.assertTrue(c3["not_interesting"])
        self.assertFalse(c3["interesting"])

    def test_flag_clear(self):
        # Explicitly clearing a flag to false (the "toggle off" path).
        status, data = self.api_json("PATCH", "/api/figures/c3", {"interesting": False})
        self.assertEqual(200, status)
        c3 = next(f for f in data["figures"] if f["id"] == "c3")
        self.assertFalse(c3["interesting"])

    def test_post_figure(self):
        _, data = self.api_json("GET", "/api/mob")
        n = len(data["figures"])
        status, data = self.api_json(
            "POST", "/api/figures", {"name": "dana", "description": "Dana's."})
        self.assertEqual(201, status)
        self.assertEqual(n + 1, len(data["figures"]))
        dana = next(f for f in data["figures"] if f["name"] == "dana")
        self.assertIn("id", dana)

    def test_delete_figure(self):
        _, data = self.api_json("GET", "/api/mob")
        n = len(data["figures"])
        status, data = self.api_json("DELETE", "/api/figures/c3")
        self.assertEqual(200, status)
        self.assertEqual(n - 1, len(data["figures"]))
        self.assertNotIn("c3", [f["id"] for f in data["figures"]])

    def test_persist_ids_makes_unkeyed_ids_stable(self):
        # Regression: a mob whose json has no "id" fields (like the bundled
        # demo) had its ids regenerated on every load, so a DELETE (or any
        # per-figure API call) could never match the figure the page
        # rendered.  persist_ids() must write those ids to disk once so
        # later loads see the same ids.
        no_id_mob = {
            "mob_name": "demo",
            "current_theme": "t",
            "figures": [
                {"name": "stocky", "description": "a man"},
                {"name": "maria", "description": "a woman"},
            ],
        }
        (self.mob_dir / "mob_description.json").write_text(
            json.dumps(no_id_mob), encoding="utf-8")

        server.persist_ids()

        first = mob_file.load_mob(self.mob_dir)
        second = mob_file.load_mob(self.mob_dir)
        ids1 = [f.id for f in first.figures]
        ids2 = [f.id for f in second.figures]
        self.assertEqual(ids1, ids2)  # stable across loads
        self.assertTrue(all(ids1))    # ids were actually written
        raw = (self.mob_dir / "mob_description.json").read_text("utf-8")
        self.assertEqual(raw.count('"id"'), 2)

    # -- errors ---------------------------------------------------------- #
    def test_patch_unknown_id_404(self):
        status, data = self.api_json("PATCH", "/api/figures/nope", {"name": "x"})
        self.assertEqual(404, status)
        self.assertIn("error", data)

    def test_delete_unknown_id_404(self):
        status, _ = self.api("DELETE", "/api/figures/nope")
        self.assertEqual(404, status)

    def test_unknown_endpoint_404(self):
        status, _ = self.api("GET", "/api/does-not-exist")
        self.assertEqual(404, status)

    # -- generation ------------------------------------------------------ #
    def test_generate_descriptions_calls_process(self):
        def fake_descriptions(mob):
            # Append two more figures as process.generate_descriptions would.
            before = len(mob.figures)
            mob.figures.append(make_figure("x9", "xavier", "X's."))
            mob.figures.append(make_figure("y9", "yolanda", "Y's."))
            return len(mob.figures) - before
        with mock.patch("server.process.generate_descriptions",
                        side_effect=fake_descriptions):
            status, data = self.api_json("POST", "/api/mob/descriptions")
        self.assertEqual(200, status)
        self.assertEqual(["x9", "y9"],
                         [f["id"] for f in data["figures"][-2:]])
        # persisted to disk
        ids = [f.id for f in self.reloaded().figures]
        self.assertIn("x9", ids)
        self.assertIn("y9", ids)

    def test_generate_descriptions_error_500(self):
        def boom(mob):
            raise mob_file.MobFileError("no theme")
        with mock.patch("server.process.generate_descriptions", side_effect=boom):
            status, data = self.api_json("POST", "/api/mob/descriptions")
        self.assertEqual(500, status)
        self.assertIn("no theme", data.get("error", ""))

    def test_generate_image_calls_process(self):
        def fake_image(mob, fig):
            caller_id = fig.id
            fig.image_file = "%s.png" % caller_id
            return fig.image_file
        with mock.patch("server.process.generate_image", side_effect=fake_image):
            status, data = self.api_json("POST", "/api/figures/a1/image")
        self.assertEqual(200, status)
        a1 = next(f for f in data["figures"] if f["id"] == "a1")
        self.assertEqual("a1.png", a1["image_file"])
        self.assertEqual("a1.png", self.reloaded().figures[0].image_file)

    def test_generate_stl_calls_process(self):
        # Give a1 a reference image on disk first (generate_stl requires one).
        (self.mob_dir / "a1.png").write_bytes(b"fake image")
        mob = self.reloaded()
        mob.figures[0].image_file = "a1.png"
        mob_file.save_mob(mob, self.mob_dir)

        def fake_stl(mob, fig):
            fig.stl_file = "%s.stl" % fig.id
            (self.mob_dir / fig.stl_file).write_text("solid stub\nendsolid\n")
            return fig.stl_file
        with mock.patch("server.process.generate_stl", side_effect=fake_stl):
            status, data = self.api_json("POST", "/api/figures/a1/stl")
        self.assertEqual(200, status)
        a1 = next(f for f in data["figures"] if f["id"] == "a1")
        self.assertEqual("a1.stl", a1["stl_file"])
        self.assertEqual("a1.stl", self.reloaded().figures[0].stl_file)

    def test_generate_image_process_error_500(self):
        def boom(mob, fig):
            raise mob_file.MobFileError("generator exploded")
        with mock.patch("server.process.generate_image", side_effect=boom):
            status, data = self.api_json("POST", "/api/figures/a1/image")
        self.assertEqual(500, status)
        self.assertIn("generator exploded", data.get("error", ""))

    # -- file serving ----------------------------------------------------- #
    def test_serves_mob_file_with_type(self):
        (self.mob_dir / "alice.png").write_bytes(b"\x89PNG fake")
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/files/alice.png" % self.port) as r:
            self.assertEqual(200, r.status)
            self.assertEqual("image/png", r.headers.get_content_type())
            # Mob files are regenerated in place; must not be cached or a
            # browser would keep showing the stale image after a reload.
            self.assertEqual("no-store", r.headers.get("Cache-Control"))
            self.assertEqual(b"\x89PNG fake", r.read())

    def test_missing_file_404(self):
        status, _ = self.api("GET", "/api/files/nope.png")
        self.assertEqual(404, status)

    def test_file_serving_cannot_escape_mob_dir(self):
        # basename is forced into the mob folder, so a traversal-looking path
        # cannot read arbitrary host files.
        status, _ = self.api("GET", "/api/files/../../etc/passwd")
        self.assertEqual(404, status)

    # -- page -------------------------------------------------------------- #
    def test_index_served_with_data(self):
        status, raw = self.api("GET", "/")
        self.assertEqual(200, status)
        html = raw.decode("utf-8", "replace")
        self.assertIn("window.MOB_DATA", html)
        self.assertNotIn("MOB_DATA -->", html)  # the marker was replaced


if __name__ == "__main__":
    unittest.main()
