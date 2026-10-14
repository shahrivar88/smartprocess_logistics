"""A recovery rehearsed end to end on the isolated lab, with disposable records only.

The phase-19 scripts prove each act separately: the transfer moves serials, the ordinary restore
places rows, the historical one places cancelled rows, the records restore places audit records.
This one proves the thing an operator actually has to trust — that a **file taken now can put the
site back** — and the two properties that make it trustworthy: nothing that was there is
disturbed, and a restoration that cannot complete writes **nothing at all**.

What it does, in order, on records this script plants for itself:

1. exports the site's own file (the artefact a person keeps), and counts the four tables;
2. deletes one registration from a **live** invoice and puts it back from the file — the ordinary
   restore's job — then checks the row's provenance came from the file and not from this run;
3. deletes one registration from a **cancelled** invoice and puts it back through the historical
   restore, which is the only path allowed to, then runs it a second time and requires zero rows;
4. moves the cancelled invoice's serials onto its amendment and reads the audit record back:
   submitted, marked as performed *here* (no `restored_*`), and its rows naming the source
   registrations, with the source itself unchanged;
5. places the site's own audit record from a file that reissues its name, and checks
   `restored_from`/`restored_on`/`restored_by` beside the original `owner` and `creation`;
6. and then the one that matters for a recovery that goes wrong: a two-record file whose second
   record carries a name this site already uses — taken by a record of **another company**, which
   the pre-flight cannot see — is refused at the insert, and the first record must not survive it.

Step 6 needs an audit record of another company, so `arm` plants one there too. Every document is
disposable, and nothing is deleted except the two registrations this script removes on purpose, on
invoices it planted itself.

    arm      plant this run's pairs: one in the site's own company, one in the other company
    check    the six steps above, with the four tables counted around each
    disarm   report what remains; the recovery is left in place
    run      arm, check, disarm

A `check` must follow a fresh `arm`: step 4 transfers a pair's serials, and a second check against
the same pair finds them already there.

Run it inside the lab's backend container, from the sites directory:

    docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab \\
      -f deployment/lab/compose.isolation-lab.yaml \\
      exec -T -w /home/frappe/frappe-bench/sites backend \\
      /home/frappe/frappe-bench/env/bin/python \\
      /home/frappe/frappe-bench/apps/smartprocess_logistics/deployment/lab/verify_lab_recovery.py run
"""

import json
import sys

import frappe

from smartprocess_logistics.api import endpoints
from smartprocess_logistics.core import export

SITE = "sp_logistics_lab"
SITES_PATH = "/home/frappe/frappe-bench/sites"

INVOICE = "Sales Invoice"
SERIAL = "Logistics Serial"
ASSIGNMENT = "Logistics Serial Assignment"
TRANSFER = "Logistics Serial Transfer"
TRANSFER_ITEM = "Logistics Serial Transfer Item"

COMPANY = "SP LAB COMPANY"
OTHER_COMPANY = "SP LAB OTHER"

ARCHIVIST_USER = "lab-archivist@example.test"

REMARK_PREFIX = "planted by verify_lab_recovery.py"
NAME_PREFIX = "LSTR-REC"

SOURCE_QTY = 6
TARGET_QTY = 4
OTHER_QTY = 2
LIVE_LABELS = 3


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
	"""The exception a refusal raised, or the answer if it was not refused."""
	try:
		return callable_(*args, **kwargs)
	except Exception as error:  # noqa: BLE001 — the message is the evidence
		return error


def fresh():
	"""End this process's read view, so the next count is of the site and not of a snapshot."""
	frappe.db.rollback()


def counts():
	return {table: frappe.db.count(table) for table in (SERIAL, ASSIGNMENT, TRANSFER, TRANSFER_ITEM)}


# --- the run, and the records it plants ---------------------------------------------------


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


def by_remark(text):
	return frappe.db.get_value(INVOICE, {"remarks": text}, "name", order_by="creation asc")


def label_rows(invoice_name):
	return frappe.get_all(
		ASSIGNMENT,
		filters={"sales_invoice": invoice_name},
		fields=[
			"name",
			"logistics_serial",
			"item_code",
			"normalized_serial",
			"sales_invoice_item",
			"invoice_item_idx",
			"creation",
			"owner",
		],
		order_by="creation asc, name asc",
		limit_page_length=0,
	)


def item_code():
	return frappe.db.get_value(SERIAL, {"item_code": ("is", "set")}, "item_code")


