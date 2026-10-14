"""The two restores, side by side, over one file that mixes a cancelled invoice with live ones.

`docs/integration-test-plan.md` phase 6, which is the phase that decides whether the app's one
exception to "nothing is added to a cancelled invoice" is a **door** or a **hole**. The ordinary
restore must still refuse a cancelled invoice and name the historical path in its refusal; the
historical restore must place the cancelled invoice's rows and refuse the live ones. Either of
those holding alone would be a defect, and the phase says so: if the ordinary restore accepted
cancelled rows too, the exception would have become a door and the run has to stop.

The file is built from this site's own export and then **sealed again with this app's own
builder** (`core.export.build`), after two rows are added to it. Crafting rather than waiting for
a situation to arise is what makes the two cases reachable at all:

    a row on the cancelled invoice, carrying a label that exists on the site and is not on that
    invoice      → the historical restore creates it, the ordinary restore refuses it
    a row on a live invoice whose row is already full, carrying a label from the cancelled
    invoice      → EXCESS_QUANTITY, in the ordinary path, and NOT_HISTORY in the historical one

Nothing is deleted to make room for either: the crafted rows are new names, and the only write
this script performs is the one the historical restore is supposed to perform.

    arm      a cancelled invoice with room on its row, and a live one with none
    check    both previews, both refusals, the one write, and a second attempt that writes nothing
    disarm   report what the run leaves behind; nothing is deleted
    run      arm, check, disarm

Run it inside the lab's backend container, from the sites directory:

    docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab \\
      -f deployment/lab/compose.isolation-lab.yaml \\
      exec -T -w /home/frappe/frappe-bench/sites backend \\
      /home/frappe/frappe-bench/env/bin/python \\
      /home/frappe/frappe-bench/apps/smartprocess_logistics/deployment/lab/verify_lab_restore_history.py run
"""

import json
import sys

import frappe

from smartprocess_logistics.api import endpoints
from smartprocess_logistics.core import authz, export

SITE = "sp_logistics_lab"
SITES_PATH = "/home/frappe/frappe-bench/sites"

COMPANY = "SP LAB COMPANY"
INVOICE = "Sales Invoice"
SERIAL = "Logistics Serial"
ASSIGNMENT = "Logistics Serial Assignment"

REMARK_PREFIX = "planted by verify_lab_restore_history.py"

CANCELLED_QTY = 20
CANCELLED_LABELS = 2
LIVE_QTY = 2

ARCHIVIST_USER = "lab-archivist@example.test"
MANAGER_USER = "lab-manager@example.test"

HIST_ROW = "LSA-HISTORY-0001"
EXCESS_ROW = "LSA-EXCESS-0001"

PROVENANCE = {
	"creation": "2026-01-01 00:00:00",
	"modified": "2026-01-01 00:00:00",
	"owner": "Administrator",
	"modified_by": "Administrator",
}


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


def as_user(email, work):
	frappe.set_user(email)

	try:
		return work()
	finally:
		frappe.set_user("Administrator")


# --- the run ------------------------------------------------------------------------------


def planted_runs():
	remarks = frappe.get_all(
		INVOICE,
		filters={"remarks": ["like", f"{REMARK_PREFIX}%"]},
		pluck="remarks",
		limit_page_length=0,
	)
	numbers = set()

	for text in remarks:
		head = text.split("#")[-1].split("—")[0].strip()

		if head.isdigit():
			numbers.add(int(head))

	return numbers


def remark(run, role):
	return f"{REMARK_PREFIX} #{run} — {role}"


def invoice_by_remark(text):
	return frappe.db.get_value(INVOICE, {"remarks": text}, "name", order_by="creation asc")


def registrations(invoice_name):
	return frappe.db.count(ASSIGNMENT, {"sales_invoice": invoice_name})


def label_rows(invoice_name):
	"""The labels on an invoice: name, master and identity, as the file carries them."""
	rows = frappe.get_all(
		ASSIGNMENT,
		filters={"sales_invoice": invoice_name},
		fields=[
			"name",
			"logistics_serial",
			"item_code",
			"normalized_serial",
			"sales_invoice_item",
			"invoice_item_idx",
		],
		order_by="creation asc, name asc",
		limit_page_length=0,
	)

	return rows


# --- arm ----------------------------------------------------------------------------------


