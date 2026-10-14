"""The derived identity on an assignment, and the rules that keep it true."""

import dataclasses

from smartprocess_logistics.core.assignment import (
	CODE_IDENTITY_MISMATCH,
	CODE_IDENTITY_SUPPLIED,
	CODE_SERIAL_MISSING,
	DERIVED_FIELDS,
	DIRECT_SQL_LIMITATION,
	RUNTIME_ENFORCEMENT,
	AssignmentIdentityError,
	derive_assignment_values,
	identity_mismatch,
	is_consistent,
	reject_supplied_identity,
)
from smartprocess_logistics.core.normalization import normalize_serial
from smartprocess_logistics.tests.base import UnitTestCase

SERIAL = normalize_serial("AB-0012")


def values(**overrides):
	arguments = {
		"serial": SERIAL,
		"serial_name": "LS-2026-00001",
		"item_code": "ITEM-1",
		"sales_invoice": "IN-260013",
		"sales_invoice_item": "rj9flqllm1",
		"company": "شرکت آزمون",
		"row_index": 1,
	}
	arguments.update(overrides)
	return derive_assignment_values(**arguments)


class TestDerivedValues(UnitTestCase):
	def test_the_identity_comes_from_the_serial(self):
		self.assertEqual(values().normalized_serial, SERIAL.normalized)

	def test_the_identity_is_the_normalized_form_not_the_typed_text(self):
		self.assertEqual(values().normalized_serial, "ab-0012")
		self.assertNotEqual(values().normalized_serial, SERIAL.original)

	def test_every_stored_value_is_present(self):
		built = values()

		self.assertEqual(built.logistics_serial, "LS-2026-00001")
		self.assertEqual(built.item_code, "ITEM-1")
		self.assertEqual(built.sales_invoice, "IN-260013")
		self.assertEqual(built.sales_invoice_item, "rj9flqllm1")
		self.assertEqual(built.company, "شرکت آزمون")
		self.assertEqual(built.invoice_item_idx, 1)

	def test_the_row_index_is_optional(self):
		self.assertIsNone(values(row_index=None).invoice_item_idx)

	def test_a_missing_serial_is_refused(self):
		with self.assertRaises(AssignmentIdentityError) as caught:
			values(serial=None)

		self.assertEqual(caught.exception.code, CODE_SERIAL_MISSING)

	def test_a_missing_string_value_is_refused(self):
		for field in ("serial_name", "item_code", "sales_invoice", "sales_invoice_item", "company"):
			with self.subTest(field=field):
				with self.assertRaises(AssignmentIdentityError) as caught:
					values(**{field: "   "})

				self.assertEqual(caught.exception.code, CODE_SERIAL_MISSING)

	def test_a_bad_row_index_is_refused(self):
		for row_index in (-1, 1.5, "1", True):
			with self.subTest(row_index=row_index):
				with self.assertRaises(AssignmentIdentityError) as caught:
					values(row_index=row_index)

				self.assertEqual(caught.exception.code, CODE_SERIAL_MISSING)

	def test_the_built_values_are_frozen(self):
		# The exact exception, not `Exception`: a blind assert passes for a typo as readily as for
		# a frozen field, and this test is about the field being frozen.
		with self.assertRaises(dataclasses.FrozenInstanceError):
			values().normalized_serial = "other"


class TestSuppliedIdentityIsRefused(UnitTestCase):
	def test_a_payload_carrying_the_identity_is_refused(self):
		with self.assertRaises(AssignmentIdentityError) as caught:
			reject_supplied_identity({"logistics_serial": "LS-1", "normalized_serial": "ab-12"})

		self.assertEqual(caught.exception.code, CODE_IDENTITY_SUPPLIED)

	def test_a_clean_payload_passes(self):
		reject_supplied_identity({"logistics_serial": "LS-1", "sales_invoice": "IN-1"})

	def test_a_non_mapping_payload_is_ignored(self):
		for payload in (None, [], "x", 5):
			with self.subTest(payload=payload):
				reject_supplied_identity(payload)

	def test_every_derived_field_is_refused(self):
		for field in DERIVED_FIELDS:
			with self.subTest(field=field):
				with self.assertRaises(AssignmentIdentityError):
					reject_supplied_identity({field: "anything"})


class TestConsistency(UnitTestCase):
	def test_matching_values_are_consistent(self):
		self.assertIsNone(identity_mismatch("ab-0012", "ab-0012"))
		self.assertTrue(is_consistent("ab-0012", "ab-0012"))

	def test_a_different_value_is_a_mismatch(self):
		reason = identity_mismatch("ab-0012", "ab-0013")

		self.assertEqual(reason, CODE_IDENTITY_MISMATCH)
		self.assertFalse(is_consistent("ab-0012", "ab-0013"))

	def test_a_missing_value_is_a_mismatch(self):
		self.assertEqual(identity_mismatch(None, "ab-0012"), CODE_IDENTITY_MISMATCH)
		self.assertEqual(identity_mismatch("ab-0012", None), CODE_IDENTITY_MISMATCH)


class TestTheContractIsRecorded(UnitTestCase):
	def test_the_runtime_requirements_are_written_down(self):
		# The enforcement lives in a controller this phase does not write, so the requirement
		# has to survive as data until the code exists.
		self.assertGreaterEqual(len(RUNTIME_ENFORCEMENT), 4)
		self.assertTrue(any("deriv" in step or "set" in step for step in RUNTIME_ENFORCEMENT))

	def test_the_direct_sql_limitation_is_written_down(self):
		self.assertIn("INSERT", DIRECT_SQL_LIMITATION)
		self.assertIn("documented", DIRECT_SQL_LIMITATION)
