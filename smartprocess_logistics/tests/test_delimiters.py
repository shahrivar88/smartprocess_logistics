"""Delimiter interpretation, validation, ordering and splitting."""

from smartprocess_logistics.tests.base import UnitTestCase

from smartprocess_logistics.core.delimiters import (
	CODE_ALPHANUMERIC,
	CODE_EMPTY,
	CODE_NOT_TEXT,
	CODE_TEXT_NOT_TEXT,
	CODE_TEXT_TOO_LONG,
	CODE_TOO_LONG,
	CODE_UNPRINTABLE,
	MAX_DELIMITER_CHARS,
	MAX_TEXT_CHARS,
	DelimiterError,
	DelimiterSet,
	find_duplicates,
	find_overlaps,
	interpret_delimiter,
	order_delimiters,
	split_tokens,
	validate_delimiter,
)


class TestInterpretDelimiter(UnitTestCase):
	def test_carriage_return_and_newline(self):
		self.assertEqual(interpret_delimiter("\\r\\n"), "\r\n")

	def test_lone_newline(self):
		self.assertEqual(interpret_delimiter("\\n"), "\n")

	def test_tab(self):
		self.assertEqual(interpret_delimiter("\\t"), "\t")

	def test_plain_characters_pass_through(self):
		for stored in (",", "،", ";", "#", "|"):
			with self.subTest(stored=stored):
				self.assertEqual(interpret_delimiter(stored), stored)

	def test_double_backslash_is_one_literal_backslash(self):
		self.assertEqual(interpret_delimiter("\\\\"), "\\")

	def test_backslash_followed_by_backslash_then_n_is_literal(self):
		self.assertEqual(interpret_delimiter("\\\\n"), "\\n")

	def test_trailing_lone_backslash_stays_literal(self):
		self.assertEqual(interpret_delimiter("\\"), "\\")

	def test_unknown_escape_is_left_alone(self):
		self.assertEqual(interpret_delimiter("\\x"), "\\x")

	def test_non_text_is_refused(self):
		with self.assertRaises(DelimiterError) as caught:
			interpret_delimiter(None)

		self.assertEqual(caught.exception.code, CODE_NOT_TEXT)

	def test_interpretation_is_deterministic(self):
		self.assertEqual(interpret_delimiter("\\r\\n\\t"), interpret_delimiter("\\r\\n\\t"))


class TestValidateDelimiter(UnitTestCase):
	def test_a_space_is_a_valid_delimiter(self):
		self.assertEqual(validate_delimiter(" "), " ")

	def test_empty_is_refused(self):
		with self.assertRaises(DelimiterError) as caught:
			validate_delimiter("")

		self.assertEqual(caught.exception.code, CODE_EMPTY)

	def test_no_escape_sequence_can_interpret_to_nothing(self):
		# Every stored character contributes at least itself, so the guard against an empty
		# interpretation is unreachable from non-empty input. This pins that property, because
		# a future escape that decoded to nothing would silently create an empty delimiter.
		for stored in ("\\\\", "\\\\n", "\\r", "\\t", "\\", "#", " "):
			with self.subTest(stored=stored):
				self.assertTrue(interpret_delimiter(stored))

	def test_empty_stored_text_interprets_to_empty(self):
		self.assertEqual(interpret_delimiter(""), "")

	def test_letters_and_digits_are_refused(self):
		for stored in ("A", "z", "1", "اب"):
			with self.subTest(stored=stored):
				with self.assertRaises(DelimiterError) as caught:
					validate_delimiter(stored)

				self.assertEqual(caught.exception.code, CODE_ALPHANUMERIC)

	def test_unprintable_control_characters_are_refused(self):
		with self.assertRaises(DelimiterError) as caught:
			validate_delimiter("\x01")

		self.assertEqual(caught.exception.code, CODE_UNPRINTABLE)

	def test_tab_and_newline_are_allowed_despite_being_unprintable(self):
		self.assertEqual(validate_delimiter("\\t"), "\t")
		self.assertEqual(validate_delimiter("\\n"), "\n")

	def test_too_long_is_refused(self):
		with self.assertRaises(DelimiterError) as caught:
			validate_delimiter("#" * (MAX_DELIMITER_CHARS + 1))

		self.assertEqual(caught.exception.code, CODE_TOO_LONG)

	def test_non_text_is_refused(self):
		with self.assertRaises(DelimiterError) as caught:
			validate_delimiter(5)

		self.assertEqual(caught.exception.code, CODE_NOT_TEXT)