def plant(recorder, run, text, qty, labels):
	abbr = frappe.db.get_value("Company", COMPANY, "abbr")
	customer = frappe.db.get_value(INVOICE, {"company": COMPANY}, "customer")
	item = frappe.db.get_value(SERIAL, {"item_code": ("is", "set")}, "item_code")

	invoice = frappe.get_doc(
		{
			"doctype": INVOICE,
			"company": COMPANY,
			"customer": customer,
			"posting_date": frappe.utils.nowdate(),
			"debit_to": f"Debtors - {abbr}",
			"remarks": text,
			"items": [{"item_code": item, "qty": qty, "rate": 1, "income_account": f"Sales - {abbr}"}],
		}
	).insert()
	invoice.submit()

	if labels:
		call(
			"add_row_serials",
			sales_invoice=invoice.name,
			sales_invoice_item=invoice.items[0].name,
			serials=list(labels),
		)

	frappe.db.commit()

	return invoice


def ensure_users(recorder):
	"""The two callers this phase needs, so the script does not depend on another one's arm."""
	for email, roles in (
		(ARCHIVIST_USER, ("Logistics Serial Manager", "Logistics Serial Archivist")),
		(MANAGER_USER, ("Logistics Serial Manager",)),
	):
		if frappe.db.exists("User", email):
			continue

		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
				"roles": [{"role": role} for role in ("Accounts User", *roles)],
			}
		).insert()

		recorder.note(f"created {email} with {', '.join(roles)}")


def arm(recorder, run):
	frappe.set_user("Administrator")
	ensure_users(recorder)

	cancelled = invoice_by_remark(remark(run, "cancelled source"))

	if cancelled:
		recorder.note(f"run {run} is already planted: {cancelled}")
		frappe.db.commit()

		return run

	invoice = plant(
		recorder,
		run,
		remark(run, "cancelled source"),
		CANCELLED_QTY,
		[f"LAB-HIST-C{run}-{index:02d}" for index in range(1, CANCELLED_LABELS + 1)],
	)
	invoice.reload()
	invoice.cancel()

	live = plant(
		recorder,
		run,
		remark(run, "live invoice with a full row"),
		LIVE_QTY,
		[f"LAB-HIST-L{run}-{index:02d}" for index in range(1, LIVE_QTY + 1)],
	)

	frappe.db.commit()

	recorder.note(
		f"run {run}: cancelled {invoice.name} holds {CANCELLED_LABELS} of {CANCELLED_QTY}, "
		f"live {live.name} holds {LIVE_QTY} of {LIVE_QTY}"
	)

	return run


# --- the crafted file ---------------------------------------------------------------------


def exported_document():
	return call("export_serials")["document"]


def craft(base, assignments):
	"""A file of its own: the rows this phase crafts, the site's serials, and every audit record.

	`core.export.build` is what writes every export this app produces, so a crafted file is a file
	the importer cannot tell from a real one — which is the point: the checks below are about what
	the plan does with rows, not about whether it can spot a fixture.

	**Not** the site's own assignment rows, and that changed after the recovery phase: the preview
	carries a *sample* of its conflicts, so a file with sixty of the site's rows in it pushed the
	two this phase is about out of that sample and the sentence they carry could not be read. The
	records do travel — the file a reinstall produces is the whole of what the site knew, and the
	two assignment modes are expected to report those records without offering to place one — and
	the serials do, because the crafted rows name them.
	"""
	return export.build(
		base["serials"],
		list(assignments),
		base["transfers"],
		base["transfer_items"],
		site=base["site"],
		exported_on=base["exported_on"],
		exported_by=base["exported_by"],
		app_version=base["app_version"],
		normalization_version=base["normalization_version"],
	)


def crafted(name, invoice, row, master, item_code, normalized, index):
	return {
		"name": name,
		"logistics_serial": master,
		"item_code": item_code,
		"normalized_serial": normalized,
		"sales_invoice": invoice,
		"company": COMPANY,
		"sales_invoice_item": row,
		"invoice_item_idx": index,
		"remarks": "",
		**PROVENANCE,
	}


