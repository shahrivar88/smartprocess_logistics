"""Serial normalization: digits, Unicode, case, accents, invisible characters and boundaries.

Rule set 2 refuses control and invisible characters instead of deleting them, and drops
combining marks so the identity matches the database collation. The expectations here are
lower case because `casefold` is used, not `upper`; the stored identity is hidden from
operators, so its case never reaches a screen.
"""

from dataclasses import FrozenInstanceError

from smartprocess_logistics.core.normalization import (
	CODE_EMPTY,
	CODE_HAS_CONTROL,
	CODE_NOT_TEXT,
	CODE_TOO_LONG,
	DEFAULT_MAX_LENGTH,
	MAX_RAW_CHARS,
	NORMALIZATION_VERSION,
	NormalizationPolicy,
	SerialTextError,
	find_forbidden_character,
	is_outdated,
	normalize_serial,
	renaming_risk,
)
from smartprocess_logistics.tests.base import UnitTestCase


class TestDisplayAndIdentity(UnitTestCase):
	def test_original_text_is_preserved_for_display(self):
		serial = normalize_serial("  ab-0012  ")

		self.assertEqual(serial.original, "  ab-0012  ")
		self.assertEqual(serial.normalized, "ab-0012")

	def test_leading_zeros_are_kept(self):
		self.assertEqual(normalize_serial("000123").normalized, "000123")

	def test_version_is_recorded(self):
		serial = normalize_serial("A1")

		self.assertEqual(serial.version, NORMALIZATION_VERSION)
		self.assertFalse(is_outdated(serial))
		self.assertTrue(is_outdated(serial, NormalizationPolicy(version=NORMALIZATION_VERSION + 1)))

	def test_result_is_frozen(self):
		serial = normalize_serial("A1")

		with self.assertRaises(FrozenInstanceError):
			serial.normalized = "OTHER"

	def test_normalization_is_deterministic(self):
		raw = "  12Ab-12  "

		self.assertEqual(normalize_serial(raw).normalized, "12ab-12")
		self.assertEqual(normalize_serial(raw).normalized, normalize_serial(raw).normalized)


class TestDigits(UnitTestCase):
	def test_persian_digits_fold_to_ascii(self):
		self.assertEqual(normalize_serial("۱۲۳۴۵").normalized, "12345")

	def test_arabic_indic_digits_fold_to_ascii(self):
		self.assertEqual(normalize_serial("١٢٣").normalized, "123")

	def test_mixed_digit_scripts_become_one_identity(self):
		self.assertEqual(normalize_serial("AB-۱۲۳").normalized, normalize_serial("AB-123").normalized)


class TestUnicodeFolding(UnitTestCase):
	def test_full_width_forms_fold(self):
		self.assertEqual(normalize_serial("ＡＢ１２").normalized, "ab12")

	def test_case_is_ignored(self):
		self.assertEqual(normalize_serial("aB-12").normalized, normalize_serial("Ab-12").normalized)

	def test_accents_fold_away_because_the_database_ignores_them(self):
		# `unicode_ci` compares without regard to accents, so the app has to as well: otherwise
		# the app would accept two serials the unique index then refuses.
		for accented, plain in (("é", "e"), ("è", "e"), ("ǻ", "a"), ("ñ", "n")):
			with self.subTest(accented=accented):
				self.assertEqual(normalize_serial(accented).normalized, normalize_serial(plain).normalized)

	def test_a_dotted_capital_i_folds_like_a_plain_i(self):
		self.assertEqual(normalize_serial("İ").normalized, normalize_serial("i").normalized)

	def test_compatibility_forms_fold(self):
		for source, target in (("Ⅳ", "IV"), ("①", "1"), ("ﬁ", "fi"), ("０", "0"), ("ﻻ", "لا")):
			with self.subTest(source=source):
				self.assertEqual(normalize_serial(source).normalized, normalize_serial(target).normalized)

	def test_spacing_combining_marks_are_kept(self):
		# The collation keeps them too, so dropping them here would make the app coarser than
		# the database and merge two serials the database calls distinct.
		self.assertNotEqual(normalize_serial("क").normalized, normalize_serial("कि").normalized)

	def test_final_sigma_folds_to_sigma(self):
		self.assertEqual(normalize_serial("ς").normalized, normalize_serial("σ").normalized)

	def test_arabic_letter_shapes_fold_to_persian(self):
		self.assertEqual(normalize_serial("سريال").normalized, normalize_serial("سریال").normalized)

	def test_arabic_letter_folding_can_be_switched_off(self):
		policy = NormalizationPolicy(fold_arabic_letters=False)

		self.assertNotEqual(
			normalize_serial("سريال", policy=policy).normalized,
			normalize_serial("سریال", policy=policy).normalized,
		)


