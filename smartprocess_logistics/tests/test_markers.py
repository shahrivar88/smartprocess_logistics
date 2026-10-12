"""Marker pattern validation, whole-token matching and ambiguity detection."""

from smartprocess_logistics.tests.base import UnitTestCase

from smartprocess_logistics.core.markers import (
	CODE_AMBIGUOUS,
	CODE_BRACKETS,
	CODE_EMPTY,
	CODE_INDEX_NOT_POSITIVE,
	CODE_INDEX_TOO_LARGE,
	CODE_NO_LITERAL,
	CODE_NOT_TEXT,
	CODE_PLACEHOLDER_COUNT,
	CODE_TOO_LONG,
	MAX_PATTERN_CHARS,
	MAX_ROW_INDEX,
	MarkerPatternError,
	find_ambiguous_patterns,
	lookup_marker,
	match_token,
	parse_pattern,
	parse_patterns,
)


class TestParsePattern(UnitTestCase):
	def test_the_four_default_shapes_parse(self):
		for pattern in ("{n}*", "*{n}", "{n}#", "#{n}"):
			with self.subTest(pattern=pattern):
				parsed = parse_pattern(pattern)

				self.assertEqual(parsed.pattern, pattern)
				self.assertEqual(parsed.prefix + parsed.suffix, pattern.replace("{n}", ""))

	def test_a_custom_marker_is_accepted(self):
		parsed = parse_pattern("ردیف {n}:")

		self.assertEqual(parsed.prefix, "ردیف ")
		self.assertEqual(parsed.suffix, ":")

	def test_marker_for_a_row(self):
		self.assertEqual(parse_pattern("{n}*").marker_for(3), "3*")
		self.assertEqual(parse_pattern("*{n}").marker_for(12), "*12")
		self.assertEqual(parse_pattern("ردیف {n}:").marker_for(1), "ردیف 1:")

	def test_missing_placeholder_is_refused(self):
		with self.assertRaises(MarkerPatternError) as caught:
			parse_pattern("**")

		self.assertEqual(caught.exception.code, CODE_PLACEHOLDER_COUNT)

	def test_two_placeholders_are_refused(self):
		with self.assertRaises(MarkerPatternError) as caught:
			parse_pattern("{n}*{n}")

		self.assertEqual(caught.exception.code, CODE_PLACEHOLDER_COUNT)

	def test_a_wrong_placeholder_is_refused(self):
		with self.assertRaises(MarkerPatternError) as caught:
			parse_pattern("{m}*")

		self.assertEqual(caught.exception.code, CODE_PLACEHOLDER_COUNT)

	def test_a_bare_placeholder_is_refused(self):
		with self.assertRaises(MarkerPatternError) as caught:
			parse_pattern("{n}")

		self.assertEqual(caught.exception.code, CODE_NO_LITERAL)

	def test_stray_brackets_are_refused(self):
		for pattern in ("{{n}}", "{n}{}"):
			with self.subTest(pattern=pattern):
				with self.assertRaises(MarkerPatternError) as caught:
					parse_pattern(pattern)

				self.assertEqual(caught.exception.code, CODE_BRACKETS)

	def test_empty_and_blank_are_refused(self):
		for pattern in ("", "   ", "\t"):
			with self.subTest(pattern=pattern):
				with self.assertRaises(MarkerPatternError) as caught:
					parse_pattern(pattern)

				self.assertEqual(caught.exception.code, CODE_EMPTY)

	def test_too_long_is_refused(self):
		with self.assertRaises(MarkerPatternError) as caught:
			parse_pattern("#" * MAX_PATTERN_CHARS + "{n}*")

		self.assertEqual(caught.exception.code, CODE_TOO_LONG)

	def test_non_text_is_refused(self):
		with self.assertRaises(MarkerPatternError) as caught:
			parse_pattern(None)

		self.assertEqual(caught.exception.code, CODE_NOT_TEXT)

	def test_row_index_must_be_positive(self):
		for row_index in (0, -1, "2", 2.0, True):
			with self.subTest(row_index=row_index):
				with self.assertRaises(MarkerPatternError):
					parse_pattern("{n}*").marker_for(row_index)

	def test_row_index_has_an_upper_bound(self):
		with self.assertRaises(MarkerPatternError) as caught:
			parse_pattern("{n}*").marker_for(MAX_ROW_INDEX + 1)

		self.assertEqual(caught.exception.code, CODE_INDEX_TOO_LARGE)


