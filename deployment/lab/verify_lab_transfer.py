"""Run the audit record and the transfer against a real site, which is the half the unit tests skip.

`docs/integration-test-plan.md` phases 3 and 4, executed rather than described. Everything the
database-free suites assert about the plan is asserted here against MariaDB: that a record is
inserted **submitted** and that the framework then refuses to change or delete it, that a serial
transfer onto an amendment writes the registrations `core.assignment` would have derived, and that
the cancelled source is left exactly as it was.

What the two suites cannot agree on without this run is the *order* of things the framework does
inside `insert`: `Document._save` checks write permission before it reads an explicit `docstatus`,
which is why a record is written by inserting it as submitted rather than by inserting and
submitting. That was read from the source in phase 17; here it either works or it does not.

Two pairs of invoices are planted, because the plan's two scenarios cannot both hold in one:

    pair A   18 serials on a cancelled invoice, an amendment that invoices 10 of them. The
             expected outcome is 10 moved and 8 refused for want of room, and it consumes the
             amendment's whole capacity.
    pair B   2 serials, one of which is also registered on a **live** invoice. The amendment has
             room for both, so the only thing standing between the serial and the move is the
             acknowledgment — which is what makes an answer from the operator observable.

A run plants its own pair, numbered, so the script can be run again on the same site: the audit
records it writes are immutable by design and nothing here deletes one to make room for a second
run. `check` therefore reads the newest run and **consumes it** — it performs the transfers it
asserts about — so a fresh `arm` has to come before each `check`. `arm` is the phase that writes,
it is idempotent per run, and it plants the next number rather than touching the last one.

Run it inside the lab's backend container, from the sites directory:

    docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab \\
      -f deployment/lab/compose.isolation-lab.yaml \\
      exec -T -w /home/frappe/frappe-bench/sites backend \\
      /home/frappe/frappe-bench/env/bin/python \\
      /home/frappe/frappe-bench/apps/smartprocess_logistics/deployment/lab/verify_lab_transfer.py run

    arm      plant this run's two pairs — invoices, registrations, cancellation, amendment
    check    the assertions; read-only apart from the two transfers it performs on purpose
    disarm   report what the run leaves behind. Nothing is deleted: a submitted document cannot
             be, and an audit record must not be
    run      arm, check, disarm — disarm in a `finally`, so a failed check still reports
"""

import sys
import traceback

import frappe

from smartprocess_logistics.api import endpoints
from smartprocess_logistics.core import transfer as transfer_rules

SITE = "sp_logistics_lab"
SITES_PATH = "/home/frappe/frappe-bench/sites"

COMPANY = "SP LAB COMPANY"
INVOICE = "Sales Invoice"
SERIAL = "Logistics Serial"
ASSIGNMENT = "Logistics Serial Assignment"
TRANSFER = "Logistics Serial Transfer"
TRANSFER_ITEM = "Logistics Serial Transfer Item"

# The remark every planted document carries, with the run number appended. A run is recognised by
# its prefix rather than by its documents, so a half-planted run is finished by the next one.
REMARK_PREFIX = "planted by verify_lab_transfer.py"

SOURCE_A_QTY = 18
TARGET_A_QTY = 10
SOURCE_B_QTY = 2
TARGET_B_QTY = 2

MANAGER_USER = "lab-manager@example.test"


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
	"""One endpoint, called the way an HTTP request would call it."""
	return getattr(endpoints, method)(**arguments)


def thrown(callable_):
	"""The exception a refused operation raised, or `None` if it was allowed through."""
	try:
		callable_()
	except Exception as error:  # noqa: BLE001 — the point is to look at what came back
		return error

	return None


# --- the run, and what it is called -------------------------------------------------------


def planted_runs():
	"""Which runs are already on the site, read off the remarks rather than counted."""
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


