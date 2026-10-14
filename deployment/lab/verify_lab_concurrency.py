"""Two transfers onto one amendment at the same instant, in two processes.

`docs/integration-test-plan.md` phase 5, and the phase says why the obvious version of it is not
the test: two calls issued one after the other prove nothing about concurrency — the cap is simply
applied twice, and the second answer is about a site the first one already changed. Both callers
here are separate processes that connect, authenticate and then **wait on a wall-clock instant**,
so when the clock reaches it both are already inside the endpoint. That is the shape
`verify_scan_station.py` uses for the row cap, for the same reason.

The invoice is a cancelled one holding twenty labels and an amendment that invoices ten, so the
two callers between them want twenty places in a row that has ten. What is asserted afterwards is
the site, not the answers: ten registrations on the amendment's row and no more, and a record of
everything that was attempted.

The second half is failure injection. A request that names a serial the source does not hold is
refused whole — that is the one state in which the confirmed set and the moved set would differ —
and the point of the check is that nothing at all is written by it: no registration, no record,
and the source untouched. Rollback is asserted the same way, by counting before and after.

    arm      plant this run's pair: a cancelled invoice with 20 labels, an amendment invoicing 10
    check    the two races and the refusal, with the site counted before and after each
    disarm   report what the run leaves behind; nothing is deleted
    run      arm, check, disarm

Run it inside the lab's backend container, from the sites directory:

    docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab \\
      -f deployment/lab/compose.isolation-lab.yaml \\
      exec -T -w /home/frappe/frappe-bench/sites backend \\
      /home/frappe/frappe-bench/env/bin/python \\
      /home/frappe/frappe-bench/apps/smartprocess_logistics/deployment/lab/verify_lab_concurrency.py run
"""

import json
import os
import subprocess
import sys
import time
import traceback

import frappe

from smartprocess_logistics.api import endpoints

SITE = "sp_logistics_lab"
SITES_PATH = "/home/frappe/frappe-bench/sites"

COMPANY = "SP LAB COMPANY"
INVOICE = "Sales Invoice"
SERIAL = "Logistics Serial"
ASSIGNMENT = "Logistics Serial Assignment"
TRANSFER = "Logistics Serial Transfer"

REMARK_PREFIX = "planted by verify_lab_concurrency.py"

SOURCE_QTY = 20
TARGET_QTY = 10

RACE_PREFIX = "RACE_RESULT "


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
		callable_(*args, **kwargs)
	except Exception as error:  # noqa: BLE001 — the message is the evidence
		return error

	return None


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


def labels_of(invoice_name):
	rows = frappe.get_all(
		ASSIGNMENT,
		filters={"sales_invoice": invoice_name},
		fields=["logistics_serial"],
		order_by="creation asc, name asc",
		limit_page_length=0,
	)
	names = [row.logistics_serial for row in rows if row.logistics_serial]
	texts = {}

	if names:
		for serial in frappe.get_all(
			SERIAL, filters={"name": ["in", names]}, fields=["name", "serial_no"], limit_page_length=0
		):
			texts[serial.name] = serial.serial_no

	return [texts.get(name) for name in names]


# --- arm ----------------------------------------------------------------------------------


def item_code():
	return frappe.db.get_value(SERIAL, {"item_code": ("is", "set")}, "item_code")


