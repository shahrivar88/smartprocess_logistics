"""The serial-requirement rule: what is mandatory, how many, and what it is not.

This module used to answer a different question — which invoices the report *might* cover, by
comparing each invoice's creation with the app's installation timestamp. That rule is gone, and
these tests are the reason it cannot come back quietly: every function below takes an item's flag
and a quantity, and there is no timestamp anywhere in them to take.

What the rule says: a marked item needs one label per unit, and a row short of them is a shortage
the report must show. An unmarked item needs nothing, so however many labels it collects it can
never be short — the registration is still allowed and still listed, which is why "optional" is a
state and not a refusal.
"""

import ast
import pathlib

from smartprocess_logistics.core.eligibility import (
	CONFLICT,
	INCOMPLETE,
	MANDATORY,
	OPTIONAL,
	SAVED,
	STAGED,
	is_eligible,
	is_reported,
	kind,
	requirement,
	row_state,
)
from smartprocess_logistics.core.quantities import QuantityError, row_coverage
from smartprocess_logistics.tests.base import UnitTestCase


class TestRequirement(UnitTestCase):
	"""How many labels a row must carry."""

	def test_a_marked_item_needs_one_label_per_unit(self):
		self.assertEqual(requirement(True, 3), 3)

	def test_a_fractional_quantity_rounds_up(self):
		"""Half a unit still needs a label, or a sold unit goes unlabelled."""
		self.assertEqual(requirement(True, 2.5), 3)
		self.assertEqual(requirement(True, 2.0000000001), 2)

	def test_a_credit_note_needs_one_per_unit_returned(self):
		"""A return stores a negative quantity, and the requirement is its magnitude."""
		self.assertEqual(requirement(True, -4), 4)

	def test_a_row_of_nothing_needs_nothing(self):
		self.assertEqual(requirement(True, 0), 0)

	def test_an_unmarked_item_needs_nothing_whatever_it_invoiced(self):
		"""The rule that stops optional registrations from inventing shortages."""
		for quantity in (0, 1, 7, 2.5, -3):
			with self.subTest(quantity=quantity):
				self.assertEqual(requirement(False, quantity), 0)

	def test_a_quantity_that_cannot_be_counted_is_refused_for_a_marked_item(self):
		with self.assertRaises(QuantityError):
			requirement(True, "three")

	def test_the_station_and_the_report_count_a_marked_row_the_same_way(self):
		"""`row_coverage` is what the report counts with, and the station shows for a required row.

		This policy and that function have to agree, or a column the station calls short and a row
		the report calls complete would be describing the same labels differently.
		"""
		for quantity in (0, 1, 3, 2.5, -4, 10):
			with self.subTest(quantity=quantity):
				self.assertEqual(
					requirement(True, quantity),
					row_coverage("row-1", "ITEM-1", quantity, 0).required,
				)


class TestKind(UnitTestCase):
	"""The word the station puts on a column."""

	def test_a_marked_item_is_mandatory(self):
		self.assertEqual(kind(True), MANDATORY)

	def test_an_unmarked_item_is_optional(self):
		for value in (False, None, 0, ""):
			with self.subTest(value=value):
				self.assertEqual(kind(value), OPTIONAL)


class TestIsReported(UnitTestCase):
	"""Which rows the report may hold, as a rule rather than as a filter."""

	def test_only_a_marked_item_is_reported(self):
		self.assertTrue(is_reported(True))

	def test_an_unmarked_item_is_never_reported(self):
		for value in (False, None, 0, ""):
			with self.subTest(value=value):
				self.assertFalse(is_reported(value))


class TestRowEligibility(UnitTestCase):
	"""Which rows this app offers for serials, and which it refuses to register on.

	One predicate, read by three surfaces — the window serves the rows it returns true for,
	`core.validation` refuses a registration on any other, and the report audits the same set — so
	a wrong answer here is a row that is on one screen and not another, or a row the window offers
	and the server refuses.
	"""

	def test_a_marked_item_is_eligible(self):
		for value in (True, 1, "1"):
			with self.subTest(value=value):
				self.assertTrue(is_eligible(value))

	def test_an_unmarked_item_is_not(self):
		for value in (False, None, 0, ""):
			with self.subTest(value=value):
				self.assertFalse(is_eligible(value))


