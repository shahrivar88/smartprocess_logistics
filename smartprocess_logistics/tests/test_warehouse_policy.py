"""Pure tests for virtual-warehouse classification and movement rules."""

from smartprocess_logistics.tests.base import UnitTestCase
from smartprocess_logistics.warehouse import policy

A = "Virtual A"


class TestClassification(UnitTestCase):
	def test_the_standard_virtual_type_names_are_recognized(self):
		for warehouse_type in ("Virtual", "مجازی", "Transit"):
			with self.subTest(warehouse_type=warehouse_type):
				self.assertTrue(policy.is_virtual_type(warehouse_type))

	def test_a_blank_or_other_type_is_physical(self):
		for warehouse_type in (None, "", "Stores", "Finished Goods"):
			with self.subTest(warehouse_type=warehouse_type):
				self.assertFalse(policy.is_virtual_type(warehouse_type))

	def test_the_explicit_physical_type_is_physical(self):
		"""The type this app ships alongside the empty one; both keep stock sellable."""
		for warehouse_type in ("Physical", "فیزیکی"):
			with self.subTest(warehouse_type=warehouse_type):
				self.assertFalse(policy.is_virtual_type(warehouse_type))

	def test_the_shipped_physical_type_is_not_in_the_virtual_list(self):
		self.assertNotIn("Physical", policy.DEFAULT_VIRTUAL_TYPES)
		self.assertNotIn("فیزیکی", policy.DEFAULT_VIRTUAL_TYPES)

	def test_a_site_override_can_name_its_own_types(self):
		self.assertTrue(policy.is_virtual_type("SP Virtual", ("SP Virtual",)))
		self.assertFalse(policy.is_virtual_type("Virtual", ("SP Virtual",)))


class TestStockEntry(UnitTestCase):
	def test_any_transfer_between_virtual_and_physical_is_allowed(self):
		for source_virtual, target_virtual in (
			(True, True),
			(True, False),
			(False, True),
			(False, False),
		):
			with self.subTest(source_virtual=source_virtual, target_virtual=target_virtual):
				self.assertIsNone(
					policy.stock_entry_problem("Material Transfer", source_virtual, target_virtual, 1)
				)

	def test_a_non_transfer_touching_a_virtual_warehouse_is_refused(self):
		for source_virtual, target_virtual in ((True, False), (False, True), (True, True)):
			with self.subTest(source_virtual=source_virtual, target_virtual=target_virtual):
				problem = policy.stock_entry_problem("Material Issue", source_virtual, target_virtual, 2)

				self.assertEqual(problem.code, "VIRTUAL_MATERIAL_TRANSFER_ONLY")
				self.assertIn("ردیف 2", problem.message)

	def test_a_physical_only_movement_is_left_to_the_standard_controller(self):
		self.assertIsNone(policy.stock_entry_problem("Material Issue", False, False, 1))


class TestSales(UnitTestCase):
	def test_a_virtual_warehouse_is_never_a_sales_source(self):
		problem = policy.sales_source_problem(A, True, 3)

		self.assertEqual(problem.code, "VIRTUAL_NOT_SELLABLE")
		self.assertIn(A, problem.message)
		self.assertIn("انتقال مواد", problem.message)

	def test_a_physical_warehouse_is_allowed(self):
		self.assertIsNone(policy.sales_source_problem("Stores", False, 1))
