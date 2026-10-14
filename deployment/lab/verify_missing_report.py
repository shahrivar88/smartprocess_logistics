"""The Missing Sales Serials report, executed against a real site.

The database-free suite pins the *statement*: that the registrations are LEFT joined from one
grouped subquery, that the count is read through `COALESCE`, that the grain is the invoice item
row, that no date is an implicit filter. Whether MariaDB returns the right rows for that statement
is this script's question, and it answers it with an invoice whose rows cover the cases at once:

    three rows of a **marked** item, quantities 3, 2 and 1 — none registered, partly registered,
    fully registered — and one row of an **unmarked** item, which may be registered but can never
    be short.

It also asserts the two things the rule change was for:

    **Historical coverage.** This site's own two invoices were created *before* the app's
    `installed_on`, and they are in the report like any other. Nothing is written to make that
    true — this script changes no setting in order to arrange it.

    **Nothing can take them out again.** `installed_on` is moved to the future, the report is read
    again, and the rows must be identical. That is the upgrade, reinstall, restore and
    move-to-another-server case in one line, and the value is put back before the script ends.

Two rules are *not* exercised on real data, and are stated rather than asserted: cancelled
invoices and returns. Both need a submitted document, and therefore a ledger this site's tests
should not be writing; they are covered at the statement level in `tests/test_reports.py` and by
`core.eligibility`'s own tests.

Run it in the lab's backend container, from the sites directory:

    docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab \\
      -f deployment/lab/compose.isolation-lab.yaml \\
      exec -T -w /home/frappe/frappe-bench/sites backend \\
      /home/frappe/frappe-bench/env/bin/python \\
      /home/frappe/frappe-bench/apps/smartprocess_logistics/deployment/lab/verify_missing_report.py run
"""

import json
import sys

import frappe

SITE = "sp_logistics_lab"
SITES_PATH = "/home/frappe/frappe-bench/sites"

COMPANY = "SP LAB COMPANY"
PLANTED_REMARK = "verify_missing_report draft"
OPTIONAL_ITEM = "SP-LAB-OPTIONAL-ITEM"
LABEL = "SCAN-RPT-"

# Required quantities for the marked item's rows, and how many labels each already carries: the
# first has none, the second is short, the third is complete.
MANDATORY_QUANTITIES = (3, 2, 1)
MANDATORY_REGISTERED = (0, 1, 1)
OPTIONAL_QUANTITY = 2
OPTIONAL_REGISTERED = 1

REPORT = "smartprocess_logistics.smartprocess_logistics.report.missing_sales_serials.missing_sales_serials"

SERIAL = "Logistics Serial"
ASSIGNMENT = "Logistics Serial Assignment"

# The lab's own invoices, created before its `installed_on` — the historical case.
HISTORICAL = ("ACC-SINV-2026-00001", "ACC-SINV-2026-00002")


class Recorder:
	def __init__(self):
		self.failures = 0

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


def invoice_name():
	return frappe.db.get_value("Sales Invoice", {"remarks": ("like", f"{PLANTED_REMARK}%")}, "name")


def flagged_item():
	"""The item this site marks as requiring serials — the report's own entry condition."""
	return frappe.db.get_value("Item", {"sp_serial_required": 1}, "name")


def report(filters=None):
	return frappe.get_attr(f"{REPORT}.execute")(filters or {})


def signature(rows):
	"""What a report answer is made of, for comparing two of them."""
	return sorted(
		(row.get("sales_invoice"), row.get("sales_invoice_item"), row.get("registered")) for row in rows
	)


# --- arm ---------------------------------------------------------------------------------


def ensure_optional_item(item):
	"""An item the site does *not* mark as requiring serials.

	Its rows may still be registered against — that has always been allowed — and the point of the
	row below is that neither the report nor the station may call it short. The site's own item
	group is reused rather than invented.
	"""
	if frappe.db.exists("Item", OPTIONAL_ITEM):
		return OPTIONAL_ITEM

	frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": OPTIONAL_ITEM,
			"item_name": "SP Lab Optional Serial Item",
			"item_group": frappe.db.get_value("Item", item, "item_group"),
			"is_stock_item": 0,
			"sp_serial_required": 0,
		}
	).insert()
	frappe.db.commit()

	return OPTIONAL_ITEM