def plant_invoice(company, text, qty):
	abbr = frappe.db.get_value("Company", company, "abbr")
	invoice = frappe.get_doc(
		{
			"doctype": INVOICE,
			"company": company,
			"customer": frappe.db.get_value(INVOICE, {"company": company}, "customer"),
			"posting_date": frappe.utils.nowdate(),
			"debit_to": f"Debtors - {abbr}",
			"remarks": text,
			"items": [{"item_code": item_code(), "qty": qty, "rate": 1, "income_account": f"Sales - {abbr}"}],
		}
	).insert()

	return invoice


def plant_pair(recorder, company, run, role, qty, labels):
	"""One cancelled invoice with labels on it, and its amendment — the shape a transfer needs."""
	source = by_remark(remark(run, f"{role} source"))
	amendment = frappe.db.get_value(INVOICE, {"amended_from": source}, "name") if source else None

	if not source:
		invoice = plant_invoice(company, remark(run, f"{role} source"), qty)
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

		source = invoice.name

	if not amendment:
		document = frappe.copy_doc(frappe.get_doc(INVOICE, source))
		document.amended_from = source
		document.remarks = remark(run, f"{role} amendment")
		document.items[0].qty = min(TARGET_QTY, qty)
		document.insert()
		document.submit()
		frappe.db.commit()

		amendment = document.name

	recorder.note(f"run {run}: {source} → {amendment} ({company})")

	return source, amendment


def arm(recorder, run):
	frappe.set_user("Administrator")

	live = by_remark(remark(run, "live invoice"))

	if not live:
		invoice = plant_invoice(COMPANY, remark(run, "live invoice"), SOURCE_QTY)
		invoice.submit()

		call(
			"add_row_serials",
			sales_invoice=invoice.name,
			sales_invoice_item=invoice.items[0].name,
			serials=[f"LAB-REC-L{run}-{index:02d}" for index in range(1, LIVE_LABELS + 1)],
		)
		frappe.db.commit()
		live = invoice.name

		recorder.note(f"run {run}: live {live} with {LIVE_LABELS} disposable registrations")

	source, amendment = plant_pair(
		recorder,
		COMPANY,
		run,
		"main",
		SOURCE_QTY,
		[f"LAB-REC-S{run}-{index:02d}" for index in range(1, SOURCE_QTY + 1)],
	)
	other_source, other_amendment = plant_pair(
		recorder,
		OTHER_COMPANY,
		run,
		"other",
		OTHER_QTY,
		[f"LAB-REC-O{run}-{index:02d}" for index in range(1, OTHER_QTY + 1)],
	)

	# The other company needs a *record*, not just a pair: step 6 collides on one of its names,
	# and a name that does not exist collides with nothing.
	if not frappe.db.exists(TRANSFER, {"source_invoice": other_source}):
		preview = call("preview_serial_transfer", sales_invoice=other_source, target_invoice=other_amendment)
		call(
			"apply_serial_transfer",
			sales_invoice=other_source,
			target_invoice=other_amendment,
			token=preview["token"],
		)
		frappe.db.commit()

		recorder.note(f"run {run}: and a record of its own, for step 6 to collide with")

	frappe.db.commit()

	return run


# --- the file, and what a restore puts back ------------------------------------------------


def exported():
	return call("export_serials")["document"]


def preview_endpoint(mode):
	return {
		export.MODE_LIVE: "preview_serial_restore",
		export.MODE_HISTORY: "preview_cancelled_serial_history",
		export.MODE_RECORDS: "preview_restore_audit_records",
	}[mode]


def restore(content, endpoint="apply_serial_restore", mode=export.MODE_LIVE):
	"""One restore, taken through the preview the way the window takes it."""
	preview = call(preview_endpoint(mode), content=content)

	return preview, call(endpoint, content=content, token=preview["token"])


def delete_one(invoice_name):
	"""One registration off a disposable invoice, and the row that was removed."""
	rows = label_rows(invoice_name)

	if not rows:
		return None

	call("delete_assignments", names=[rows[-1].name])
	frappe.db.commit()
	fresh()

	return rows[-1]


def row_of(document, name):
	return next(row for row in document["assignments"] if row["name"] == name)


def child_of(document, parent):
	return [row for row in document["transfer_items"] if row["parent"] == parent]


def record_of(document, source_invoice):
	return next(row for row in document["transfers"] if row["source_invoice"] == source_invoice)


