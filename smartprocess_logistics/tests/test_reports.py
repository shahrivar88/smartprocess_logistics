"""Report query construction, checked without a database.

A fake cannot run the SQL, so what these tests establish is narrower and worth stating: the
statement is parameterized, the parameters carry the values that were asked for, the fragments
that belong in the statement are there, and the ones that must not be are not. Whether MariaDB
returns the right rows for the statement is a database test and stays on the list of what is
unverified.

Two exceptions are real checks rather than structural ones: the requirement formula is compared
against the tested Python function, and the ordering is checked for totality, because a
non-total order makes pagination repeat or skip rows whatever the database does.
"""

from datetime import datetime

from smartprocess_logistics.core.authz import ROLE_VIEWER, Principal
from smartprocess_logistics.core.quantities import required_units
from smartprocess_logistics.core.reports import (
	CODE_BAD_DATE_RANGE,
	CODE_BAD_PAGE,
	CODE_NOT_INSTALLED,
	CODE_NO_CONDITION,
	CODE_UNSAFE_CONDITION,
	DEFAULT_PAGE_LENGTH,
	DOCSTATUS_CANCELLED,
	DOCSTATUS_DRAFT,
	DOCSTATUS_SUBMITTED,
	MAX_PAGE_LENGTH,
	MISSING_COLUMNS,
	REGISTER_COLUMNS,
	REQUIRED_UNITS_SQL,
	MissingFilters,
	Query,
	RegisterFilters,
	ReportError,
	build_missing_query,
	build_register_query,
	check_permission_conditions,
	describe_reports,
	missing_report_available,
	required_units_sql,
)
from smartprocess_logistics.tests.base import UnitTestCase

INSTALLED = datetime(2026, 10, 12, 18, 30)

ANYONE = Principal(user="viewer@example.com", roles=frozenset({ROLE_VIEWER}))
ALPHA_VIEWER = Principal(
	user="alpha@example.com",
	roles=frozenset({ROLE_VIEWER}),
	companies=frozenset({"Alpha"}),
)
TWO_COMPANIES = Principal(
	user="two@example.com",
	roles=frozenset({ROLE_VIEWER}),
	companies=frozenset({"Alpha", "Beta"}),
)
NOWHERE = Principal(
	user="nowhere@example.com",
	roles=frozenset({ROLE_VIEWER}),
	companies=None,
	strict_companies=True,
)

PERMISSION = ("`tabSales Invoice`.`owner` = 'viewer@example.com'",)


def register(
	*,
	filters=None,
	principal=ANYONE,
	permission_conditions=PERMISSION,
):
	return build_register_query(
		RegisterFilters(**(filters or {})),
		principal,
		permission_conditions=permission_conditions,
	)


def missing(
	*,
	filters=None,
	principal=ANYONE,
	installed_on=INSTALLED,
	permission_conditions=PERMISSION,
):
	return build_missing_query(
		MissingFilters(**(filters or {})),
		principal,
		installed_on=installed_on,
		permission_conditions=permission_conditions,
	)


