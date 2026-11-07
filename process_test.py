#!/usr/bin/env python3
"""Tests for process.py (image / STL generation steps).

Run via ``make test`` (unittest discover) or directly:
    python3 -m unittest process_test
"""

import builtins
import tempfile
import unittest
import types
from pathlib import Path

import mob_file
import process


def _no_image_generation_import(name, *a, **k):
    """__import__ stand-in that pretends the GenAI SDK is missing."""
    if name == "generation":
        raise ImportError("no module named 'generation'")
    return original_import(name, *a, **k)


def _stub_image_generation_import(generate_fn):
    """__import__ stand-in exposing a stub generation module."""
    def do_import(name, *a, **k):
        if name == "generation":
            mod = types.ModuleType("generation")
            mod.generate_image_from_prompt = generate_fn
            return mod
        return original_import(name, *a, **k)
    return do_import


def _stub_import_with(functions):
    """__import__ stand-in exposing a stub module with given callables.

    ``functions`` maps attribute name -> callable.  Missing attributes raise
    AttributeError (like a real partially-populated module would not, but it
    makes accidental uses visible).
    """
    def do_import(name, *a, **k):
        if name == "generation":
            mod = types.ModuleType("generation")
            for attr, fn in functions.items():
                setattr(mod, attr, fn)
            return mod
        return original_import(name, *a, **k)
    return do_import


original_import = builtins.__import__


class GenerateImageTest(unittest.TestCase):

    def _mob(self, d):
        mob_dir = Path(d)
        return mob_file.Mob(directory=mob_dir,
                            description_path=mob_dir / "mob_description.json")

    def test_generate_image_fails_cleanly_without_sdk(self):
        with tempfile.TemporaryDirectory() as d:
            mob = self._mob(d)
            fig = mob_file.Figure(id="f1", name="fig", description="a figure")
            fig._mob = mob
            builtins.__import__ = _no_image_generation_import
            try:
                with self.assertRaises(mob_file.MobFileError) as ctx:
                    process.generate_image(mob, fig)
            finally:
                builtins.__import__ = original_import
            self.assertIn("image generation unavailable", str(ctx.exception))
            self.assertEqual("", fig.image_file)  # unchanged on failure

    def test_generate_image_writes_and_sets_image_file(self):
        def fake_generate(prompt, filename):
            with open(filename, "wb") as f:
                f.write(b"fake png bytes")
            return filename

        with tempfile.TemporaryDirectory() as d:
            mob = self._mob(d)
            fig = mob_file.Figure(id="f1", name="my figure", description="A figure.")
            fig._mob = mob
            builtins.__import__ = _stub_image_generation_import(fake_generate)
            try:
                result = process.generate_image(mob, fig)
            finally:
                builtins.__import__ = original_import
            self.assertEqual("my-figure.png", result)
            self.assertEqual("my-figure.png", fig.image_file)
            out = Path(d) / "my-figure.png"
            self.assertTrue(out.exists())
            self.assertEqual(b"fake png bytes", out.read_bytes())


class GenerateDescriptionsTest(unittest.TestCase):

    def _mob(self, theme):
        mob = mob_file.Mob(mob_name="m", current_theme=theme)
        return mob

    def test_appends_one_figure_per_nonempty_line(self):
        # generate_prompts returns a list of lines, some of which are blank.
        def fake_prompts(theme):
            self.assertEqual("the theme", theme)
            return ["  A shopkeeper in an apron.",
                    "",
                    "A soldier at ease.",
                    "  "]

        mob = self._mob("the theme")
        builtins.__import__ = _stub_import_with({"generate_prompts": fake_prompts})
        try:
            added = process.generate_descriptions(mob)
        finally:
            builtins.__import__ = original_import

        self.assertEqual(2, added)
        self.assertEqual(2, len(mob.figures))
        self.assertEqual("A shopkeeper in an apron.", mob.figures[0].description)
        self.assertEqual("A soldier at ease.", mob.figures[1].description)
        # derived default name + auto id
        self.assertEqual("a_shopkeeper_in", mob.figures[0].name)
        self.assertTrue(mob.figures[0].id)

    def test_raises_when_no_theme(self):
        mob = self._mob("   ")
        with self.assertRaises(mob_file.MobFileError):
            process.generate_descriptions(mob)

    def test_raises_without_sdk(self):
        mob = self._mob("theme")
        builtins.__import__ = _no_image_generation_import
        try:
            with self.assertRaises(mob_file.MobFileError) as ctx:
                process.generate_descriptions(mob)
        finally:
            builtins.__import__ = original_import
        self.assertIn("description generation unavailable", str(ctx.exception))


class GenerateSTLTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.mob_dir = Path(self._tmp.name)
        self.mob = mob_file.Mob(directory=self.mob_dir,
                                description_path=self.mob_dir / "mob_description.json")
        (self.mob_dir / "ref.png").write_bytes(b"fake image")
        self.fig = mob_file.Figure(id="f1", name="bob", description="a figure",
                                   image_file="ref.png")
        self.fig._mob = self.mob

    def tearDown(self):
        self._tmp.cleanup()

    def test_writes_stl_and_sets_stl_file(self):
        result = process.generate_stl(self.mob, self.fig)
        self.assertEqual("bob.stl", result)
        self.assertEqual("bob.stl", self.fig.stl_file)
        out = self.mob_dir / "bob.stl"
        self.assertTrue(out.exists())
        text = out.read_text()
        self.assertTrue(text.startswith("solid mobmaker_placeholder"))
        self.assertTrue(text.strip().endswith("endsolid mobmaker_placeholder"))
        # A placeholder box has 6 faces = 12 triangles.
        self.assertEqual(12, text.count("facet normal"))

    def test_raises_without_image(self):
        bare = mob_file.Figure(id="f2", name="nobody")
        bare._mob = self.mob
        with self.assertRaises(mob_file.MobFileError) as ctx:
            process.generate_stl(self.mob, bare)
        self.assertIn("no reference image", str(ctx.exception))
        self.assertEqual("", bare.stl_file)  # unchanged on failure

    def test_file_stem_uses_name(self):
        self.assertEqual("bob", process._figure_stem(self.fig))
        empty = mob_file.Figure(id="f3", name=None)
        self.assertEqual("f3", process._figure_stem(empty))  # falls back to id
        noid = mob_file.Figure(id=None, name=None)
        self.assertEqual("figure", process._figure_stem(noid))  # final fallback


if __name__ == "__main__":
    unittest.main()