def labels_of(invoice_name):
	"""The labels on an invoice, in the order they were registered.

	Read from the two tables rather than carried in a dictionary from `arm`, so that `check` can
	be run on its own and so that what it asserts about is what the database holds.
	"""
	rows = frappe.get_all(
		ASSIGNMENT,
		filters={"sales_invoice": invoice_name},
		fields=["name", "logistics_serial"],
		order_by="creation asc, name asc",
		limit_page_length=0,
	)
	names = [row.logistics_serial for row in rows if row.logistics_serial]
	texts = {}

	if names:
		for serial in frappe.get_all(
			SERIAL,
			filters={"name": ["in", names]},
			fields=["name", "serial_no"],
			limit_page_length=0,
		):
			texts[serial.name] = serial.serial_no

	return [texts.get(name) for name in names]


def fact_of(run):
	"""This run's documents, looked up by the remark each one carries."""
	source_a = invoice_by_remark(remark(run, "source A"))
	source_b = invoice_by_remark(remark(run, "source B"))
	live_b = invoice_by_remark(remark(run, "a live invoice that also holds one of B's labels"))

	return {
		"run": run,
		"labels_a": labels_of(source_a) if source_a else [],
		"labels_b": labels_of(source_b) if source_b else [],
		"source_a": source_a,
		"target_a": frappe.db.get_value(INVOICE, {"amended_from": source_a}, "name") if source_a else None,
		"source_b": source_b,
		"target_b": frappe.db.get_value(INVOICE, {"amended_from": source_b}, "name") if source_b else None,
		"live_b": live_b,
	}


def item_code():
	# An item the lab already sells with labels on it: it is marked `sp_serial_required`, which is
	# the flag every eligibility decision reads.
	return frappe.db.get_value(SERIAL, {"item_code": ("is", "set")}, "item_code")


def customer():
	return frappe.db.get_value(INVOICE, {"company": COMPANY}, "customer")


def plant_invoice(text, qty):
	abbr = frappe.db.get_value("Company", COMPANY, "abbr")
	invoice = frappe.get_doc(
		{
			"doctype": INVOICE,
			"company": COMPANY,
			"customer": customer(),
			"posting_date": frappe.utils.nowdate(),
			"debit_to": f"Debtors - {abbr}",
			"remarks": text,
			"items": [
				{
					"item_code": item_code(),
					"qty": qty,
					"rate": 1,
					"income_account": f"Sales - {abbr}",
				}
			],
		}
	).insert()

	return invoice


def first_row(name):
	return frappe.get_doc(INVOICE, name).items[0]


def register(invoice_name, serials):
	"""Register labels the way the station does: one call, one transaction, one answer."""
	row = first_row(invoice_name)

	return call(
		"add_row_serials",
		sales_invoice=invoice_name,
		sales_invoice_item=row.name,
		serials=list(serials),
	)


def amend(source_name, text, qty):
	"""The amendment a person makes with the Amend button: a copy, linked, with the new quantity."""
	document = frappe.copy_doc(frappe.get_doc(INVOICE, source_name))
	document.amended_from = source_name
	document.remarks = text
	document.items[0].qty = qty
	document.insert()
	document.submit()

	return document


def plant_source(text, qty, labels):
	invoice = plant_invoice(text, qty)
	invoice.submit()

	answer = register(invoice.name, labels)
	frappe.db.commit()

	invoice.reload()
	invoice.cancel()
	frappe.db.commit()

	return {"invoice": invoice.name, "row": first_row(invoice.name).name, "answer": answer}


# --- arm ---------------------------------------------------------------------------------


def ensure_manager(recorder):
	"""A Manager, because one of the framework's refusals is about a user and not about a flag."""
	if not frappe.db.exists("User", MANAGER_USER):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": MANAGER_USER,
				"first_name": "lab-manager",
				"send_welcome_email": 0,
				"roles": [{"role": "Logistics Serial Manager"}, {"role": "Accounts User"}],
			}
		).insert()

		recorder.note(f"created {MANAGER_USER} with Logistics Serial Manager")

	user = frappe.get_doc("User", MANAGER_USER)

	for role in ("Logistics Serial Manager", "Accounts User"):
		if role not in {row.role for row in user.roles}:
			user.append("roles", {"role": role})

	user.save()


