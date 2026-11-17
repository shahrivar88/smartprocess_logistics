"""The rules `core/serial.py` states for `Logistics Serial`, decided without a database.

The validation that applies them lives in `serial_link.validate_serial`, which needs a live site;
these tests exercise the pure decisions that validator wires together, so the identity rules are
pinned even where no document can be saved. The AST shape of the validator itself is pinned in
`test_serial_link.py`.
"""

from smartprocess_logistics.core.normalization import (
	CODE_EMPTY,
	CODE_HAS_CONTROL,
	CODE_NOT_TEXT,
	NORMALIZATION_VERSION,
	SerialTextError,
)
from smartprocess_logistics.core.serial import (
	CODE_IDENTITY_CHANGED,
	DIRECT_SQL_LIMITATION,
	RUNTIME_ENFORCEMENT,
	SERIAL_IDENTITY_FIELDS,
	changed_identity_fields,
	derive_serial_identity,
)
from smartprocess_logistics.tests.base import UnitTestCase


class TestDeriveSerialIdentity(UnitTestCase):
	def test_the_identity_is_the_normalized_form_not_the_typed_text(self):
		identity = derive_serial_identity("AB-0012")

		self.assertEqual(identity.normalized, "ab-0012")
		self.assertEqual(identity.original, "AB-0012")

	def test_the_identity_carries_the_current_rule_version(self):
		self.assertEqual(derive_serial_identity("AB-0012").version, NORMALIZATION_VERSION)

	def test_an_empty_serial_is_refused(self):
		with self.assertRaises(SerialTextError) as caught:
			derive_serial_identity("   ")

		self.assertEqual(caught.exception.code, CODE_EMPTY)

	def test_a_control_character_is_refused(self):
		with self.assertRaises(SerialTextError) as caught:
			derive_serial_identity("AB\t12")

		self.assertEqual(caught.exception.code, CODE_HAS_CONTROL)

	def test_a_non_text_value_is_refused(self):
		for value in (None, 5, ["AB-1"]):
			with self.subTest(value=value):
				with self.assertRaises(SerialTextError) as caught:
					derive_serial_identity(value)

				self.assertEqual(caught.exception.code, CODE_NOT_TEXT)


class TestChangedIdentityFields(UnitTestCase):
	STORED = {"serial_no": "AB-1", "item_code": "ITEM-1"}

	def test_an_unchanged_row_is_not_changed(self):
		self.assertEqual(changed_identity_fields(self.STORED, dict(self.STORED)), ())

	def test_the_two_identity_fields_are_exactly_serial_no_and_item_code(self):
		self.assertEqual(SERIAL_IDENTITY_FIELDS, ("serial_no", "item_code"))

	def test_a_changed_serial_no_is_reported(self):
		self.assertEqual(
			changed_identity_fields(self.STORED, {"serial_no": "AB-2", "item_code": "ITEM-1"}),
			("serial_no",),
		)

	def test_a_changed_item_code_is_reported(self):
		self.assertEqual(
			changed_identity_fields(self.STORED, {"serial_no": "AB-1", "item_code": "ITEM-2"}),
			("item_code",),
		)

	def test_both_changes_are_reported_in_order(self):
		self.assertEqual(
			changed_identity_fields(self.STORED, {"serial_no": "AB-2", "item_code": "ITEM-2"}),
			("serial_no", "item_code"),
		)

	def test_a_missing_value_and_an_empty_value_are_the_same(self):
		self.assertEqual(
			changed_identity_fields({"serial_no": "", "item_code": "ITEM-1"}, {"serial_no": None, "item_code": "ITEM-1"}),
			(),
		)

	def test_the_derived_fields_are_not_identity_fields(self):
		"""`normalized_serial` is server-owned, so a caller's edit to it is not a changed identity."""
		stored = {**self.STORED, "normalized_serial": "ab-1", "normalization_version": 2}

		self.assertEqual(
			changed_identity_fields(stored, {**self.STORED, "normalized_serial": "tampered"}),
			(),
		)


class TestTheContractIsRecorded(UnitTestCase):
	def test_the_identity_error_code_is_stable(self):
		self.assertEqual(CODE_IDENTITY_CHANGED, "SERIAL_IDENTITY_CHANGED")

	def test_the_runtime_requirements_are_written_down(self):
		self.assertGreaterEqual(len(RUNTIME_ENFORCEMENT), 3)
		self.assertTrue(any("derive" in step for step in RUNTIME_ENFORCEMENT))
		self.assertTrue(any("refuse" in step for step in RUNTIME_ENFORCEMENT))

	def test_the_direct_sql_limitation_is_written_down(self):
		self.assertIn("INSERT", DIRECT_SQL_LIMITATION)
		self.assertIn("UPDATE", DIRECT_SQL_LIMITATION)