class TestParameterization(UnitTestCase):
	def test_the_register_query_carries_its_values_as_parameters(self):
		query = register(filters={"customer": "CUST-1", "item_code": "ITEM-1"})

		self.assertIn("%(customer)s", query.sql)
		self.assertIn("%(item_code)s", query.sql)
		self.assertEqual(query.params["customer"], "CUST-1")
		self.assertEqual(query.params["item_code"], "ITEM-1")

	def test_no_filter_value_appears_in_the_statement(self):
		# A value that reached the statement would show up here. This is the test that fails if
		# somebody ever builds the query with an f-string.
		query = register(
			filters={
				"customer": "CUST-INJECT",
				"item_code": "ITEM-INJECT",
				"serial_no": "SERIAL-INJECT",
				"sales_invoice": "IN-INJECT",
			}
		)

		for value in ("CUST-INJECT", "ITEM-INJECT", "SERIAL-INJECT", "IN-INJECT"):
			with self.subTest(value=value):
				self.assertNotIn(value, query.sql)

	def test_a_quote_in_a_value_does_not_reach_the_statement(self):
		query = register(filters={"customer": "CUST' OR 1=1 --"})
		register_missing = missing(filters={"customer": "CUST' OR 1=1 --"})

		for query in (query, register_missing):
			with self.subTest(query=query.sql[:20]):
				self.assertNotIn("OR 1=1", query.sql)

	def test_the_missing_query_carries_its_values_as_parameters(self):
		query = missing(filters={"item_code": "ITEM-1", "from_date": "2026-10-01"})

		self.assertEqual(query.params["item_code"], "ITEM-1")
		self.assertEqual(query.params["from_date"], "2026-10-01")
		self.assertNotIn("ITEM-1", query.sql)

	def test_the_installation_timestamp_is_a_parameter(self):
		query = missing()

		self.assertIn("%(installed_on)s", query.sql)
		self.assertEqual(query.params["installed_on"], INSTALLED)
		self.assertNotIn("2026-10-12", query.sql)

	def test_the_company_list_becomes_one_placeholder_per_company(self):
		query = register(principal=TWO_COMPANIES)

		self.assertIn("%(company_0)s", query.sql)
		self.assertIn("%(company_1)s", query.sql)
		self.assertEqual({query.params["company_0"], query.params["company_1"]}, {"Alpha", "Beta"})

	def test_the_company_placeholders_are_ordered_so_the_statement_is_reproducible(self):
		first = register(principal=TWO_COMPANIES)
		second = register(principal=TWO_COMPANIES)

		self.assertEqual(first.sql, second.sql)
		self.assertEqual(first.params["company_0"], "Alpha")

	def test_a_like_search_escapes_what_the_operator_typed(self):
		query = register(filters={"serial_no": "AB_1%"})

		self.assertIn("ESCAPE", query.sql)
		self.assertEqual(query.params["serial_no"], "%AB\\_1\\%%")

	def test_a_backslash_in_a_like_search_is_escaped_too(self):
		self.assertEqual(register(filters={"serial_no": "A\\B"}).params["serial_no"], "%A\\\\B%")

	def test_every_query_is_one_statement(self):
		self.assertTrue(register().is_single_statement())
		self.assertTrue(missing().is_single_statement())

	def test_the_register_report_is_a_single_select(self):
		self.assertEqual(register().select_count(), 1)

	def test_the_missing_report_uses_one_subquery_and_not_a_query_per_row(self):
		# Two SELECTs: the report and the grouped count. More would mean an N+1.
		self.assertEqual(missing().select_count(), 2)
		self.assertIn("GROUP BY", missing().sql)


class TestCompanyIsolation(UnitTestCase):
	def test_an_unrestricted_principal_gets_no_company_condition(self):
		query = register()

		self.assertNotIn("company_0", query.sql)
		self.assertNotIn("1 = 0", query.sql)

	def test_a_restricted_principal_gets_its_companies(self):
		query = register(principal=ALPHA_VIEWER)

		self.assertIn("si.company IN", query.sql)
		self.assertEqual(query.params["company_0"], "Alpha")

	def test_a_principal_with_no_companies_matches_nothing(self):
		query = register(principal=NOWHERE)

		self.assertIn("1 = 0", query.sql)

	def test_the_missing_report_is_restricted_the_same_way(self):
		self.assertIn("si.company IN", missing(principal=ALPHA_VIEWER).sql)
		self.assertIn("1 = 0", missing(principal=NOWHERE).sql)

	def test_the_company_condition_uses_the_invoice_alias(self):
		self.assertIn("si.company", register(principal=ALPHA_VIEWER).sql)


class TestInvoicePermissions(UnitTestCase):
	def test_the_condition_is_required(self):
		with self.assertRaises(ReportError) as caught:
			build_register_query(RegisterFilters(), ANYONE, permission_conditions=None)

		self.assertEqual(caught.exception.code, CODE_NO_CONDITION)

	def test_an_empty_condition_set_is_allowed_when_it_is_deliberate(self):
		self.assertTrue(register(permission_conditions=()).is_single_statement())

	def test_the_framework_condition_reaches_the_statement(self):
		query = register()

		self.assertIn("`tabSales Invoice`.`owner` = 'viewer@example.com'", query.sql)

	def test_an_empty_fragment_is_refused(self):
		with self.assertRaises(ReportError) as caught:
			register(permission_conditions=("",))

		self.assertEqual(caught.exception.code, CODE_UNSAFE_CONDITION)

	def test_a_statement_separator_in_a_fragment_is_refused(self):
		for fragment in ("1=1; DROP TABLE x", "1=1 --", "1=1 /*", "*/ 1=1"):
			with self.subTest(fragment=fragment):
				with self.assertRaises(ReportError) as caught:
					register(permission_conditions=(fragment,))

				self.assertEqual(caught.exception.code, CODE_UNSAFE_CONDITION)

	def test_a_non_string_fragment_is_refused(self):
		with self.assertRaises(ReportError):
			check_permission_conditions((None,))

	def test_a_missing_report_also_requires_conditions(self):
		with self.assertRaises(ReportError):
			build_missing_query(MissingFilters(), ANYONE, installed_on=INSTALLED, permission_conditions=None)