def seal(base, transfers, items):
	return export.build(
		base["serials"],
		base["assignments"],
		transfers,
		items,
		site=base["site"],
		exported_on=base["exported_on"],
		exported_by=base["exported_by"],
		app_version=base["app_version"],
		normalization_version=base["normalization_version"],
	)


def reissue(document, wanted):
	"""The file's records, renamed where `wanted` says so, with their rows following them."""
	transfers = []
	items = []
	counter = 0

	for record in document["transfers"]:
		new = wanted.get(record["name"])
		row = dict(record)

		if new:
			row["name"] = new

		transfers.append(row)

		for item in child_of(document, record["name"]):
			child = dict(item)

			if new:
				counter += 1
				child["parent"] = new
				child["name"] = f"{new}-{counter:02d}"

			items.append(child)

	return seal(document, transfers, items)


def next_names(count):
	"""Free audit-record names, derived from the ones this script has already placed.

	A counter over the *existing* names rather than the run number: this script can be run again
	without arming a new pair, and a name built from the run number alone met a record an earlier
	invocation had left behind — refused, correctly, as a duplicate row inside the file.
	"""
	existing = frappe.get_all(
		TRANSFER,
		filters={"name": ["like", f"{NAME_PREFIX}-%"]},
		pluck="name",
		limit_page_length=0,
	)
	suffixes = [int(name.rsplit("-", 1)[1]) for name in existing if name.rsplit("-", 1)[1].isdigit()]
	start = max(suffixes, default=0)

	return [f"{NAME_PREFIX}-{start + index + 1:03d}" for index in range(count)]


# --- the checks -----------------------------------------------------------------------------


def check_a_no_op_restore(recorder, document, run):
	"""Steps 1 and 2: the file the site already is, and a live row put back from it."""
	fresh()
	before = counts()
	content = json.dumps(document, ensure_ascii=False)

	_preview, answer = restore(content)
	frappe.db.commit()
	fresh()

	recorder.check(
		answer["counts"]["created"] == {"serials": 0, "assignments": 0},
		"a restore of the file the site already is creates nothing",
		str(answer["counts"]),
	)
	recorder.check(
		counts() == before, "and the four tables are exactly as they were", f"{before} → {counts()}"
	)

	live = by_remark(remark(run, "live invoice"))
	removed = delete_one(live)

	recorder.check(removed is not None, "a registration was removed from the live invoice")
	recorder.check(
		len(label_rows(live)) == LIVE_LABELS - 1,
		"and the invoice holds one fewer",
		str(len(label_rows(live))),
	)

	_preview, answer = restore(content)
	frappe.db.commit()
	fresh()

	restored = [row for row in label_rows(live) if row.normalized_serial == removed.normalized_serial]
	from_file = row_of(document, removed.name)

	recorder.check(bool(restored), "the ordinary restore put it back", str(len(label_rows(live))))
	recorder.check(
		len(restored) == 1 and restored[0].item_code == removed.item_code,
		"with the identity it had",
		str(restored),
	)
	recorder.check(
		bool(restored)
		and str(restored[0].creation) == str(from_file["creation"])
		and restored[0].owner == from_file["owner"],
		"and the provenance the file carried, not this run's clock",
		f"{restored[0].creation}/{restored[0].owner} vs {from_file['creation']}/{from_file['owner']}"
		if restored
		else "nothing came back",
	)


def check_a_cancelled_row(recorder, document, source):
	"""Step 3: the historical restore is the only path that may put one back, and it is idempotent."""
	removed = delete_one(source)

	recorder.check(removed is not None, "a registration was removed from the cancelled invoice")

	content = json.dumps(document, ensure_ascii=False)
	preview, _answer = restore(content)

	recorder.check(
		preview["by_reason"].get(export.REASON_INVOICE_CANCELLED, 0) >= 1,
		"the ordinary preview refuses a row on a cancelled invoice",
		str(preview["by_reason"]),
	)

	_preview, answer = restore(content, "restore_cancelled_serial_history", export.MODE_HISTORY)
	frappe.db.commit()
	fresh()

	back = [row for row in label_rows(source) if row.normalized_serial == removed.normalized_serial]

	recorder.check(bool(back), "the historical restore put it back", str(len(label_rows(source))))
	recorder.check(
		bool(back) and str(back[0].creation) == str(row_of(document, removed.name)["creation"]),
		"with the file's provenance",
		str(back),
	)

	_preview, again = restore(content, "restore_cancelled_serial_history", export.MODE_HISTORY)
	frappe.db.commit()
	fresh()

	recorder.check(
		again["counts"]["created"]["assignments"] == 0,
		"and running it again creates nothing: a duplicate is skipped, never written twice",
		str(again["counts"]),
	)
	recorder.check(
		len(label_rows(source)) == SOURCE_QTY,
		"the cancelled invoice holds its own count again",
		str(len(label_rows(source))),
	)


