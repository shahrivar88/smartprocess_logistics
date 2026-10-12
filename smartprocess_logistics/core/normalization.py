"""Versioned, deterministic normalization of a serial number.

Normalization turns the text an operator typed into the single form this app treats as the
serial's identity. It is versioned: `NORMALIZATION_VERSION` names the rule set, every stored
serial records the version it was normalized with, and a later rule change is applied by a
migration that reports collisions instead of silently merging two serials.

The function is pure. It reads no global state and calls nothing outside the standard
library, so the same input always produces the same output.

How the rule was chosen
-----------------------
The identity is stored in a `varchar` column of a table whose collation is
`utf8mb4_unicode_ci`, and the database enforces the unique constraint with **its** rules, not
this function's. If this function ever called two serials different when the database calls
them the same, the database would reject the second one with a duplicate-key error the
operator cannot understand. The rule is therefore chosen to be at least as coarse as the
collation, and `tests/test_collation_agreement.py` pins that property against a table of
verdicts measured on this MariaDB build.

Rule set 2 is `NFKD`, then combining marks are dropped, then `NFC`, then digits are folded,
then case is folded:

- `NFKD` folds compatibility forms, so `Ⅳ` is `IV`, `①` is `1`, `ﬁ` is `fi` and `ﻻ` is `لا`.
  The collation folds these too.
- **Dropping combining marks** is what makes the rule match the accent-insensitive collation.
  Rule set 1 used `NFKC` and did not drop them, so it called `é` different from `e` and `İ`
  different from `i` while the database called each pair equal. That is the defect rule set 2
  fixes. Only `Mn` and `Me` are dropped: `Mc` is kept, because the collation keeps it too —
  `क` and `कि` are different serials to the database and stay different here.
- `NFC` recomposes what `NFKD` split, so the stored form is the composed one.
- Persian and Arabic-Indic digits fold to ASCII, which the collation also equates.
- `casefold` is used rather than `upper`. It is Unicode's caseless-matching operation, and it
  agrees with the collation on the Greek final sigma and on `ß`. Its visible consequence is
  that a folded value is not always upper-case, which is why the stored identity form is
  described as case-folded rather than upper-cased. The field is hidden, so its case never
  reaches a screen; `serial_no` keeps the text exactly as typed.

Invisible and control characters are **refused**, not removed
------------------------------------------------------------
Rule set 2 raises `SERIAL_HAS_CONTROL` for any character in the Unicode `Cc`, `Cf`, `Cs`,
`Co` or `Cn` categories. Rule set 1 silently deleted them, which let a separator that was
never read as a separator glue two serial fragments into one: `AB` + tab + `12` became `ab12`
and was indistinguishable from a serial an operator had typed as `AB12`. Deleting content
from an identity is not a transformation an operator can see, so the rule now refuses it and
says so. `serial_no` is unaffected; the value is simply not accepted.

Equivalences this rule set deliberately creates, which an operator can observe:

- **Width**, **digit script**, **case**, **compatibility forms** and **accents**, all because
  the collation creates them too.
- **Arabic letter shapes.** Arabic `ي` (U+064A) and `ك` (U+0643) fold to Persian `ی`
  (U+06CC) and `ک` (U+06A9). The pairs are visually identical and come from different
  keyboard layouts, so keeping them apart would create duplicates no operator can see. The
  collation does **not** equate them, so this makes the app stricter than the database, never
  looser. The folding is switchable through `NormalizationPolicy.fold_arabic_letters`.

Equivalences this rule set does **not** create. Two serials that differ in any of these stay
different serials:

- `AB-12` and `AB12` — punctuation is never removed.
- `AB 12` and `AB12` — an internal space is never removed. A space is also a configured
  delimiter in the default settings, so such a serial is refused as ambiguous rather than
  silently folded.
- `A1` and `AA1`, or any other letter that is not one of the two Arabic/Persian pairs.
- Any serial containing a control or invisible character is refused outright.
"""

import unicodedata
from dataclasses import dataclass

NORMALIZATION_VERSION = 2

# A hard ceiling that applies even when a caller configures a longer business limit, so no
# single call can be made arbitrarily expensive.
MAX_RAW_CHARS = 1000

# The business limit. `Logistics Settings.serial_max_length` overrides it through a policy.
DEFAULT_MAX_LENGTH = 60

# Characters carrying no visible content. They are refused rather than deleted, so nothing an
# operator typed can vanish from the identity without being reported.
INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Cn", "Co", "Cs"})

# Combining marks that the database collation ignores. `Mc` is deliberately absent: the
# collation keeps spacing combining marks, so dropping them here would make the app coarser
# than the database and refuse serials the database accepts as distinct.
DROPPED_MARK_CATEGORIES = frozenset({"Me", "Mn"})

