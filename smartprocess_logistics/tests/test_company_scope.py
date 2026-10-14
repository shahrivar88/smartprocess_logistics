"""The company restriction: one policy, and the paths that have to carry it.

Two subjects, tested two ways. The decision itself (`core.authz`) is pure and is asserted on
directly. The gateway is exercised against a fake runtime that records the filters it passes and
answers from planted tables, so "the export does not contain company B" is asserted on the rows an
operator would receive. The export path used to be covered by assertions about the *text* of the
endpoint and the form script, which is what let an unfiltered read sit in the middle of it.

What these tests cannot show — and what `deployment/lab/verify_company_isolation.py` is for — is
that a real Frappe site with real `User Permission` rows and a real restricted session behaves the
same way. The fake states the policy; the lab proves the runtime.
"""

import importlib
import unittest

from smartprocess_logistics.core import authz, export
from smartprocess_logistics.tests.fake_bench import FakeBench, FakeReadRuntime

SERIAL = "Logistics Serial"
ASSIGNMENT = "Logistics Serial Assignment"
INVOICE = "Sales Invoice"
ITEM = "Item"
TRANSFER = "Logistics Serial Transfer"
TRANSFER_ITEM = "Logistics Serial Transfer Item"


def principal(companies=None, strict=False):
	"""A Manager who is restricted to `companies`, or `None` for a user with no restriction."""
	return authz.Principal(
		user="manager@example.test",
		roles=frozenset({authz.ROLE_MANAGER}),
		companies=companies,
		strict_companies=strict,
	)


def quote(value):
	"""Enough of `frappe.db.escape` to state a condition. The real one is Frappe's business."""
	return "'" + str(value).replace("'", "''") + "'"


def two_companies():
	"""One item, one serial and one invoice per company, both companies selling the same item.

	The shared item is deliberate: it is the case where a filter on the item would *look* like a
	company filter and would not be one.
	"""
	bench = FakeBench()
	bench.add_doctype(
		ASSIGNMENT,
		rows=[
			{
				"name": "LSA-A",
				"logistics_serial": "LS-A",
				"item_code": "ITEM",
				"normalized_serial": "AAA111",
				"sales_invoice": "SINV-A",
				"sales_invoice_item": "SINV-A-1",
				"company": "A",
			},
			{
				"name": "LSA-B",
				"logistics_serial": "LS-B",
				"item_code": "ITEM",
				"normalized_serial": "BBB222",
				"sales_invoice": "SINV-B",
				"sales_invoice_item": "SINV-B-1",
				"company": "B",
			},
		],
	)
	bench.add_doctype(
		SERIAL,
		rows=[
			{"name": "LS-A", "item_code": "ITEM", "normalized_serial": "AAA111", "serial_no": "AAA111"},
			{"name": "LS-B", "item_code": "ITEM", "normalized_serial": "BBB222", "serial_no": "BBB222"},
		],
	)
	bench.add_doctype(INVOICE, rows=[{"name": "SINV-A", "company": "A"}, {"name": "SINV-B", "company": "B"}])
	bench.add_doctype(
		"Sales Invoice Item",
		rows=[
			{"name": "SINV-A-1", "parent": "SINV-A", "qty": 1},
			{"name": "SINV-B-1", "parent": "SINV-B", "qty": 1},
		],
	)
	bench.add_doctype(
		TRANSFER,
		rows=[
			{
				"name": "LST-A",
				"source_invoice": "SINV-A",
				"target_invoice": "SINV-A",
				"company": "A",
				"docstatus": 1,
			},
			{
				"name": "LST-B",
				"source_invoice": "SINV-B",
				"target_invoice": "SINV-B",
				"company": "B",
				"docstatus": 1,
			},
		],
	)
	bench.add_doctype(
		TRANSFER_ITEM,
		rows=[
			{"name": "LST-A-1", "parent": "LST-A", "normalized_serial": "AAA111", "result": "Moved"},
			{"name": "LST-B-1", "parent": "LST-B", "normalized_serial": "BBB222", "result": "Moved"},
		],
	)
	bench.add_doctype("Company", rows=[{"name": "A"}, {"name": "B"}])
	bench.add_doctype(ITEM, rows=[{"name": "ITEM"}])

	return bench


