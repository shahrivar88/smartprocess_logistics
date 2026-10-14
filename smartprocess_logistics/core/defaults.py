"""The delimiter and marker values a fresh install starts with.

They live here rather than beside the code that writes them, so the shipped configuration can
be checked by the core tests without a database. `setup.seeds` only writes them.

`\\r\\n` and `\\n` are both active on purpose. A file saved by Windows uses the pair and a
file saved by Linux uses the single character; splitting takes the longest match first, so a
Windows line break is read as one separator and never as an empty token between two. That
overlap is reported to the operator as a warning and refuses nothing.

Space is written `\\s`, not `" "`. Both decode to one space, but `delimiter` is a required
field, and Frappe's mandatory validation treats a whitespace-only value as missing, so a stored
`" "` is refused and the install stops. `\\s` carries the same meaning in a form the field can
hold. `tests/test_delimiters.py` pins that every value here survives that rule.
"""

DEFAULT_DELIMITERS = (
	("Enter (CRLF)", "\\r\\n"),
	("Enter (LF)", "\\n"),
	("Tab", "\\t"),
	("Space", "\\s"),
	("English comma", ","),
	("Persian comma", "،"),
	("Semicolon", ";"),
)

DEFAULT_ITEM_MARKERS = (
	("ستاره پس از شمارهٔ ردیف", "{n}*"),
	("ستاره پیش از شمارهٔ ردیف", "*{n}"),
	("هش پس از شمارهٔ ردیف", "{n}#"),
	("هش پیش از شمارهٔ ردیف", "#{n}"),
)

# What the scan window's "next column" command starts as. Two asterisks because it is a value a
# scanner can be programmed with and a person cannot type by accident while scanning serials —
# and because the serials this app sees are alphanumeric, so nothing real normalizes to it.
# It is only ever a starting value: written when the field is empty, never over a site's own.
DEFAULT_NEXT_ROW_COMMAND = "**"