def register(row_key, label):
	from smartprocess_logistics.api import endpoints

	endpoints.add_row_serial(sales_invoice=invoice_name(), sales_invoice_item=row_key, serial=label)
	frappe.db.commit()


def arm(recorder):
	frappe.set_user("Administrator")

	item = flagged_item()

	if not item:
		raise SystemExit("refused: no item on this site has sp_serial_required set")

	if invoice_name():
		recorder.note(f"{invoice_name()} is already planted")

		return

	optional = ensure_optional_item(item)
	abbr = frappe.db.get_value("Company", COMPANY, "abbr")
	customer = frappe.db.get_value("Sales Invoice", {"company": COMPANY}, "customer")

	invoice = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"company": COMPANY,
			"customer": customer,
			"posting_date": frappe.utils.nowdate(),
			"debit_to": f"Debtors - {abbr}",
			"remarks": PLANTED_REMARK,
			"items": [
				{"item_code": code, "qty": quantity, "rate": 1, "income_account": f"Sales - {abbr}"}
				for code, quantity in [(item, quantity) for quantity in MANDATORY_QUANTITIES]
				+ [(optional, OPTIONAL_QUANTITY)]
			],
		}
	).insert()
	frappe.db.commit()

	recorder.note(
		f"planted {invoice.name}: {MANDATORY_QUANTITIES} of the marked item and {OPTIONAL_QUANTITY} "
		f"of {optional}, which is not marked"
	)

	counter = 0

	# `strict=True`: the invoice is expected to carry a row for every mandatory quantity, and a
	# silent truncation here would skip the last of them rather than say so.
	for row, count in zip(invoice.items[: len(MANDATORY_QUANTITIES)], MANDATORY_REGISTERED, strict=True):
		for _ in range(count):
			counter += 1
			register(row.name, f"{LABEL}{counter:04d}")

	for _ in range(OPTIONAL_REGISTERED):
		counter += 1
		register(invoice.items[-1].name, f"{LABEL}{counter:04d}")

	recorder.note(
		f"registered {counter} label(s): {MANDATORY_REGISTERED} on the marked rows and "
		f"{OPTIONAL_REGISTERED} on the optional one"
	)


# --- check -------------------------------------------------------------------------------


def row_for(rows, row_key):
	return next((row for row in rows if row.get("sales_invoice_item") == row_key), None)


def check_the_rows(recorder, invoice, keys):
	"""Zero, partial, complete — and the optional row that must never be one of them."""
	_columns, short = report()
	mine = [row for row in short if row.get("sales_invoice") == invoice]

	recorder.check(
		len(mine) == 2,
		"a marked row with no registrations and a marked row with some are both reported",
		f"{len(mine)} row(s): {[row.get('row_index') for row in mine]}",
	)

	none_registered = row_for(mine, keys[0])
	partial = row_for(mine, keys[1])

	recorder.check(
		none_registered is not None
		and none_registered["registered"] == 0
		and none_registered["required_units"] == 3
		and none_registered["shortage"] == 3,
		"a row with zero registrations is discovered and shows its full shortage",
		str(none_registered),
	)
	recorder.check(
		partial is not None
		and partial["registered"] == 1
		and partial["required_units"] == 2
		and partial["shortage"] == 1,
		"a partly registered row shows what is left",
		str(partial),
	)
	recorder.check(row_for(mine, keys[2]) is None, "a complete marked row is not reported as short")
	recorder.check(
		row_for(mine, keys[3]) is None,
		"and an unmarked row is not reported at all, though a label is registered on it",
	)

	_columns, everything = report({"only_incomplete": 0})
	all_mine = [row for row in everything if row.get("sales_invoice") == invoice]

	recorder.check(
		len(all_mine) == 3 and len({row["item_code"] for row in all_mine}) == 1,
		"three marked rows of one item are three rows of the report, not one",
		f"{len(all_mine)} rows, {len({row['item_code'] for row in all_mine})} item(s)",
	)
	recorder.check(len({row["sales_invoice_item"] for row in all_mine}) == 3, "each keyed by its own row")
	completed = row_for(all_mine, keys[2])
	recorder.check(
		completed is not None and completed["shortage"] == 0,
		"the complete row appears when the incomplete filter is off, with no shortage",
		str(completed),
	)
	recorder.check(
		all(row["shortage"] == row["required_units"] - row["registered"] for row in all_mine),
		"and every shortage is the requirement minus the registrations",
	)

	sample = none_registered or {}
	recorder.check(
		all(
			sample.get(field) not in (None, "")
			for field in ("sales_invoice", "company", "customer", "item_code", "sales_invoice_item")
		),
		"every row names its invoice, company, customer, item and row",
		json.dumps(
			{key: sample.get(key) for key in ("sales_invoice", "company", "customer")}, ensure_ascii=False
		),
	)


