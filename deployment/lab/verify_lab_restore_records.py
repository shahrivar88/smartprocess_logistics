"""Placing the audit records themselves, with every refusal the phase names.

`docs/integration-test-plan.md` phase 8. The audit records are the one thing this app never
places by accident: a restored record says on its own face that it was restored, a name this site
already uses is refused rather than resolved, and a record goes in whole or not at all.

Those three decisions were approved in phase 18 and are asserted in the database-free tests. What
this script adds is that they hold on a real site, where the record is written **submitted** —
because `Document._save` checks `write` before it reads an explicit `docstatus`, and no role has
`write` on this DocType — and where the framework then refuses to change or delete it.

Every case is a crafted file: this site's own export, with the records renamed so that they are
placeable at all, and one deliberate defect per variant chosen from the seven refusals the plan
can produce. Crafting rather than waiting, because a name collision with a record that does not
exist yet is not something a run can wait for.

    arm      nothing: this phase reads what the earlier phases left, and needs no fixture of its own
    check    seven files, seven answers, and the site counted around each
    disarm   report what the run leaves behind; nothing is deleted
    run      arm, check, disarm

Run it inside the lab's backend container, from the sites directory:

    docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab \\
      -f deployment/lab/compose.isolation-lab.yaml \\
      exec -T -w /home/frappe/frappe-bench/sites backend \\
      /home/frappe/frappe-bench/env/bin/python \\
      /home/frappe/frappe-bench/apps/smartprocess_logistics/deployment/lab/verify_lab_restore_records.py run
"""

import json
import sys

import frappe

from smartprocess_logistics.api import endpoints
from smartprocess_logistics.core import authz, export

SITE = "sp_logistics_lab"
SITES_PATH = "/home/frappe/frappe-bench/sites"

INVOICE = "Sales Invoice"
TRANSFER = "Logistics Serial Transfer"
TRANSFER_ITEM = "Logistics Serial Transfer Item"
ASSIGNMENT = "Logistics Serial Assignment"

ARCHIVIST_USER = "lab-archivist@example.test"
EDITOR_USER = "lab-editor@example.test"

NAME_PREFIX = "LSTR-LAB"


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


def fresh():
	frappe.db.rollback()


# --- crafting -----------------------------------------------------------------------------


def base_document():
	document = call("export_serials")["document"]

	if not document["transfers"]:
		raise SystemExit("no audit record exists on this site: run phase 4 first")

	return document


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


def reissue(base, wanted, run, counters):
	"""The file's records, renamed where `wanted` says so, with their rows following them.

	Both halves have to move together: a child row carries its own name, and those names are
	unique over the site too, so a record renamed without its rows would collide on the child
	table instead — a different failure with the same cause.
	"""
	transfers = []
	items = []

	for record in base["transfers"]:
		new = wanted.get(record["name"])
		row = dict(record)
		rows = [item for item in base["transfer_items"] if item["parent"] == record["name"]]

		if new:
			row["name"] = new

		transfers.append(row)

		for item in rows:
			child = dict(item)

			if new:
				counters[0] += 1
				child["parent"] = new
				child["name"] = f"{new}-{counters[0]:02d}"

			items.append(child)

	return seal(base, transfers, items)


def new_name(run, index):
	return f"{NAME_PREFIX}-{run}-{index:02d}"


def variant_placeable(base, run):
	wanted = {
		record["name"]: new_name(run, index) for index, record in enumerate(base["transfers"][:2], start=1)
	}

	return reissue(base, wanted, run, [0]), wanted


def variant_colliding(base, run):
	"""One record renamed, one left exactly as it is: that name is already on this site."""
	kept = base["transfers"][0]["name"]
	wanted = {base["transfers"][1]["name"]: new_name(run, 11)}

	return reissue(base, wanted, run, [0]), kept, new_name(run, 11)


