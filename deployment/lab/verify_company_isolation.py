"""Prove the company restriction against a real Frappe site, not a stand-in.

What the database-free suite cannot show is the half of this that belongs to the framework: that
`permission_query_conditions` is *consumed* — that a query Frappe builds itself carries the
condition this app registers — and that a real `User Permission` on `Company` reaches
`core.authz` through a real session. A fake runtime can only state what it was told.

So this script plants a second company in the lab, with one serial and one assignment of its own,
and then asks two questions as two different users:

    as a Manager restricted to the first company    B's rows must not appear anywhere
    as Administrator, who is unrestricted           B's rows must appear, so that "not appearing"
                                                    means something

Both are read-only questions: the export endpoint, the gateway's scoped reads, and the list API
the Desk itself uses. Nothing is submitted, and nothing is written outside `arm`/`disarm`.

    arm      create the second company, its invoice, its serial and its assignment, and a
             restricted Manager — idempotent, so a re-run after a failure continues
    check    the assertions, as each user in turn
    disarm   remove the planted serial, assignment and invoice, leaving no trace in the data the
             other lab script counts. The company and the user are kept: they carry no serial
             data, and keeping them keeps a re-run cheap.
    run      arm, check, disarm — disarm in a `finally`, so a failed check still cleans up

Run it inside the lab's backend container, from the sites directory (Frappe's logger resolves
`../../logs` from the working directory, and the wrong one is a FileNotFoundError):

    docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab \\
      -f deployment/lab/compose.isolation-lab.yaml \\
      exec -T -w /home/frappe/frappe-bench/sites backend \\
      /home/frappe/frappe-bench/env/bin/python \\
      /home/frappe/frappe-bench/apps/smartprocess_logistics/deployment/lab/verify_company_isolation.py run

Read-only where it matters: `check` performs no write to the site's business data.
"""

import sys

import frappe

SITE = "sp_logistics_lab"
SITES_PATH = "/home/frappe/frappe-bench/sites"

HOME_COMPANY = "SP LAB COMPANY"
OTHER_COMPANY = "SP LAB OTHER"
OTHER_ABBR = "SLO"

RESTRICTED_USER = "lab-restricted@example.test"

SERIAL = "Logistics Serial"
ASSIGNMENT = "Logistics Serial Assignment"

PLANTED_SERIAL_NO = "LAB-B-0001"
PLANTED_NORMALIZED = "LABB0001"
PLANTED_INVOICE_REMARK = "planted by verify_company_isolation.py"

MANAGER_ROLE = "Logistics Serial Manager"


class Recorder:
	def __init__(self):
		self.failures = 0
		self.notes = []

	def check(self, condition, description, detail=""):
		print(f"  {'ok  ' if condition else 'FAIL'} {description}")

		if not condition:
			self.failures += 1

			if detail:
				print(f"       {detail}")

		return bool(condition)

	def note(self, text):
		print(f"       {text}")


def connect():
	frappe.init(site=SITE, sites_path=SITES_PATH)
	frappe.connect()


# --- arm ---------------------------------------------------------------------------------


def ensure_other_company(recorder):
	"""The second company, with a chart of accounts — ERPNext's own test mixin does exactly this
	(`erpnext/accounts/test/accounts_mixin.py:70-89`), which is why it is the shape used here."""
	if frappe.db.exists("Company", OTHER_COMPANY):
		recorder.note(f"company {OTHER_COMPANY} already exists")
		return frappe.get_doc("Company", OTHER_COMPANY)

	company = frappe.get_doc(
		{
			"doctype": "Company",
			"company_name": OTHER_COMPANY,
			"abbr": OTHER_ABBR,
			"country": frappe.db.get_value("Company", HOME_COMPANY, "country") or "Iran",
			"default_currency": frappe.db.get_value("Company", HOME_COMPANY, "default_currency"),
			"create_chart_of_accounts_based_on": "Standard Template",
			"chart_of_accounts": "Standard",
		}
	).insert()

	recorder.note(f"created company {company.name} (abbr {company.abbr})")

	return company