class TestPaging(UnitTestCase):
	def test_the_default_page(self):
		query = register()

		self.assertEqual(query.limit, DEFAULT_PAGE_LENGTH)
		self.assertEqual(query.offset, 0)

	def test_a_page_is_bounded(self):
		for length in (0, -1, MAX_PAGE_LENGTH + 1):
			with self.subTest(length=length):
				with self.assertRaises(ReportError) as caught:
					register(filters={"page_length": length})

				self.assertEqual(caught.exception.code, CODE_BAD_PAGE)

	def test_the_largest_page_is_allowed(self):
		self.assertEqual(register(filters={"page_length": MAX_PAGE_LENGTH}).limit, MAX_PAGE_LENGTH)

	def test_a_negative_offset_is_refused(self):
		with self.assertRaises(ReportError) as caught:
			missing(filters={"offset": -1})

		self.assertEqual(caught.exception.code, CODE_BAD_PAGE)

	def test_the_page_is_a_parameter(self):
		query = register(filters={"page_length": 25, "offset": 50})

		self.assertEqual((query.params["limit"], query.params["offset"]), (25, 50))
		self.assertIn("%(limit)s", query.sql)
		self.assertIn("%(offset)s", query.sql)

	def test_the_missing_report_pages_the_same_way(self):
		query = missing(filters={"page_length": 10, "offset": 30})

		self.assertEqual((query.limit, query.offset), (10, 30))


class TestInstallationTimestamp(UnitTestCase):
	def test_the_report_is_unavailable_without_a_timestamp(self):
		for value in (None, "", 0):
			with self.subTest(value=value):
				self.assertFalse(missing_report_available(value))

	def test_the_report_is_unavailable_and_says_so(self):
		with self.assertRaises(ReportError) as caught:
			missing(installed_on=None)

		self.assertEqual(caught.exception.code, CODE_NOT_INSTALLED)

	def test_a_timestamp_makes_the_report_available(self):
		self.assertTrue(missing_report_available(INSTALLED))

	def test_the_eligibility_rule_is_in_the_statement(self):
		self.assertIn("si.creation >= %(installed_on)s", missing().sql)

	def test_the_register_report_needs_no_timestamp(self):
		self.assertTrue(register().is_single_statement())


class TestDocumentStatus(UnitTestCase):
	def test_the_missing_report_excludes_cancelled_by_default(self):
		query = missing()

		self.assertIn("si.docstatus <> 2", query.sql)

	def test_cancelled_can_be_included_explicitly(self):
		query = missing(filters={"include_cancelled": True})

		self.assertNotIn("si.docstatus <> 2", query.sql)

	def test_the_register_report_lists_cancelled_rows_by_default(self):
		# A cancelled invoice's registrations stay viewable and editable, so the register shows
		# them unless the caller asks otherwise.
		query = register()

		self.assertNotIn("si.docstatus <> 2", query.sql)

	def test_the_register_report_can_exclude_them(self):
		query = register(filters={"include_cancelled": False})

		self.assertIn("si.docstatus <> 2", query.sql)

	def test_an_explicit_status_list_is_parameterized(self):
		query = missing(filters={"docstatuses": (DOCSTATUS_DRAFT, DOCSTATUS_SUBMITTED)})

		self.assertIn("si.docstatus IN", query.sql)
		self.assertEqual({query.params["docstatus_0"], query.params["docstatus_1"]}, {0, 1})

	def test_an_unknown_status_is_refused(self):
		for value in (3, -1, "1", None, True):
			with self.subTest(value=value):
				with self.assertRaises(ReportError):
					missing(filters={"docstatuses": (value,)})

	def test_cancelled_can_be_asked_for_by_name(self):
		query = missing(filters={"docstatuses": (DOCSTATUS_CANCELLED,)})

		self.assertEqual(query.params["docstatus_0"], DOCSTATUS_CANCELLED)


class TestDateRange(UnitTestCase):
	def test_there_is_no_default_window(self):
		query = missing()

		self.assertNotIn("posting_date >=", query.sql)
		self.assertNotIn("posting_date <=", query.sql)
		self.assertNotIn("from_date", query.params)

	def test_a_from_date_only(self):
		query = register(filters={"from_date": "2026-01-01"})

		self.assertIn("si.posting_date >= %(from_date)s", query.sql)
		self.assertEqual(query.params["from_date"], "2026-01-01")

	def test_a_to_date_only(self):
		self.assertIn("si.posting_date <= %(to_date)s", register(filters={"to_date": "2026-12-31"}).sql)

	def test_both_ends(self):
		query = register(filters={"from_date": "2026-01-01", "to_date": "2026-12-31"})

		self.assertIn("si.posting_date BETWEEN %(from_date)s AND %(to_date)s", query.sql)

	def test_a_reversed_range_is_refused(self):
		with self.assertRaises(ReportError) as caught:
			register(filters={"from_date": "2026-12-31", "to_date": "2026-01-01"})

		self.assertEqual(caught.exception.code, CODE_BAD_DATE_RANGE)

	def test_the_missing_report_uses_the_same_date_filter(self):
		self.assertIn(
			"si.posting_date BETWEEN",
			missing(filters={"from_date": "2026-01-01", "to_date": "2026-12-31"}).sql,
		)