def variant_link_cancelled(base, run):
	"""One of the two renamed records points at an invoice that is cancelled *now*."""
	cancelled = frappe.db.get_value(INVOICE, {"docstatus": 2}, "name", order_by="creation desc")
	wanted = {
		base["transfers"][0]["name"]: new_name(run, 21),
		base["transfers"][1]["name"]: new_name(run, 22),
	}
	document = reissue(base, wanted, run, [0])

	for record in document["transfers"]:
		if record["name"] == new_name(run, 22):
			record["target_invoice"] = cancelled

	return seal(base, document["transfers"], document["transfer_items"]), new_name(run, 22)


def variant_unresolved(base, run):
	"""One row of a renamed record names a serial this site does not have.

	The record is chosen as the first one that actually moved a serial, because the row to break
	is one of its moved rows: a refusal that would be produced by any row of the file would not
	show that an unresolved serial takes the record down whole.
	"""
	moved = {item["parent"] for item in base["transfer_items"] if item.get("result") == "Moved"}
	chosen = next(record["name"] for record in base["transfers"] if record["name"] in moved)
	wanted = {chosen: new_name(run, 31)}
	document = reissue(base, wanted, run, [0])
	target = new_name(run, 31)

	for item in document["transfer_items"]:
		if item["parent"] == target and item.get("result") == "Moved":
			item["normalized_serial"] = "LABNOTONTHISSITE"
			item["serial_no"] = "LAB-NOT-ON-THIS-SITE"
			break
	else:
		raise SystemExit("no moved row was found to break: the fixture is not what it claims")

	return seal(base, document["transfers"], document["transfer_items"]), target


def variant_inconsistent(base, run):
	"""A renamed record whose counts no longer agree with its own rows."""
	wanted = {base["transfers"][0]["name"]: new_name(run, 41)}
	document = reissue(base, wanted, run, [0])
	target = new_name(run, 41)

	for record in document["transfers"]:
		if record["name"] == target:
			record["moved_count"] = (record.get("moved_count") or 0) + 1
			break

	return seal(base, document["transfers"], document["transfer_items"]), target


def file_of(document):
	return json.dumps(document, ensure_ascii=False)


# --- checks -------------------------------------------------------------------------------


def restored_fields(name):
	return frappe.db.get_value(
		TRANSFER,
		name,
		[
			"docstatus",
			"restored_from",
			"restored_on",
			"restored_by",
			"owner",
			"creation",
			"modified",
			"modified_by",
			"source_invoice",
			"target_invoice",
			"moved_count",
			"refused_count",
		],
		as_dict=True,
	)


def rows_of(name):
	return sorted(
		(row.name, row.serial_no, row.result, row.source_assignment)
		for row in frappe.get_all(
			TRANSFER_ITEM,
			filters={"parent": name},
			fields=["name", "serial_no", "result", "source_assignment"],
			limit_page_length=0,
		)
	)


def check_the_columns(recorder):
	"""8.1 — the three columns migrate was supposed to create."""
	columns = {
		row.column_name
		for row in frappe.db.sql(
			"select column_name from information_schema.columns where table_name = %s",
			("tabLogistics Serial Transfer",),
			as_dict=True,
		)
	}

	recorder.note(f"columns: {sorted(columns)}")

	for column in export.RESTORE_FIELDS:
		recorder.check(column in columns, f"the record table has `{column}`")


def check_the_role(recorder, file):
	"""8.12 — who may place a record, and with which sentence they are turned away.

	Two refusals, and they are not the same one: an Editor does not reach the *level* this path
	needs, and is told so; a Manager reaches it and is told the missing thing is a **role**.
	Asking only the first would not show the role gate at all, which is the whole reason the
	fourth role exists.
	"""
	refused = as_user(EDITOR_USER, lambda: thrown(call, "preview_restore_audit_records", content=file))
	recorder.check(refused is not None, "an Editor is refused at the preview", str(refused)[:200])

	refused = as_user(
		EDITOR_USER,
		lambda: thrown(call, "restore_audit_records", content=file, token="x"),
	)
	recorder.check(refused is not None, "and at the apply, before the token is read", str(refused)[:200])

	refused = as_user(
		ARCHIVIST_USER.replace("archivist", "manager"),
		lambda: thrown(call, "preview_restore_audit_records", content=file),
	)
	recorder.check(
		refused is not None and authz.ROLE_ARCHIVIST in str(refused),
		"a Manager without the Archivist role reaches the level and is refused by the role",
		str(refused)[:200],
	)