def arm(recorder, run):
	frappe.set_user("Administrator")
	ensure_manager(recorder)

	if invoice_by_remark(remark(run, "source A")):
		recorder.note(f"run {run} is already planted; arming it again would plant a second pair")

		return run

	labels_a = [f"LAB-TRA-{run}-{index:02d}" for index in range(1, SOURCE_A_QTY + 1)]
	labels_b = [f"LAB-TRB-{run}-{index:02d}" for index in range(1, SOURCE_B_QTY + 1)]

	source_a = plant_source(remark(run, "source A"), SOURCE_A_QTY, labels_a)
	target_a = amend(source_a["invoice"], remark(run, "amendment A"), TARGET_A_QTY)

	source_b = plant_source(remark(run, "source B"), SOURCE_B_QTY, labels_b)
	target_b = amend(source_b["invoice"], remark(run, "amendment B"), TARGET_B_QTY)

	# A second live invoice carrying one of B's labels. Registering an identity that already exists
	# reuses its `Logistics Serial` and adds a registration, which is how a site ends up with one
	# serial text on two invoices — the state the acknowledgment exists for.
	live_b = plant_invoice(remark(run, "a live invoice that also holds one of B's labels"), 2)
	live_b.submit()
	register(live_b.name, [labels_b[1]])
	frappe.db.commit()

	recorder.note(f"run {run}: A {source_a['invoice']} ({SOURCE_A_QTY} labels) → {target_a.name}")
	recorder.note(f"run {run}: B {source_b['invoice']} ({SOURCE_B_QTY} labels) → {target_b.name}")
	recorder.note(f"run {run}: {labels_b[1]} is also on the live {live_b.name}")

	return run


# --- observation helpers ------------------------------------------------------------------


def fresh():
	"""End this process's read view, so the next count is of the site and not of this transaction.

	An endpoint called directly is not a request: nothing commits for it at a boundary that does
	not exist here. Every write this script performs is therefore committed where a request would
	commit it, and every count that is meant to be evidence of durability is taken afterwards,
	through a new transaction. Without this the phase passed on writes that the process exit rolled
	back — measured, and exactly what the first run of this script did.
	"""
	frappe.db.rollback()


def assignments_of(invoice_name):
	return sorted(
		(
			row.name,
			row.logistics_serial,
			row.normalized_serial,
			row.sales_invoice_item,
			row.invoice_item_idx,
		)
		for row in frappe.get_all(
			ASSIGNMENT,
			filters={"sales_invoice": invoice_name},
			fields=[
				"name",
				"logistics_serial",
				"normalized_serial",
				"sales_invoice_item",
				"invoice_item_idx",
			],
			limit_page_length=0,
		)
	)


def serial_masters():
	return sorted(
		(
			row.name,
			row.serial_no,
			row.normalized_serial,
			row.item_code,
			row.status,
			str(row.first_registered_on),
		)
		for row in frappe.get_all(
			SERIAL,
			fields=[
				"name",
				"serial_no",
				"normalized_serial",
				"item_code",
				"status",
				"first_registered_on",
			],
			limit_page_length=0,
		)
	)


def invoice_rows(invoice_name):
	return sorted(
		(row.name, row.item_code, row.qty, row.idx)
		for row in frappe.get_all(
			"Sales Invoice Item",
			filters={"parent": invoice_name},
			fields=["name", "item_code", "qty", "idx"],
			limit_page_length=0,
		)
	)


def outcome_rows(preview):
	return {row["serial"]: row for row in preview["serials"]}


def record_fields(name):
	fields = [
		"source_total",
		"moved_count",
		"refused_count",
		"unselected_count",
		"acknowledged_count",
		"docstatus",
		"target_invoice",
		"company",
	]

	return frappe.db.get_value(TRANSFER, name, fields, as_dict=True)


# --- check: pair A ------------------------------------------------------------------------


