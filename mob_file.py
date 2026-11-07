#!/usr/bin/env python3
"""Load and save MobMaker mob directories.

File layout (see README.md): there is one folder for each mob.  The
top level file ``mob_description.json`` contains the current
description of the mob - the collection of potential figures - and the
list of all descriptions with the filenames of images created and STL
files produced.  The rest of the folder contains those files.

This module defines the two Python objects that back the web UI:

* :class:`Mob`    -- summary information about the whole set of figures.
* :class:`Figure` -- one figure: description, interest marks, image and
  STL filenames.

Public API
----------
``load_mob(path)``       load ``<path>/mob_description.json`` (or the
                         json file itself) and return a :class:`Mob`.
``load_mob_text(text)``  parse description text and return a :class:`Mob`.
``save_mob(mob, path)``  write a :class:`Mob`` back to a json file.
``Mob.summary()``        dict with counts for the figure set.
``Mob.describe()``       readable one-paragraph summary.
``Figure.describe()``    readable one-line description.

The description file must be valid JSON (``json.loads`` is used).  The
``"figures"`` value may be either an array of figure objects, or an
object mapping a summary string to a figure object (the summary is
injected into the object if it lacks one).  Unknown keys are preserved
in ``Mob.extra`` / ``Figure.extra`` for forward compatibility.

"""

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = ["Figure", "Mob", "MobFileError", "load_mob", "load_mob_text", "save_mob"]

#: Canonical name of the top-level json file inside a mob folder.
MOB_JSON_NAME = "mob_description.json"

#: Keys recognized at the top level.
_MOB_KEYS = {"mob_name", "current_theme", "figures"}

#: Keys recognized on a figure.
_FIGURE_KEYS = {
    "id", "name", "description",
    "interesting", "not_interesting",
    "image_file", "stl_file",
}

class MobFileError(Exception):
    """Raised when a mob description file cannot be read or parsed."""


def _gen_id(name, description):
    """Make a stable-looking, human-readable id for a figure.

    Uses a slug of the name/description plus a short random suffix for
    uniqueness.  The id is persisted to the json file when the mob is
    saved, so it stays stable across reloads.

    ids are used primarily for identifying a figure in REST APIs.
    """
    base = (name or description or "fig").replace("\n", " ")
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:40]
    suffix = uuid.uuid4().hex[:6]
    if slug:
        return "%s-%s" % (slug, suffix)
    return "fig-%s" % uuid.uuid4().hex[:10]


def potential_name(description):
    """Return a potential short name for a figure without one.

    This is intended to create reasonable filenames for images and STL
    files inside the mob database so that the user can retrieve or edit
    interesting files.
    """
    return '_'.join(description.split()[:3]).lower()

# ---------------------------------------------------------------------------
# Figure
# ---------------------------------------------------------------------------