def check_placement(recorder, document, renamed):
	"""8.2 to 8.7 — the ordinary case, and the second attempt.

	`renamed` is the pair the variant reissued. The file carries every record this site has, and
	the ones left under their own names are already here: they are refused as name collisions, so
	the records offered for placing are exactly the renamed pair and not the whole file.
	"""
	file = file_of(document)
	wanted = sorted(renamed.values())

	preview = as_user(ARCHIVIST_USER, lambda: call("preview_restore_audit_records", content=file))

	recorder.note(f"records preview: {preview['to_create_records']}, file {preview['records_in_file']}")
	recorder.check(
		preview["records_in_file"] == len(document["transfers"]),
		"the preview reports how many records the file carries — all of them, not the placeable ones",
		f"{preview['records_in_file']} of {len(document['transfers'])}",
	)
	recorder.check(
		preview["to_create_records"]["transfers"] == len(wanted),
		"and offers every one of them for placing",
		str(preview["to_create_records"]),
	)

	answer = as_user(
		ARCHIVIST_USER,
		lambda: call("restore_audit_records", content=file, token=preview["token"]),
	)
	# The request boundary, which an in-process call does not get for free: without it every
	# check below reads a rollback and the records look as though they were never written.
	frappe.db.commit()
	fresh()

	created = list(answer["created"]["transfers"])
	recorder.note(f"created records: {created}")
	recorder.check(
		sorted(created) == sorted(wanted),
		"each record is placed under its own name",
		str(created),
	)

	for name in created:
		stored = restored_fields(name)

		recorder.check(stored.docstatus == 1, f"{name} is submitted", str(stored.docstatus))
		recorder.check(
			len(rows_of(name)) == stored.moved_count + stored.refused_count,
			f"{name} carries its own child rows",
			str(len(rows_of(name))),
		)
		recorder.check(
			stored.restored_from == document["site"],
			f"{name} says which site it came from",
			str(stored.restored_from),
		)
		recorder.check(
			stored.restored_by == ARCHIVIST_USER,
			f"{name} says who restored it",
			str(stored.restored_by),
		)
		recorder.check(
			str(stored.restored_on)[:10] == frappe.utils.nowdate(),
			f"{name} says when it was restored, which is today",
			str(stored.restored_on),
		)

		# The act still belongs to whoever performed it, which is what makes the marker
		# meaningful rather than decorative.
		from_file = next(record for record in document["transfers"] if record["name"] == name)
		# `name` is the reissued one, so its rows are the ones the variant renamed with it.
		recorder.check(
			stored.owner == from_file.get("owner") and str(stored.creation) == str(from_file.get("creation")),
			f"{name} keeps the original user and moment of the act",
			f"{stored.owner}/{stored.creation} vs {from_file.get('owner')}/{from_file.get('creation')}",
		)

	# 8.6 — a placed record is as immutable as any other submitted document.
	name = created[0]
	document_ = frappe.get_doc(TRANSFER, name)
	document_.remarks = "changed by the test"
	refused = thrown(document_.save)
	recorder.check(refused is not None, "a restored record refuses to be saved after a change")
	recorder.check(
		not frappe.db.exists(TRANSFER, {"name": name, "remarks": "changed by the test"}),
		"and the stored row never changed",
	)
	refused = thrown(lambda: frappe.delete_doc(TRANSFER, name, force=True))
	recorder.check(refused is not None, "and cannot be deleted", str(refused)[:160])

	# 8.7 — the same file again.
	before = {row: restored_fields(row) for row in created}
	second_preview = as_user(ARCHIVIST_USER, lambda: call("preview_restore_audit_records", content=file))
	second = as_user(
		ARCHIVIST_USER,
		lambda: call("restore_audit_records", content=file, token=second_preview["token"]),
	)
	fresh()

	after = {row: restored_fields(row) for row in created}

	recorder.check(
		second["created"]["transfers"] == [],
		"a second restore of the same file places nothing",
		str(second["created"]["transfers"]),
	)
	# The *uncapped* conflict list from the apply, and the two names this run placed: a count over
	# the whole file depends on how much the lab has accumulated, and a record of another company
	# is refused for its invoice rather than for its name — which is correct and not what this is
	# about.
	second_reasons = {row["row"]: row["reason"] for row in second["conflicts"]}

	recorder.check(
		all(second_reasons.get(row) == export.REASON_RECORD_EXISTS for row in created),
		"the two records this run placed are reported as names this site already has",
		str({row: second_reasons.get(row) for row in created}),
	)
	recorder.check(
		second_preview["to_create_records"]["transfers"] == 0,
		"and the preview offers nothing to place",
		str(second_preview["to_create_records"]),
	)
	recorder.check(after == before, "with nothing rewritten", str(after.get(name)))

	return created