class TestInvisibleCharactersAreRefused(UnitTestCase):
	def test_invisible_characters_are_refused_not_deleted(self):
		# Rule set 1 deleted these, which let a separator glue two fragments into one serial
		# that looked exactly like a serial somebody had typed. Refusing is the only way the
		# operator finds out.
		for invisible in ("​", "‌", "‍", "‎", "﻿", "­"):
			with self.subTest(invisible=hex(ord(invisible))):
				with self.assertRaises(SerialTextError) as caught:
					normalize_serial(f"AB{invisible}12")

				self.assertEqual(caught.exception.code, CODE_HAS_CONTROL)

	def test_control_characters_are_refused_not_deleted(self):
		for control in ("\x00", "\t", "\n", "\r", "\x1f", "\x7f"):
			with self.subTest(control=hex(ord(control))):
				with self.assertRaises(SerialTextError) as caught:
					normalize_serial(f"AB{control}12")

				self.assertEqual(caught.exception.code, CODE_HAS_CONTROL)

	def test_the_refusal_names_the_character(self):
		with self.assertRaises(SerialTextError) as caught:
			normalize_serial("AB\t12")

		self.assertIn("U+0009", caught.exception.message)

	def test_the_check_runs_on_the_text_as_typed(self):
		self.assertEqual(find_forbidden_character("A\tB"), "\t")
		self.assertEqual(find_forbidden_character("AB"), None)

	def test_only_leading_and_trailing_spaces_are_trimmed(self):
		self.assertEqual(normalize_serial("  AB12  ").normalized, "ab12")

	def test_a_non_breaking_space_is_trimmed(self):
		self.assertEqual(normalize_serial(" AB12 ").normalized, "ab12")

	def test_an_internal_space_is_kept(self):
		self.assertEqual(normalize_serial("AB 12").normalized, "ab 12")

	def test_punctuation_is_not_removed(self):
		self.assertEqual(normalize_serial("AB-12").normalized, "ab-12")


class TestEmptyAndInvalid(UnitTestCase):
	def test_whitespace_only_is_empty(self):
		with self.assertRaises(SerialTextError) as caught:
			normalize_serial("   ")

		self.assertEqual(caught.exception.code, CODE_EMPTY)

	def test_empty_string_is_empty(self):
		with self.assertRaises(SerialTextError) as caught:
			normalize_serial("")

		self.assertEqual(caught.exception.code, CODE_EMPTY)

	def test_non_text_is_refused(self):
		for value in (None, 12, 12.5, b"AB12", ["AB12"], True):
			with self.subTest(value=value):
				with self.assertRaises(SerialTextError) as caught:
					normalize_serial(value)

				self.assertEqual(caught.exception.code, CODE_NOT_TEXT)

	def test_length_boundary_is_inclusive(self):
		at_limit = "A" * DEFAULT_MAX_LENGTH

		self.assertEqual(normalize_serial(at_limit).normalized, at_limit.lower())

		with self.assertRaises(SerialTextError) as caught:
			normalize_serial("A" * (DEFAULT_MAX_LENGTH + 1))

		self.assertEqual(caught.exception.code, CODE_TOO_LONG)

	def test_policy_can_lower_the_length_limit(self):
		with self.assertRaises(SerialTextError):
			normalize_serial("ABCDE", policy=NormalizationPolicy(max_length=4))

	def test_absolute_ceiling_applies_even_with_a_large_policy(self):
		policy = NormalizationPolicy(max_length=MAX_RAW_CHARS + 100)

		with self.assertRaises(SerialTextError) as caught:
			normalize_serial("A" * (MAX_RAW_CHARS + 1), policy=policy)

		self.assertEqual(caught.exception.code, CODE_TOO_LONG)


class TestRenamingRisk(UnitTestCase):
	def test_a_value_already_in_rule_set_2_is_unchanged(self):
		self.assertFalse(renaming_risk(normalize_serial("ab-12").normalized))

	def test_a_value_written_by_rule_set_1_is_unchanged_too(self):
		# Rule set 1 had already removed invisible characters and accents, so re-running the
		# stricter rule set over the stored value leaves it alone. That is why the migration
		# from version 1 to version 2 cannot introduce a collision.
		for stored in ("ab12", "e", "iv", "لا", "क"):
			with self.subTest(stored=stored):
				self.assertFalse(renaming_risk(stored))
