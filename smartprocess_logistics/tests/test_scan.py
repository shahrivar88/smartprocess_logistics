"""The scan window's next-column command: what may be stored, and what it may never be.

The command is the one setting in this app that is matched against what an operator scans, so the
two ways it goes wrong are both quiet: a value that can never be typed (invisible characters) makes
a scanner that does nothing, and a value that is really a separator makes one that navigates when
the operator meant to register. The rule that refuses those lives in `core.scan` and is checked
here, in full, without a site.

The matching itself — "the whole scanned value, exactly" — is the scan window's own decision and
lives in `public/js/sales_invoice_serial_button.js`; `deployment/lab/verify_scan_logic.js` runs it,
against the real file, in node.
"""

import pathlib

from smartprocess_logistics.core.defaults import DEFAULT_DELIMITERS, DEFAULT_NEXT_ROW_COMMAND
from smartprocess_logistics.core.scan import (
	CODE_DELIMITER,
	CODE_EMPTY,
	CODE_INVISIBLE,
	CODE_TOO_LONG,
	FIELDNAME,
	ScanCommandError,
	served_next_row_command,
	validate_next_row_command,
)
from smartprocess_logistics.tests.base import UnitTestCase

APP_ROOT = pathlib.Path(__file__).resolve().parents[2] / "smartprocess_logistics"

SHIPPED_DELIMITERS = tuple(stored for _, stored in DEFAULT_DELIMITERS)


class TestWhatMayBeStored(UnitTestCase):
	"""The four refusals, and the values that are none of their business."""

	def refused(self, value, **kwargs):
		with self.assertRaises(ScanCommandError) as caught:
			validate_next_row_command(value, **kwargs)

		return caught.exception

	def test_the_shipped_default_is_a_command_this_rule_accepts(self):
		"""`DEFAULT_NEXT_ROW_COMMAND` is only a default if the rule behind it agrees.

		The two live in different modules on purpose — one is what a site starts with, one is what
		a site may have — and this is what keeps them from drifting into a value that cannot be
		saved.
		"""
		self.assertEqual(
			validate_next_row_command(DEFAULT_NEXT_ROW_COMMAND, delimiters=SHIPPED_DELIMITERS),
			DEFAULT_NEXT_ROW_COMMAND,
		)

	def test_a_value_is_stored_without_the_whitespace_around_it(self):
		"""A trailing space is invisible on the screen and fatal in the comparison."""
		self.assertEqual(validate_next_row_command("  **  ", delimiters=SHIPPED_DELIMITERS), "**")

	def test_an_empty_value_is_refused(self):
		for value in (None, "", "   ", "\t"):
			with self.subTest(value=value):
				error = self.refused(value)

				self.assertEqual(error.code, CODE_EMPTY)

	def test_an_invisible_character_is_refused(self):
		"""A zero-width joiner cannot be typed by a scanner or seen by the person editing the field.

		Stored, it would be a command no scan can ever equal — the feature would look enabled and
		do nothing, which is the failure this rule exists to prevent.
		"""
		error = self.refused("**‍")

		self.assertEqual(error.code, CODE_INVISIBLE)

	def test_a_control_character_is_refused(self):
		error = self.refused("**\u0007")

		self.assertEqual(error.code, CODE_INVISIBLE)

	def test_a_value_longer_than_the_serial_length_is_refused(self):
		error = self.refused("A" * 61, max_length=60)

		self.assertEqual(error.code, CODE_TOO_LONG)

	def test_a_value_exactly_the_serial_length_is_a_command(self):
		value = "A" * 60

		self.assertEqual(validate_next_row_command(value, max_length=60), value)

	def test_a_site_that_set_no_length_has_no_length_rule(self):
		value = "A" * 200

		self.assertEqual(validate_next_row_command(value, max_length=None), value)

	def test_a_command_that_is_one_of_this_site_separators_is_refused(self):
		"""`,` is the character this site splits a typed list on: it cannot also be one value."""
		for delimiter in (",", ";", "،"):
			with self.subTest(delimiter=delimiter):
				error = self.refused(delimiter, delimiters=SHIPPED_DELIMITERS)

				self.assertEqual(error.code, CODE_DELIMITER)

	def test_a_delimiter_this_site_cannot_read_does_not_refuse_a_good_command(self):
		"""An unreadable separator is that field's problem, reported where it is validated."""
		self.assertEqual(
			validate_next_row_command("**", delimiters=(None, "")),
			"**",
		)

	def test_a_command_that_merely_contains_a_separator_is_allowed(self):
		"""Containment is not ambiguity: this value is still one value, matched whole.

		The rule refuses a command that *is* a separator, because that text can never stand on its
		own. Refusing one that contains one would refuse `NEXT,ROW` — a value a scanner can send
		and the window can match exactly — for a confusion that belongs to a different field.
		"""
		self.assertEqual(validate_next_row_command("NEXT,ROW", delimiters=SHIPPED_DELIMITERS), "NEXT,ROW")

	def test_a_command_a_serial_could_look_like_is_allowed(self):
		"""Nothing here knows what the site's serials look like, and it must not guess."""
		for value in ("W-0001", "شا8326980", "**"):
			with self.subTest(value=value):
				self.assertEqual(validate_next_row_command(value), value)