def file_for(recorder, run):
	"""This run's file: the site's own export plus the two rows the phase needs."""
	cancelled = invoice_by_remark(remark(run, "cancelled source"))
	live = invoice_by_remark(remark(run, "live invoice with a full row"))
	on_cancelled = label_rows(cancelled)
	on_live = label_rows(live)

	base = exported_document()
	# A label that is on the site and not on the cancelled invoice: the historical restore has
	# something real to place, and it is not a duplicate of what is already there.
	foreign = on_live[0]
	# And a label from the cancelled invoice, aimed at the live row that has no room.
	home = on_cancelled[0]

	cancelled_row = frappe.db.get_value(
		"Sales Invoice Item", {"parent": cancelled}, "name", order_by="idx asc"
	)
	live_row = frappe.db.get_value("Sales Invoice Item", {"parent": live}, "name", order_by="idx asc")

	document = craft(
		base,
		[
			crafted(
				HIST_ROW,
				cancelled,
				cancelled_row,
				foreign.logistics_serial,
				foreign.item_code,
				foreign.normalized_serial,
				CANCELLED_LABELS + 1,
			),
			crafted(
				EXCESS_ROW,
				live,
				live_row,
				home.logistics_serial,
				home.item_code,
				home.normalized_serial,
				LIVE_QTY + 1,
			),
		],
	)

	recorder.note(f"file: {document['counts']} sealed as {document['digest'][:16]}…")

	return json.dumps(document, ensure_ascii=False), cancelled, live


# --- check --------------------------------------------------------------------------------


def check_the_role_gate(recorder, file):
	"""6.1 — the gate the whole path rests on."""
	refused = as_user(
		MANAGER_USER,
		lambda: thrown(call, "preview_cancelled_serial_history", content=file),
	)
	recorder.check(
		refused is not None and authz.ROLE_ARCHIVIST in str(refused),
		"a Manager without the Archivist role is refused by name",
		str(refused)[:200],
	)

	refused = as_user(
		MANAGER_USER, lambda: thrown(call, "restore_cancelled_serial_history", content=file, token="x")
	)
	recorder.check(
		refused is not None and authz.ROLE_ARCHIVIST in str(refused),
		"and the apply is refused at the same gate, before the token is even read",
		str(refused)[:200],
	)


def check_the_historical_preview(recorder, file, cancelled, live):
	"""6.2 — what the historical restore offers."""
	preview = as_user(ARCHIVIST_USER, lambda: call("preview_cancelled_serial_history", content=file))

	recorder.note(f"historical preview: to_create {preview['to_create']}, {preview['by_reason']}")

	names = {row["row"] for row in preview["sample"]}

	# Exactly one: the file is this site's own export, so every row it carries is already stored
	# here, and the crafted row is the only thing the historical restore has to place.
	recorder.check(
		preview["to_create"]["assignments"] == 1,
		"exactly the crafted row on the cancelled invoice is offered for creation",
		f"to_create {preview['to_create']}",
	)
	# One, not `LIVE_QTY`: this file is the site's own export, so the live invoice's own two rows
	# are already stored here and are *skipped* rather than refused. The only row aimed at a live
	# invoice that the plan has to answer for is the crafted one, and a file exported from a site
	# that has not yet received these rows would refuse every one of them the same way.
	recorder.check(
		preview["by_reason"].get(export.REASON_NOT_HISTORY, 0) >= 1,
		"and the row aimed at a live invoice is refused as belonging to the ordinary restore",
		str(preview["by_reason"]),
	)
	recorder.check(
		preview["by_reason"].get(export.REASON_EXCESS_QUANTITY, 0) == 0,
		"the full live row is refused for being live, not for having no room",
		str(preview["by_reason"]),
	)
	recorder.note(f"the crafted row in the sample: {HIST_ROW in names}")

	return preview


def check_the_ordinary_preview(recorder, file, cancelled, live):
	"""6.3 — the exception did not become a door."""
	before_cancelled = registrations(cancelled)
	before_live = registrations(live)

	preview = as_user(ARCHIVIST_USER, lambda: call("preview_serial_restore", content=file))

	recorder.note(f"ordinary preview: to_create {preview['to_create']}, {preview['by_reason']}")

	recorder.check(
		preview["by_reason"].get(export.REASON_INVOICE_CANCELLED, 0) > 0,
		"the ordinary restore still refuses rows on the cancelled invoice",
		str(preview["by_reason"]),
	)

	sentences = [
		row["message"] for row in preview["sample"] if row["reason"] == export.REASON_INVOICE_CANCELLED
	]
	recorder.check(
		any("بازآوری تاریخچه" in sentence for sentence in sentences),
		"and its refusal names the historical path as the one that may",
		str(sentences[:1]),
	)
	recorder.check(
		preview["by_reason"].get(export.REASON_EXCESS_QUANTITY, 0) >= 1,
		"a row with no room left is refused with its own reason",
		str(preview["by_reason"]),
	)
	recorder.check(
		registrations(cancelled) == before_cancelled and registrations(live) == before_live,
		"a preview writes nothing, whichever rows it is about",
		f"{before_cancelled}/{before_live}",
	)

	# Plan phase 7's two export questions, asked of a live site: the file carries the audit
	# records, and neither of the two assignment modes offers to place one (7.2 and 7.4).
	recorder.check(
		preview["file_counts"]["transfers"] > 0 and preview["file_counts"]["transfer_items"] > 0,
		"the file carries the audit records and their rows",
		str(preview["file_counts"]),
	)
	recorder.check(
		preview["records_in_file"] > 0 and preview["to_create_records"]["transfers"] == 0,
		"and this mode reports them without offering to place a single one",
		f"{preview['records_in_file']} in the file, {preview['to_create_records']} offered",
	)

	return preview