def check_pair_a(recorder, fact):
	source = fact["source_a"]
	target = fact["target_a"]

	before_assignments = assignments_of(source)
	before_rows = invoice_rows(source)
	before_serials = serial_masters()

	recorder.check(
		len(before_assignments) == SOURCE_A_QTY,
		"the cancelled invoice holds all 18 registrations",
		f"found {len(before_assignments)}",
	)
	recorder.check(frappe.db.get_value(INVOICE, source, "docstatus") == 2, "and it is cancelled")
	recorder.check(
		frappe.db.get_value(INVOICE, target, "amended_from") == source,
		"the amendment points at it",
	)

	preview = call("preview_serial_transfer", sales_invoice=source, target_invoice=target)
	counts = preview["counts"]

	recorder.note(f"preview counts: {counts}")
	recorder.check(counts["source"] == SOURCE_A_QTY, "the preview reads all 18 source labels")
	recorder.check(
		counts["moved"] == TARGET_A_QTY,
		f"and plans {TARGET_A_QTY} moves, the room the amendment has",
		str(counts),
	)
	recorder.check(
		counts["refused"] == SOURCE_A_QTY - TARGET_A_QTY,
		"and reports the other 8 as refused",
		str(counts),
	)
	recorder.check(counts["unselected"] == 0, "with nothing left unasked", str(counts))

	refused_reasons = {row["outcome"] for row in preview["serials"] if row["outcome"] != "Moved"}
	recorder.check(
		refused_reasons == {"NO_ROOM"},
		"the eight are refused for want of room and for no other reason",
		str(refused_reasons),
	)
	recorder.check(
		all(row["message"] for row in preview["serials"] if row["outcome"] != "Moved"),
		"every serial the plan does not move carries a sentence for the operator",
	)
	recorder.check(
		[row["capacity"] for row in preview["target_rows"]] == [TARGET_A_QTY],
		"the amendment's row offers the quantity it invoices",
		str(preview["target_rows"]),
	)

	# 4.4 — the apply.
	answer = call(
		"apply_serial_transfer",
		sales_invoice=source,
		target_invoice=target,
		token=preview["token"],
	)
	record = answer["transfer"]

	recorder.check(bool(record), "the apply names the audit record it wrote", str(answer["transfer"]))
	recorder.check(answer["counts"]["moved"] == TARGET_A_QTY, "and moved ten serials")

	# The record itself, read from the database rather than from the answer.
	stored = record_fields(record)
	recorder.note(f"stored record {record}: {stored}")
	recorder.check(stored.docstatus == 1, "the record is submitted", str(stored.docstatus))
	recorder.check(stored.source_total == SOURCE_A_QTY, "it counts the source", str(stored))
	recorder.check(stored.moved_count == TARGET_A_QTY, "and the moves", str(stored))
	recorder.check(stored.refused_count == SOURCE_A_QTY - TARGET_A_QTY, "and the refusals", str(stored))
	recorder.check(stored.unselected_count == 0, "and the unselected", str(stored))
	recorder.check(
		stored.moved_count + stored.refused_count + stored.unselected_count == stored.source_total,
		"and the three partition the source exactly",
		str(stored),
	)
	recorder.check(stored.target_invoice == target, "the record names its target")
	recorder.check(stored.company == COMPANY, "and the company both invoices are in")

	rows = frappe.get_all(
		TRANSFER_ITEM,
		filters={"parent": record},
		fields=["serial_no", "result", "reason", "reason_message", "source_assignment", "acknowledged"],
		limit_page_length=0,
	)

	recorder.check(
		len(rows) == stored.moved_count + stored.refused_count,
		"one child row per serial a decision was made about",
		f"{len(rows)} rows for {stored.moved_count} moved and {stored.refused_count} refused",
	)
	recorder.check(
		all(row.source_assignment for row in rows),
		"every row names the source registration it came from",
	)
	recorder.check(
		all(row.reason_message for row in rows if row.result == "Refused"),
		"and every refusal carries its sentence",
	)

	# The request boundary, which a call made in-process does not get for free.
	frappe.db.commit()
	fresh()

	recorder.check(
		frappe.db.count(TRANSFER, {"name": record}) == 1
		and record_fields(record).moved_count == TARGET_A_QTY,
		"the record is still there in a new transaction, so it was committed rather than held",
		str(record_fields(record)),
	)

	# 3.7 — the source and the serial masters, untouched.
	recorder.check(
		assignments_of(source) == before_assignments,
		"the source's registrations are byte-for-byte what they were",
	)
	recorder.check(invoice_rows(source) == before_rows, "and so are its item rows")
	after_serials = serial_masters()
	kept = [row for row in after_serials if row[0] in {r[0] for r in before_serials}]
	recorder.check(
		kept == before_serials,
		"no serial master was rewritten, dropped or re-dated",
		f"{len(before_serials)} before, {len(after_serials)} after",
	)

	# The target: what a registration the station made would look like.
	target_assignments = assignments_of(target)
	recorder.check(
		len(target_assignments) == TARGET_A_QTY,
		"the amendment holds ten registrations afterwards",
		str(len(target_assignments)),
	)
	recorder.check(
		{row[3] for row in target_assignments} == {first_row(target).name},
		"all of them on the amendment's single row",
	)
	recorder.check(
		{row[1] for row in target_assignments} <= {row[1] for row in before_assignments},
		"pointing at the source's own serial masters, not at copies",
	)
	recorder.check(
		len(after_serials) == len(before_serials),
		"and the transfer created no serial master at all",
		f"{len(before_serials)} before, {len(after_serials)} after",
	)

	# 4.5 — the same button pressed again.
	second_preview = call("preview_serial_transfer", sales_invoice=source, target_invoice=target)
	second_counts = second_preview["counts"]
	second_reasons = {row["outcome"] for row in second_preview["serials"] if row["outcome"] != "Moved"}

	recorder.note(f"second preview counts: {second_counts}, reasons {second_reasons}")
	recorder.check(second_counts["moved"] == 0, "a second preview moves nothing", str(second_counts))
	recorder.check(
		"ALREADY_ON_TARGET" in second_reasons,
		"and says which of them the amendment already holds",
		str(second_reasons),
	)

	second = call(
		"apply_serial_transfer",
		sales_invoice=source,
		target_invoice=target,
		token=second_preview["token"],
	)
	frappe.db.commit()
	fresh()

	recorder.check(second["transfer"] != record, "a second attempt writes a second record")
	recorder.check(
		frappe.db.get_value(TRANSFER, second["transfer"], "moved_count") == 0,
		"which counts no moves",
	)
	recorder.check(
		frappe.db.count(TRANSFER, {"source_invoice": source}) == 2,
		"so the cancelled invoice has two records, one per attempt",
	)

	# 3.3 — changing a stored record.
	stored_doc = frappe.get_doc(TRANSFER, record)
	original = stored_doc.remarks
	stored_doc.remarks = f"{original} — changed by the test"

	refusal = thrown(stored_doc.save)
	recorder.check(
		refusal is not None,
		"a stored record refuses `save()` after a field was changed",
		type(refusal).__name__ if refusal else "save() succeeded",
	)

	if refusal is not None:
		recorder.note(f"save() refused with {type(refusal).__name__}: {str(refusal)[:160]}")

	stored_doc.reload()
	recorder.check(stored_doc.remarks == original, "and the stored value never changed")

	# The framework's own after-submit rule, asked directly rather than through a permission check
	# that fires first. Nothing is written: the document is only compared with its stored copy.
	changed = frappe.get_doc(TRANSFER, record)
	changed.remarks = f"{changed.remarks} — changed in memory"

	after_submit = thrown(changed._validate_update_after_submit)
	recorder.check(
		after_submit is not None and "after submission" in str(after_submit).lower(),
		"the framework's own rule refuses a changed submitted document",
		str(after_submit)[:160] if after_submit else "no error",
	)

	# 3.4 — deleting it.
	as_admin = thrown(lambda: frappe.delete_doc(TRANSFER, record, force=True))
	recorder.check(
		as_admin is not None and "cannot be deleted" in str(as_admin).lower(),
		"Administrator cannot delete a submitted record",
		str(as_admin)[:160] if as_admin else "deleted",
	)

	frappe.set_user(MANAGER_USER)
	as_manager = thrown(lambda: frappe.delete_doc(TRANSFER, record, force=True))
	frappe.set_user("Administrator")

	recorder.check(as_manager is not None, "a Manager cannot delete it either", str(as_manager)[:160])
	recorder.check(frappe.db.exists(TRANSFER, record), "and it is still there after both attempts")

	# 3.2 — what the Desk's read-only form is a picture of: the server's own answer.
	permissions = {
		user: {
			level: frappe.has_permission(TRANSFER, level, doc=record, user=user)
			for level in ("read", "write", "delete", "submit", "cancel", "amend")
		}
		for user in ("Administrator", MANAGER_USER)
	}
	recorder.note(f"permissions on the record: {permissions}")
	manager = permissions[MANAGER_USER]
	recorder.check(
		not manager["write"] and not manager["delete"] and not manager["cancel"] and not manager["amend"],
		"a Manager — the highest level this app grants — may not write, delete, cancel or amend one",
		str(manager),
	)
	recorder.check(
		permissions["Administrator"]["write"] is True,
		"Administrator bypasses the permission layer entirely, which is why 3.4 is asked of the "
		"framework rather than of the permissions",
		str(permissions["Administrator"]),
	)

	return {"record": record, "token": preview["token"]}