class TestOrdering(UnitTestCase):
	def test_longest_first(self):
		self.assertEqual(order_delimiters({";;", ";"}), (";;", ";"))

	def test_equal_lengths_are_ordered_by_text(self):
		self.assertEqual(order_delimiters({";", ","}), (",", ";"))

	def test_ordering_does_not_depend_on_input_order(self):
		first = order_delimiters({";", ",", ";;", "  "})
		second = order_delimiters({"  ", ";;", ",", ";"})

		self.assertEqual(first, second)


class TestOverlapReporting(UnitTestCase):
	def test_no_overlap_between_unrelated_delimiters(self):
		self.assertEqual(find_overlaps({",", ";"}), ())

	def test_contains_is_reported_longest_first(self):
		self.assertEqual(find_overlaps({",", ",,"}), ((",,", ","),))

	def test_containment_is_reported_in_either_configured_order(self):
		self.assertEqual(find_overlaps({",,", ","}), find_overlaps({",", ",,"}))

	def test_duplicates_are_reported_once(self):
		self.assertEqual(find_duplicates([",", ",", ";"]), (",",))

	def test_no_duplicates(self):
		self.assertEqual(find_duplicates([",", ";"]), ())


class TestSplitTokens(UnitTestCase):
	def test_single_delimiter(self):
		self.assertEqual(split_tokens("1,2,3", DelimiterSet.of([","])), ["1", "2", "3"])

	def test_two_delimiters_used_together(self):
		tokens = split_tokens("1,2;3", DelimiterSet.of([",", ";"]))

		self.assertEqual(tokens, ["1", "2", "3"])

	def test_escaped_newline_delimiter(self):
		self.assertEqual(split_tokens("A\r\nB", DelimiterSet.of(["\\r\\n"])), ["A", "B"])

	def test_longest_match_wins(self):
		token_set = DelimiterSet.of([",", ",,"])

		self.assertEqual(split_tokens("a,,b", token_set), ["a", "b"])
		self.assertEqual(split_tokens("a,b", token_set), ["a", "b"])

	def test_consecutive_delimiters_leave_an_empty_token(self):
		self.assertEqual(split_tokens("a,,b", DelimiterSet.of([","])), ["a", "", "b"])

	def test_leading_and_trailing_delimiters_leave_empty_tokens(self):
		self.assertEqual(split_tokens(",a,", DelimiterSet.of([","])), ["", "a", ""])

	def test_text_without_a_delimiter_is_one_token(self):
		self.assertEqual(split_tokens("A1", DelimiterSet.of([","])), ["A1"])

	def test_empty_text_is_one_empty_token(self):
		self.assertEqual(split_tokens("", DelimiterSet.of([","])), [""])

	def test_result_does_not_depend_on_configuration_order(self):
		first = split_tokens("1,2;3", DelimiterSet.of([";", ","]))
		second = split_tokens("1,2;3", DelimiterSet.of([",", ";"]))

		self.assertEqual(first, second)

	def test_multi_character_delimiter(self):
		self.assertEqual(split_tokens("A@@B", DelimiterSet.of(["@@"])), ["A", "B"])

	def test_non_text_is_refused(self):
		with self.assertRaises(DelimiterError) as caught:
			split_tokens(None, DelimiterSet.of([","]))

		self.assertEqual(caught.exception.code, CODE_TEXT_NOT_TEXT)

	def test_oversized_text_is_refused(self):
		with self.assertRaises(DelimiterError) as caught:
			split_tokens("A" * (MAX_TEXT_CHARS + 1), DelimiterSet.of([","]))

		self.assertEqual(caught.exception.code, CODE_TEXT_TOO_LONG)


class TestDelimiterSet(UnitTestCase):
	def test_builds_from_stored_text(self):
		delimiter_set = DelimiterSet.of(["\\t", ","])

		self.assertIn("\t", delimiter_set)
		self.assertIn(",", delimiter_set)
		self.assertEqual(len(delimiter_set), 2)

	def test_duplicates_collapse(self):
		self.assertEqual(len(DelimiterSet.of([",", ",", ",,"])), 2)

	def test_an_unusable_delimiter_stops_construction(self):
		with self.assertRaises(DelimiterError):
			DelimiterSet.of(["A"])

	def test_empty_set_never_matches(self):
		self.assertEqual(split_tokens("A,B", DelimiterSet.of([])), ["A,B"])

	def test_falsy_when_empty(self):
		self.assertFalse(DelimiterSet.of([]))
		self.assertTrue(DelimiterSet.of([","]))

	def test_as_list_is_ordered(self):
		self.assertEqual(DelimiterSet.of([";", ",,"]).as_list(), [",,", ";"])
