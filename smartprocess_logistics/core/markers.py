"""The `{n}` item markers that say which invoice row a group of serials belongs to.

A marker pattern is a template with exactly one `{n}`, where `n` is the 1-based position of
an item row on the invoice. `{n}*` and `*{n}` both describe a marker whose text identifies a
row; the marker itself is never stored, it only tells an import which row the following
serials belong to.

A token matches a pattern only when the **whole** token is that pattern's marker. That rule is
what keeps the shapes apart: `1**` is the marker of `{n}**` and is not a marker of `{n}*`,
because the digits between the literal parts have to be digits. With that rule, two patterns
can only describe the same marker when their literal parts are identical, so ambiguity is
decidable by comparing the literal parts instead of enumerating every row number.
"""

from dataclasses import dataclass

PLACEHOLDER = "{n}"
MAX_PATTERN_CHARS = 16
MAX_ROW_INDEX = 100_000

CODE_NOT_TEXT = "MARKER_NOT_TEXT"
CODE_EMPTY = "MARKER_EMPTY"
CODE_TOO_LONG = "MARKER_TOO_LONG"
CODE_PLACEHOLDER_COUNT = "MARKER_PLACEHOLDER_COUNT"
CODE_BRACKETS = "MARKER_BRACKETS"
CODE_NO_LITERAL = "MARKER_NO_LITERAL"
CODE_AMBIGUOUS = "MARKER_AMBIGUOUS"
CODE_INDEX_NOT_POSITIVE = "MARKER_INDEX_NOT_POSITIVE"
CODE_INDEX_TOO_LARGE = "MARKER_INDEX_TOO_LARGE"


class MarkerPatternError(ValueError):
	"""A marker pattern, or a candidate marker, that cannot be used. `code` is stable."""

	def __init__(self, code, message):
		super().__init__(message)
		self.code = code
		self.message = message


@dataclass(frozen=True)
class MarkerPattern:
	"""One marker pattern, split into the text around its single `{n}`."""

	pattern: str
	prefix: str
	suffix: str

	def marker_for(self, row_index):
		"""The exact token that marks `row_index`."""
		check_row_index(row_index)
		return f"{self.prefix}{row_index}{self.suffix}"


@dataclass(frozen=True)
class MarkerMatch:
	"""The result of asking whether a token is a marker.

	`pattern` is set when the token has a marker's shape. `row_index` is set only when it also
	carries a usable row number, so a token such as `0*` is reported as a pattern match with a
	problem rather than being quietly handed on as a serial number.
	"""

	pattern: str | None
	row_index: int | None
	problem: str | None


NO_MATCH = MarkerMatch(pattern=None, row_index=None, problem=None)


def check_row_index(row_index):
	"""Refuse anything that is not a usable 1-based row position."""
	if isinstance(row_index, bool) or not isinstance(row_index, int):
		raise MarkerPatternError(CODE_NOT_TEXT, "شمارهٔ ردیف باید عدد صحیح باشد.")
	if row_index < 1:
		raise MarkerPatternError(CODE_INDEX_NOT_POSITIVE, "شمارهٔ ردیف از ۱ شروع می‌شود.")
	if row_index > MAX_ROW_INDEX:
		raise MarkerPatternError(CODE_INDEX_TOO_LARGE, f"شمارهٔ ردیف بیش از {MAX_ROW_INDEX} است.")


def parse_pattern(pattern):
	"""Turn pattern text into a `MarkerPattern`, or raise `MarkerPatternError`."""
	if not isinstance(pattern, str):
		raise MarkerPatternError(CODE_NOT_TEXT, "الگوی نشانگر باید متن باشد.")

	if not pattern.strip():
		raise MarkerPatternError(CODE_EMPTY, "الگوی نشانگر نمی‌تواند خالی باشد.")

	if len(pattern) > MAX_PATTERN_CHARS:
		raise MarkerPatternError(CODE_TOO_LONG, f"الگوی نشانگر بیش از {MAX_PATTERN_CHARS} نویسه دارد.")

	if pattern.count(PLACEHOLDER) != 1:
		raise MarkerPatternError(
			CODE_PLACEHOLDER_COUNT,
			f"الگوی نشانگر باید دقیقاً یک {PLACEHOLDER} داشته باشد.",
		)

	remainder = pattern.replace(PLACEHOLDER, "")
	if "{" in remainder or "}" in remainder:
		raise MarkerPatternError(CODE_BRACKETS, "الگوی نشانگر فقط یک جای‌نگهدار مجاز دارد.")

	if not remainder:
		raise MarkerPatternError(
			CODE_NO_LITERAL,
			"الگوی نشانگر باید دست‌کم یک نویسهٔ ثابت داشته باشد، وگرنه هر عددی نشانگر می‌شود.",
		)

	prefix, _, suffix = pattern.partition(PLACEHOLDER)

	return MarkerPattern(pattern=pattern, prefix=prefix, suffix=suffix)


def parse_patterns(patterns):
	"""Parse several patterns at once, keeping their order."""
	return tuple(parse_pattern(pattern) for pattern in patterns)


def match_token(token, pattern):
	"""Whether the whole token is `pattern`'s marker, and which row it names.

	Returns a `MarkerMatch`. A token with the right shape but an unusable row number comes
	back with `problem` set, so the caller can report it instead of treating it as a serial.
	"""
	if not isinstance(token, str) or not token:
		return NO_MATCH

	prefix, suffix = pattern.prefix, pattern.suffix

	if len(token) <= len(prefix) + len(suffix):
		return NO_MATCH
	if not token.startswith(prefix) or not token.endswith(suffix):
		return NO_MATCH

	digits = token[len(prefix) : len(token) - len(suffix)]

	if not digits.isdecimal():
		return NO_MATCH

	try:
		row_index = int(digits)
	except ValueError:
		return NO_MATCH

	if row_index < 1:
		return MarkerMatch(pattern=pattern.pattern, row_index=None, problem=CODE_INDEX_NOT_POSITIVE)
	if row_index > MAX_ROW_INDEX:
		return MarkerMatch(pattern=pattern.pattern, row_index=None, problem=CODE_INDEX_TOO_LARGE)

	return MarkerMatch(pattern=pattern.pattern, row_index=row_index, problem=None)


def lookup_marker(token, patterns):
	"""Find which pattern makes a marker of the token.

	At most one pattern can match, because two patterns describe the same marker only when
	their literal parts are equal and identical patterns are refused as ambiguous. More than
	one match is therefore a defect and is raised rather than resolved by picking one.
	"""
	matches = []

	for pattern in patterns:
		match = match_token(token, pattern)
		if match.pattern:
			matches.append(match)

	if not matches:
		return NO_MATCH

	if len(matches) > 1:
		raise MarkerPatternError(
			CODE_AMBIGUOUS,
			"این نشانگر با بیش از یک الگو مطابقت دارد: " + "، ".join(sorted(m.pattern for m in matches)),
		)

	return matches[0]


def find_ambiguous_patterns(patterns):
	"""Pairs of patterns that describe the very same marker.

	Two patterns agree when the text before and after the placeholder is identical. This is
	the only way two patterns can produce the same marker, because a marker is exactly
	`prefix + digits + suffix` and the digits part cannot contain the literal parts.
	"""
	parsed = parse_patterns(patterns)
	pairs = []

	for index, first in enumerate(parsed):
		for second in parsed[index + 1 :]:
			if (first.prefix, first.suffix) == (second.prefix, second.suffix):
				pairs.append((first.pattern, second.pattern))

	return tuple(pairs)