class TestWhatTheWindowIsServed(UnitTestCase):
	"""A stored value that cannot be matched is served as nothing, never as a refusal.

	The window is opened to register serials. A settings row that holds a command nobody could
	scan is a reason to navigate on nothing, not a reason to withhold the invoice's rows from the
	operator who came for them — and not a reason for the server to refuse a read that has nothing
	to do with the setting.
	"""

	def test_a_command_the_rule_accepts_is_served_as_it_is(self):
		self.assertEqual(
			served_next_row_command(DEFAULT_NEXT_ROW_COMMAND, delimiters=SHIPPED_DELIMITERS),
			DEFAULT_NEXT_ROW_COMMAND,
		)

	def test_a_site_that_has_not_got_the_field_yet_is_served_nothing(self):
		"""The state every site is in between the deploy and the migrate: no column, no value."""
		self.assertEqual(served_next_row_command(None, delimiters=SHIPPED_DELIMITERS), "")

	def test_the_four_refusals_all_arrive_as_nothing(self):
		cases = (
			("", {}),
			("   ", {}),
			("**‍", {}),
			("A" * 61, {"max_length": 60}),
			(",", {"delimiters": SHIPPED_DELIMITERS}),
		)

		for value, kwargs in cases:
			with self.subTest(value=value, kwargs=kwargs):
				self.assertEqual(served_next_row_command(value, **kwargs), "")

	def test_a_site_that_chose_its_own_command_is_served_that_one(self):
		self.assertEqual(served_next_row_command("§§"), "§§")

	def test_a_command_that_is_long_only_because_the_site_allows_it_is_served(self):
		value = "A" * 60

		self.assertEqual(served_next_row_command(value, max_length=60), value)


class TestTheFieldItGuards(UnitTestCase):
	def test_the_fieldname_is_the_one_the_doc_type_carries(self):
		"""Three modules name this field — the rule, the endpoint and the seed. One string."""
		self.assertEqual(FIELDNAME, "next_row_command")

	def test_the_endpoint_serves_it_without_deciding_anything_about_it(self):
		"""The decision is `core.scan`'s, and the endpoint is the wiring that hands it two values.

		An endpoint that decided what to do with a refusal would be an endpoint whose rule no
		database-free test could reach — which is the split this app keeps everywhere else.
		"""
		source = (APP_ROOT / "api" / "endpoints.py").read_text(encoding="utf-8")
		start = source.index("def _scan_command(gateway):")
		body = source[start : source.index("\ndef ", start + 1)]

		self.assertIn("served_next_row_command(", body)
		self.assertNotIn("except", body)
		self.assertNotIn("ScanCommandError", body)