class TestRowState(UnitTestCase):
	"""The one state a serial row is in, and the order the four answers are decided in.

	Every colour an operator sees comes from here: gray for a row short of what it needs, green for
	one holding exactly that much with labels still unsaved, blue for one whose labels are all
	written, red for one holding more than the invoice allows. The window calls it with the labels
	it has in hand and the server calls it with none, which is why the two can never disagree about
	a row that has nothing pending.
	"""

	def test_a_short_row_is_incomplete(self):
		for persisted, staged in ((0, 0), (1, 0), (0, 1), (2, 1), (4, 0)):
			with self.subTest(persisted=persisted, staged=staged):
				self.assertEqual(row_state(5, 5, persisted, staged), INCOMPLETE)

	def test_exactly_what_it_needs_with_something_in_hand_is_staged(self):
		"""Green: finished work that has not been sent."""
		for persisted, staged in ((0, 5), (4, 1), (3, 2), (2, 3)):
			with self.subTest(persisted=persisted, staged=staged):
				self.assertEqual(row_state(5, 5, persisted, staged), STAGED)

	def test_exactly_what_it_needs_and_nothing_in_hand_is_saved(self):
		"""Blue: the state only the server's own numbers can produce."""
		self.assertEqual(row_state(5, 5, 5, 0), SAVED)
		self.assertEqual(row_state(0, 0, 0, 0), SAVED)

	def test_more_than_the_invoice_allows_is_a_conflict(self):
		"""Red, and red before anything else is asked: a row to fix is not a row nearly done."""
		for persisted, staged in ((6, 0), (5, 1), (0, 6), (7, 3)):
			with self.subTest(persisted=persisted, staged=staged):
				self.assertEqual(row_state(5, 5, persisted, staged), CONFLICT)

	def test_a_conflict_outranks_a_shortage(self):
		"""A row over its quantity is not also reported as short of it, whatever the numbers say."""
		self.assertEqual(row_state(10, 5, 6, 0), CONFLICT)

	def test_the_state_is_decided_by_what_is_held_not_by_where_it_is_held(self):
		"""The staged/persisted split matters only once the count is exactly right."""
		self.assertEqual(row_state(5, 5, 5, 0), SAVED)
		self.assertEqual(row_state(5, 5, 3, 2), STAGED)
		self.assertEqual(row_state(5, 5, 0, 5), STAGED)

	def test_removing_a_staged_label_takes_the_row_back_to_gray(self):
		self.assertEqual(row_state(5, 5, 4, 1), STAGED)
		self.assertEqual(row_state(5, 5, 4, 0), INCOMPLETE)

	def test_the_four_names_are_distinct(self):
		"""The window paints by name: two states sharing a name would be one colour."""
		self.assertEqual(len({INCOMPLETE, STAGED, SAVED, CONFLICT}), 4)


class TestNoInstallationTimestamp(UnitTestCase):
	"""The regression this rule replaced: reporting keyed on when the app was installed."""

	def source(self):
		return (pathlib.Path(__file__).resolve().parent.parent / "core" / "eligibility.py").read_text(
			encoding="utf-8"
		)

	def code(self):
		"""The module's executable code — every docstring dropped, comments never present.

		The prose in this module *describes* the removed rule on purpose, and that is the opposite
		of a regression. What must not come back is an argument list or a comparison that consults
		an invoice's age, so the check is made against the code alone.
		"""
		tree = ast.parse(self.source())

		for node in ast.walk(tree):
			if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
				continue

			first = node.body[0] if node.body else None
			value = getattr(first, "value", None)

			if (
				isinstance(first, ast.Expr)
				and isinstance(value, ast.Constant)
				and isinstance(value.value, str)
			):
				node.body.pop(0)

		return ast.unparse(tree)

	def test_the_rule_consults_no_invoice_date_and_no_install_timestamp(self):
		code = self.code()

		for word in ("installed", "installation", "creation", "docstatus", "timestamp"):
			with self.subTest(word=word):
				self.assertNotIn(word, code)

	def test_it_imports_nothing_from_the_framework(self):
		"""It is `core`: decisions, with no database and no framework behind them."""
		self.assertNotIn("frappe", self.source())