def ensure_invoice(company, recorder):
	"""One draft invoice of the second company, carrying no stock and no tax.

	A draft is enough: what the restriction decides on is the invoice's *company*, which is read
	from the stored document, and nothing here needs a ledger effect.
	"""
	existing = frappe.db.get_value(
		"Sales Invoice", {"company": OTHER_COMPANY, "remarks": PLANTED_INVOICE_REMARK}, "name"
	)

	if existing:
		return frappe.get_doc("Sales Invoice", existing)

	source = frappe.get_doc(
		"Sales Invoice", frappe.db.get_value("Sales Invoice", {"company": HOME_COMPANY}, "name")
	)
	item = frappe.db.get_value("Logistics Serial", {"item_code": ("is", "set")}, "item_code")

	invoice = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"company": OTHER_COMPANY,
			"customer": source.customer,
			"posting_date": frappe.utils.nowdate(),
			"debit_to": f"Debtors - {OTHER_ABBR}",
			"remarks": PLANTED_INVOICE_REMARK,
			"items": [
				{
					"item_code": item,
					"qty": 1,
					"rate": 1,
					"income_account": f"Sales - {OTHER_ABBR}",
					"cost_center": company.cost_center,
				}
			],
		}
	).insert()

	recorder.note(f"created a draft invoice {invoice.name} for {OTHER_COMPANY}")

	return invoice


def ensure_data(recorder):
	"""One serial and one assignment of the second company — the rows that must stay invisible."""
	whole = frappe.get_doc("Company", OTHER_COMPANY)
	invoice = ensure_invoice(whole, recorder)
	item = frappe.db.get_value("Logistics Serial", {"item_code": ("is", "set")}, "item_code")

	if not frappe.db.exists(SERIAL, {"serial_no": PLANTED_SERIAL_NO}):
		serial = frappe.get_doc(
			{
				"doctype": SERIAL,
				"serial_no": PLANTED_SERIAL_NO,
				"normalized_serial": PLANTED_NORMALIZED,
				"item_code": item,
			}
		).insert()

		recorder.note(f"created serial {serial.name} ({PLANTED_SERIAL_NO})")

	serial_name = frappe.db.get_value(SERIAL, {"serial_no": PLANTED_SERIAL_NO}, "name")

	if not frappe.db.exists(
		ASSIGNMENT, {"sales_invoice": invoice.name, "normalized_serial": PLANTED_NORMALIZED}
	):
		assignment = frappe.get_doc(
			{
				"doctype": ASSIGNMENT,
				"logistics_serial": serial_name,
				"item_code": item,
				"normalized_serial": PLANTED_NORMALIZED,
				"sales_invoice": invoice.name,
				"sales_invoice_item": invoice.items[0].name,
				"company": OTHER_COMPANY,
			}
		).insert()

		recorder.note(f"created assignment {assignment.name} for {OTHER_COMPANY}")


def ensure_restricted_user(recorder):
	"""A Manager whose only company is the first one — a `User Permission`, the framework's own
	mechanism, so that `core.authz` is fed exactly what it would be fed in production."""
	meta = frappe.get_meta("User Permission")
	fields = {field.fieldname for field in meta.fields}

	if {"user", "allow", "for_value"} - fields:
		raise SystemExit(f"refused: User Permission has no {sorted({'user', 'allow', 'for_value'} - fields)}")

	if not frappe.db.exists("User", RESTRICTED_USER):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": RESTRICTED_USER,
				"first_name": "Lab Restricted",
				"send_welcome_email": 0,
				"roles": [{"role": MANAGER_ROLE}],
			}
		).insert()

		recorder.note(f"created user {RESTRICTED_USER} with role {MANAGER_ROLE}")

	if not frappe.db.exists("User Permission", {"user": RESTRICTED_USER, "allow": "Company"}):
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": RESTRICTED_USER,
				"allow": "Company",
				"for_value": HOME_COMPANY,
			}
		).insert()

		recorder.note(f"restricted {RESTRICTED_USER} to {HOME_COMPANY}")

	frappe.db.commit()