# --- check: pair B ------------------------------------------------------------------------


def check_pair_b(recorder, fact):
	source = fact["source_b"]
	target = fact["target_b"]
	live = fact["live_b"]
	label = fact["labels_b"][1]

	first = call("preview_serial_transfer", sales_invoice=source, target_invoice=target)
	rows = outcome_rows(first)

	recorder.note(f"pair B preview counts: {first['counts']}")
	recorder.check(
		"ACKNOWLEDGEMENT_MISSING" in {row["outcome"] for row in first["serials"]},
		"the label that is live elsewhere comes back as a question, not a move",
		str({row["outcome"] for row in first["serials"]}),
	)
	recorder.check(
		rows[label]["answerable"] is True,
		"and the window is told an answer would change it",
		str(rows[label]),
	)
	recorder.check(rows[label]["message"], "with a sentence saying why", rows[label]["message"])

	# 4.6 — confirmed without acknowledging it.
	answer = call(
		"apply_serial_transfer",
		sales_invoice=source,
		target_invoice=target,
		token=first["token"],
	)
	frappe.db.commit()
	fresh()

	record = answer["transfer"]
	refused = frappe.db.get_value(
		TRANSFER_ITEM,
		{"parent": record, "reason": "ACKNOWLEDGEMENT_MISSING"},
		["serial_no", "result", "acknowledged"],
		as_dict=True,
	)

	recorder.check(bool(refused), "the unacknowledged label is written to the record as refused")
	recorder.check(
		refused and refused.result == "Refused" and not refused.acknowledged,
		"with no acknowledgment claimed",
		str(refused),
	)
	recorder.check(
		label not in {row[1] for row in assignments_of(target)},
		"and it was not registered on the amendment",
	)

	before = len(assignments_of(target))

	# 4.7 — the same preview, with the acknowledgment.
	second = call(
		"preview_serial_transfer",
		sales_invoice=source,
		target_invoice=target,
		acknowledged=[label],
	)
	second_rows = outcome_rows(second)
	recorder.check(
		second_rows[label]["outcome"] == "Moved",
		"acknowledged, it becomes a move",
		str(second_rows[label]),
	)
	recorder.check(second["counts"]["acknowledged"] == 1, "and the plan counts one acknowledgment")

	applied = call(
		"apply_serial_transfer",
		sales_invoice=source,
		target_invoice=target,
		acknowledged=[label],
		token=second["token"],
	)
	frappe.db.commit()
	fresh()
	after = assignments_of(target)

	recorder.check(
		len(after) == before + 1, "the acknowledged label is registered", f"{before} → {len(after)}"
	)
	stored = frappe.db.get_value(
		TRANSFER_ITEM,
		{"parent": applied["transfer"], "serial_no": label},
		["result", "acknowledged", "target_row"],
		as_dict=True,
	)
	recorder.check(
		stored and stored.result == "Moved" and stored.acknowledged == 1,
		"and the record says it moved with the operator's acknowledgment",
		str(stored),
	)
	recorder.check(
		frappe.db.get_value(TRANSFER, applied["transfer"], "acknowledged_count") == 1,
		"the record counts one acknowledgment",
	)

	# The label is now on three documents at once, which is a property of this app rather than a
	# defect: it has never had a site-wide uniqueness rule over serial text, and the plan's phase
	# note says so — the acknowledgment is what makes the overlap the operator's decision.
	normalized = frappe.db.get_value(SERIAL, {"serial_no": label}, "normalized_serial")
	invoices = sorted(
		{
			row.sales_invoice
			for row in frappe.get_all(
				ASSIGNMENT,
				filters={"normalized_serial": normalized},
				fields=["sales_invoice"],
				limit_page_length=0,
			)
		}
	)
	recorder.check(
		{source, target, live} <= set(invoices),
		"the acknowledged label is on the cancelled invoice, the amendment and the live one",
		f"{normalized}: {invoices}",
	)

	# 4.8 — a target that does not amend the source.
	refusal = thrown(lambda: call("preview_serial_transfer", sales_invoice=source, target_invoice=live))
	recorder.check(
		refusal is not None and transfer_rules.CODE_TARGET_NOT_AMENDMENT in str(refusal),
		"a target that is not the amendment is refused by code",
		str(refusal)[:200],
	)
	recorder.check(
		frappe.db.count(TRANSFER, {"source_invoice": source}) == 2,
		"and the refusal wrote no record",
	)

	# A stale token: what a window left open while somebody else used the site sends.
	stale = thrown(
		lambda: call(
			"apply_serial_transfer",
			sales_invoice=source,
			target_invoice=target,
			token="0" * 64,
		)
	)
	recorder.check(stale is not None, "a token that matches no selection is refused", str(stale)[:200])


