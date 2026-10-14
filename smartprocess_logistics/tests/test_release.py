"""The two places a version is written down, and the one that is read at run time.

`flit` takes the version from the package, and every export this app writes stamps it into the
file header — so `smartprocess_logistics.__version__` is the version users see. The changelog is
where a release is *described*, and it is the half a bump forgets: cutting 0.4.0 means editing
both, by hand, in the same commit, and nothing else notices if one of them is missed.

The failure this guards against is quiet rather than loud. A package whose `__version__` says
0.4.0 while the changelog's newest section says 0.3.0 is a release whose exports claim a version
the project has not announced; a package whose changelog has a section the code never reached is a
release that was written down and never shipped. Both are invisible until somebody reads the two
side by side, which is exactly what this does.

A module of its own rather than a class in `tests/test_desk_artifacts.py`: that one is about the
files the Desk loads, and this is about the two files that describe the release.
"""

import pathlib
import re

import smartprocess_logistics
from smartprocess_logistics.tests.base import UnitTestCase

APP = pathlib.Path(__file__).resolve().parent.parent
CHANGELOG = APP.parent / "CHANGELOG.md"
PYPROJECT = APP.parent / "pyproject.toml"


class TheVersionIsWrittenOnceTest(UnitTestCase):
	def headings(self):
		"""Every released version the changelog names, newest first, as it is written."""
		return re.findall(r"^## (\d+\.\d+\.\d+) — ", CHANGELOG.read_text(encoding="utf-8"), re.M)

	def test_the_changelog_names_the_version_the_package_ships(self):
		headings = self.headings()

		self.assertTrue(headings, "the changelog names no released version")
		self.assertIn(
			smartprocess_logistics.__version__,
			headings,
			f"{smartprocess_logistics.__version__} is not a section in the changelog",
		)

	def test_the_newest_section_is_the_version_the_package_ships(self):
		"""Newest first, so the *first* heading is the release this tree is: a section above it
		would be a version that was written down before it existed."""
		self.assertEqual(self.headings()[0], smartprocess_logistics.__version__)

	def test_the_package_is_where_the_version_comes_from(self):
		"""One source, declared: a literal in `pyproject.toml` would be a second one to forget."""
		project = PYPROJECT.read_text(encoding="utf-8")

		self.assertIn('dynamic = ["version"]', project)
		self.assertNotRegex(project, r"(?m)^version\s*=")

	def test_every_released_version_is_a_section_and_not_a_mention(self):
		"""The list this reads is the changelog's own index of releases, and it is ordered."""
		headings = self.headings()
		ordered = sorted(headings, key=lambda text: [int(part) for part in text.split(".")])

		self.assertEqual(headings, list(reversed(ordered)), "the changelog is not newest first")
		self.assertGreaterEqual(len(headings), 2, "a released version and the candidate")
