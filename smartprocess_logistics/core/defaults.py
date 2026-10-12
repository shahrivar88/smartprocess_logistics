"""The delimiter and marker values a fresh install starts with.

They live here rather than beside the code that writes them, so the shipped configuration can
be checked by the core tests without a database. `setup.seeds` only writes them.

`\\r\\n` and `\\n` are both active on purpose. A file saved by Windows uses the pair and a
file saved by Linux uses the single character; splitting takes the longest match first, so a
Windows line break is read as one separator and never as an empty token between two. That
overlap is reported to the operator as a warning and refuses nothing.
"""

DEFAULT_DELIMITERS = (
	("Enter (CRLF)", "\\r\\n"),
	("Enter (LF)", "\\n"),
	("Tab", "\\t"),
	("Space", " "),
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