@dataclass
class Figure:
    """One figure in a mob.

    Attributes mirror ``mob_description.json``:

    * ``id`` -- stable unique identifier assigned on load; persisted to
      the file so the web UI / REST APIs can address this figure.
    * ``name`` -- short name shown in the UI (e.g. "foreman bob").  This
      doubles as a short, human-friendly filename stem for the files
      belonging to this figure.
    * ``description`` -- the full figure description fed to the image
      generator.
    * ``interesting`` -- user marked this figure as interesting (star).
    * ``not_interesting`` -- user marked it as not interesting (frown).
    * ``image_file`` -- filename (relative to the mob folder) of the
      generated reference image; empty/None if not yet generated.
    * ``stl_file`` -- filename of the Tripo3D-produced STL; empty/None
      if not yet produced.
    """

    id: Optional[str] = None
    name: Optional[str] = None
    description: str = ""
    interesting: bool = False
    not_interesting: bool = False
    image_file: Optional[str] = None
    stl_file: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    # Back-reference to the parent Mob (set by Mob.from_dict / load_mob).
    # Used to resolve image/STL filenames against the mob folder.
    _mob = None

    def __post_init__(self):
        self.image_file = self.image_file or ""
        self.stl_file = self.stl_file or ""

    # -- construction -------------------------------------------------------
    @staticmethod
    def _first(d, aliases, default=None):
        for a in aliases:
            if a in d:
                return d[a]
        return default

    @classmethod
    def from_dict(cls, data, mob=None):
        """Build a Figure from its dict representation.

        ``data`` may be a figure dict, a (key, value) pair where the key
        carries the name, or a plain string (treated as the
        description).
        """
        key_data, value = data, None
        if isinstance(data, tuple) and len(data) == 2 and isinstance(data[0], (str, dict)):
            key_data, value = data
        if isinstance(key_data, (str, dict)) and value is None:
            key_data, value = {}, key_data
        if not isinstance(value, dict):
            value = {"description": value if isinstance(value, str) else ""}
        merged = dict(key_data)
        merged.update(value)  # value's fields win over the key's fields
        data = merged

        desc = data.get('description', '')
        name = data.get('name')
        if not name:
            name = potential_name(desc)

        fig_id = data.get("id")
        if not fig_id:
            fig_id = _gen_id(name, desc)
            
        interesting = data.get('interesting', False)
        not_interesting = data.get('not_interesting', False)
        image_file = data.get('image_file', '')
        stl_file = data.get('stl_file', '')

        fig = cls(
            id=fig_id,
            name=name,
            description=desc,
            interesting=bool(interesting) if interesting is not None else False,
            not_interesting=bool(not_interesting) if not_interesting is not None else False,
            image_file=image_file if isinstance(image_file, str) else "",
            stl_file=stl_file if isinstance(stl_file, str) else "",
        )
        if mob is not None:
            fig._mob = mob
        return fig

    # -- derived ------------------------------------------------------------
    def image_path(self):
        """Absolute path of the reference image, or None."""
        if self._mob is None or not self.image_file:
            return None
        return self._mob.image_path(self.image_file)

    def stl_path(self):
        """Absolute path of the STL file, or None."""
        if self._mob is None or not self.stl_file:
            return None
        return self._mob.image_path(self.stl_file)

    def has_image(self):
        return bool(self.image_file)

    def has_stl(self):
        return bool(self.stl_file)

    def prompt(self):
        """The full prompt sent to the image generator for this figure."""

        # TODO(bowdidge): avoid duplication between here and generation.py

        fixed = ("Create a reference image with a front, side, and rear image "
                 "of a realistic human figure suitable for creating a 3d model "
                 "of a person.  Each of the views must be on a blank background.  "
                 "Each of the views must have the figure in the same pose.")
        return fixed + (self.description or self.name or "")

    def describe(self):
        label = self.name or self.description[:40] or "unnamed figure"
        bits = [label]
        if self.has_image():
            bits.append("image: %s" % self.image_file)
        else:
            bits.append("no image")
        if self.has_stl():
            bits.append("stl: %s" % self.stl_file)
        marks = []
        if self.interesting:
            marks.append("starred")
        if self.not_interesting:
            marks.append("dismissed")
        if marks:
            bits.append(", ".join(marks))
        return " - ".join(bits)

    def to_dict(self):
        d = {"id": self.id, "name": self.name, "description": self.description,
             "interesting": self.interesting, "not_interesting": self.not_interesting,
             "image_file": self.image_file or None, "stl_file": self.stl_file or None}
        d.update(self.extra)
        return {k: v for k, v in d.items() if v is not None}

    def __str__(self):
        return self.describe()


# ---------------------------------------------------------------------------
# Mob
# ---------------------------------------------------------------------------

@dataclass
class Mob:
    """A mob: the overall set of figures being produced for a theme.

    Attributes mirror the top level of ``mob_description.json``:

    * ``mob_name`` -- display name of the mob.
    * ``current_theme`` -- the current description of the mob (what the
      LLM expands into figure descriptions).
    * ``figures`` -- the :class:`Figure` objects, in file order.
    * ``extra`` -- additional top-level keys (forward compatibility,
      e.g. "things to automatically reject").
    * ``directory`` -- the mob folder (set when loaded from disk).
    * ``description_path`` -- path of the ``mob_description.json`` file
      (set when loaded from disk).
    """

    mob_name: str = ""
    current_theme: str = ""
    figures: List[Figure] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)
    directory: Optional[Path] = None
    description_path: Optional[Path] = None

    # -- construction -------------------------------------------------------
    @classmethod
    def from_dict(cls, data):
        extra = {k: v for k, v in data.items() if k not in _MOB_KEYS}
        mob = cls(
            mob_name=data.get("mob_name") or "",
            current_theme=data.get("current_theme") or "",
            extra=extra,
        )
        entries = _normalize_figures(data.get("figures", data.get("descriptions")))
        for entry in entries or []:
            mob.figures.append(Figure.from_dict(entry, mob=mob))
        return mob

    # -- derived ------------------------------------------------------------
    def image_path(self, filename):
        """Resolve a file inside the mob folder (images, STLs, photos)."""
        if not filename:
            return None
        if self.directory is not None:
            return Path(self.directory) / filename
        if self.description_path is not None:
            return self.description_path.parent / filename
        return Path(filename)

    def summary(self):
        """Summary information about the set of figures (a dict)."""
        n = len(self.figures)
        return {
            "mob_name": self.mob_name,
            "current_theme": self.current_theme,
            "figure_count": n,
            "interesting_count": sum(1 for f in self.figures if f.interesting),
            "not_interesting_count": sum(1 for f in self.figures if f.not_interesting),
            "with_images": sum(1 for f in self.figures if f.has_image()),
            "with_stl": sum(1 for f in self.figures if f.has_stl()),
        }

    def describe(self):
        """Describe a mob when debugging."""
        s = self.summary()
        if not s["figure_count"]:
            return self.mob_name + " - no figures yet"
        return self.mob_name + " - " + (
            "%d figure(s): %d starred, %d dismissed, %d with reference images, "
            "%d with STL models"
            % (s["figure_count"], s["interesting_count"],
               s["not_interesting_count"], s["with_images"], s["with_stl"])
        )

    def to_dict(self):
        d = {
            "mob_name": self.mob_name,
            "current_theme": self.current_theme,
            "figures": [f.to_dict() for f in self.figures],
        }
        d.update(self.extra)
        return {k: v for k, v in d.items()
                if v not in (None, "", []) or k == "figures"}

    def __str__(self):
        return self.describe()