# --- check -------------------------------------------------------------------------------


def exported_names():
	"""What `export_serials` would hand this session's user, as plain names.

	The endpoint function is called rather than the HTTP route so that the session, and therefore
	the user permissions, are the only variable. `frappe.set_user` is what a request would have
	done already.
	"""
	from smartprocess_logistics.api import endpoints

	document = endpoints.export_serials()

	return (
		document["counts"],
		{row["name"] for row in document["document"]["serials"]},
		{row["name"] for row in document["document"]["assignments"]},
	)


def listed(doctype, fields=("name",)):
	"""`frappe.get_list` — the permission-checking read the Desk itself issues."""
	return {row["name"] for row in frappe.get_list(doctype, fields=list(fields), limit_page_length=0)}


def check_as_home_manager(recorder):
	"""The restricted Manager: the other company's rows must be nowhere."""
	frappe.set_user(RESTRICTED_USER)

	counts, serials, assignments = exported_names()
	planted_serial = frappe.db.get_value(SERIAL, {"serial_no": PLANTED_SERIAL_NO}, "name")
	planted_assignment = frappe.db.get_value(
		ASSIGNMENT, {"normalized_serial": PLANTED_NORMALIZED, "company": OTHER_COMPANY}, "name"
	)

	recorder.check(
		planted_serial not in serials and planted_assignment not in assignments,
		f"the export of a Manager restricted to {HOME_COMPANY} holds no {OTHER_COMPANY} row",
		f"counts {counts}; planted {planted_serial}/{planted_assignment}",
	)
	recorder.check(
		counts["serials"] > 0,
		"and it is not empty, so the answer is a restriction and not an unrun export",
		f"counts {counts}",
	)
	recorder.check(
		planted_serial not in listed(SERIAL),
		"the list API the Desk uses shows no {0} serial".format(OTHER_COMPANY),
	)
	recorder.check(
		planted_assignment not in listed(ASSIGNMENT),
		"the list API the Desk uses shows no {0} assignment".format(OTHER_COMPANY),
	)

	frappe.set_user("Administrator")

	return planted_serial, planted_assignment


def check_as_administrator(recorder, planted_serial, planted_assignment):
	"""The unrestricted user: both rows are visible, which is what makes the test above mean
	something. `System Manager` keeps working — the requirement is isolation, not blindness."""
	frappe.set_user("Administrator")

	counts, serials, assignments = exported_names()

	recorder.check(
		planted_serial in serials and planted_assignment in assignments,
		"an unrestricted Administrator exports both companies' rows",
		f"counts {counts}",
	)
	recorder.check(
		planted_serial in listed(SERIAL) and planted_assignment in listed(ASSIGNMENT),
		"and the list API shows them to Administrator",
	)