def plant_source(text, qty, labels):
	abbr = frappe.db.get_value("Company", COMPANY, "abbr")
	customer = frappe.db.get_value(INVOICE, {"company": COMPANY}, "customer")

	invoice = frappe.get_doc(
		{
			"doctype": INVOICE,
			"company": COMPANY,
			"customer": customer,
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
	invoice.submit()

	call(
		"add_row_serials",
		sales_invoice=invoice.name,
		sales_invoice_item=invoice.items[0].name,
		serials=list(labels),
	)
	frappe.db.commit()

	invoice.reload()
	invoice.cancel()
	frappe.db.commit()

	return invoice.name


def amend(source_name, text, qty):
	document = frappe.copy_doc(frappe.get_doc(INVOICE, source_name))
	document.amended_from = source_name
	document.remarks = text
	document.items[0].qty = qty
	document.insert()
	document.submit()

	return document.name


def arm(recorder, run):
	frappe.set_user("Administrator")

	source = invoice_by_remark(remark(run, "source"))

	if source:
		recorder.note(f"run {run}'s source is already planted: {source}")
	else:
		source = plant_source(
			remark(run, "source"),
			SOURCE_QTY,
			[f"LAB-CONC-{run}-{index:02d}" for index in range(1, SOURCE_QTY + 1)],
		)

	target = frappe.db.get_value(INVOICE, {"amended_from": source}, "name")

	if target:
		recorder.note(f"and its amendment is too: {target}")
	else:
		target = amend(source, remark(run, "amendment"), TARGET_QTY)

	# Committed here rather than at the end of `check`. The race is between **processes**, and an
	# amendment that exists only inside this process's transaction is an amendment the two callers
	# cannot find: the first run of this script proved it, by refusing both of them with "invoice
	# not found" while the parent could read it perfectly.
	frappe.db.commit()

	recorder.note(f"run {run}: {source} ({SOURCE_QTY} labels, cancelled) → {target} ({TARGET_QTY})")

	return run


# --- the race -----------------------------------------------------------------------------


def race_child(argv):
	"""One caller, released at an instant the parent chooses."""
	deadline = float(argv[2])
	source = argv[3]
	target = argv[4]
	token = argv[5]
	serials = [value for value in argv[6].split(",") if value]

	connect()
	frappe.set_user("Administrator")

	while time.time() < deadline:
		time.sleep(0.001)

	try:
		answer = call(
			"apply_serial_transfer",
			sales_invoice=source,
			target_invoice=target,
			serials=serials,
			token=token,
		)
		frappe.db.commit()
		result = {
			"ok": True,
			"moved": answer["counts"]["moved"],
			"refused": answer["counts"]["refused"],
			"record": answer["transfer"],
		}
	except Exception as error:  # noqa: BLE001 — the refusal is the result
		frappe.db.rollback()
		result = {
			"ok": False,
			"kind": type(error).__name__,
			"error": str(error)[:400],
			"code": getattr(error, "code", None),
			"where": [line.strip() for line in traceback.format_exc().splitlines() if "/apps/" in line],
		}

	print(RACE_PREFIX + json.dumps(result, ensure_ascii=False))

	return 0


def race(recorder, source, target, labels):
	"""Two callers, ten labels each, released on the same instant."""
	halves = (labels[:TARGET_QTY], labels[TARGET_QTY : TARGET_QTY * 2])
	tokens = []

	frappe.set_user("Administrator")

	for half in halves:
		preview = call("preview_serial_transfer", sales_invoice=source, target_invoice=target, serials=half)
		tokens.append(preview["token"])

	start = time.time() + 4
	script = os.path.abspath(__file__)

	children = [
		subprocess.Popen(
			[
				sys.executable,
				script,
				"race-child",
				str(start),
				source,
				target,
				tokens[index],
				",".join(halves[index]),
			],
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
		)
		for index in range(2)
	]

	results = []

	for child in children:
		out, err = child.communicate(timeout=180)

		line = next((row for row in out.splitlines() if row.startswith(RACE_PREFIX)), None)
		results.append(
			json.loads(line[len(RACE_PREFIX) :])
			if line
			else {"ok": False, "kind": "no-result", "error": (err or out)[-400:]}
		)

	return results


# --- check --------------------------------------------------------------------------------


def target_registrations(target):
	row = frappe.get_doc(INVOICE, target).items[0]

	return frappe.db.count(ASSIGNMENT, {"sales_invoice": target, "sales_invoice_item": row.name})


def fresh():
	"""End this process's read view, so the next count is of the site and not of a snapshot.

	The callers commit in their own processes, and this one is under MariaDB's `REPEATABLE READ`:
	a count here would answer with the site as it was when this transaction first looked, which is
	*before* the race. Measured on the first run of this script — the race moved ten labels and
	the parent counted zero — so it is a rollback rather than a commit, because this process has
	nothing of its own to keep.
	"""
	frappe.db.rollback()


def check_the_race(recorder, source, target, labels):
	fresh()

	before_records = frappe.db.count(TRANSFER, {"source_invoice": source})
	results = race(recorder, source, target, labels)
	fresh()

	for index, result in enumerate(results):
		recorder.note(f"caller {index + 1}: {json.dumps(result, ensure_ascii=False)[:300]}")

	recorder.check(
		any(result.get("ok") for result in results),
		"at least one caller completed",
		str([result.get("ok") for result in results]),
	)

	# A loser that wrote a record wrote one with nothing moved: every label it wanted is already on
	# the target by the time it looks again, or the row has no room for it.
	recorder.check(
		all(
			result.get("ok") or result.get("kind") in ("ValidationError", "DuplicateEntryError")
			for result in results
		),
		"every caller that did not complete was refused, not crashed",
		str([result.get("kind") for result in results]),
	)
	recorder.check(
		all("هم‌زمان" in result.get("error", "") or result.get("ok") for result in results),
		"a lost race is told so in words an operator can act on, not in a MariaDB error",
		str([result.get("error", "")[:120] for result in results if not result.get("ok")]),
	)

	held = target_registrations(target)
	recorder.check(
		held == TARGET_QTY,
		f"the amendment's row holds exactly {TARGET_QTY} afterwards — not twenty",
		str(held),
	)

	records = frappe.get_all(
		TRANSFER,
		filters={"source_invoice": source},
		fields=["name", "moved_count"],
		limit_page_length=0,
	)
	moved = sum(row.moved_count or 0 for row in records)

	recorder.note(f"records: {[(row.name, row.moved_count) for row in records]}")
	recorder.check(
		len(records) in (before_records + 1, before_records + 2),
		"one or two records were written, one per attempt that got as far as writing",
		str(len(records)),
	)
	recorder.check(
		moved == TARGET_QTY,
		"and the moves they record add up to the ten places the row had",
		f"{moved} moved across {len(records) - before_records} new records",
	)

	return results


def check_the_refusal(recorder, source, target, labels):
	"""A request naming a serial the source does not hold is refused whole, and writes nothing."""
	fresh()

	before = {
		"target": target_registrations(target),
		"records": frappe.db.count(TRANSFER, {"source_invoice": source}),
		"source": frappe.db.count(ASSIGNMENT, {"sales_invoice": source}),
	}

	stale = [labels[0], "LAB-CONC-NOT-ON-THIS-INVOICE"]
	preview = call("preview_serial_transfer", sales_invoice=source, target_invoice=target, serials=stale)

	recorder.check(
		preview["stale"] and preview["stale"][0]["message"],
		"the preview names the label the source does not hold",
		str(preview["stale"]),
	)

	refused = thrown(
		call,
		"apply_serial_transfer",
		sales_invoice=source,
		target_invoice=target,
		serials=stale,
		token=preview["token"],
	)
	frappe.db.rollback()

	recorder.check(refused is not None, "the apply refuses the whole request", str(refused)[:200])

	fresh()

	after = {
		"target": target_registrations(target),
		"records": frappe.db.count(TRANSFER, {"source_invoice": source}),
		"source": frappe.db.count(ASSIGNMENT, {"sales_invoice": source}),
	}

	recorder.check(
		after == before,
		"and nothing was written: registrations, records and the source all unchanged",
		f"{before} → {after}",
	)

	# Idempotency: the same transfer confirmed again moves nothing and registers nothing twice.
	preview = call("preview_serial_transfer", sales_invoice=source, target_invoice=target)
	second = call(
		"apply_serial_transfer",
		sales_invoice=source,
		target_invoice=target,
		token=preview["token"],
	)
	frappe.db.commit()

	recorder.check(
		second["counts"]["moved"] == 0,
		"confirming the whole transfer again moves nothing",
		str(second["counts"]),
	)
	recorder.check(
		target_registrations(target) == before["target"],
		"and registers nothing twice",
		f"{before['target']} → {target_registrations(target)}",
	)


# --- disarm -------------------------------------------------------------------------------


def disarm(recorder):
	planted = frappe.get_all(
		INVOICE,
		filters={"remarks": ["like", f"{REMARK_PREFIX}%"]},
		pluck="name",
		limit_page_length=0,
	)
	records = frappe.get_all(TRANSFER, pluck="name", limit_page_length=0)

	recorder.note(f"{len(planted)} planted invoices and {len(records)} audit records remain")
	recorder.note("nothing was deleted")


# --- entry point --------------------------------------------------------------------------


def main(argv):
	if len(argv) > 1 and argv[1] == "race-child":
		return race_child(argv)

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

	source = invoice_by_remark(remark(run, "source"))
	target = frappe.db.get_value(INVOICE, {"amended_from": source}, "name")

	if not source or not target:
		print(f"run {run} is incomplete: arm it again")

		return 2

	labels = labels_of(source)

	try:
		if phase in ("check", "run"):
			print("check — two callers for ten places (plan phase 5)")
			check_the_race(recorder, source, target, labels)
			print()
			print("check — a refused request writes nothing")
			check_the_refusal(recorder, source, target, labels)
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