class RequireAnyCompanyTest(unittest.TestCase):
	"""The refusal the site-wide endpoints make before they read anything."""

	def test_a_site_with_no_company_permissions_is_unrestricted(self):
		self.assertTrue(authz.require_any_company(principal(companies=None)))

	def test_a_user_with_a_company_passes(self):
		self.assertTrue(authz.require_any_company(principal(companies=frozenset({"A"}))))

	def test_a_strict_site_with_no_company_for_the_user_is_refused(self):
		with self.assertRaises(authz.AuthorizationError) as caught:
			authz.require_any_company(principal(companies=None, strict=True))

		self.assertEqual(authz.CODE_STRICT_COMPANY_UNSET, caught.exception.code)
		self.assertEqual(authz.REFUSAL_NO_COMPANY, caught.exception.message)

	def test_the_same_sentence_answers_a_document_check(self):
		"""One fact, one sentence: a user who cannot name a company is told the same thing twice."""
		with self.assertRaises(authz.AuthorizationError) as from_company_check:
			authz.require_company(principal(companies=None, strict=True), "A")

		self.assertEqual(authz.REFUSAL_NO_COMPANY, from_company_check.exception.message)


class CompanySqlConditionTest(unittest.TestCase):
	"""The same policy, as SQL, for the queries the framework builds itself."""

	def test_no_restriction_produces_no_condition(self):
		self.assertIsNone(authz.company_sql_condition(principal(companies=None), quote))

	def test_a_strict_user_with_no_company_matches_nothing(self):
		condition = authz.company_sql_condition(principal(companies=None, strict=True), quote)

		self.assertEqual("1 = 0", condition)

	def test_the_companies_are_quoted_and_in_a_fixed_order(self):
		condition = authz.company_sql_condition(
			principal(companies=frozenset({"ب", "A", "C"})), quote, "`tabX`.`company`"
		)

		self.assertEqual("`tabX`.`company` IN ('A', 'C', 'ب')", condition)

	def test_a_quote_in_a_company_name_does_not_end_the_string(self):
		condition = authz.company_sql_condition(principal(companies=frozenset({"O'Brien"})), quote)

		self.assertEqual("company IN ('O''Brien')", condition)


class PrincipalForTest(unittest.TestCase):
	"""The framework seam: what a real session turns into."""

	def setUp(self):
		self.runtime = FakeReadRuntime(two_companies())
		self.runtime.start()
		# `importlib`, like the lifecycle tests: `from ... import module` finds the parent
		# package's *stale* attribute and hands back the module built for the previous test's
		# runtime, which is how a filter test can pass while testing nothing.
		gateway = importlib.import_module("smartprocess_logistics.api.gateway")

		self.gateway = gateway

	def tearDown(self):
		self.runtime.stop()

	def test_a_user_with_company_permissions_is_restricted_to_them(self):
		self.runtime.company_permissions = {"manager@example.test": ["A"]}

		built = self.gateway.principal_for("manager@example.test")

		self.assertEqual(frozenset({"A"}), built.companies)
		self.assertFalse(built.strict_companies)

	def test_a_user_without_them_is_unrestricted_unless_the_site_is_strict(self):
		self.assertIsNone(self.gateway.principal_for("manager@example.test").companies)

		self.runtime.strict_companies = True

		self.assertIsNone(self.gateway.principal_for("manager@example.test").companies)
		self.assertTrue(self.gateway.principal_for("manager@example.test").strict_companies)

	def test_guest_is_not_a_user(self):
		self.assertEqual("", self.gateway.principal_for("Guest").user)
		self.assertIsNone(self.gateway.principal_for("Guest").level())