def check_a_taken_name(recorder, base, run):
	"""8.8 — a name that exists, kept as it is in the file."""
	document, kept, renamed = variant_colliding(base, run)
	before = restored_fields(kept)
	before_rows = rows_of(kept)

	preview = as_user(
		ARCHIVIST_USER,
		lambda: call("preview_restore_audit_records", content=file_of(document)),
	)
	answer = as_user(
		ARCHIVIST_USER,
		lambda: call("restore_audit_records", content=file_of(document), token=preview["token"]),
	)
	frappe.db.commit()
	fresh()

	# The apply's answer is the whole conflict list; the preview's is a *sample* of it, capped by
	# design, and on a site with twenty-five records the one this check is about need not be in it.
	# Reading the capped list is how this check passed once for luck and failed the next run.
	conflicts = {row["row"]: row["reason"] for row in answer["conflicts"]}

	recorder.note(
		f"colliding file: conflicts {preview['by_reason']}, created {answer['created']['transfers']}"
	)
	recorder.check(
		conflicts.get(kept) == export.REASON_RECORD_EXISTS,
		"the record whose name this site already uses is reported with its own reason",
		str(conflicts),
	)
	recorder.check(
		renamed in answer["created"]["transfers"],
		"and the other record in the same file is placed anyway",
		str(answer["created"]["transfers"]),
	)
	recorder.check(
		restored_fields(kept) == before and rows_of(kept) == before_rows,
		"the existing record is untouched, field for field and row for row",
		str(restored_fields(kept)),
	)


def check_a_cancelled_target(recorder, base, run):
	"""8.9 — a record pointing at an invoice that has since been cancelled."""
	document, target = variant_link_cancelled(base, run)

	preview = as_user(
		ARCHIVIST_USER,
		lambda: call("preview_restore_audit_records", content=file_of(document)),
	)
	answer = as_user(
		ARCHIVIST_USER,
		lambda: call("restore_audit_records", content=file_of(document), token=preview["token"]),
	)
	frappe.db.commit()
	fresh()

	conflicts = {row["row"]: row["reason"] for row in answer["conflicts"]}

	recorder.note(f"cancelled-target file: {preview['by_reason']}, created {answer['created']['transfers']}")
	recorder.check(
		conflicts.get(target) == export.REASON_RECORD_LINK_CANCELLED,
		"the record whose target is cancelled is reported with its own reason",
		str(conflicts),
	)
	recorder.check(
		target not in answer["created"]["transfers"],
		"it is not placed",
		str(answer["created"]["transfers"]),
	)
	recorder.check(
		bool(answer["created"]["transfers"]),
		"while the other record of the same file is",
		str(answer["created"]["transfers"]),
	)


