"""Pure tests for the warehouse-aware Item suggestion query."""

from smartprocess_logistics.core import stock_search
from smartprocess_logistics.tests.base import UnitTestCase

TODAY = "2026-10-22"


class TestQuantityText(UnitTestCase):
	def test_whole_numbers_lose_the_decimal_point(self):
		self.assertEqual(stock_search.format_quantity(120), "120")
		self.assertEqual(stock_search.format_quantity(120.0), "120")

	def test_fractions_keep_up_to_three_places_without_trailing_zeros(self):
		self.assertEqual(stock_search.format_quantity(1.5), "1.5")
		self.assertEqual(stock_search.format_quantity("2.250"), "2.25")

	def test_a_missing_or_unusable_value_reads_as_zero(self):
		for value in (None, "", "abc"):
			with self.subTest(value=value):
				self.assertEqual(stock_search.format_quantity(value), "0")

	def test_the_label_names_the_measure(self):
		self.assertEqual(stock_search.stock_label(120, "Nos"), "Stock: 120 Nos")
		self.assertEqual(stock_search.stock_label(1.5, ""), "Stock: 1.5")


class TestPaging(UnitTestCase):
	def test_a_missing_page_is_the_link_default(self):
		self.assertEqual(stock_search.normalize_page(None, None), (0, 10))

	def test_a_negative_start_is_zero(self):
		self.assertEqual(stock_search.normalize_page(-5, 10), (0, 10))

	def test_a_page_longer_than_the_ceiling_is_clamped(self):
		self.assertEqual(stock_search.normalize_page(0, 25_000), (0, stock_search.MAX_PAGE_LENGTH))

	def test_unusable_values_fall_back_to_the_defaults(self):
		self.assertEqual(stock_search.normalize_page("x", "y"), (0, 10))


class TestFilters(UnitTestCase):
	def test_the_documented_string_filters_are_accepted(self):
		self.assertEqual(
			stock_search.filter_values(
				{"warehouse": " Stores - SP ", "company": "Company", "item_code": "ITEM-1"}
			),
			{"warehouse": "Stores - SP", "company": "Company", "item_code": "ITEM-1"},
		)

	def test_missing_and_none_values_mean_not_sent(self):
		for filters in ({}, {"warehouse": None, "company": None, "item_code": None}):
			with self.subTest(filters=filters):
				self.assertEqual(
					stock_search.filter_values(filters),
					{"warehouse": "", "company": "", "item_code": ""},
				)

	def test_a_non_string_filter_is_refused_rather_than_coerced(self):
		for value in (["=", "WH-001"], 7, {"name": "WH-001"}, True):
			with self.subTest(value=value):
				self.assertIsNone(
					stock_search.filter_values({"warehouse": value, "company": "", "item_code": ""})
				)

	def test_each_of_the_three_filters_is_checked(self):
		for key in stock_search.FILTER_KEYS:
			with self.subTest(key=key):
				self.assertIsNone(stock_search.filter_values({key: ["not", "a", "string"]}))

	def test_a_non_dict_payload_is_refused(self):
		for filters in (None, [], "warehouse=Stores", [["Warehouse", "name", "=", "Stores"]]):
			with self.subTest(filters=filters):
				self.assertIsNone(stock_search.filter_values(filters))

	def test_unknown_keys_do_not_change_the_result(self):
		self.assertEqual(
			stock_search.filter_values({"warehouse": "Stores - SP", "unexpected": ["x"]}),
			{"warehouse": "Stores - SP", "company": "", "item_code": ""},
		)


class TestQuery(UnitTestCase):
	def build(self, **overrides):
		arguments = {
			"warehouse": "Stores - SP",
			"txt": "ITEM",
			"start": 0,
			"page_len": 10,
			"today": TODAY,
		}
		arguments.update(overrides)
		return stock_search.build_query(**arguments)

	def test_it_joins_stock_and_requires_a_positive_quantity(self):
		sql, params = self.build()

		self.assertIn("inner join `tabBin`", sql)
		self.assertIn("`tabBin`.`actual_qty` > 0", sql)
		self.assertIn("`tabItem`.`is_stock_item` = 1", sql)
		self.assertEqual(params["warehouse"], "Stores - SP")

	def test_it_searches_code_and_name(self):
		sql, params = self.build(txt="LAMP")

		self.assertIn("`tabItem`.`name` like %(txt)s", sql)
		self.assertIn("`tabItem`.`item_name` like %(txt)s", sql)
		self.assertEqual(params["txt"], "%LAMP%")

	def test_an_empty_search_still_returns_the_first_page(self):
		sql, params = self.build(txt="")

		self.assertEqual(params["txt"], "%")
		self.assertEqual(params["_txt"], "")

	def test_an_exact_item_lookup_does_not_like_match(self):
		sql, params = self.build(txt="", item_code="ITEM-001")

		self.assertIn("`tabItem`.`name` = %(item_code)s", sql)
		self.assertNotIn("like %(txt)s", sql)
		self.assertEqual(params["item_code"], "ITEM-001")

	def test_the_permission_condition_is_appended_unchanged(self):
		sql, _params = self.build(match_condition="and (`tabItem`.`owner` = 'a@b.c')")

		self.assertIn("and (`tabItem`.`owner` = 'a@b.c')", sql)

	def test_the_limit_comes_from_the_clamped_page(self):
		_sql, params = self.build(start=20, page_len=25_000)

		self.assertEqual(params["start"], 20)
		self.assertEqual(params["page_len"], stock_search.MAX_PAGE_LENGTH)


class TestSuggestion(UnitTestCase):
	def test_a_suggestion_is_the_code_then_what_the_dropdown_describes(self):
		row = stock_search.suggestion("ITEM-001", "Lamp", 120, "Nos")

		self.assertEqual(row, ("ITEM-001", "Lamp", "Stock: 120 Nos"))

	def test_a_missing_item_name_does_not_produce_a_blank_part(self):
		row = stock_search.suggestion("ITEM-001", None, 80, "Nos")

		self.assertEqual(row, ("ITEM-001", "", "Stock: 80 Nos"))