def check_restore_is_scoped(recorder):
	"""A restricted Manager cannot make the preview report on the other company either.

	The preview is where an unscoped read would speak: it answers "this invoice already carries
	this serial" per row. Here the same file is planned twice, once for each user, and the
	restricted plan must not have the other company's row among the things it would create.
	"""
	from smartprocess_logistics.api import gateway
	from smartprocess_logistics.core import export

	invoice = frappe.db.get_value(
		"Sales Invoice", {"company": OTHER_COMPANY, "remarks": PLANTED_INVOICE_REMARK}, "name"
	)
	serial = frappe.db.get_value(SERIAL, {"serial_no": PLANTED_SERIAL_NO}, "name")

	document = export.build(
		[],
		[
			{
				"name": "PLANTED-ASSIGNMENT",
				"logistics_serial": serial,
				"item_code": frappe.db.get_value(SERIAL, serial, "item_code"),
				"normalized_serial": PLANTED_NORMALIZED,
				"sales_invoice": invoice,
				"sales_invoice_item": "PLANTED-ROW",
				"company": OTHER_COMPANY,
			}
		],
		app_version="0.3.0",
		site=SITE,
		exported_by="verify_company_isolation.py",
		exported_on=frappe.utils.now(),
		normalization_version=frappe.db.get_single_value("Logistics Settings", "normalization_version") or 1,
	)

	frappe.set_user(RESTRICTED_USER)
	restricted = export.plan(
		document,
		gateway.FrappeGateway().transfer_world(
			gateway.principal_for(RESTRICTED_USER), **export.world_names(document)
		),
	)

	frappe.set_user("Administrator")
	unrestricted = export.plan(
		document,
		gateway.FrappeGateway().transfer_world(
			gateway.principal_for("Administrator"), **export.world_names(document)
		),
	)

	recorder.check(
		not restricted.assignments,
		"the restricted preview would create nothing for the other company's invoice",
		f"assignments {len(restricted.assignments)}, conflicts {[c.reason for c in restricted.conflicts]}",
	)
	recorder.check(
		bool(restricted.conflicts),
		"and it says so as a conflict rather than silently dropping the row",
		f"{[c.reason for c in restricted.conflicts]}",
	)
	recorder.check(
		not unrestricted.conflicts,
		"the same row is placeable for Administrator, so the difference is the restriction",
		f"conflicts {[c.reason for c in unrestricted.conflicts]}",
	)


def check(recorder):
	planted_serial, planted_assignment = check_as_home_manager(recorder)
	check_as_administrator(recorder, planted_serial, planted_assignment)
	check_restore_is_scoped(recorder)


# --- disarm -----------------------------------------------------------------------------


def disarm(recorder):
	"""Remove the planted rows, and only those.

	The serial and the assignment would change what the other lab script counts, and the invoice
	is theirs. The company and the user stay: they hold no serial data, the other script never
	looks at them, and re-creating a chart of accounts on every run would be the expensive part.
	"""
	frappe.set_user("Administrator")

	for name in frappe.get_all(
		ASSIGNMENT, filters={"normalized_serial": PLANTED_NORMALIZED, "company": OTHER_COMPANY}, pluck="name"
	):
		frappe.delete_doc(ASSIGNMENT, name, force=True)
		recorder.note(f"removed assignment {name}")

	for name in frappe.get_all(SERIAL, filters={"serial_no": PLANTED_SERIAL_NO}, pluck="name"):
		frappe.delete_doc(SERIAL, name, force=True)
		recorder.note(f"removed serial {name}")

	for name in frappe.get_all(
		"Sales Invoice",
		filters={"company": OTHER_COMPANY, "remarks": PLANTED_INVOICE_REMARK, "docstatus": 0},
		pluck="name",
	):
		frappe.delete_doc("Sales Invoice", name, force=True)
		recorder.note(f"removed draft invoice {name}")

	frappe.db.commit()


# --- entry point ------------------------------------------------------------------------


def main(argv):
	phase = (argv[1] if len(argv) > 1 else "run").lower()
	recorder = Recorder()

	if phase not in ("arm", "check", "disarm", "run"):
		print(f"unknown phase {phase!r}: expected arm, check, disarm or run")

		return 2

	connect()

	print(f"site under test: {frappe.local.site}")
	print(f"companies: {HOME_COMPANY} (restricted to) and {OTHER_COMPANY} (must stay hidden)\n")

	if phase in ("arm", "run"):
		print("arm")
		ensure_other_company(recorder)
		ensure_data(recorder)
		ensure_restricted_user(recorder)
		print()

	try:
		if phase in ("check", "run"):
			print("check")
			check(recorder)
			print()
	finally:
		if phase in ("disarm", "run"):
			print("disarm")
			disarm(recorder)
			print()

	print("PASS" if not recorder.failures else f"FAIL ({recorder.failures} checks)")

	return 1 if recorder.failures else 0


if __name__ == "__main__":
	raise SystemExit(main(sys.argv))