# Arabic letter shapes that are indistinguishable from their Persian counterparts.
ARABIC_TO_PERSIAN = str.maketrans({"ي": "ی", "ك": "ک"})

DIGIT_TRANSLATION = str.maketrans(
	{
		**{chr(0x06F0 + offset): str(offset) for offset in range(10)},  # ۰..۹
		**{chr(0x0660 + offset): str(offset) for offset in range(10)},  # ٠..٩
	}
)

CODE_EMPTY = "SERIAL_EMPTY"
CODE_HAS_CONTROL = "SERIAL_HAS_CONTROL"
CODE_NOT_TEXT = "SERIAL_NOT_TEXT"
CODE_TOO_LONG = "SERIAL_TOO_LONG"


class SerialTextError(ValueError):
	"""A serial number that cannot be normalized.

	`code` is stable and safe to branch on. `message` is written for the operator.
	"""

	def __init__(self, code, message):
		super().__init__(message)
		self.code = code
		self.message = message


@dataclass(frozen=True)
class NormalizationPolicy:
	"""The rule set a serial is normalized with.

	The defaults are the current rules. A stored policy is reproduced by passing the values
	that were in force when a serial was written, which is what lets a later rule change be
	applied as a migration rather than as a surprise.
	"""

	version: int = NORMALIZATION_VERSION
	max_length: int = DEFAULT_MAX_LENGTH
	fold_arabic_letters: bool = True


@dataclass(frozen=True)
class NormalizedSerial:
	"""One serial, in both of its forms.

	`original` is never altered and is what the operator sees. `normalized` is the identity
	used for uniqueness and for matching.
	"""

	original: str
	normalized: str
	version: int

	def __str__(self):
		return self.normalized


def find_forbidden_character(text):
	"""The first control or invisible character in `text`, or `None`.

	The check runs on the text as it was typed, before any normalization, so a character that
	a later step would have transformed away is still reported.
	"""
	for character in text:
		if unicodedata.category(character) in INVISIBLE_CATEGORIES:
			return character

	return None


def normalize_serial(raw, policy=None):
	"""Return the display form and the identity form of one serial number.

	Raises `SerialTextError` for a value that is not text, is longer than the policy allows,
	contains a control or invisible character, or has no content left.
	"""
	policy = policy or NormalizationPolicy()

	if not isinstance(raw, str):
		raise SerialTextError(CODE_NOT_TEXT, "شمارهٔ سریال باید متن باشد.")

	if len(raw) > MAX_RAW_CHARS:
		raise SerialTextError(CODE_TOO_LONG, f"شمارهٔ سریال بیش از {MAX_RAW_CHARS} نویسه دارد.")

	if len(raw) > policy.max_length:
		raise SerialTextError(
			CODE_TOO_LONG,
			f"شمارهٔ سریال بیش از {policy.max_length} نویسه دارد.",
		)

	forbidden = find_forbidden_character(raw)

	if forbidden is not None:
		raise SerialTextError(
			CODE_HAS_CONTROL,
			f"شمارهٔ سریال نویسهٔ کنترلی یا نامرئی دارد (U+{ord(forbidden):04X}).",
		)

	text = unicodedata.normalize("NFKD", raw)
	text = "".join(
		character for character in text if unicodedata.category(character) not in DROPPED_MARK_CATEGORIES
	)
	text = unicodedata.normalize("NFC", text)
	text = text.strip()
	text = text.translate(DIGIT_TRANSLATION)

	if policy.fold_arabic_letters:
		text = text.translate(ARABIC_TO_PERSIAN)

	text = text.casefold()

	if not text:
		raise SerialTextError(CODE_EMPTY, "شمارهٔ سریال پس از نرمال‌سازی خالی است.")

	return NormalizedSerial(original=raw, normalized=text, version=policy.version)


def is_outdated(normalized_serial, policy=None):
	"""Whether a stored serial was normalized with rules other than these.

	A `True` answer makes the serial a candidate for re-normalization by a migration. It never
	means the serial is wrong, only that it was written under an older rule set.
	"""
	policy = policy or NormalizationPolicy()
	return normalized_serial.version != policy.version


def renaming_risk(stored_normalized, policy=None):
	"""Whether re-normalizing an already-normalized value would change it.

	Re-normalization works from the stored identity, because the text an operator originally
	typed is not kept on the row. Rule set 2 therefore cannot reintroduce a control character
	or an accent that rule set 1 had removed: the stored value already lacks them, and running
	the new rules over it leaves it as it is. This function exists so a migration can prove
	that instead of assuming it.
	"""
	policy = policy or NormalizationPolicy()

	try:
		again = normalize_serial(stored_normalized, policy=policy)
	except SerialTextError:
		return True

	return again.normalized != stored_normalized