# ---------------------------------------------------------------------------
# File APIs
# ---------------------------------------------------------------------------

def _normalize_figures(raw):
    """Turn the stored ``"figures"`` value into a list of figure entries.

    Accepts an array of figure dicts/strings, or an object mapping a
    name string to a figure dict (the name is injected into the
    dict if it does not already have one).  Returns None when the key
    is absent.
    """
    if raw is None:
        return None
    if not isinstance(raw, (dict, list)):
        raise MobFileError("'figures' must be an object or an array")
    if isinstance(raw, list):
        return list(raw)
    out = []
    for k, v in raw.items():
        if isinstance(v, dict):
            v = dict(v)
            if v.get("name") is None:
                v["name"] = k
        out.append(v)
    return out


def load_mob(path):
    """Load a mob from disk and return a :class:`Mob`.

    ``path`` may be a mob folder (its ``mob_description.json`` is
    loaded) or the ``mob_description.json`` file itself.  The file must
    contain valid JSON.

    Raises:
        FileNotFoundError: the given path does not exist.
        MobFileError: the file cannot be read or is not valid JSON.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError("no such file or directory: %s" % p)
    if p.is_dir():
        base_dir = p
        json_path = p / MOB_JSON_NAME
        if not json_path.exists():
            raise MobFileError("no %s in mob directory %s" % (MOB_JSON_NAME, p))
    else:
        json_path = p
        base_dir = p.parent
    try:
        text = json_path.read_text(encoding="utf-8")
    except OSError as e:
        raise MobFileError("cannot read %s: %s" % (json_path, e))
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise MobFileError("%s is not valid JSON: %s" % (json_path, e))
    if not isinstance(data, dict):
        raise MobFileError("%s: top level must be a JSON object" % json_path)
    mob = Mob.from_dict(data)
    mob.directory = base_dir
    mob.description_path = json_path
    return mob


def load_mob_text(text):
    """Parse mob description JSON text and return a :class:`Mob`.

    The text must be valid JSON.  The returned Mob has no
    ``directory``/``description_path``; relative filenames will resolve
    against the current working directory.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise MobFileError("could not parse mob description text: %s" % e)
    if not isinstance(data, dict):
        raise MobFileError("top level must be a JSON object")
    return Mob.from_dict(data)


def save_mob(mob, path=None):
    """Write a Mob back to a ``mob_description.json`` file.

    ``path`` is the destination file, or the mob folder (in which case
    ``MOB_JSON_NAME`` is used inside it).  If ``path`` is None the file
    the mob was loaded from is reused.  Returns the file written.
    """
    if path is None:
        if mob.description_path is not None:
            path = mob.description_path
        elif mob.directory is not None:
            path = Path(mob.directory) / MOB_JSON_NAME
        else:
            raise MobFileError("no path given and mob has no known location")
    p = Path(path)
    if p.is_dir():
        p = p / MOB_JSON_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(mob.to_dict(), indent=4, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return p


def main(argv=None):
    """CLI: ``python3 mob_file.py <mob_dir|json_file>`` -- print a summary."""
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        logger.info(
            "usage: python3 mob_file.py <mob_directory | mob_description.json>")
        return 1
    mob = load_mob(argv[0])
    logger.info(mob.describe())
    for fig in mob.figures:
        logger.info("  * %s", fig.describe())
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
