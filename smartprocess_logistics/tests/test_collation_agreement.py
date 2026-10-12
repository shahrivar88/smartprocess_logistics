"""The identity rule must be at least as coarse as the database collation.

The unique constraints are enforced by MariaDB with `utf8mb4_unicode_ci`, not by this app. If
the app ever called two serials *different* when the database calls them *same*, the app would
accept both and the database would then reject the second with a duplicate-key error the
operator cannot explain. That direction is the one that must never happen, and this module pins
it.

Tested equivalence, and guaranteed equivalence
----------------------------------------------
These are not the same thing, and the difference matters:

- **Tested.** The table below was built by running, on the MariaDB build this app targets:

	SELECT IF(<a> COLLATE utf8mb4_unicode_ci = <b> COLLATE utf8mb4_unicode_ci, 1, 0)

  Each verdict is a measurement, not an assumption. A pair that is not in the table has not
  been measured.
- **Guaranteed.** No finite table can cover Unicode. What is claimed in general is only this:
  the rule is `NFKD`, drop `Mn` and `Me`, `NFC`, fold digits, fold case, which is a
  *construction* chosen to sit at least as coarse as the collation's own construction, and the
  table is the evidence for that claim rather than a proof of it.

The honest statement is therefore: **for the pairs measured, the property holds; for a pair
that was never measured, the property is expected and not established.** Re-measuring on
another site means running the same query, which is why the table is data.

Collisions the rule set creates on purpose
------------------------------------------
A "collision" here means two texts that an operator could type and that the app treats as one
serial. Each one is deliberate, and each one the collation creates too:

	width            ＡＢ１２  = AB12
	case             ab12     = AB12
	digit script     ۱۲۳      = 123
	accents          é, è     = e
	compatibility    ①, Ⅳ, ﬁ  = 1, IV, fi
	trailing space   `AB `    = AB

One collision the app creates and the database does not is `ي` = `ی` and `ك` = `ک`. Being
stricter there can only refuse a serial, never accept one the database would reject.
"""

from smartprocess_logistics.core.normalization import normalize_serial
from smartprocess_logistics.tests.base import UnitTestCase

# (left, right, does MariaDB's utf8mb4_unicode_ci call them equal)
# Measured on the MariaDB build this app targets. See the module docstring.
COLLATION_VERDICTS = (
	("a", "A", True),
	("AB", "ab", True),
	("é", "e", True),
	("é", "è", True),
	("ñ", "n", True),
	("ć", "c", True),
	("ǻ", "a", True),
	("ǻ", "å", True),
	("İ", "i", True),
	("ß", "ss", True),
	("ß", "SS", True),
	("ς", "σ", True),
	("Ⅳ", "IV", True),
	("①", "1", True),
	("ﬁ", "fi", True),
	("０", "0", True),
	("½", "1⁄2", True),
	("ﻻ", "لا", True),
	("۱", "1", True),
	("æ", "Æ", True),
	("AB", "AB ", True),
	("ß", "s", False),
	("Æ", "AE", False),
	("AB", "AC", False),
	("क", "कि", False),
	("ي", "ی", False),
	("AB", "ABC", False),
	("1", "2", False),
)

# The collisions the app creates on purpose. Each pair is one serial to this app.
DELIBERATE_COLLISIONS = (
	("ＡＢ１２", "AB12", "full width and half width"),
	("ab12", "AB12", "case"),
	("۱۲۳", "123", "Persian digits"),
	("١٢٣", "123", "Arabic-Indic digits"),
	("é", "e", "accent"),
	("①", "1", "enclosed digit"),
	("Ⅳ", "IV", "Roman numeral"),
	("ﬁ", "fi", "ligature"),
	("AB ", "AB", "trailing space"),
	("ي", "ی", "Arabic yeh and Persian yeh"),
	("ك", "ک", "Arabic kaf and Persian kaf"),
)

# Collisions the app does **not** create, because the difference is a real one.
DISTINCT_PAIRS = (
	("AB-12", "AB12"),
	("AB 12", "AB12"),
	("Æ", "AE"),
	("क", "कि"),
	("AB", "ABC"),
)


def _app_equal(first, second):
	return normalize_serial(first).normalized == normalize_serial(second).normalized


class TestCollationAgreement(UnitTestCase):
	def test_the_app_never_splits_a_pair_the_database_calls_equal(self):
		unsafe = [
			(first, second)
			for first, second, database_equal in COLLATION_VERDICTS
			if database_equal and not _app_equal(first, second)
		]

		self.assertEqual(unsafe, [], msg=f"app is finer than the collation on: {unsafe}")

	def test_the_app_never_merges_a_pair_the_database_keeps_apart_unless_documented(self):
		documented = {("ي", "ی"), ("ك", "ک")}
		looser = [
			(first, second)
			for first, second, database_equal in COLLATION_VERDICTS
			if not database_equal
			and _app_equal(first, second)
			and (first, second) not in documented
		]

		self.assertEqual(looser, [], msg=f"app is coarser than the collation on: {looser}")

	def test_the_one_deliberate_stricter_case_behaves_as_documented(self):
		self.assertEqual(
			normalize_serial("ي").normalized,
			normalize_serial("ی").normalized,
		)
		self.assertEqual(
			normalize_serial("ك").normalized,
			normalize_serial("ک").normalized,
		)

	def test_the_verdict_table_covers_both_answers(self):
		# A table that only held equal pairs would pass the first test vacuously.
		self.assertEqual({verdict for _, _, verdict in COLLATION_VERDICTS}, {True, False})


class TestDocumentedCollisions(UnitTestCase):
	def test_every_deliberate_collision_collapses(self):
		for first, second, reason in DELIBERATE_COLLISIONS:
			with self.subTest(pair=(first, second), reason=reason):
				self.assertTrue(_app_equal(first, second), reason)

	def test_the_display_text_is_still_different(self):
		# Two texts that collide keep their own spelling; only the identity is shared.
		first = normalize_serial("ＡＢ１２")
		second = normalize_serial("AB12")

		self.assertNotEqual(first.original, second.original)
		self.assertEqual(first.normalized, second.normalized)

	def test_every_pair_the_app_keeps_apart_really_is_apart(self):
		for first, second in DISTINCT_PAIRS:
			with self.subTest(pair=(first, second)):
				self.assertNotEqual(
					normalize_serial(first).normalized,
					normalize_serial(second).normalized,
				)


class TestWhatIsNotEstablished(UnitTestCase):
	def test_the_table_is_evidence_and_not_a_proof(self):
		# This test records the boundary rather than a behaviour: for a pair that is not in the
		# table, the agreement property is expected and has not been measured. It exists so the
		# claim cannot quietly grow from "these pairs" into "all pairs".
		measured = {(first, second) for first, second, _ in COLLATION_VERDICTS}
		unmeasured = ("ǫ", "o")

		self.assertNotIn(unmeasured, measured)
		self.assertTrue(_app_equal(*unmeasured))
