"""Delimiter interpretation, validation, ordering and splitting."""

from smartprocess_logistics.core.defaults import DEFAULT_DELIMITERS, DEFAULT_ITEM_MARKERS
from smartprocess_logistics.core.delimiters import (
	CODE_ALPHANUMERIC,
	CODE_EMPTY,
	CODE_NOT_TEXT,
	CODE_TEXT_NOT_TEXT,
	CODE_TEXT_TOO_LONG,
	CODE_TOO_LONG,
	CODE_UNPRINTABLE,
	ESCAPES,
	MAX_DELIMITER_CHARS,
	MAX_TEXT_CHARS,
	DelimiterError,
	DelimiterSet,
	find_duplicates,
	find_overlaps,
	interpret_delimiter,
	order_delimiters,
	split_tokens,
	split_typed_list,
	validate_delimiter,
)
from smartprocess_logistics.tests.base import UnitTestCase
from smartprocess_logistics.tests.schema_checks import APP_ROOT


class TestInterpretDelimiter(UnitTestCase):
	def test_carriage_return_and_newline(self):
		self.assertEqual(interpret_delimiter("\\r\\n"), "\r\n")

	def test_lone_newline(self):
		self.assertEqual(interpret_delimiter("\\n"), "\n")

	def test_tab(self):
		self.assertEqual(interpret_delimiter("\\t"), "\t")

	def test_space_token_decodes_to_one_space(self):
		"""`\\s` is the storable spelling of a space: one ASCII space, nothing else."""
		self.assertEqual(interpret_delimiter("\\s"), " ")
		self.assertEqual(len(interpret_delimiter("\\s")), 1)
		self.assertEqual(ord(interpret_delimiter("\\s")), 0x20)

	def test_the_original_escapes_are_unchanged(self):
		"""Adding `\\s` must not disturb the four mappings that were already there."""
		self.assertEqual(
			ESCAPES,
			(("\\\\", "\\"), ("\\r", "\r"), ("\\n", "\n"), ("\\t", "\t"), ("\\s", " ")),
		)

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

	def test_a_question_mark_in_a_delimiter_is_a_question_mark(self):
		"""A delimiter is literal text, and that includes the characters a pattern would give meaning.

		A site was found carrying `\\r?\\n` — the notation of a regular expression, "a CR if there is
		one, then an LF" — in its settings. Nothing here reads it that way and nothing should: this
		function has no quantifiers to give a `?` or a `*`, so the entry is the three characters CR,
		`?`, LF and matches no text any serial can hold. The pinning matters more than the finding:
		a delimiter is configuration, and configuration interpolated into a pattern is a
		regexp-injection hole with a mundane name.
		"""
		self.assertEqual(interpret_delimiter("\\r?\\n"), "\r?\n")
		self.assertEqual(len(interpret_delimiter("\\r?\\n")), 3)
		self.assertEqual(interpret_delimiter("*"), "*")
		self.assertEqual(interpret_delimiter(".+"), ".+")

	def test_the_odd_entry_cannot_stand_in_for_a_real_line_break(self):
		"""`\\r?\\n` and `\\r\\n` are different strings, so the real one still ends the line."""
		delimiters = DelimiterSet.of(("\\r?\\n", "\\r\\n"))

		self.assertEqual(delimiters.as_list(), ["\r?\n", "\r\n"])
		self.assertEqual(split_tokens("A\r\nB", delimiters), ["A", "B"])
		# The odd entry matches its own three characters and nothing else: this text is the only
		# shape it can split, and it is a shape no spreadsheet, file or scanner produces.
		self.assertEqual(split_tokens("A\r?\nB", delimiters), ["A", "B"])

	def test_non_text_is_refused(self):
		with self.assertRaises(DelimiterError) as caught:
			interpret_delimiter(None)

		self.assertEqual(caught.exception.code, CODE_NOT_TEXT)

	def test_interpretation_is_deterministic(self):
		self.assertEqual(interpret_delimiter("\\r\\n\\t"), interpret_delimiter("\\r\\n\\t"))


class TestValidateDelimiter(UnitTestCase):
	def test_a_space_is_a_valid_delimiter(self):
		self.assertEqual(validate_delimiter(" "), " ")

	def test_the_space_token_is_a_valid_delimiter(self):
		"""A required field cannot hold `" "`, so `\\s` is how a space is configured."""
		self.assertEqual(validate_delimiter("\\s"), " ")
		self.assertIn(" ", DelimiterSet.of(["\\s"]))

	def test_a_literal_space_is_still_accepted(self):
		"""Compatibility: a delimiter already configured as a bare space keeps working."""
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


