#!/usr/bin/env python3
"""Minimal web server for MobMaker.

Serves the web UI from the ``web/`` directory, populated with the mob
loaded (per request) from a currently hardcoded mob directory via
``mob_file``.  The loaded mob is injected into the page as a script
block defining ``window.MOB_DATA``.

REST APIs
---------
The server exposes a small JSON API under ``/api/...`` which edits the
mob's ``mob_description.json`` on disk.  Every request loads the mob,
applies the change, and writes it back; there is no in-memory state
(other than a lock to serialize load/mutate/save cycles).

* ``GET     /api/mob``              -- the full mob payload.
* ``PATCH   /api/mob``              -- update ``mob_name`` / ``current_theme``.
* ``POST    /api/mob/descriptions`` -- generate figures from the theme
  (via ``process.generate_descriptions``).
* ``POST    /api/figures``          -- append a new figure.
* ``PATCH   /api/figures/<id>``     -- update description / name / flags.
* ``POST    /api/figures/<id>/image`` -- generate a reference image.
* ``POST    /api/figures/<id>/stl``   -- generate an STL from the image.
* ``DELETE  /api/figures/<id>``     -- remove a figure.
* ``GET     /api/files/<name>``     -- serve a file from the mob folder
  (e.g. a generated reference image or STL).

Each mutation endpoint returns the refreshed full mob payload.  All
requests and responses are JSON (UTF-8).

Run:
    python3 server.py [options] [PORT]

Options:
    -p, --port PORT      listening port (default: 8000)
    -m, --mob_dir DIR    a mob folder (or its mob_description.json) to serve;
    -m, --mob-dir DIR    (alias for --mob_dir; defaults to $MM_MOB_DIR or the
                         bundled demo_mob)
    -h, --help           show usage and exit

A bare positional PORT is accepted as a shorthand for --port.

The server binds to 127.0.0.1 (localhost only).
"""

import getopt
import json
import logging
import mimetypes
import os
import posixpath
import sys
import threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

logger = logging.getLogger(__name__)

import mob_file
import process

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")

# The mob to serve.  Defaults to the bundled sample; override with the
# MM_MOB_DIR environment variable (a mob folder or its
# mob_description.json file).  Picking/creating mobs from the UI comes
# later.
MOB_DIR = os.environ.get("MM_MOB_DIR") or os.path.join(BASE_DIR, "demo_mob")

DEFAULT_PORT = 8000


class _FigureNotFound(Exception):
    """Raised when an API refers to a figure id that does not exist."""

# Marker in web/index.html where the mob-data script block is spliced in
# (it appears just before the page's own <script>).
MOB_DATA_MARKER = "<!-- MOB_DATA -->"

with open(os.path.join(WEB_DIR, "index.html"), encoding="utf-8") as _f:
    INDEX_TEMPLATE = _f.read()
if MOB_DATA_MARKER not in INDEX_TEMPLATE:
    raise SystemExit("web/index.html is missing the %r marker" % MOB_DATA_MARKER)


def mob_payload(mob=None):
    """A JSON-serializable view of the mob for the web page / API.

    The mob is reloaded on every request so edits to the mob file show
    up on a page refresh.  If the file cannot be loaded, the payload is
    empty and carries the error message for the page to display.
    """
    if mob is not None:
        return _payload_from_mob(mob)
    try:
        mob = mob_file.load_mob(MOB_DIR)
    except (OSError, mob_file.MobFileError) as e:
        sys.stderr.write("[mobmaker] %s\n" % e)
        return {
            "mob_name": "", "current_theme": "", "figures": [],
            "error": "%s: %s" % (type(e).__name__, e),
        }
    return _payload_from_mob(mob)


# Fields surfaced to the browser/API for each figure.  Values come from
# ``Figure.to_dict`` (single source); missing file names are normalized.
API_FIGURE_KEYS = ("id", "name", "description", "interesting",
                    "not_interesting", "image_file", "stl_file")


def _api_figure(fig):
    d = fig.to_dict()
    d["image_file"] = fig.image_file or ""
    d["stl_file"] = fig.stl_file or ""
    return {k: d.get(k) for k in API_FIGURE_KEYS}


def _payload_from_mob(mob):
    return {
        "mob_name": mob.mob_name,
        "current_theme": mob.current_theme,
        "figures": [_api_figure(f) for f in mob.figures],
    }


