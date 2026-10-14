"""The scan window's one stored rule: the command that moves the cursor to the next column.

A barcode scanner is a keyboard. Everything an operator scans is typed into the serial box and
sent — which is right for a serial, and useless for the one thing the operator needs that is not a
serial: getting to the next item row without reaching for the mouse. The answer is a value the
scanner can be made to send, configured per site, which the window recognises **instead of**
registering.

What "recognised" means, exactly
--------------------------------
The scanned text, stripped, must equal the configured command in full. Not a prefix, not a
substring, not a case-folded match: a serial that happens to contain the command is a serial, and
the only way to be sure of that is to compare the whole value. Everything else about the value —
how it is normalized, whether it is already registered, whether the row has room — is the server's
business and is deliberately not consulted here, because a navigation instruction never reaches the
server at all.

Why the value is validated here rather than at the screen that reads it
----------------------------------------------------------------------
The command is stored in `Logistics Settings`, so the one place it can be prevented from being
wrong is the moment it is saved. `validate_next_row_command` is that rule, and it is a pure
function: `tests/test_scan.py` reaches every branch of it without a site, and the DocType
controller only has to call it.

The rules are the four ways a command is unusable rather than merely unusual:

* **empty** — there would be nothing to match, and a scan of nothing is every scan;
* **invisible** — a control or zero-width character cannot be typed by a scanner or seen by the
  operator editing the field, so the value could never be reproduced;
* **too long** — past the serial length the site already allows, it is a serial, not a command;
* **a delimiter** — the site's own separator text can never stand on its own as one value, so a
  command that *is* one would be a value the app reads as two.

Anything else is allowed. A command that contains a delimiter, or looks like a serial, is the
operator's decision to make; a command that cannot be matched is not.
"""

from smartprocess_logistics.core.delimiters import DelimiterError, interpret_delimiter
from smartprocess_logistics.core.normalization import find_forbidden_character

CODE_EMPTY = "SCAN_COMMAND_EMPTY"
CODE_INVISIBLE = "SCAN_COMMAND_INVISIBLE"
CODE_TOO_LONG = "SCAN_COMMAND_TOO_LONG"
CODE_DELIMITER = "SCAN_COMMAND_DELIMITER"

# The field this rule guards, named here so the controller, the tests and the seed all speak about
# the same one value rather than repeating a string.
FIELDNAME = "next_row_command"


class ScanCommandError(ValueError):
	"""A command that cannot be stored. `code` is stable, the message is for an operator."""

	def __init__(self, code, message):
		super().__init__(message)
		self.code = code
		self.message = message


def validate_next_row_command(value, *, delimiters=(), max_length=None):
	"""The command as it should be stored, or a refusal saying why it cannot be.

	`delimiters` is the site's own configured separator text (as stored: `\\n`, `\\s`, `,`), and
	`max_length` the serial length this site allows. Both are given by the caller rather than read
	here, so this stays a function of its arguments — the tests pass the values they mean.
	"""
	stripped = "" if value is None else str(value).strip()

	if not stripped:
		raise ScanCommandError(
			CODE_EMPTY,
			"کاراکتر ردیف بعدی نمی‌تواند خالی باشد؛ اسکنر باید چیزی بفرستد که همین مقدار باشد.",
		)

	forbidden = find_forbidden_character(stripped)

	if forbidden is not None:
		raise ScanCommandError(
			CODE_INVISIBLE,
			"کاراکتر ردیف بعدی نویسهٔ ناپیدا یا کنترلی دارد؛ چنین مقداری با اسکنر تکرارشدنی نیست.",
		)

	if max_length and len(stripped) > int(max_length):
		raise ScanCommandError(
			CODE_TOO_LONG,
			f"کاراکتر ردیف بعدی بیش از {int(max_length)} نویسه است؛ این یک سریال است، نه فرمان.",
		)

	for stored in delimiters or ():
		try:
			separator = interpret_delimiter(stored)
		except DelimiterError:
			# A delimiter this site cannot read is a problem with that field and is reported
			# where it is validated. It is not a reason to refuse this one.
			continue

		if separator and stripped == separator:
			raise ScanCommandError(
				CODE_DELIMITER,
				"کاراکتر ردیف بعدی با یکی از جداکننده‌های همین سایت یکسان است؛ چنین مقداری "
				"یک جداکننده است و نمی‌تواند یک فرمان باشد.",
			)

	return stripped


def served_next_row_command(value, *, delimiters=(), max_length=None):
	"""The command to hand the scan window: the stored one, or nothing at all.

	This is the second half of the rule, and the half that decides what a *bad* value does rather
	than what a good one is. A stored command that cannot be matched is not an error anybody is
	waiting for: the window is opened to register serials, and refusing to describe the invoice
	because a setting is wrong would take the operator's work away over a field they cannot even
	see. So the answer is empty — the window navigates on nothing and registers every scan, which
	is exactly what it did before this feature existed.

	An empty answer is also what a site gets *before* the field exists at all: the migrate that
	adds the column has not run, `settings.get` answers `None`, and that is an empty command. The
	window works, without the command, and nothing has to be deployed in a particular order.
	"""
	try:
		return validate_next_row_command(value, delimiters=delimiters, max_length=max_length)
	except ScanCommandError:
		return ""