def check_the_transfer_and_its_record(recorder, source, amendment):
	"""Step 4: the act, its record, and the source left exactly as it was."""
	before_source = label_rows(source)
	before_masters = frappe.db.count(SERIAL)

	preview = call("preview_serial_transfer", sales_invoice=source, target_invoice=amendment)
	counts_ = preview["counts"]

	recorder.check(counts_["source"] == SOURCE_QTY, "the source holds what it held", str(counts_))
	recorder.check(
		counts_["moved"] == min(TARGET_QTY, SOURCE_QTY),
		"and the amendment takes what it invoices",
		str(counts_),
	)

	answer = call(
		"apply_serial_transfer", sales_invoice=source, target_invoice=amendment, token=preview["token"]
	)
	frappe.db.commit()
	fresh()

	record = answer["transfer"]
	stored = frappe.db.get_value(
		TRANSFER,
		record,
		["docstatus", "moved_count", "refused_count", "restored_from", "restored_by", "owner"],
		as_dict=True,
	)

	recorder.check(stored.docstatus == 1, "the record is submitted", str(stored))
	recorder.check(
		not stored.restored_from and not stored.restored_by,
		"and says nothing about a restoration: this act was performed here",
		str(stored),
	)
	recorder.check(
		stored.moved_count + stored.refused_count == SOURCE_QTY,
		"every serial of the source is accounted for",
		str(stored),
	)
	recorder.check(
		label_rows(source) == before_source,
		"the cancelled invoice is byte-for-byte what it was",
		"a source row changed",
	)
	recorder.check(
		frappe.db.count(SERIAL) == before_masters,
		"and no serial master was created or removed",
		f"{before_masters} → {frappe.db.count(SERIAL)}",
	)

	rows = frappe.get_all(
		TRANSFER_ITEM,
		filters={"parent": record},
		fields=["serial_no", "result", "source_assignment"],
		limit_page_length=0,
	)

	recorder.check(
		all(row.source_assignment for row in rows) and len(rows) == SOURCE_QTY,
		"its rows name the source registration of every serial",
		str(len(rows)),
	)

	return record


def check_the_records_restore(recorder, document, run):
	"""Step 5: an audit record itself, with its marker and its original actor."""
	record = record_of(document, by_remark(remark(run, "main source")))
	new_name = next_names(1)[0]
	crafted = reissue(document, {record["name"]: new_name})
	content = json.dumps(crafted, ensure_ascii=False)

	_preview, answer = restore(content, "restore_audit_records", export.MODE_RECORDS)
	frappe.db.commit()
	fresh()

	placed = list(answer["created"]["transfers"])

	recorder.check(placed == [new_name], "the record is placed under the name the file gives it", str(placed))

	stored = frappe.db.get_value(
		TRANSFER,
		new_name,
		["docstatus", "restored_from", "restored_by", "restored_on", "owner", "creation"],
		as_dict=True,
	)

	recorder.check(stored.docstatus == 1, "and it is submitted", str(stored))
	recorder.check(
		stored.restored_from == crafted["site"] and stored.restored_by,
		"saying which site it came from and who restored it",
		str(stored),
	)
	recorder.check(
		stored.owner == record["owner"] and str(stored.creation) == str(record["creation"]),
		"while the act still belongs to whoever performed it",
		f"{stored.owner}/{stored.creation} vs {record['owner']}/{record['creation']}",
	)

	placed_rows = frappe.get_all(
		TRANSFER_ITEM, filters={"parent": new_name}, pluck="name", limit_page_length=0
	)

	recorder.check(
		sorted(placed_rows) == sorted(row["name"] for row in child_of(crafted, new_name)),
		"with its own rows, under the names the file gives them",
		str(sorted(placed_rows)),
	)

	_preview, again = restore(content, "restore_audit_records", export.MODE_RECORDS)
	frappe.db.commit()
	fresh()

	recorder.check(
		again["created"]["transfers"] == [],
		"and a second attempt places nothing",
		str(again["created"]["transfers"]),
	)
	recorder.check(
		frappe.db.get_value(TRANSFER, new_name, "restored_on") == stored.restored_on,
		"leaving the record it found untouched",
		"the record was rewritten",
	)