class MobMakerHandler(SimpleHTTPRequestHandler):
    """Serve the UI plus a small JSON REST API.

    Static files (and the rendered index page) are served via the
    ``SimpleHTTPRequestHandler`` machinery; ``/api/...`` paths are routed
    to the JSON handlers below.  ``_lock`` serializes the
    load-mutate-save cycle so concurrent requests cannot drop edits.
    """

    _lock = threading.Lock()

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("directory", WEB_DIR)
        super().__init__(*args, **kwargs)

    def log_message(self, fmt, *args):
        sys.stderr.write("[mobmaker] %s - %s\n" % (self.address_string(), fmt % args))

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _send_json(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        """Parse the request body as JSON, tolerating an empty body."""
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw.strip():
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError("invalid JSON body: %s" % e)

    def _load_save(self, mutate):
        """Run ``mutate(mob)`` under the lock and persist the result.

        Loads the mob from disk, calls ``mutate`` with it, writes it back,
        and returns the (mutated) mob.
        """
        with self._lock:
            mob = mob_file.load_mob(MOB_DIR)
            mutate(mob)
            mob_file.save_mob(mob)
            return mob

    @staticmethod
    def _find_figure(mob, key):
        for f in mob.figures:
            if f.id == key:
                return f
        return None

    # ------------------------------------------------------------------ #
    # methods
    # ------------------------------------------------------------------ #
    def do_GET(self):
        if self.path.startswith("/api/"):
            self._route_api("GET")
        elif self.path in ("/", "/index.html"):
            self._serve_index()
        else:
            super().do_GET()

    def do_POST(self):
        self._route_api("POST")

    def do_PATCH(self):
        self._route_api("PATCH")

    def do_DELETE(self):
        self._route_api("DELETE")

    def _route_api(self, method):
        path = self.path.split("?")[0]
        try:
            if path == "/api/mob/descriptions":
                if method == "POST":
                    return self._generate_descriptions()

            if path == "/api/mob":
                if method == "GET":
                    return self._send_json(200, mob_payload())
                if method == "PATCH":
                    return self._patch_mob()

            elif path == "/api/figures":
                if method == "POST":
                    return self._post_figure()

            elif path.startswith("/api/figures/"):
                rest = path[len("/api/figures/"):].strip("/")
                parts = rest.split("/", 1)
                key = parts[0]
                if not key:
                    return self._send_json(404, {"error": "missing figure id"})
                if len(parts) == 1:
                    if method == "PATCH":
                        return self._patch_figure(key)
                    if method == "DELETE":
                        return self._delete_figure(key)
                else:
                    sub = parts[1]
                    if method == "POST" and sub == "image":
                        return self._generate_image(key)
                    if method == "POST" and sub == "stl":
                        return self._generate_stl(key)

            elif path.startswith("/api/files/"):
                if method == "GET":
                    return self._serve_mob_file(path[len("/api/files/"):])

            return self._send_json(404, {"error": "unknown endpoint: %s" % path})
        except ValueError as e:
            return self._send_json(400, {"error": str(e)})
        except _FigureNotFound as e:
            return self._send_json(404, {"error": str(e)})
        except (OSError, mob_file.MobFileError) as e:
            sys.stderr.write("[mobmaker] %s\n" % e)
            return self._send_json(500, {"error": str(e)})

    # -- mob level ----------------------------------------------------- #
    def _patch_mob(self):
        patch = self._read_json()
        def edit(mob):
            if "current_theme" in patch:
                mob.current_theme = str(patch["current_theme"])
            if "mob_name" in patch:
                mob.mob_name = str(patch["mob_name"])
            # ignore (preserve) anything we don't understand
        mob = self._load_save(edit)
        return self._send_json(200, mob_payload(mob))

    def _generate_descriptions(self):
        """POST /api/mob/descriptions: generate figures from the theme."""
        def edit(mob):
            process.generate_descriptions(mob)  # appends to mob.figures
        mob = self._load_save(edit)
        return self._send_json(200, mob_payload(mob))

    # -- figures ------------------------------------------------------- #
    def _post_figure(self):
        body = self._read_json()
        data = dict(body)
        if "description" not in data and "name" not in data:
            data.setdefault("description", "")
        def add(mob):
            fig = mob_file.Figure.from_dict(data, mob=mob)
            mob.figures.append(fig)
        mob = self._load_save(add)
        return self._send_json(201, mob_payload(mob))

    def _patch_figure(self, key):
        patch = self._read_json()
        def edit(mob):
            fig = self._find_figure(mob, key)
            if fig is None:
                raise _FigureNotFound(key)
            if "description" in patch:
                fig.description = str(patch["description"])
            if "name" in patch:
                fig.name = patch["name"]
            if "interesting" in patch:
                fig.interesting = bool(patch["interesting"])
                if fig.interesting:
                    fig.not_interesting = False
            if "not_interesting" in patch:
                fig.not_interesting = bool(patch["not_interesting"])
                if fig.not_interesting:
                    fig.interesting = False
        mob = self._load_save(edit)
        return self._send_json(200, mob_payload(mob))

    def _delete_figure(self, key):
        def edit(mob):
            fig = self._find_figure(mob, key)
            if fig is None:
                raise _FigureNotFound(key)
            mob.figures.remove(fig)
        mob = self._load_save(edit)
        return self._send_json(200, mob_payload(mob))

    # -- generation --------------------------------------------------- #
    def _generate_image(self, key):
        """POST /api/figures/<id>/image: run the image generator."""
        def edit(mob):
            fig = self._find_figure(mob, key)
            if fig is None:
                raise _FigureNotFound(key)
            process.generate_image(mob, fig)  # updates fig.image_file
        mob = self._load_save(edit)
        return self._send_json(200, mob_payload(mob))

    def _generate_stl(self, key):
        """POST /api/figures/<id>/stl: run the STL generator."""
        def edit(mob):
            fig = self._find_figure(mob, key)
            if fig is None:
                raise _FigureNotFound(key)
            process.generate_stl(mob, fig)  # updates fig.stl_file
        mob = self._load_save(edit)
        return self._send_json(200, mob_payload(mob))

    # -- serving generated files -------------------------------------- #
    def _serve_mob_file(self, name):
        """GET /api/files/<name>: serve a file from the mob folder.

        Names are reduced to their basename and must exist under MOB_DIR,
        so this cannot be used to read arbitrary paths on the host.
        """
        base = posixpath.basename(name)
        if not base or "\x00" in base:
            return self._send_json(404, {"error": "invalid file name"})
        file_path = os.path.join(MOB_DIR, base)
        if not os.path.isfile(file_path):
            return self._send_json(404, {"error": "no such file: %s" % base})
        ctype = (mimetypes.guess_type(base)[0]
                 or "application/octet-stream")
        try:
            with open(file_path, "rb") as f:
                body = f.read()
        except OSError as e:
            return self._send_json(500, {"error": "cannot read file: %s" % e})
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        # Mob files are overwritten in place when an image/STL is
        # regenerated, but their URL stays the same; without an explicit
        # freshness rule the browser may serve a stale cached copy after
        # a reload.  Disable caching so re-renders always fetch the
        # current file.
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------ #
    # index rendering
    # ------------------------------------------------------------------ #
    def _serve_index(self):
        # Escape "</" so a description cannot terminate the injected script.
        data = json.dumps(mob_payload()).replace("</", "<\\/")
        html = INDEX_TEMPLATE.replace(
            MOB_DATA_MARKER, '<script>window.MOB_DATA = %s;</script>' % data)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def persist_ids():
    """Ensure every figure has a stable, persisted id before serving.

    ``Figure.from_dict`` auto-assigns a brand-new id to any figure that
    lacks one (the bundled demo mob has no ids in its json), so a figure's
    id can change on every load.  Saving once at startup writes those
    generated ids back to the file so they stay stable for the life of the
    app and across restarts.

    This save must be unconditional: after ``from_dict`` has assigned one,
    a freshly-generated id is indistinguishable from a stored id, so there
    is no reliable way to skip the write when nothing changed.
    """
    try:
        with MobMakerHandler._lock:
            mob = mob_file.load_mob(MOB_DIR)
            mob_file.save_mob(mob)
    except (OSError, mob_file.MobFileError) as e:
        sys.stderr.write("[mobmaker] could not ensure ids: %s\n" % e)


_USAGE = """usage: python3 server.py [options] [PORT]

Serve the MobMaker web UI and its JSON REST API.

Options:
    -p, --port PORT      listening port (default: %(port)d)
    -m, --mob_dir DIR    a mob folder (or its mob_description.json) to serve
    -m, --mob-dir DIR    (alias for --mob_dir; defaults to the $MM_MOB_DIR
                         environment variable, or the bundled demo_mob)
    -h, --help           show this help message and exit

A bare POSITIONAL  PORT is accepted as a shorthand for --port.
""" % {"port": DEFAULT_PORT}


def main(argv=None):
    """Parse command-line options and run the server.

    Args:
        argv: argument list (defaults to ``sys.argv[1:]``).  Overridable for
            testing.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    global MOB_DIR
    argv = sys.argv[1:] if argv is None else argv
    port = DEFAULT_PORT
    try:
        opts, args = getopt.getopt(
            argv, "hp:m:", ["help", "port=", "mob_dir=", "mob-dir="])
    except getopt.GetoptError as e:
        logger.error("server.py: %s", e)
        logger.info(_USAGE)
        return 2

    for opt, val in opts:
        if opt in ("-h", "--help"):
            logger.info(_USAGE)
            return 0
        if opt in ("-p", "--port"):
            port = int(val)
        elif opt in ("-m", "--mob_dir", "--mob-dir"):
            MOB_DIR = val

    # Backward compatible shorthand: a bare positional port.
    if args and len(opts) == 0:
        port = int(args[0])

    server = ThreadingHTTPServer(("127.0.0.1", port), MobMakerHandler)
    persist_ids()
    logger.info("MobMaker UI at http://127.0.0.1:%d/  (mob: %s)",
                port, os.path.relpath(MOB_DIR, BASE_DIR))
    logger.info("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down.")
        server.server_close()
    return 0


if __name__ == "__main__":
    main()
