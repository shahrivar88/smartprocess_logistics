"""Pure tests for the configurable inventory policy parser."""

from smartprocess_logistics.core import inventory, policies
from smartprocess_logistics.tests.base import UnitTestCase


class TestPolicyDefaults(UnitTestCase):
	def test_missing_fields_keep_the_approved_defaults(self):
		policy = policies.policy_from_values({}, set())

		self.assertEqual(policy, policies.DEFAULT_POLICY)
		self.assertEqual(policy.allocation_priority, inventory.ORDER_THEN_DELIVERY)
		self.assertTrue(policy.virtual_stock_covers_procurement)
		self.assertTrue(policy.recommended_purchase_net_of_open_po)

	def test_an_unknown_allocation_value_falls_back(self):
		policy = policies.policy_from_values(
			{policies.ALLOCATION_FIELD: "not-a-strategy"},
			{policies.ALLOCATION_FIELD},
		)

		self.assertEqual(policy.allocation_priority, inventory.ORDER_THEN_DELIVERY)


class TestSavedFalse(UnitTestCase):
	def test_explicit_false_is_not_replaced_by_the_default(self):
		policy = policies.policy_from_values(
			{
				policies.VIRTUAL_STOCK_FIELD: 0,
				policies.NET_OPEN_PO_FIELD: 0,
			},
			{policies.VIRTUAL_STOCK_FIELD, policies.NET_OPEN_PO_FIELD},
		)

		self.assertFalse(policy.virtual_stock_covers_procurement)
		self.assertFalse(policy.recommended_purchase_net_of_open_po)

	def test_string_zero_and_one_are_parsed_as_checks(self):
		policy = policies.policy_from_values(
			{policies.VIRTUAL_STOCK_FIELD: "0", policies.NET_OPEN_PO_FIELD: "1"},
			{policies.VIRTUAL_STOCK_FIELD, policies.NET_OPEN_PO_FIELD},
		)

		self.assertFalse(policy.virtual_stock_covers_procurement)
		self.assertTrue(policy.recommended_purchase_net_of_open_po)


class TestAllocationStrategies(UnitTestCase):
	def test_each_supported_strategy_is_kept(self):
		for strategy in inventory.ALLOCATION_STRATEGIES:
			with self.subTest(strategy=strategy):
				policy = policies.policy_from_values(
					{policies.ALLOCATION_FIELD: strategy},
					{policies.ALLOCATION_FIELD},
				)
				self.assertEqual(policy.allocation_priority, strategy)

	def test_a_later_value_changes_the_policy(self):
		first = policies.policy_from_values(
			{policies.ALLOCATION_FIELD: inventory.OLDEST_ORDER},
			{policies.ALLOCATION_FIELD},
		)
		second = policies.policy_from_values(
			{policies.ALLOCATION_FIELD: inventory.EARLIEST_DELIVERY},
			{policies.ALLOCATION_FIELD},
		)

		self.assertNotEqual(first, second)
		self.assertEqual(second.allocation_priority, inventory.EARLIEST_DELIVERY)