class TestMatchToken(UnitTestCase):
	def setUp(self):
		self.star_after = parse_pattern("{n}*")
		self.star_before = parse_pattern("*{n}")

	def test_whole_token_match(self):
		match = match_token("3*", self.star_after)

		self.assertEqual(match.pattern, "{n}*")
		self.assertEqual(match.row_index, 3)
		self.assertIsNone(match.problem)

	def test_a_serial_that_is_not_a_marker(self):
		self.assertIsNone(match_token("AB-12", self.star_after).pattern)

	def test_the_mirror_pattern_does_not_match(self):
		self.assertIsNone(match_token("3*", self.star_before).pattern)
		self.assertIsNone(match_token("*3", self.star_after).pattern)

	def test_the_whole_token_must_match(self):
		# `3**` is the marker of `{n}**`, never of `{n}*`, because the digits part must be
		# digits. Without this rule the two shapes would be ambiguous.
		self.assertIsNone(match_token("3**", self.star_after).pattern)
		self.assertEqual(match_token("3**", parse_pattern("{n}**")).row_index, 3)

	def test_a_token_without_digits_is_not_a_marker(self):
		self.assertIsNone(match_token("*", self.star_after).pattern)
		self.assertIsNone(match_token("AB*", self.star_after).pattern)

	def test_zero_is_reported_as_a_pattern_with_a_problem_not_as_a_serial(self):
		match = match_token("0*", self.star_after)

		self.assertEqual(match.pattern, "{n}*")
		self.assertIsNone(match.row_index)
		self.assertEqual(match.problem, CODE_INDEX_NOT_POSITIVE)

	def test_a_row_number_above_the_bound_is_reported(self):
		match = match_token(f"{MAX_ROW_INDEX + 1}*", self.star_after)

		self.assertEqual(match.problem, CODE_INDEX_TOO_LARGE)

	def test_persian_digits_in_a_marker_are_read(self):
		self.assertEqual(match_token("۳*", self.star_after).row_index, 3)

	def test_leading_zeros_are_read_as_a_number(self):
		self.assertEqual(match_token("003*", self.star_after).row_index, 3)

	def test_superscript_digits_are_not_digits(self):
		self.assertIsNone(match_token("²*", self.star_after).pattern)

	def test_empty_token_never_matches(self):
		self.assertIsNone(match_token("", self.star_after).pattern)
		self.assertIsNone(match_token(None, self.star_after).pattern)

	def test_arabic_text_prefix(self):
		pattern = parse_pattern("ردیف {n}:")

		self.assertEqual(match_token("ردیف 4:", pattern).row_index, 4)
		self.assertIsNone(match_token("ردیف 4", pattern).pattern)


class TestLookupMarker(UnitTestCase):
	def setUp(self):
		self.patterns = parse_patterns(("{n}*", "*{n}", "{n}#", "#{n}"))

	def test_picks_the_pattern_that_matches(self):
		self.assertEqual(lookup_marker("7#", self.patterns).row_index, 7)
		self.assertEqual(lookup_marker("#7", self.patterns).row_index, 7)

	def test_a_plain_serial_is_not_a_marker(self):
		self.assertIsNone(lookup_marker("AB-12", self.patterns).pattern)

	def test_no_patterns_means_no_marker(self):
		self.assertIsNone(lookup_marker("7#", ()).pattern)


class TestAmbiguity(UnitTestCase):
	def test_the_default_set_is_not_ambiguous(self):
		self.assertEqual(find_ambiguous_patterns(("{n}*", "*{n}", "{n}#", "#{n}")), ())

	def test_identical_patterns_are_ambiguous(self):
		self.assertEqual(find_ambiguous_patterns(("{n}*", "{n}*")), (("{n}*", "{n}*"),))

	def test_a_whitespace_difference_makes_two_distinct_markers(self):
		self.assertEqual(find_ambiguous_patterns(("A{n}B", "A {n}B")), ())

	def test_mirror_shapes_are_not_ambiguous(self):
		self.assertEqual(find_ambiguous_patterns(("{n}*", "*{n}")), ())

	def test_an_unparsable_pattern_stops_the_check(self):
		with self.assertRaises(MarkerPatternError):
			find_ambiguous_patterns(("{n}*", "**"))