def check_historical(recorder):
	"""The change this script exists for: an invoice older than the app is still audited."""
	_columns, everything = report({"only_incomplete": 0})

	found = sorted({row.get("sales_invoice") for row in everything if row.get("sales_invoice") in HISTORICAL})
	installed = frappe.db.get_single_value("Logistics Settings", "installed_on")
	created = {name: str(frappe.db.get_value("Sales Invoice", name, "creation")) for name in HISTORICAL}

	recorder.check(
		bool(found),
		"an invoice created before the app's installed_on is in the report",
		f"found {found}; created {created}; installed_on {installed}",
	)
	recorder.check(
		all(created[name] < str(installed) for name in found),
		"and the ones found really do predate that timestamp",
		f"{created} vs {installed}",
	)


def check_installed_on_does_not_matter(recorder):
	"""Moving the install marker must not move a single row — the reinstall case, in one line."""
	before = signature(report({"only_incomplete": 0})[1])
	original = frappe.db.get_single_value("Logistics Settings", "installed_on")

	try:
		frappe.db.set_single_value("Logistics Settings", "installed_on", "2030-01-01 00:00:00.000000")
		frappe.db.commit()
		frappe.clear_cache(doctype="Logistics Settings")

		after = signature(report({"only_incomplete": 0})[1])
	finally:
		frappe.db.set_single_value("Logistics Settings", "installed_on", original)
		frappe.db.commit()
		frappe.clear_cache(doctype="Logistics Settings")

	recorder.check(
		after == before,
		"an installed_on moved to the future changes nothing: the report does not read it",
		f"{len(before)} row(s) before, {len(after)} after",
	)
	recorder.check(
		frappe.db.get_single_value("Logistics Settings", "installed_on") == original,
		"and the value is back where it was found",
		str(frappe.db.get_single_value("Logistics Settings", "installed_on")),
	)


def check_the_scan_window(recorder, invoice, keys):
	"""The same rule, as the scan station reads it: required columns and optional ones."""
	from smartprocess_logistics.api import endpoints

	status = endpoints.get_invoice_serial_status(sales_invoice=invoice)
	rows = {row["row_key"]: row for row in status["rows"]}

	marked = rows[keys[0]]
	optional = rows[keys[3]]

	recorder.check(
		marked["serial_required"] is True
		and marked["required"] == 3
		and marked["shortage"] == 3
		and marked["is_complete"] is False,
		"the station is told the marked row is required, and still short",
		str({key: marked[key] for key in ("serial_required", "required", "shortage", "is_complete")}),
	)
	recorder.check(
		optional["serial_required"] is False
		and optional["required"] == 0
		and optional["shortage"] == 0
		and optional["is_complete"] is False,
		"and the unmarked row is optional: nothing required, nothing short, never complete",
		str({key: optional[key] for key in ("serial_required", "required", "shortage", "is_complete")}),
	)
	recorder.check(
		optional["registered"] == OPTIONAL_REGISTERED and len(optional["assignments"]) == OPTIONAL_REGISTERED,
		"while the label registered on it is still counted and still listed",
		str(optional["registered"]),
	)
	recorder.check(
		status["summary"]["rows"] == len(MANDATORY_QUANTITIES)
		and status["summary"]["optional_rows"] == 1
		and status["summary"]["optional_registered"] == OPTIONAL_REGISTERED,
		"the totals cover the required rows, and count the optional ones beside them",
		str(status["summary"]),
	)