class ExportScopeTest(unittest.TestCase):
	"""An export carries the caller's companies and nothing else."""

	def setUp(self):
		self.runtime = FakeReadRuntime(two_companies())
		self.runtime.start()
		# `importlib`, like the lifecycle tests: `from ... import module` finds the parent
		# package's *stale* attribute and hands back the module built for the previous test's
		# runtime, which is how a filter test can pass while testing nothing.
		gateway = importlib.import_module("smartprocess_logistics.api.gateway")

		self.gateway = gateway.FrappeGateway()

	def tearDown(self):
		self.runtime.stop()

	def names(self, rows):
		return [row["name"] for row in rows]

	def test_a_restricted_manager_exports_only_their_own_company(self):
		rows = self.gateway.export_rows(principal(companies=frozenset({"A"})))

		self.assertEqual(["LS-A"], self.names(rows["serials"]))
		self.assertEqual(["LSA-A"], self.names(rows["assignments"]))
		self.assertEqual(["LST-A"], self.names(rows["transfers"]))
		self.assertEqual(["LST-A-1"], self.names(rows["transfer_items"]))

	def test_the_other_company_is_never_read(self):
		"""The filter is on the read, not on the result: B's rows are not fetched and dropped."""
		self.gateway.export_rows(principal(companies=frozenset({"A"})))

		self.assertEqual(
			[{"company": ["in", ("A",)]}],
			[call["filters"] for call in self.runtime.calls_to(ASSIGNMENT)],
		)
		self.assertEqual(
			[("LS-A",)],
			[tuple(call["filters"]["name"][1]) for call in self.runtime.calls_to(SERIAL)],
		)
		# The transfer records get the same treatment, and their rows are asked for by the parents
		# that survived the filter rather than read whole and dropped here.
		self.assertEqual(
			[{"company": ["in", ("A",)]}],
			[call["filters"] for call in self.runtime.calls_to(TRANSFER)],
		)
		self.assertEqual(
			[("LST-A",)],
			[tuple(call["filters"]["parent"][1]) for call in self.runtime.calls_to(TRANSFER_ITEM)],
		)

	def test_an_unrestricted_manager_exports_the_whole_site(self):
		rows = self.gateway.export_rows(principal(companies=None))

		self.assertEqual(["LS-A", "LS-B"], self.names(rows["serials"]))
		self.assertEqual(["LST-A", "LST-B"], self.names(rows["transfers"]))
		self.assertEqual(["LST-A-1", "LST-B-1"], self.names(rows["transfer_items"]))
		self.assertEqual(["LSA-A", "LSA-B"], self.names(rows["assignments"]))

		# No read is restricted by company. A filter that *selects* rows is not a restriction —
		# the transfer rows are asked for by their parents, which is how a child table is read —
		# so what is asserted is the thing that matters: no read names a company.
		for call in self.runtime.get_all_calls:
			with self.subTest(doctype=call.get("doctype")):
				self.assertNotIn("company", call.get("filters") or {})

	def test_a_strict_user_with_no_company_exports_nothing(self):
		"""Defence in depth: the endpoint refuses this caller before the gateway is reached."""
		rows = self.gateway.export_rows(principal(companies=None, strict=True))

		self.assertEqual((), rows["serials"])
		self.assertEqual((), rows["assignments"])


