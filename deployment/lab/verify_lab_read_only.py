"""Every read path this app has, against the lab site, without writing anything.

The phase-19 scripts prove the writes and they have to *make* writes to do it, so they cannot be
run while the lab is frozen. This one exists for the other case: a change to the read half — an
authorization order, a filter, a plan that decides what a preview says — has to be checkable while
nothing may be written at all.

So it calls the endpoints that read, and only those. Each is in `core.authz`'s read-only set or is
`preview_serial_transfer`, which the documentation calls out as taking no lock and storing no
session; the four tables this app owns are counted before and after, and the run fails if the
numbers moved. A preview that wrote would be the bug this script exists to catch.

    check    call every read endpoint, count the tables around it, report

Run it inside the lab's backend container, from the sites directory:

    docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab \\
      -f deployment/lab/compose.isolation-lab.yaml \\
      exec -T -w /home/frappe/frappe-bench/sites backend \\
      /home/frappe/frappe-bench/env/bin/python \\
      /home/frappe/frappe-bench/apps/smartprocess_logistics/deployment/lab/verify_lab_read_only.py
"""

import json
import sys

import frappe

from smartprocess_logistics.api import endpoints

SITE = "sp_logistics_lab"
SITES_PATH = "/home/frappe/frappe-bench/sites"

INVOICE = "Sales Invoice"
TABLES = (
	"Logistics Serial",
	"Logistics Serial Assignment",
	"Logistics Serial Transfer",
	"Logistics Serial Transfer Item",
)


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


def call(method, **arguments):
	return getattr(endpoints, method)(**arguments)


def thrown(callable_, *args, **kwargs):
	try:
		return callable_(*args, **kwargs)
	except Exception as error:  # noqa: BLE001 — the message is the evidence
		return error


def counts():
	return {table: frappe.db.count(table) for table in TABLES}


def main(argv):
	if len(argv) > 1 and argv[1] not in ("check", "run"):
		print(f"unknown phase {argv[1]!r}: this script has one phase, `check`")

		return 2

	recorder = Recorder()
	connect()

	print(f"site under test: {frappe.local.site}")

	before = counts()
	recorder.note(f"before: {before}")

	live = frappe.db.get_value(INVOICE, {"docstatus": 1}, "name", order_by="creation desc")
	# A *pair*, not just a cancelled invoice: the transfer preview is about two documents, and an
	# amendment that is itself cancelled would be refused for a reason that says nothing about
	# whether the read path works.
	amendment = frappe.db.get_value(
		INVOICE, {"amended_from": ["is", "set"], "docstatus": 1}, "name", order_by="creation desc"
	)
	cancelled = frappe.db.get_value(INVOICE, amendment, "amended_from") if amendment else None

	recorder.note(f"live {live}, cancelled {cancelled}, amendment {amendment}")
	print()

	status = call("get_invoice_serial_status", sales_invoice=live)
	recorder.check(isinstance(status, dict) and "rows" in status, "the status read answers")

	page = call("get_serial_assignments", sales_invoice=live, limit=5, offset=0)
	recorder.check(
		isinstance(page, dict) and isinstance(page.get("assignments"), list),
		"a page of assignments answers",
		str(page)[:120],
	)

	export = call("export_serials")
	document = export.get("document") or {}
	content = json.dumps(document, ensure_ascii=False)

	recorder.check(
		document.get("format_version") == 2,
		"the export answers, in the current format",
		str(document.get("format_version")),
	)
	recorder.note(f"the file carries {document.get('counts')}")

	ordinary = call("preview_serial_restore", content=content)
	recorder.check(isinstance(ordinary, dict) and bool(ordinary.get("token")), "the ordinary preview answers")

	history = thrown(call, "preview_cancelled_serial_history", content=content)
	recorder.check(
		history is None or "needs the role" not in str(history),
		"the historical preview answers (or is refused only by the named role)",
		str(history)[:160],
	)

	records = call("preview_restore_audit_records", content=content)
	recorder.check(
		isinstance(records, dict) and records.get("records_in_file") == document["counts"]["transfers"],
		"the records preview answers and counts the file's records",
		str(records)[:160],
	)

	if amendment:
		transfer = call("preview_serial_transfer", sales_invoice=cancelled, target_invoice=amendment)
		recorder.check(
			isinstance(transfer, dict) and "counts" in transfer,
			"the transfer preview answers",
			str(transfer)[:120],
		)
	else:
		recorder.note("no cancelled invoice with an amendment, so the transfer preview is not called")

	after = counts()
	recorder.note(f"after:  {after}")

	recorder.check(
		after == before,
		"not one row of the four tables changed: every call above is a read",
		f"{before} → {after}",
	)

	print()
	print("PASS" if not recorder.failures else f"FAIL ({recorder.failures} checks)")

	return 1 if recorder.failures else 0


if __name__ == "__main__":
	raise SystemExit(main(sys.argv))