def check_filters(recorder, invoice):
	_columns, by_company = report({"company": COMPANY})
	recorder.check(
		any(row.get("sales_invoice") == invoice for row in by_company),
		"the company filter keeps this site's own rows",
	)
	_columns, other = report({"company": "No Such Company"})
	recorder.check(
		not any(row.get("sales_invoice") == invoice for row in other),
		"and a company filter for somebody else returns none of them",
		f"{len(other)} row(s)",
	)
	_columns, submitted = report({"docstatuses": (1,)})
	recorder.check(
		not any(row.get("sales_invoice") == invoice for row in submitted),
		"a submitted-only filter leaves this draft out",
	)
	_columns, drafts = report({"docstatuses": (0,)})
	recorder.check(any(row.get("sales_invoice") == invoice for row in drafts), "and a draft filter keeps it")
	_columns, from_date = report({"from_date": "2099-01-01", "only_incomplete": 0})
	recorder.check(
		not any(row.get("sales_invoice") == invoice for row in from_date),
		"a date range the operator types is still applied",
	)
	_columns, nothing = report({"customer": "No Such Customer"})
	recorder.check(len(nothing) == 0, "a filter that matches nothing returns nothing")

	frappe.set_user("lab-restricted@example.test")
	_columns, as_restricted = report({"only_incomplete": 0})
	frappe.set_user("Administrator")

	recorder.check(
		any(row.get("sales_invoice") == invoice for row in as_restricted),
		"a user restricted to this company still reads its rows",
		f"{len(as_restricted)} row(s)",
	)


def check(recorder):
	frappe.set_user("Administrator")

	invoice = invoice_name()
	keys = [row.name for row in frappe.get_doc("Sales Invoice", invoice).items]

	print("  zero, partial, complete, and one optional row")
	check_the_rows(recorder, invoice, keys)

	print("  the historical invoices this change was for")
	check_historical(recorder)

	print("  the install marker, moved and put back")
	check_installed_on_does_not_matter(recorder)

	print("  the scan window reads the same rule")
	check_the_scan_window(recorder, invoice, keys)

	print("  filters and authorization")
	check_filters(recorder, invoice)

	frappe.db.commit()

	return recorder


# --- disarm ------------------------------------------------------------------------------


def disarm(recorder):
	frappe.set_user("Administrator")

	name = invoice_name()
	removed = {"assignments": 0, "serials": 0, "invoice": name, "optional_item": None}

	if name:
		labels = set()

		for assignment in frappe.get_all(
			ASSIGNMENT, filters={"sales_invoice": name}, fields=["name", "logistics_serial"]
		):
			if assignment.logistics_serial:
				labels.add(assignment.logistics_serial)

			frappe.delete_doc(ASSIGNMENT, assignment.name, force=True)
			removed["assignments"] += 1

		for label in labels:
			if frappe.db.exists(SERIAL, label):
				frappe.delete_doc(SERIAL, label, force=True)
				removed["serials"] += 1

		frappe.delete_doc("Sales Invoice", name, force=True)

	# A label a refused attempt created has no assignment left to find it by.
	for serial in frappe.get_all(SERIAL, filters={"serial_no": ("like", f"{LABEL}%")}, pluck="name"):
		if not frappe.db.exists(ASSIGNMENT, {"logistics_serial": serial}):
			frappe.delete_doc(SERIAL, serial, force=True)
			removed["serials"] += 1

	# The item this script invented, once nothing points at it.
	if frappe.db.exists("Item", OPTIONAL_ITEM) and not frappe.db.exists(
		"Sales Invoice Item", {"item_code": OPTIONAL_ITEM}
	):
		frappe.delete_doc("Item", OPTIONAL_ITEM, force=True)
		removed["optional_item"] = OPTIONAL_ITEM

	frappe.db.commit()
	recorder.note(json.dumps(removed, ensure_ascii=False))


# --- entry point -------------------------------------------------------------------------


def main(argv):
	phase = (argv[1] if len(argv) > 1 else "run").lower()

	if phase not in ("arm", "check", "disarm", "run"):
		print(f"unknown phase {phase!r}: expected arm, check, disarm or run")

		return 2

	connect()

	recorder = Recorder()

	print(
		f"site: {frappe.local.site}  |  invoice: {invoice_name() or '(none)'}  |  "
		f"marked item: {flagged_item() or '(none)'}\n"
	)

	try:
		if phase in ("arm", "run"):
			print("arm")
			arm(recorder)
			print()

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