class TransferWorldScopeTest(unittest.TestCase):
	"""What a preview is allowed to know, and therefore what it is allowed to say."""

	def setUp(self):
		self.runtime = FakeReadRuntime(two_companies())
		self.runtime.start()
		# `importlib`, like the lifecycle tests: `from ... import module` finds the parent
		# package's *stale* attribute and hands back the module built for the previous test's
		# runtime, which is how a filter test can pass while testing nothing.
		gateway = importlib.import_module("smartprocess_logistics.api.gateway")

		self.gateway = gateway.FrappeGateway()

	def tearDown(self):
		self.runtime.stop()

	def world(self, companies, **names):
		return self.gateway.transfer_world(principal(companies=companies), **names)

	def test_another_company_invoice_is_not_a_world_invoice(self):
		world = self.world(
			frozenset({"A"}),
			item_codes=("ITEM",),
			company_names=("A", "B"),
			invoice_names=("SINV-A", "SINV-B"),
		)

		self.assertEqual({"SINV-A": "A"}, world.invoices)
		self.assertEqual(frozenset({"A"}), world.companies)
		self.assertEqual({("SINV-A", "AAA111"): "LS-A"}, dict(world.assignments_by_identity))

	def test_every_row_it_lists_carries_the_quantity_it_is_allowed(self):
		"""The cap the import obeys is a number the world has to supply, row by row.

		The plan reads a row's capacity from this world and checks nothing when it is absent, so a
		world that lists rows without their quantities is a world whose cap silently does not
		exist. Both come from one read of the invoice's item rows, and this is what says so.
		"""
		world = self.world(
			frozenset({"A"}),
			item_codes=("ITEM",),
			company_names=("A",),
			invoice_names=("SINV-A",),
		)

		self.assertEqual(frozenset({("SINV-A", "SINV-A-1")}), world.invoice_rows)
		self.assertEqual({("SINV-A", "SINV-A-1"): 1}, world.row_capacity)
		self.assertEqual(
			set(world.row_capacity),
			set(world.invoice_rows),
			"every row the plan may place has a quantity, and no row it cannot place has one",
		)

	def test_the_assignment_it_already_holds_is_counted_against_that_quantity(self):
		world = self.world(
			frozenset({"A"}),
			item_codes=("ITEM",),
			company_names=("A",),
			invoice_names=("SINV-A",),
		)

		self.assertEqual({("SINV-A", "SINV-A-1"): 1}, world.row_registered)

	def test_another_company_serial_is_not_an_identity_this_site_has(self):
		world = self.world(frozenset({"A"}), item_codes=("ITEM",), company_names=("A",), invoice_names=())

		self.assertEqual({("ITEM", "AAA111")}, world.serial_identities)

	def test_a_serial_name_is_the_one_thing_that_crosses_a_company_boundary(self):
		"""The sanctioned exception, stated as the two facts it is made of.

		The phase-20 security review asked what `World.serial_names` discloses, and the answer is:
		**that a name is taken, and nothing else.** The identity of that serial — its item and its
		normalized text — is still hidden here, as is the company whose invoice carries it.

		The disclosure is necessary rather than convenient: a `Logistics Serial` name is unique over
		the whole site and cannot be scoped to a company, so a plan that called a taken name new
		would insert it and collide on the primary key — measured on the lab in phase 19, where a
		restricted caller lost the whole request to `DuplicateEntryError` and was told somebody else
		had registered concurrently.
		"""
		world = self.world(
			frozenset({"A"}),
			item_codes=("ITEM",),
			company_names=("A",),
			invoice_names=(),
			serial_names=("LS-A", "LS-B"),
		)

		self.assertEqual(frozenset({"LS-A", "LS-B"}), world.serial_names)
		self.assertEqual(
			{("ITEM", "AAA111")},
			world.serial_identities,
			"the other company's identity is still not an identity this caller's world has",
		)
		self.assertEqual(
			{("ITEM", "AAA111"): "LS-A"},
			dict(world.serials_by_identity),
			"and the only serial it can point an assignment at is its own",
		)

	def test_the_name_read_asks_for_the_name_and_nothing_else(self):
		"""A name is the whole of the disclosure, so the read may not carry an identity beside it.

		`serial_no` and `item_code` on the same read would turn "is this name taken" into "which
		company's item is this", which is the fact the rest of this class exists to withhold.
		"""
		self.world(
			frozenset({"A"}),
			item_codes=("ITEM",),
			company_names=("A",),
			invoice_names=(),
			serial_names=("LS-A", "LS-B"),
		)

		reads = [
			call
			for call in self.runtime.calls_to(SERIAL)
			if call.get("filters", {}).get("name") == ["in", ("LS-A", "LS-B")]
		]

		self.assertEqual(1, len(reads), f"expected one read by name, found {reads}")
		self.assertEqual(["name"], reads[0].get("fields"))

	def test_no_serial_is_ever_read_by_a_name_nobody_asked_about(self):
		"""Two name-filtered reads, and both name their names — no enumeration either way.

		One read answers "which of *my* serials carry this document's identities" and names the
		serials the caller's own assignments reach; the other answers "is this *name* taken" and
		names the names the document carries. A read by a name in neither set would be the gateway
		looking something up on its own, which is what the sanctioned exception is bounded against:
		the API decides the names (`export.world_names`), and what it hands over is a tuple of
		strings, so a read whose filter is anything else cannot come from this path at all.
		"""

		def filtered_reads():
			return [
				call["filters"]["name"][1]
				for call in self.runtime.calls_to(SERIAL)
				if isinstance(call.get("filters", {}).get("name"), list)
			]

		self.world(frozenset({"A"}), item_codes=("ITEM",), company_names=("A",), invoice_names=())
		without_a_document = filtered_reads()

		self.assertTrue(without_a_document, "no read happened at all: this asserts nothing")
		self.assertEqual(
			[("LS-A",)],
			without_a_document,
			"with no document, the only names read are the serials this caller's own assignments reach",
		)

		self.world(
			frozenset({"A"}),
			item_codes=("ITEM",),
			company_names=("A",),
			invoice_names=(),
			serial_names=("LS-A", "LS-B", "LS-C"),
		)
		with_a_document = filtered_reads()

		self.assertIn(
			("LS-A", "LS-B", "LS-C"),
			with_a_document,
			"the names the document gave were not among the names read",
		)
		self.assertEqual(
			{("LS-A",), ("LS-A", "LS-B", "LS-C")},
			set(with_a_document),
			"a read named something that was neither the caller's own serials nor the document's",
		)

	def test_an_unrestricted_manager_still_sees_both_companies(self):
		world = self.world(
			None,
			item_codes=("ITEM",),
			company_names=("A", "B"),
			invoice_names=("SINV-A", "SINV-B"),
		)

		self.assertEqual({("ITEM", "AAA111"), ("ITEM", "BBB222")}, world.serial_identities)
		self.assertEqual({"SINV-A": "A", "SINV-B": "B"}, world.invoices)

	def test_the_plan_refuses_a_file_naming_another_company_invoice(self):
		"""End to end through `core.export.plan`: the preview cannot report on B's invoice."""
		document = export.build(
			[],
			[
				{
					"name": "LSA-B",
					"logistics_serial": "LS-B",
					"item_code": "ITEM",
					"normalized_serial": "BBB222",
					"sales_invoice": "SINV-B",
					"sales_invoice_item": "SINV-B-1",
					"company": "B",
				}
			],
			app_version="0.3.0",
			site="lab",
			exported_by="somebody@example.test",
			exported_on="2026-10-13 00:00:00",
			normalization_version=2,
		)

		plan = export.plan(document, self.world(frozenset({"A"}), **export.world_names(document)))

		self.assertEqual((), plan.assignments)
		self.assertEqual([export.REASON_COMPANY_MISSING], [conflict.reason for conflict in plan.conflicts])