# --- check: the record as a document -------------------------------------------------------


def check_record_moves_nowhere_else(recorder):
	"""(3.1) A record exists for each attempt and none of them is a draft."""
	rows = frappe.get_all(TRANSFER, fields=["name", "docstatus"], limit_page_length=0)
	drafts = [row.name for row in rows if row.docstatus != 1]

	recorder.check(bool(rows), "there are audit records on the site", str(len(rows)))
	recorder.check(not drafts, "and every one of them is submitted", str(drafts))


# --- disarm -------------------------------------------------------------------------------


def disarm(recorder):
	"""Report what the run leaves behind. It deletes nothing, and that is the feature.

	A submitted invoice cannot be deleted by anyone — the framework refuses before any permission
	is consulted — and a cancelled one would take its reversal entries with it. The audit records
	are immutable on purpose. So the lab accumulates one pair per run, which is why the script
	numbers its runs rather than reusing a fixed remark.
	"""
	planted = frappe.get_all(
		INVOICE,
		filters={"remarks": ["like", f"{REMARK_PREFIX}%"]},
		fields=["name", "remarks", "docstatus"],
		limit_page_length=0,
	)
	records = frappe.get_all(TRANSFER, pluck="name", limit_page_length=0)

	recorder.note(
		f"{len(planted)} planted invoices remain, docstatus {sorted({r.docstatus for r in planted})}"
	)
	recorder.note(f"{len(records)} audit records remain: {', '.join(sorted(records))}")
	recorder.note("nothing was deleted: submitted documents and audit records cannot be")


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

	fact = fact_of(run)
	print(f"run number: {run}\n")

	if phase in ("arm", "run"):
		print("arm")
		arm(recorder, run)
		fact = fact_of(run)
		print()

	if not fact["source_a"] or not fact["target_a"]:
		print(f"run {run} is incomplete: arm it again (arm is idempotent)")

		return 2

	try:
		if phase in ("check", "run"):
			print("check — the transfer onto an amendment (plan phase 4)")
			check_pair_a(recorder, fact)
			print()
			check_pair_b(recorder, fact)
			print()
			print("check — the audit records (plan phase 3)")
			check_record_moves_nowhere_else(recorder)
			print()
	finally:
		if phase in ("disarm", "run"):
			print("disarm")
			try:
				disarm(recorder)
			except Exception:  # noqa: BLE001 — the report matters more than this phase
				traceback.print_exc()
			print()

	print("PASS" if not recorder.failures else f"FAIL ({recorder.failures} checks)")

	return 1 if recorder.failures else 0


if __name__ == "__main__":
	raise SystemExit(main(sys.argv))
