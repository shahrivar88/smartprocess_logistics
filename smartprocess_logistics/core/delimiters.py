"""How a configured delimiter is interpreted, and how text is split into tokens.

A delimiter is stored as text a person can type and see in a grid cell. A literal newline or
tab cannot be typed there usefully, so `\\r`, `\\n` and `\\t` are stored as the two characters
backslash plus letter and interpreted here. `\\\\` is a literal backslash, which is what makes
the encoding reversible.

Splitting is deterministic and does not depend on the order the delimiters happen to be
configured in. When several delimiters match at the same position, the longest one wins, which
is the rule that resolves the `,` versus `,,` overlap. Delimiters of equal length are ordered
by their own text, so the result is the same on every run and in every process.
"""

from dataclasses import dataclass

MAX_DELIMITER_CHARS = 8
MAX_TEXT_CHARS = 1_000_000
MAX_TOKENS = 50_000

ESCAPES = (("\\\\", "\\"), ("\\r", "\r"), ("\\n", "\n"), ("\\t", "\t"))

# Characters a delimiter may be made of. Tab, newline and carriage return are allowed; every
# other control character and every letter or digit is not, because a separator that is part
# of a serial's own text would split serials apart.
ALLOWED_DELIMITER_CHARACTERS = frozenset({"\t", "\n", "\r"})

CODE_NOT_TEXT = "DELIMITER_NOT_TEXT"
CODE_EMPTY = "DELIMITER_EMPTY"
CODE_TOO_LONG = "DELIMITER_TOO_LONG"
CODE_ALPHANUMERIC = "DELIMITER_ALPHANUMERIC"
CODE_UNPRINTABLE = "DELIMITER_UNPRINTABLE"
CODE_TEXT_NOT_TEXT = "TEXT_NOT_TEXT"
CODE_TEXT_TOO_LONG = "TEXT_TOO_LONG"
CODE_TOO_MANY_TOKENS = "TOO_MANY_TOKENS"


class DelimiterError(ValueError):
	"""A delimiter or an input text that cannot be handled. `code` is stable."""

	def __init__(self, code, message):
		super().__init__(message)
		self.code = code
		self.message = message


@dataclass(frozen=True)
class DelimiterSet:
	"""The active delimiters, in the order they are matched."""

	delimiters: tuple

	@classmethod
	def of(cls, stored_delimiters):
		"""Build a set from stored delimiter text, refusing anything unusable."""
		interpreted = {validate_delimiter(stored) for stored in stored_delimiters}
		return cls(delimiters=order_delimiters(interpreted))

	def __bool__(self):
		return bool(self.delimiters)

	def __contains__(self, item):
		return item in self.delimiters

	def __iter__(self):
		return iter(self.delimiters)

	def __len__(self):
		return len(self.delimiters)

	def as_list(self):
		return list(self.delimiters)


def interpret_delimiter(stored):
	"""Turn stored delimiter text into the characters it stands for.

	Reading goes left to right and consumes the two-character escapes, so a trailing lone
	backslash stays a literal backslash instead of swallowing the end of the string.
	"""
	if not isinstance(stored, str):
		raise DelimiterError(CODE_NOT_TEXT, "جداکننده باید متن باشد.")

	parts = []
	index = 0

	while index < len(stored):
		for escape, character in ESCAPES:
			if stored.startswith(escape, index):
				parts.append(character)
				index += len(escape)
				break
		else:
			parts.append(stored[index])
			index += 1

	return "".join(parts)


def validate_delimiter(stored):
	"""Return the interpreted delimiter, or raise `DelimiterError`."""
	if not isinstance(stored, str):
		raise DelimiterError(CODE_NOT_TEXT, "جداکننده باید متن باشد.")

	if len(stored) > MAX_DELIMITER_CHARS:
		raise DelimiterError(
			CODE_TOO_LONG,
			f"جداکننده بیش از {MAX_DELIMITER_CHARS} نویسه دارد.",
		)

	interpreted = interpret_delimiter(stored)

	if not interpreted:
		raise DelimiterError(CODE_EMPTY, "جداکننده نمی‌تواند خالی باشد.")

	for character in interpreted:
		if character.isalnum():
			raise DelimiterError(
				CODE_ALPHANUMERIC,
				"جداکننده نمی‌تواند حرف یا رقم باشد.",
			)
		if not character.isprintable() and character not in ALLOWED_DELIMITER_CHARACTERS:
			raise DelimiterError(
				CODE_UNPRINTABLE,
				"جداکننده نویسهٔ چاپ‌نشدنی غیرمجاز دارد.",
			)

	return interpreted


def order_delimiters(delimiters):
	"""Order delimiters so the longest matches first and equal lengths are decided by text.

	Sorting by text as the second key matters: an unordered set would otherwise let the
	process-wide hash seed decide the order of two delimiters of equal length, and the split
	result would stop being reproducible.
	"""
	return tuple(sorted(delimiters, key=lambda delimiter: (-len(delimiter), delimiter)))


def find_overlaps(delimiters):
	"""Pairs where one delimiter contains the other.

	An overlap is not an error, because the longest match resolves it, but it is worth
	reporting: `,` and `,,` both being active means a `,,` in the file is read as one
	separator and the operator should know that.
	"""
	ordered = order_delimiters(delimiters)
	pairs = []

	for index, first in enumerate(ordered):
		for second in ordered[index + 1 :]:
			if first == second:
				continue
			if first in second:
				pairs.append((second, first))
			elif second in first:
				pairs.append((first, second))

	return tuple(pairs)


def find_duplicates(delimiters):
	"""Delimiters that appear more than once, after interpretation and ordering."""
	seen = set()
	duplicates = []

	for delimiter in delimiters:
		if delimiter in seen and delimiter not in duplicates:
			duplicates.append(delimiter)
		seen.add(delimiter)

	return tuple(duplicates)


def split_tokens(text, delimiters):
	"""Split text on every delimiter, longest match first.

	Consecutive delimiters produce an empty token, which is deliberately kept: an empty token
	is almost always a mistake in the file, and silently dropping it would hide the mistake.
	Callers filter or report it.
	"""
	if not isinstance(text, str):
		raise DelimiterError(CODE_TEXT_NOT_TEXT, "متن ورودی باید متن باشد.")

	if len(text) > MAX_TEXT_CHARS:
		raise DelimiterError(CODE_TEXT_TOO_LONG, f"متن ورودی بیش از {MAX_TEXT_CHARS} نویسه دارد.")

	if isinstance(delimiters, DelimiterSet):
		ordered = delimiters.delimiters
	else:
		ordered = order_delimiters(set(delimiters))

	tokens = []
	buffer = []
	index = 0

	while index < len(text):
		for delimiter in ordered:
			if delimiter and text.startswith(delimiter, index):
				tokens.append("".join(buffer))
				buffer = []
				index += len(delimiter)
				break
		else:
			buffer.append(text[index])
			index += 1

		if len(tokens) > MAX_TOKENS:
			raise DelimiterError(CODE_TOO_MANY_TOKENS, f"تعداد توکن‌ها بیش از {MAX_TOKENS} است.")

	tokens.append("".join(buffer))

	return tokens