def check_an_unknown_serial(recorder, base, run):
	"""8.10 — a row whose serial is on neither the site nor the file."""
	document, target = variant_unresolved(base, run)

	preview = as_user(
		ARCHIVIST_USER,
		lambda: call("preview_restore_audit_records", content=file_of(document)),
	)
	answer = as_user(
		ARCHIVIST_USER,
		lambda: call("restore_audit_records", content=file_of(document), token=preview["token"]),
	)
	frappe.db.commit()
	fresh()

	conflicts = {row["row"]: row for row in answer["conflicts"]}
	conflict = conflicts.get(target)

	recorder.note(f"unknown-serial file: {preview['by_reason']}, created {answer['created']['transfers']}")
	recorder.check(
		conflict is not None and conflict["reason"] == export.REASON_SERIAL_UNRESOLVED,
		"the record is reported with the unresolved serial",
		str(conflicts),
	)
	recorder.check(
		conflict is not None
		and "LABNOTONTHISSITE" in conflict["detail"]
		and "SP-LAB-WARRANTY-ITEM" in conflict["detail"],
		"and the row names the item and the identity the file could not resolve",
		str(conflict),
	)
	recorder.check(
		target not in answer["created"]["transfers"] and not rows_of(target),
		"the whole record is left out, rows and all",
		f"created {answer['created']['transfers']}, rows {len(rows_of(target))}",
	)


def check_an_inconsistent_file(recorder, base, run):
	"""8.11 — a file edited by hand, sealed again so the digest agrees."""
	document, target = variant_inconsistent(base, run)

	before = frappe.db.count(TRANSFER)

	refused = as_user(
		ARCHIVIST_USER,
		lambda: thrown(call, "preview_restore_audit_records", content=file_of(document)),
	)

	recorder.check(
		refused is not None and export.CODE_ROW_INVALID in str(refused),
		"a record whose counts disagree with its own rows is refused before anything is planned",
		str(refused)[:200],
	)
	recorder.check(
		frappe.db.count(TRANSFER) == before,
		"and nothing was written",
		f"{before} → {frappe.db.count(TRANSFER)}",
	)
	recorder.check(
		not frappe.db.exists(TRANSFER, target),
		"the record the file claimed does not exist",
		target,
	)


# --- disarm -------------------------------------------------------------------------------


def disarm(recorder):
	records = frappe.get_all(TRANSFER, pluck="name", limit_page_length=0)
	items = frappe.db.count(TRANSFER_ITEM)

	recorder.note(f"{len(records)} audit records and {items} child rows remain")
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

	run = 1 + len(
		frappe.get_all(
			TRANSFER, filters={"name": ["like", f"{NAME_PREFIX}%"]}, pluck="name", limit_page_length=0
		)
	)
	print(f"this run's records will be numbered from {new_name(run, 1)}\n")

	try:
		if phase in ("check", "run"):
			print("check — the table (plan phase 8)")
			check_the_columns(recorder)
			print()

			base = base_document()
			recorder.note(f"the site's own export carries {len(base['transfers'])} records")

			print("check — who may place one")
			check_the_role(recorder, file_of(variant_placeable(base, run)[0]))
			print()

			print("check — placing them (8.2 to 8.7)")
			placeable, renamed = variant_placeable(base, run)
			check_placement(recorder, placeable, renamed)
			print()

			print("check — the refusals (8.8 to 8.11)")
			check_a_taken_name(recorder, base, run)
			print()
			check_a_cancelled_target(recorder, base, run)
			print()
			check_an_unknown_serial(recorder, base, run)
			print()
			check_an_inconsistent_file(recorder, base, run)
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