class TestRequirementFormula(UnitTestCase):
	def test_the_sql_formula_is_the_documented_one(self):
		self.assertEqual(required_units_sql(), REQUIRED_UNITS_SQL)

	def test_the_formula_agrees_with_the_tested_python_function(self):
		# The statement computes the requirement in SQL and the dialog computes it in Python.
		# Both have to answer the same, so the formula is evaluated here the way SQL would and
		# compared with the function the rest of the app uses.
		import math

		for quantity in (0, 1, 2, 2.5, 3, 0.1, -2, -2.5, 10, 1.999, 2.0000000001):
			with self.subTest(quantity=quantity):
				as_sql_would = math.ceil(max(abs(quantity) - 0.000000001, 0))
				self.assertEqual(as_sql_would, required_units(quantity))

	def test_the_formula_is_in_the_select_list(self):
		self.assertIn("AS required_units", missing().sql)

	def test_an_incomplete_filter_uses_the_formula(self):
		self.assertIn("COALESCE(a.registered, 0) < CEIL(", missing().sql)

	def test_the_complete_rows_can_be_asked_for(self):
		self.assertNotIn("COALESCE(a.registered, 0) < CEIL(", missing(filters={"only_incomplete": False}).sql)


class TestGrainAndColumns(UnitTestCase):
	def test_the_register_lists_assignments(self):
		self.assertIn("FROM `tabLogistics Serial Assignment` lsa", register().sql)

	def test_the_register_marks_an_orphaned_assignment(self):
		self.assertIn("is_orphaned", register().sql)
		self.assertIn("is_orphaned", REGISTER_COLUMNS)

	def test_the_register_joins_the_serial_for_its_number(self):
		self.assertIn("JOIN `tabLogistics Serial` ls", register().sql)

	def test_the_missing_report_lists_invoice_rows_that_require_a_serial(self):
		query = missing().sql

		self.assertIn("FROM `tabSales Invoice Item` sii", query)
		self.assertIn("JOIN `tabItem` i", query)
		self.assertIn("i.sp_serial_required = 1", query)

	def test_the_registered_count_comes_from_a_grouped_subquery(self):
		query = missing().sql

		self.assertIn("COUNT(*) AS registered", query)
		self.assertIn("GROUP BY sales_invoice, sales_invoice_item", query)
		self.assertIn("a.sales_invoice_item = sii.name", query)

	def test_the_register_keeps_an_orphan_row_visible(self):
		# A LEFT JOIN on the item row, so a deleted row leaves the assignment in the report
		# rather than dropping it.
		self.assertIn("LEFT JOIN `tabSales Invoice Item` sii", register().sql)

	def test_both_reports_have_distinct_grains(self):
		described = describe_reports()

		self.assertIn("assignment", described["sales_serial_register"]["grain"])
		self.assertIn("row", described["missing_sales_serials"]["grain"])

	def test_the_column_lists_match_the_statements(self):
		for column in REGISTER_COLUMNS:
			with self.subTest(column=column):
				self.assertIn(f"AS {column}", register().sql)

		for column in MISSING_COLUMNS:
			with self.subTest(column=column):
				self.assertIn(f"AS {column}", missing().sql)


class TestOrdering(UnitTestCase):
	def test_the_register_orders_by_a_total_key(self):
		# Pagination repeats or skips rows when the order is not total, whatever the database
		# does with the rows themselves.
		order = register().sql.split("ORDER BY", 1)[1].split("LIMIT", 1)[0]

		self.assertIn("si.posting_date DESC", order)
		self.assertIn("si.name ASC", order)
		self.assertIn("lsa.sales_invoice_item ASC", order)
		self.assertIn("lsa.name ASC", order)

	def test_the_missing_report_orders_by_a_total_key(self):
		order = missing().sql.split("ORDER BY", 1)[1].split("LIMIT", 1)[0]

		self.assertIn("si.posting_date DESC", order)
		self.assertIn("si.name ASC", order)
		self.assertIn("sii.idx ASC", order)

	def test_the_statement_is_reproducible(self):
		self.assertEqual(register().sql, register().sql)
		self.assertEqual(missing().sql, missing().sql)


class TestQueryValue(UnitTestCase):
	def test_a_query_carries_no_connection(self):
		query = register()

		self.assertIsInstance(query, Query)
		self.assertNotIn("execute", dir(query))
		self.assertNotIn("commit", dir(query))

	def test_the_parameters_are_plain_values(self):
		query = missing(filters={"item_code": "ITEM-1"})

		for key, value in query.params.items():
			with self.subTest(key=key):
				self.assertIsInstance(value, (str, int, float, datetime, tuple, type(None)))
