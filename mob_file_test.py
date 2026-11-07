#!/usr/bin/env python3
"""Tests for mob_file.py.

Run via ``make test`` (unittest discover) or directly:
    python3 -m unittest mob_file_test
"""

import json
import tempfile
import unittest
from pathlib import Path

import mob_file
from mob_file import Figure, Mob, MobFileError, load_mob, load_mob_text, save_mob

EXAMPLE_MOB = Path(__file__).resolve().parent / "example_mob"

# TODO(bowdidge): Don't duplicate this everywhere.
PROMPT_PREFIX = (
    "Create a reference image with a front, side, and rear image "
    "of a realistic human figure suitable for creating a 3d model "
    "of a person.  Each of the views must be on a blank background.  "
    "Each of the views must have the figure in the same pose."
)


def fig_dict(**kw):
    """A complete figure dict with defaults; override with keyword args."""
    d = {
        "name": "test figure",
        "description": "A test figure description.",
        "interesting": False,
        "not_interesting": False,
        "image_file": "",
        "stl_file": "",
    }
    d.update(kw)
    return d


def mob_dict(figures=(), **kw):
    """A complete top-level mob dict with defaults; override as needed."""
    d = {
        "mob_name": "test mob",
        "current_theme": "test theme",
        "past_themes": [],
        "figures": list(figures),
    }
    d.update(kw)
    return d


class BasicTest(unittest.TestCase):
    def testEmpty(self):
        mob_text = """
        {
        "mob_name": "mall_rats",
        "current_theme": "the human figures should represent teenagerrs in a 1980's mall.  The figures should be realistic, but can be exaggerated stereotypes.",
        "past_themes": [],
        "figures": {
        }
        }
        """
        mob = mob_file.load_mob_text(mob_text)
        self.assertEqual("mall_rats", mob.mob_name)
        self.assertEqual(0, len(mob.figures))

    def testEmptySummaryAndDescribe(self):
        mob = mob_file.load_mob_text(
            json.dumps(mob_dict(mob_name="empty", current_theme="t")))
        s = mob.summary()
        self.assertEqual(0, s["figure_count"])
        self.assertEqual(0, s["interesting_count"])
        self.assertEqual(0, s["with_images"])
        self.assertEqual(0, s["with_stl"])
        self.assertIn("no figures yet", mob.describe())

    def testFigureCounts(self):
        mob = mob_file.load_mob_text(json.dumps(mob_dict(
            figures=[
                fig_dict(name="a", interesting=True,
                         image_file="a.jpg", stl_file="a.stl"),
                fig_dict(name="b", interesting=True, image_file="b.jpg"),
                fig_dict(name="c", not_interesting=True),
                fig_dict(name="d"),
            ])))
        s = mob.summary()
        self.assertEqual(4, s["figure_count"])
        self.assertEqual(2, s["interesting_count"])
        self.assertEqual(1, s["not_interesting_count"])
        self.assertEqual(2, s["with_images"])
        self.assertEqual(1, s["with_stl"])