def check_the_historical_apply(recorder, file, preview, cancelled, live):
	"""6.4, 6.5, 6.6, 6.7 — the one write, and what a second attempt does."""
	before_cancelled = registrations(cancelled)
	before_live = registrations(live)

	answer = as_user(
		ARCHIVIST_USER,
		lambda: call("restore_cancelled_serial_history", content=file, token=preview["token"]),
	)

	recorder.note(f"historical apply: {answer['counts']}")
	recorder.check(
		registrations(cancelled) == before_cancelled + 1,
		"exactly one registration was added to the cancelled invoice",
		f"{before_cancelled} → {registrations(cancelled)}",
	)
	recorder.check(
		HIST_ROW in answer["created"]["assignments"],
		"and it is the crafted row",
		str(answer["created"]["assignments"][:3]),
	)
	recorder.check(
		registrations(live) == before_live,
		"no row of the live invoice was registered by the historical path",
		f"{before_live} → {registrations(live)}",
	)
	recorder.check(
		not any(name.startswith("LSA-EXCESS") for name in answer["created"]["assignments"]),
		"and the full live row was not among them",
		str(answer["created"]["assignments"][:3]),
	)
	recorder.check(
		answer["created"]["transfers"] == [],
		"the historical restore places assignments and no audit record",
		str(answer["created"]["transfers"]),
	)
	recorder.check(
		answer["counts"]["by_reason"].get(export.REASON_NOT_HISTORY, 0) >= 1,
		"the live rows are reported, not written",
		str(answer["counts"]["by_reason"]),
	)

	# 6.7 — the same file again.
	second_preview = as_user(ARCHIVIST_USER, lambda: call("preview_cancelled_serial_history", content=file))
	second = as_user(
		ARCHIVIST_USER,
		lambda: call("restore_cancelled_serial_history", content=file, token=second_preview["token"]),
	)

	recorder.check(
		second["counts"]["created"]["assignments"] == 0,
		"a second historical restore of the same file creates nothing",
		str(second["counts"]),
	)
	recorder.check(
		registrations(cancelled) == before_cancelled + 1,
		"and the cancelled invoice is exactly as the first one left it",
		str(registrations(cancelled)),
	)
	recorder.check(
		second["counts"]["skipped"]["assignments"] >= 1,
		"because the row is reported as already there",
		str(second["counts"]),
	)


# --- disarm -------------------------------------------------------------------------------


def disarm(recorder):
	planted = frappe.get_all(
		INVOICE,
		filters={"remarks": ["like", f"{REMARK_PREFIX}%"]},
		pluck="name",
		limit_page_length=0,
	)

	recorder.note(f"{len(planted)} planted invoices remain")
	recorder.note("nothing was deleted")


# --- entry point --------------------------------------------------------------------------


def main(argv):
	phase = (argv[1] if len(argv) > 1 else "run").lower()

	if phase not in ("arm", "check", "disarm", "run"):
		print(f"unknown phase {phase!r}: expected arm, check, disarm or run")

		return 2

	recorder = Recorder()
	connect()

	print(f"site under test: {frappe.local.site}")

	planted = planted_runs()

	if phase in ("arm", "run"):
		run = max(planted) + 1 if planted else 1
	else:
		if not planted:
			print("no run has been armed on this site yet: run `arm` first")

			return 2

		run = max(planted)

	print(f"run number: {run}\n")

	if phase in ("arm", "run"):
		print("arm")
		arm(recorder, run)
		print()

	if not invoice_by_remark(remark(run, "cancelled source")):
		print(f"run {run} is incomplete: arm it again")

		return 2

	file, cancelled, live = file_for(recorder, run)
	print()

	try:
		if phase in ("check", "run"):
			print("check — the historical path (plan phase 6)")
			check_the_role_gate(recorder, file)
			print()
			preview = check_the_historical_preview(recorder, file, cancelled, live)
			print()
			check_the_ordinary_preview(recorder, file, cancelled, live)
			print()
			check_the_historical_apply(recorder, file, preview, cancelled, live)
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