class TestSplitTypedList(UnitTestCase):
	"""The one rule a typed list has that a file does not.

	The shipped configuration does not make a comma a delimiter, so a file line `A,B` holds one
	serial named `A,B`. Somebody typing the same characters into the dialog's box means two, and
	the placeholder says so. That difference used to live in the dialog's JavaScript, where no
	server test could reach it, and where it disagreed with the file path in the other direction:
	it split on a comma whatever the configuration said. It now lives in `core`, next to the
	splitting rule it extends.
	"""

	STORED = ("\\r\\n", "\\n", "\\t")

	def test_a_typed_comma_separates(self):
		self.assertEqual(split_typed_list("A1, A2", self.STORED), ("A1", "A2"))

	def test_a_semicolon_and_an_arabic_comma_separate(self):
		self.assertEqual(split_typed_list("A1;A2،A3", self.STORED), ("A1", "A2", "A3"))

	def test_a_newline_and_a_tab_separate_even_when_not_configured(self):
		self.assertEqual(split_typed_list("A1\nA2\tA3", ()), ("A1", "A2", "A3"))

	def test_a_configured_delimiter_separates_too(self):
		self.assertEqual(split_typed_list("A1|A2", ("|",)), ("A1", "A2"))

	def test_the_same_text_is_one_token_for_a_file(self):
		# The difference, pinned: `split_tokens` is the file path and knows only the configuration.
		delimiter_set = DelimiterSet.of(self.STORED)

		self.assertEqual(split_tokens("A1,A2", delimiter_set), ["A1,A2"])
		self.assertEqual(split_typed_list("A1,A2", self.STORED), ("A1", "A2"))

	def test_blank_tokens_are_dropped(self):
		self.assertEqual(split_typed_list("A1,,A2;;;A3", self.STORED), ("A1", "A2", "A3"))

	def test_blank_input_is_no_serials(self):
		self.assertEqual(split_typed_list("   ", self.STORED), ())

	def test_a_broken_stored_delimiter_is_still_refused(self):
		# The configured values go through the same validation as everywhere else, so a
		# configuration an administrator broke is refused rather than quietly ignored.
		for broken in ("", "ab"):
			with self.subTest(stored=broken), self.assertRaises(DelimiterError):
				split_typed_list("A1,A2", (broken,))


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


class TestTheShippedDefaults(UnitTestCase):
	"""The values a fresh install writes into two required fields.

	This is the check whose absence stopped the installation on SmartProcessERP. `delimiter` and
	`pattern` are both `reqd`, and Frappe's mandatory validation asks `strip_html(value).strip()`
	for truth (`model/base_document.py`), so a whitespace-only value is reported as missing and
	`save()` refuses the whole document. The shipped defaults contained `("Space", " ")`, which is
	exactly that, and `after_install` died on it.

	The rule lives in the framework, so a database-free suite cannot exercise it directly. What it
	can do is demand the property the rule demands of the values this app ships.
	"""

	def test_no_shipped_delimiter_is_blank(self):
		for label, stored in DEFAULT_DELIMITERS:
			with self.subTest(label=label):
				self.assertTrue(stored.strip(), f"{label!r} would be refused as a blank value")

	def test_no_shipped_marker_pattern_is_blank(self):
		for label, stored in DEFAULT_ITEM_MARKERS:
			with self.subTest(label=label):
				self.assertTrue(stored.strip(), f"{label!r} would be refused as a blank value")

	def test_every_shipped_delimiter_validates(self):
		for label, stored in DEFAULT_DELIMITERS:
			with self.subTest(label=label):
				self.assertTrue(validate_delimiter(stored))

	def test_every_shipped_pattern_is_text(self):
		for label, stored in DEFAULT_ITEM_MARKERS:
			with self.subTest(label=label):
				self.assertIsInstance(stored, str)

	def test_the_space_delimiter_is_the_escape_token(self):
		"""A literal space cannot be stored in a required field, so it is written `\\s`."""
		space = [stored for label, stored in DEFAULT_DELIMITERS if label == "Space"]

		self.assertEqual(space, ["\\s"])
		self.assertEqual(validate_delimiter(space[0]), " ")
		self.assertNotIn(" ", space)

	def test_the_shipped_delimiters_include_a_space(self):
		"""The feature the token exists for, asserted rather than assumed."""
		self.assertIn(" ", DelimiterSet.of(self.stored_delimiters()))

	def test_no_two_shipped_delimiters_decode_to_the_same_thing(self):
		interpreted = [interpret_delimiter(stored) for stored in self.stored_delimiters()]

		self.assertEqual(find_duplicates(interpreted), ())

	def test_a_space_delimiter_splits_text(self):
		"""The expression `importer.preview_from_text` uses, with a space instead of a comma."""
		delimiters = DelimiterSet.of(["\\s"])

		self.assertEqual(split_tokens("A1 A2 A3", delimiters), ["A1", "A2", "A3"])

	def test_consecutive_spaces_leave_an_empty_token(self):
		delimiters = DelimiterSet.of(["\\s"])

		self.assertEqual(split_tokens("A1  A2", delimiters), ["A1", "", "A2"])

	def stored_delimiters(self):
		return [stored for _, stored in DEFAULT_DELIMITERS]


class TestTheEndpointServesThem(UnitTestCase):
	"""The wiring: two values in, one rule applied, nothing decided here.

	A decision taken in an endpoint is a decision no database-free test can reach, which is the
	split this app keeps everywhere else. The rule lives in `core.delimiters`; the endpoint is the
	line that hands it this site's configuration.
	"""

	def endpoint_source(self):

		return (APP_ROOT / "smartprocess_logistics" / "api" / "endpoints.py").read_text(encoding="utf-8")

	def test_the_endpoint_applies_the_rule_and_does_not_reimplement_it(self):
		source = self.endpoint_source()
		start = source.index("def _intake_rules(gateway):")
		body = source[start : source.index("\ndef ", start + 1)]

		self.assertIn("served_delimiters(", body)
		self.assertNotIn("TYPED_SEPARATORS", body)
		self.assertNotIn("except", body)
		self.assertNotIn("DelimiterError", body)

	def test_the_window_is_told_the_length_this_site_allows(self):
		source = self.endpoint_source()
		start = source.index("def _intake_rules(gateway):")
		body = source[start : source.index("\ndef ", start + 1)]

		self.assertIn("serial_max_length", body)
		self.assertIn("DEFAULT_MAX_LENGTH", body)

	def test_the_payload_carries_them_with_the_invoice(self):
		"""One call, one authorization: the window already asks for this invoice."""
		source = self.endpoint_source()

		self.assertIn('"intake": _intake_rules(gateway),', source)