class FigureTest(unittest.TestCase):
    def testFromDictBasic(self):
        fig = Figure.from_dict(fig_dict(
            name="foreman bob", description="crossed arms",
            interesting=True, image_file="bob.jpg", stl_file="bob.stl"))
        self.assertEqual("foreman bob", fig.name)
        self.assertEqual("crossed arms", fig.description)
        self.assertTrue(fig.interesting)
        self.assertFalse(fig.not_interesting)
        self.assertEqual("bob.jpg", fig.image_file)
        self.assertEqual("bob.stl", fig.stl_file)

    def testSummaryPreserved(self):
        # Regression: an earlier from_dict popped the summary key,
        # so every figure fell back to the derived 60-char summary.
        fig = Figure.from_dict(fig_dict(name="maria picking prunes",
                                        description="x" * 100))
        self.assertEqual("maria picking prunes", fig.name)

    def testSummaryDerivedFromDescription(self):
        long_desc = "word " * 20  # 100 chars, one line
        fig = Figure.from_dict(fig_dict(name=None, description=long_desc))
        self.assertEqual("word_word_word", fig.name)

    def testNoneFilenamesNormalized(self):
        fig = Figure.from_dict(fig_dict(image_file=None, stl_file=None))
        self.assertEqual("", fig.image_file)
        self.assertEqual("", fig.stl_file)

    def testStringTreatedAsDescription(self):
        fig = Figure.from_dict("just a description string")
        self.assertEqual("just a description string", fig.description)
        self.assertEqual("just_a_description", fig.name)

    def testHasImageHasStl(self):
        self.assertFalse(Figure.from_dict(fig_dict()).has_image())
        self.assertTrue(Figure.from_dict(fig_dict(image_file="a.jpg")).has_image())
        self.assertFalse(Figure.from_dict(fig_dict()).has_stl())
        self.assertTrue(Figure.from_dict(fig_dict(stl_file="a.stl")).has_stl())

    def testPromptIsTemplatePlusDescription(self):
        fig = Figure.from_dict(fig_dict(description="a tall man"))
        self.assertEqual(PROMPT_PREFIX + "a tall man", fig.prompt())

    def testPromptFallsBackToSummary(self):
        fig = Figure.from_dict(fig_dict(name="foreman bob", description=""))
        self.assertEqual(PROMPT_PREFIX + "foreman bob", fig.prompt())

    def testImagePathWithoutMobIsNone(self):
        fig = Figure.from_dict(fig_dict(image_file="a.jpg"))
        self.assertIsNone(fig.image_path())
        self.assertIsNone(fig.stl_path())

    def testDescribe(self):
        fig = Figure.from_dict(fig_dict(
            name="maria", interesting=True, image_file="maria.jpg"))
        self.assertEqual("maria - image: maria.jpg - starred", fig.describe())

    def testToDictRoundTrip(self):
        src = fig_dict(name="bob", interesting=True, image_file="b.jpg")
        fig = Figure.from_dict(src)
        again = Figure.from_dict(fig.to_dict())
        self.assertEqual("bob", again.name)
        self.assertTrue(again.interesting)
        self.assertEqual("b.jpg", again.image_file)


class MobTest(unittest.TestCase):
    def testMultipleFiguresFromList(self):
        mob = mob_file.load_mob_text(json.dumps(mob_dict(
            figures=[fig_dict(name="a"), fig_dict(name="b"),
                     fig_dict(name="c")])))
        self.assertEqual(["a", "b", "c"],
                          [f.name for f in mob.figures])
        self.assertEqual("test mob", mob.mob_name)
        self.assertEqual("test theme", mob.current_theme)

    def testFiguresKeyedBySummaryString(self):
        # The legacy object-keyed form: keys carry the summary.
        text = json.dumps({
            "mob_name": "m",
            "current_theme": "t",
            "figures": {
                "foreman bob": {"description": "arms crossed",
                                "interesting": True},
                "maria": {"description": "picking prunes"},
            },
        })
        mob = mob_file.load_mob_text(text)
        self.assertEqual({"foreman bob", "maria"},
                          {f.name for f in mob.figures})
        bob = next(f for f in mob.figures if f.name == "foreman bob")
        self.assertEqual("arms crossed", bob.description)
        self.assertTrue(bob.interesting)

    def testFiguresWrongTypeRaises(self):
        text = json.dumps(
            {"mob_name": "m", "current_theme": "t",
             "figures": "not an array or object"})
        with self.assertRaises(MobFileError):
            mob_file.load_mob_text(text)

    def testDescribe(self):
        mob = mob_file.load_mob_text(json.dumps(mob_dict(
            figures=[fig_dict(name="a", interesting=True,
                              image_file="a.jpg")])))
        self.assertEqual(
            "test mob - 1 figure(s): 1 starred, 0 dismissed, "
            "1 with reference images, 0 with STL models",
            mob.describe())

    def testFiguresBackReferencedByMob(self):
        mob = mob_file.load_mob_text(json.dumps(mob_dict(
            figures=[fig_dict(name="a", image_file="a.jpg")])))
        self.assertIs(mob, mob.figures[0]._mob)


class FileApiTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _write(self, name, data):
        p = self.tmp / name
        if isinstance(data, str):
            p.write_text(data, encoding="utf-8")
        else:
            p.write_text(json.dumps(data, indent=4), encoding="utf-8")
        return p

    def testLoadMobFromDirectory(self):
        self._write("mob_description.json", mob_dict(
            mob_name="from dir",
            figures=[fig_dict(name="a", image_file="a.jpg")]))
        mob = load_mob(self.tmp)
        self.assertEqual("from dir", mob.mob_name)
        self.assertEqual(self.tmp, mob.directory)
        self.assertEqual(self.tmp / "mob_description.json",
                         mob.description_path)
        self.assertEqual((self.tmp / "a.jpg").as_posix(),
                         mob.figures[0].image_path().as_posix())

    def testLoadMobFromJsonFile(self):
        p = self._write("mob_description.json",
                        mob_dict(mob_name="from file"))
        mob = load_mob(p)
        self.assertEqual("from file", mob.mob_name)
        self.assertEqual(p.parent, mob.directory)
        self.assertEqual(p, mob.description_path)

    def testLoadMobMissingPath(self):
        with self.assertRaises(FileNotFoundError):
            load_mob(self.tmp / "nope")

    def testLoadMobDirectoryWithoutJson(self):
        (self.tmp / "other.txt").write_text("hi")
        with self.assertRaises(MobFileError):
            load_mob(self.tmp)

    def testLoadMobInvalidJsonFile(self):
        self._write("mob_description.json", '{"mob_name": "x",')
        with self.assertRaises(MobFileError):
            load_mob(self.tmp)

    def testLoadMobTextInvalid(self):
        with self.assertRaises(MobFileError):
            load_mob_text('{"mob_name": "x"')

    def testLoadMobTextNonObject(self):
        with self.assertRaises(MobFileError):
            load_mob_text("[1, 2, 3]")

    def testSaveMobRoundTrip(self):
        self._write("mob_description.json", mob_dict(
            mob_name="rt", current_theme="t",
            figures=[fig_dict(name="a", interesting=True),
                     fig_dict(name="b", stl_file="b.stl")]))
        mob = load_mob(self.tmp / "mob_description.json")
        out = save_mob(mob, self.tmp / "resaved.json")
        mob2 = load_mob(out)
        self.assertEqual("rt", mob2.mob_name)
        self.assertEqual(["a", "b"], [f.name for f in mob2.figures])
        self.assertTrue(mob2.figures[0].interesting)
        self.assertEqual("b.stl", mob2.figures[1].stl_file)
        self.assertEqual(
            json.loads(out.read_text()), mob2.to_dict())

    def testSaveMobToDirectory(self):
        mob = mob_file.load_mob_text(json.dumps(mob_dict(mob_name="dirsave")))
        new_dir = self.tmp / "new_mob_dir"
        new_dir.mkdir()
        out = save_mob(mob, new_dir)
        self.assertEqual(new_dir / "mob_description.json", out)
        self.assertTrue(out.exists())
        self.assertEqual("dirsave", load_mob(new_dir).mob_name)

    def testSaveMobWithoutLocation(self):
        mob = mob_file.load_mob_text(json.dumps(mob_dict(mob_name="nowhere")))
        self.assertIsNone(mob.directory)
        self.assertIsNone(mob.description_path)
        with self.assertRaises(MobFileError):
            save_mob(mob)


class ExampleMobTest(unittest.TestCase):
    """Integration test against the checked-in example_mob directory."""

    def testExampleMobLoads(self):
        mob = load_mob(EXAMPLE_MOB)
        self.assertEqual("1930s cannery figures", mob.mob_name)
        self.assertEqual(2, len(mob.figures))
        maria, bob = mob.figures
        self.assertEqual("maria picking prunes", maria.name)
        self.assertTrue(maria.interesting)
        self.assertFalse(maria.has_image())
        self.assertEqual("foreman bob", bob.name)
        self.assertEqual("foreman_bob_reference.jpg", bob.image_file)
        self.assertTrue(bob.has_image())
        self.assertFalse(bob.has_stl())
        self.assertEqual(PROMPT_PREFIX + bob.description, bob.prompt())


if __name__ == "__main__":
    unittest.main()