class PermissionConditionTest(unittest.TestCase):
	"""The conditions the framework's own queries use, registered in `hooks.py`."""

	def setUp(self):
		self.runtime = FakeReadRuntime(two_companies())
		self.runtime.start()
		self.permissions = importlib.import_module("smartprocess_logistics.permissions")

	def tearDown(self):
		self.runtime.stop()

	def test_an_unrestricted_user_gets_no_condition(self):
		self.assertEqual("", self.permissions.assignment_query_conditions("Administrator"))
		self.assertEqual("", self.permissions.serial_query_conditions("Administrator"))

	def test_a_restricted_user_gets_a_company_condition(self):
		self.runtime.company_permissions = {"manager@example.test": ["A"]}

		self.assertEqual(
			"`tabLogistics Serial Assignment`.`company` IN ('A')",
			self.permissions.assignment_query_conditions("manager@example.test"),
		)

	def test_serials_are_restricted_through_the_assignments_that_name_them(self):
		"""`Logistics Serial` has no company column, so the condition has to be a subquery.

		The identifiers are *table* names. A condition that named the doctype instead reached
		MariaDB as written and failed with `Table '...Logistics Serial Assignment' doesn't exist` —
		the mistake this assertion exists to keep from coming back, and one the fake runtime cannot
		catch because it never parses the SQL.
		"""
		self.runtime.company_permissions = {"manager@example.test": ["A"]}

		self.assertEqual(
			"`tabLogistics Serial`.`name` in (select `logistics_serial` from "
			"`tabLogistics Serial Assignment` where "
			"`tabLogistics Serial Assignment`.`company` IN ('A'))",
			self.permissions.serial_query_conditions("manager@example.test"),
		)

	def test_a_strict_site_with_no_company_matches_nothing(self):
		self.runtime.strict_companies = True

		self.assertIn("1 = 0", self.permissions.assignment_query_conditions("manager@example.test"))
		self.assertIn("1 = 0", self.permissions.serial_query_conditions("manager@example.test"))

	# The hook contract: Frappe calls the method as `method(user, doctype=...)`
	# (`frappe/model/db_query.py:1159`). A signature that refused the keyword would break every
	# list of these two DocTypes at once, which is not a failure the suite should discover later.
	def test_the_signature_takes_the_doctype_keyword_the_framework_passes(self):
		self.assertEqual(
			"",
			self.permissions.serial_query_conditions("Administrator", doctype=SERIAL),
		)
		self.assertEqual(
			"",
			self.permissions.assignment_query_conditions("Administrator", doctype=ASSIGNMENT),
		)


if __name__ == "__main__":
	unittest.main()