def check_the_rollback(recorder, document, run, other_source):
	"""Step 6: a record that cannot be placed takes the records placed before it down with it.

	The collision is built to be invisible to the pre-flight, and that is the whole difficulty:
	`world.transfers` holds the record names of the caller's own companies, so a name taken by a
	record of **another** company is not in it, and a record whose invoice, serials and rows are
	all the caller's own passes every check and collides at the insert.

	The caller has to be restricted. As the Administrator the world is unscoped, the other
	company's name *is* in it, and the pre-flight refuses the file without writing — a correct
	answer, and not the transaction this step is about. It runs as the Archivist, who is restricted
	to one company and holds the role the endpoint asks for.

	The file is built for this step and nothing else: two records, both copies of the caller's own,
	one under a free name and one under the taken one. Building it out of the whole site export was
	the first attempt, and it kept meeting the archive validator over rows that had nothing to do
	with the collision.
	"""
	other_record = record_of(document, other_source)
	record = record_of(document, by_remark(remark(run, "main source")))
	children = child_of(document, record["name"])
	first, _second = next_names(2)

	def copy_of(name):
		header = dict(record)
		header["name"] = name
		rows = []

		for index, child in enumerate(children, start=1):
			copied = dict(child)
			copied["parent"] = name
			copied["name"] = f"{name}-{index:02d}"
			rows.append(copied)

		return header, rows

	first_header, first_children = copy_of(first)
	taken_header, taken_children = copy_of(other_record["name"])

	recorder.note(
		f"the file names {first} (free) and {taken_header['name']} (taken in {OTHER_COMPANY});"
		f" both copies of {record['name']}"
	)

	crafted = export.build(
		document["serials"],
		[],
		[first_header, taken_header],
		[*first_children, *taken_children],
		site=document["site"],
		exported_on=document["exported_on"],
		exported_by=document["exported_by"],
		app_version=document["app_version"],
		normalization_version=document["normalization_version"],
	)
	content = json.dumps(crafted, ensure_ascii=False)
	before = counts()

	frappe.set_user(ARCHIVIST_USER)

	try:
		refused = thrown(lambda: restore(content, "restore_audit_records", export.MODE_RECORDS))
	finally:
		frappe.set_user("Administrator")

	frappe.db.rollback()
	fresh()

	recorder.note(f"the refusal: {str(refused)[:200]}")

	if isinstance(refused, tuple):
		# No exception: the apply answered, which means the pre-flight saw something. Printing what
		# it saw is what turns "the file was refused" into which rule refused it.
		recorder.note(f"and it answered instead: {refused[1]['counts']}")

	recorder.check(refused is not None, "a file that cannot be placed is refused")
	recorder.check(
		refused is not None and "نام یکی از سابقه" in str(refused),
		"with the sentence about a name, not the one about a concurrent request",
		str(refused)[:200],
	)
	recorder.check(
		counts() == before,
		"and not one row of the four tables was left behind",
		f"{before} → {counts()}",
	)
	recorder.check(
		not frappe.db.exists(TRANSFER, first),
		"the record that was already inside the transaction is not there either",
		first,
	)
	recorder.check(
		frappe.db.exists(TRANSFER, other_record["name"]),
		"and the record whose name was taken is untouched",
		other_record["name"],
	)


# --- disarm ---------------------------------------------------------------------------------


def disarm(recorder):
	recorder.note(f"{len(planted_runs())} planted runs and {frappe.db.count(TRANSFER)} records remain")
	recorder.note("nothing was deleted: the two registrations removed on purpose were put back")


# --- entry point ----------------------------------------------------------------------------


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

	source = by_remark(remark(run, "main source"))
	amendment = by_remark(remark(run, "main amendment"))
	other_source = by_remark(remark(run, "other source"))

	if not (source and amendment and other_source):
		print(f"run {run} is incomplete: arm it again")

		return 2

	try:
		if phase in ("check", "run"):
			print("check — a file taken now, and what it puts back (steps 1-3)")
			check_a_no_op_restore(recorder, exported(), run)
			print()
			check_a_cancelled_row(recorder, exported(), source)
			print()

			print("check — the act, its record, and the source untouched (step 4)")
			check_the_transfer_and_its_record(recorder, source, amendment)
			print()

			print("check — an audit record, placed and never overwritten (step 5)")
			check_the_records_restore(recorder, exported(), run)
			print()

			print("check — a restoration that cannot complete writes nothing (step 6)")
			check_the_rollback(recorder, exported(), run, other_source)
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
