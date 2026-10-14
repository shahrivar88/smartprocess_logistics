"""The scan station's server half, and the invoice it is scanned into.

The station is a browser subject: that twenty labels in a row leave the page usable, that one
column's completion makes exactly one sound, that a duplicate is refused with the label still in
the box — those are interaction, and a script cannot see them. (`deployment/lab/verify_scan_logic.js`
runs the decisions behind them; the browser itself is a person's to check.)

This script owns the other half, and the half that decides whether the interaction is *safe*:

    arm      a draft invoice with three rows — 20 units, then the same item for 2, then 3 — so
             that twenty scans, a repeated model in two rows and a label over the quantity all
             have somewhere to happen. Plus a Viewer, to prove a level is still enforced.
    check    the endpoint the station calls, exercised against the real site
    race     two separate processes, released at the same instant, for the last place in a row
    disarm   remove the invoice **and everything the scans put on it**

Two things here are measured rather than assumed, because assuming them is what a test gets
wrong: the refusal code the station classifies on is read out of a live refusal
(`[EXCESS_QUANTITY]`), and the item's model is compared with what the site actually keeps on the
item rather than with what the field is called. An over-filled row cannot be produced by calling
the app — that is the point of the cap — so `check_capacity` builds one by hand, out of records
that already exist, and the app is then asked to describe it.

`disarm` deletes the assignments and the serials before the invoice, because nothing else will:
this app deliberately registers no `doc_events` on Sales Invoice, so deleting an invoice leaves
its registrations behind. It removes only labels named `SCAN-`, so the fixture the other lab
script counts is not touched — and a scan test that left twenty-five serials behind would look
like data growth in that count.

Run it inside the lab's backend container, from the sites directory:

    docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab \\
      -f deployment/lab/compose.isolation-lab.yaml \\
      exec -T -w /home/frappe/frappe-bench/sites backend \\
      /home/frappe/frappe-bench/env/bin/python \\
      /home/frappe/frappe-bench/apps/smartprocess_logistics/deployment/lab/verify_scan_station.py run
"""

import json
import os
import subprocess
import sys
import time
import traceback

import frappe

SITE = "sp_logistics_lab"
SITES_PATH = "/home/frappe/frappe-bench/sites"

COMPANY = "SP LAB COMPANY"
PLANTED_REMARK = "verify_scan_station draft"
LABEL = "SCAN-"
ROWS = (20, 2, 3)

VIEWER_USER = "lab-viewer@example.test"
RESTRICTED_USER = "lab-restricted@example.test"

SERIAL = "Logistics Serial"
ASSIGNMENT = "Logistics Serial Assignment"


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
	return frappe.db.get_value("Sales Invoice", {"remarks": PLANTED_REMARK}, "name")


def item_code():
	# Reuse an item the lab already sells, so the scan test needs nothing new on the site.
	return frappe.db.get_value(SERIAL, {"item_code": ("is", "set")}, "item_code")


def row_keys():
	return {row.idx: row.name for row in frappe.get_doc("Sales Invoice", invoice_name()).items}


def status():
	return frappe_call("get_invoice_serial_status", sales_invoice=invoice_name())


def registered():
	call = status()

	return {row["row_key"]: row["registered"] for row in call["rows"]}


def frappe_call(method, **arguments):
	from smartprocess_logistics.api import endpoints

	return getattr(endpoints, method)(**arguments)


# --- arm ---------------------------------------------------------------------------------


def ensure_lab_users(recorder):
	"""The two operators this test needs, with the roles the feature actually requires.

	`Accounts User` is not decoration, and it is not the role you would guess. Every write and
	read of the station ends at `gateway.assert_can_read_invoice`, which asks the **framework**
	whether this caller may read the Sales Invoice — deliberately, because a serial belongs to an
	invoice the operator has to be allowed to see. Measured on this site, `Sales Invoice` grants
	read to `Accounts Manager`, `Accounts User` and `All`, and **not** to `Sales User`: a user
	holding only `Sales User` is refused at that check, which is how the first run of this script
	failed. The prerequisite is therefore an operational one — an operator needs a role that can
	read sales invoices on this site, in addition to a Logistics role.

	The Viewer is here for the other half: a level that may look and may not write.
	"""
	for email, logistics_role in (
		(VIEWER_USER, "Logistics Serial Viewer"),
		(RESTRICTED_USER, "Logistics Serial Manager"),
	):
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": email.split("@")[0],
					"send_welcome_email": 0,
					"roles": [{"role": logistics_role}],
				}
			).insert()

			recorder.note(f"created user {email} with role {logistics_role}")

		user = frappe.get_doc("User", email)
		held = {row.role for row in user.roles}

		for role in ("Accounts User", logistics_role):
			if role not in held:
				user.append("roles", {"role": role})

		user.save()

		if not frappe.db.exists("User Permission", {"user": email, "allow": "Company"}):
			frappe.get_doc(
				{
					"doctype": "User Permission",
					"user": email,
					"allow": "Company",
					"for_value": COMPANY,
				}
			).insert()

			recorder.note(f"restricted {email} to {COMPANY}")


def arm(recorder):
	frappe.set_user("Administrator")

	if not invoice_name():
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
					{
						"item_code": item_code(),
						"qty": quantity,
						"rate": 1,
						"income_account": f"Sales - {abbr}",
					}
					for quantity in ROWS
				],
			}
		).insert()

		recorder.note(f"created draft invoice {invoice.name} with rows {ROWS}")

	ensure_lab_users(recorder)
	frappe.db.commit()


# --- check -------------------------------------------------------------------------------


def append(row_key, serial):
	"""One registration, committed — because that is what one scan is.

	Each call here is one HTTP request in production, so it is one transaction: the request
	boundary commits it. Committing per call is what makes a later refusal roll back only itself.
	Without it the first refused duplicate would take every earlier label down with it, and the
	test would be measuring the script's transaction rather than the station's.
	"""
	answer = frappe_call(
		"add_row_serial",
		sales_invoice=invoice_name(),
		sales_invoice_item=row_key,
		serial=serial,
	)
	frappe.db.commit()

	return answer


def _persian(message):
	"""Whether a refusal is written for an operator rather than handed over as a server error."""
	return any("؀" <= character <= "ۿ" for character in message)


def refused(recorder, work, description):
	"""A call that must be refused, in Persian, and must leave the database as it was."""
	try:
		work()
	except Exception as error:  # noqa: BLE001 — what kind it is, is the next thing asserted
		frappe.db.rollback()

		message = str(error)

		recorder.check(_persian(message), description, message)

		return message

	recorder.check(False, description, "nothing was refused")

	return ""


def check(recorder):
	rows = row_keys()

	frappe.set_user("Administrator")

	# --- twenty labels in a row ----------------------------------------------------------
	seen = []

	for scan in range(1, 21):
		seen.append(append(rows[1], f"{LABEL}{scan:04d}")["row"]["registered"])

	recorder.check(seen == list(range(1, 21)), "twenty labels in a row all land", str(seen))

	row_1 = next(row for row in status()["rows"] if row["row_key"] == rows[1])
	recorder.check(
		row_1["registered"] == 20 and row_1["shortage"] == 0 and row_1["is_complete"],
		"the twentieth label completes the column, and the server says so",
		str({key: row_1[key] for key in ("required", "registered", "shortage", "is_complete")}),
	)
	recorder.check(
		len(row_1["assignments"]) == 20,
		"and every label is a cell the station can draw",
		f"{len(row_1['assignments'])} cells",
	)

	# --- a duplicate, and a row over its quantity ----------------------------------------
	refused(recorder, lambda: append(rows[1], f"{LABEL}0007"), "a duplicate on the same invoice is refused")
	recorder.check(
		registered()[rows[1]] == 20,
		"and a refused label leaves the row exactly as it was",
		str(registered()[rows[1]]),
	)

	for scan in range(1, 4):
		append(rows[3], f"{LABEL}9{scan:03d}")

	# The code is asserted, not assumed: the station classifies a refusal by the bracket in it and
	# by nothing else, so a refusal that changed its code would stop being recognised as an excess
	# and start appearing as an unexplained failure — silently, and only in front of an operator.
	over = refused(
		recorder,
		lambda: append(rows[3], f"{LABEL}9004"),
		"a fourth label on a three-unit row is refused",
	)
	recorder.check(
		"[EXCESS_QUANTITY]" in over,
		"and it carries the code the station classifies on",
		over,
	)
	recorder.check(
		registered()[rows[3]] == 3,
		"and the refused label is not in the row",
		str(registered()[rows[3]]),
	)
	recorder.check(
		not frappe.db.exists(SERIAL, {"serial_no": f"{LABEL}9004"}),
		"and the label it would have created rolled back with the refusal",
	)

	# --- the same model in two invoice rows ----------------------------------------------
	append(rows[2], f"{LABEL}8001")
	counts = registered()
	recorder.check(
		counts[rows[2]] == 1 and counts[rows[1]] == 20,
		"a label on the second row of the same model leaves the first row alone",
		str(counts),
	)

	row_2 = next(row for row in status()["rows"] if row["row_key"] == rows[2])
	stored_against = frappe.db.get_value(ASSIGNMENT, row_2["assignments"][-1]["name"], "sales_invoice_item")
	recorder.check(
		stored_against == rows[2],
		"and it is stored against that row's own key, not the model's",
		f"{stored_against} != {rows[2]}",
	)

	# --- another user's label, counted for the next reader -------------------------------
	frappe.set_user("lab-restricted@example.test")
	append(rows[2], f"{LABEL}8002")
	frappe.set_user("Administrator")

	row_2 = next(row for row in status()["rows"] if row["row_key"] == rows[2])
	recorder.check(
		row_2["registered"] == 2,
		"the next reader counts the previous user's label",
		str(row_2["registered"]),
	)

	# --- the level a delete answers to ---------------------------------------------------
	frappe.set_user(VIEWER_USER)
	refused(
		recorder,
		lambda: frappe_call("delete_assignments", names=[row_2["assignments"][0]["name"]]),
		"a viewer may not remove a label",
	)
	refused(
		recorder,
		lambda: append(rows[2], f"{LABEL}8003"),
		"a viewer may not add one either",
	)
	frappe.set_user("Administrator")

	# --- removing one label, and removing a whole column ---------------------------------
	before = registered()
	# The label that was refused a moment ago while the row was full. Removing *that* one is what
	# makes the last check a real one: the same label, refused then and accepted now.
	refused_then = next(entry for entry in row_1["assignments"] if entry["serial_no"] == f"{LABEL}0007")
	removed = frappe_call("delete_assignments", names=[refused_then["name"]])
	after = registered()

	recorder.check(
		after[rows[1]] == before[rows[1]] - 1
		and after[rows[2]] == before[rows[2]]
		and after[rows[3]] == before[rows[3]],
		"removing one label leaves every other row untouched",
		f"{before} -> {after} ({removed})",
	)

	row_1 = next(row for row in status()["rows"] if row["row_key"] == rows[1])
	recorder.check(
		not row_1["is_complete"] and row_1["shortage"] == 1,
		"and the column it left is no longer complete — which is what makes a fresh chime possible",
		str({key: row_1[key] for key in ("registered", "shortage", "is_complete")}),
	)

	row_2 = next(row for row in status()["rows"] if row["row_key"] == rows[2])
	sent = frappe_call("delete_assignments", names=[entry["name"] for entry in row_2["assignments"]])
	after_all = registered()

	recorder.check(
		after_all[rows[2]] == 0 and after_all[rows[1]] == 19,
		"removing a whole column removes exactly that column",
		f"{sent} {after_all}",
	)

	# --- the completing label can be added again -----------------------------------------
	back = append(rows[1], f"{LABEL}0007")
	recorder.check(
		back["row"]["is_complete"] and back["row"]["shortage"] == 0,
		"the label that was refused while the row was full completes it once it is not",
		str(back["row"]["is_complete"]),
	)

	check_capacity(recorder, rows)
	check_scan_command(recorder)

	frappe.db.commit()


def check_scan_command(recorder):
	"""What the window is told about the site's next-column command — in either state.

	Written to hold before the migrate as well as after it, because the two states are both real
	for a while: the code is deployed to a site whose settings row has no such column yet, and the
	contract has to be that the window still opens and still registers. So what is asserted is the
	invariant rather than the value — the answer is always there, and it is **never** something the
	window could not match: it is either empty, or a value `core.scan` accepts.
	"""
	from smartprocess_logistics.core.scan import (
		ScanCommandError,
		validate_next_row_command,
	)

	call = status()
	recorder.check(
		"scan_command" in call,
		"the status the window is drawn from always carries the next-column command",
		str(sorted(call)),
	)

	served = call.get("scan_command")

	# Empty is the answer in both of the states a site can be in — no column yet, or a column no
	# administrator has filled — so it is accepted here rather than re-validated: the rule refuses
	# an empty *stored* value, and this is what a refusal is turned into before it is served.
	if served == "":
		recorder.check(True, "and it is empty here, which is a site with no command yet")

		return

	try:
		checked = validate_next_row_command(
			served,
			delimiters=tuple(
				row.delimiter for row in frappe.get_single("Logistics Settings").delimiters if row.enabled
			),
			max_length=frappe.db.get_single_value("Logistics Settings", "serial_max_length"),
		)
	except ScanCommandError as error:
		recorder.check(
			False,
			"and a value that is not empty is one the rule accepts — never one it refuses",
			f"{error.code}: {served!r}",
		)

		return

	recorder.check(
		checked == served,
		"and a value that is not empty is one the rule accepts, unchanged",
		f"{checked!r} vs {served!r}",
	)


def check_capacity(recorder, rows):
	"""What the station is told about a row's quantity, and what it is told about a row over it.

	Row 3 invoices three units and holds three labels by the time this runs. The over-filled row
	below is built out of records that already exist, by pointing one of row 1's labels at row 3 —
	the app will not write that state any more, which is exactly why the display of it has to be
	tested by constructing it. Nothing is invented: the same assignment, the same serial, the same
	company, moved. It is moved back at the end.
	"""
	expectation = frappe.db.get_value("Item", item_code(), "item_name")

	row_3 = next(row for row in status()["rows"] if row["row_key"] == rows[3])
	recorder.check(
		bool(expectation) and row_3["model"] == expectation,
		"a column is titled by the model this site keeps on the item",
		f"model={row_3['model']!r} item_name={expectation!r} item_code={row_3['item_code']!r}",
	)
	recorder.check(
		row_3["capacity"] == 3 and row_3["required"] == 3 and row_3["invoiced_qty"] == 3,
		"and its capacity is the row's own invoiced quantity",
		str({key: row_3[key] for key in ("invoiced_qty", "capacity")}),
	)
	recorder.check(
		row_3["is_complete"]
		and row_3["excess"] == 0
		and not any(entry["is_excess"] for entry in row_3["assignments"]),
		"a row filled to its quantity is complete and marks nothing red",
		str({key: row_3[key] for key in ("registered", "excess", "is_complete")}),
	)

	# Two reads in a row, because the red cell has to be the same cell on every refresh: an
	# operator deleting the wrong one would be deleting a label that was never the problem.
	first = [entry["name"] for entry in row_3["assignments"]]
	second = [
		entry["name"]
		for entry in next(row for row in status()["rows"] if row["row_key"] == rows[3])["assignments"]
	]
	recorder.check(
		first == second and len(first) == 3,
		"the order the labels are read in is the same on every read",
		f"{first} vs {second}",
	)

	row_1 = next(row for row in status()["rows"] if row["row_key"] == rows[1])
	donor = row_1["assignments"][-1]
	frappe.db.set_value(ASSIGNMENT, donor["name"], "sales_invoice_item", rows[3])
	frappe.db.commit()
	recorder.note(f"moved {donor['serial_no']} onto row {rows[3]}, which invoices 3")

	over = next(row for row in status()["rows"] if row["row_key"] == rows[3])
	recorder.check(
		over["registered"] == 4 and over["capacity"] == 3 and over["excess"] == 1,
		"a stored row holding one label more than its quantity reports the excess as a number",
		str({key: over[key] for key in ("registered", "capacity", "excess")}),
	)
	recorder.check(
		[entry["is_excess"] for entry in over["assignments"]] == [False, False, False, True],
		"and marks only the registrations past the quantity, in the order they were registered",
		str([(entry["serial_no"], entry["is_excess"]) for entry in over["assignments"]]),
	)
	recorder.check(
		over["assignments"][-1]["name"] == donor["name"],
		"which is the label registered last, not one chosen by name or by position in the table",
		f"{over['assignments'][-1]['serial_no']} vs moved {donor['serial_no']}",
	)
	recorder.check(
		not over["is_complete"] and over["shortage"] == 0,
		"an over-filled column is not complete, and is not short either",
		str({key: over[key] for key in ("registered", "shortage", "is_complete")}),
	)

	totals = status()["summary"]
	recorder.check(
		totals["excess"] == 1 and totals["excess_rows"] == 1 and not totals["is_complete"],
		"the invoice carries the excess too, so it cannot read as finished while one row is over",
		str({key: totals[key] for key in ("excess", "excess_rows", "is_complete")}),
	)

	# Back to normal, through the control an operator has: delete the red cell.
	frappe_call("delete_assignments", names=[donor["name"]])
	frappe.db.commit()

	after = next(row for row in status()["rows"] if row["row_key"] == rows[3])
	recorder.check(
		after["registered"] == 3 and after["excess"] == 0 and after["is_complete"],
		"deleting the excess label returns the column to normal, complete again",
		str({key: after[key] for key in ("registered", "excess", "is_complete")}),
	)
	recorder.check(
		not any(entry["is_excess"] for entry in after["assignments"]),
		"with no cell left marked red",
	)

	# The donor belonged to row 1 before this, and is put back so the fixture ends as it was found.
	restored = frappe_call(
		"add_row_serial",
		sales_invoice=invoice_name(),
		sales_invoice_item=rows[1],
		serial=donor["serial_no"],
	)
	frappe.db.commit()
	recorder.check(
		restored["row"]["registered"] == 20 and restored["row"]["is_complete"],
		"and the label that was borrowed is registered back onto its own row",
		str(restored["row"]["registered"]),
	)


# --- race --------------------------------------------------------------------------------


RACE_PREFIX = "RACE_RESULT "
RACE_WARM = "RACE_WARM "


def race_child(argv):
	"""One caller for one place in a row, released at an instant the parent chooses.

	It waits on a wall-clock instant rather than a queue, so the process is already connected,
	already authenticated and already inside the endpoint when the clock reaches it. Two calls
	issued one after the other would prove nothing about concurrency: the cap would simply have
	been applied twice.

	`warm` makes it read the invoice *before* waiting, and then call. That is the interleaving a
	plain lock wait produces and a simultaneous start hides: this process's transaction has
	already read the row, then it blocks on the invoice, then it proceeds after the other caller
	has committed. Whether it reads the row's new count or its own older one is the whole question
	— so it is asked here rather than argued about.
	"""
	deadline = float(argv[2])
	row_key = argv[3]
	serial = argv[4]
	warm = len(argv) > 5 and argv[5] == "warm"

	connect()
	frappe.set_user("Administrator")

	if warm:
		before = next(row for row in status()["rows"] if row["row_key"] == row_key)
		print(RACE_WARM + json.dumps({"registered": before["registered"]}))

	while time.time() < deadline:
		time.sleep(0.001)

	try:
		answer = frappe_call(
			"add_row_serial",
			sales_invoice=invoice_name(),
			sales_invoice_item=row_key,
			serial=serial,
		)
		frappe.db.commit()
		result = {"ok": True, "registered": answer["row"]["registered"], "serial": serial}
	except Exception as error:  # noqa: BLE001 — the refusal itself is the result
		frappe.db.rollback()
		result = {
			"ok": False,
			"error": str(error)[:400],
			"serial": serial,
			# Which guard answered: the two a lost race can produce look alike in the message an
			# operator reads, and they are not alike in what they mean.
			"kind": type(error).__name__,
			"safety": getattr(error, "code", None),
			"where": [line.strip() for line in traceback.format_exc().splitlines() if "/apps/" in line],
		}

	print(RACE_PREFIX + json.dumps(result, ensure_ascii=False))

	return 0


def race_setup(recorder, *, stored):
	"""Leave one row a single label short of its quantity, so both callers want the same place.

	`stored` decides whether the two labels the callers will use already exist on the site. They
	have to, for the second round: a caller writing a **new** serial row also inserts into the
	index the other caller is holding a range lock on, and MariaDB aborts one of them as a
	deadlock — which is a real answer, but a different one, and it arrives before the cap is ever
	consulted. Labels the site already holds remove that collision and leave the cap as the only
	thing standing between two simultaneous callers and an over-filled row.
	"""
	rows = row_keys()
	current = next(row for row in status()["rows"] if row["row_key"] == rows[3])

	if current["assignments"]:
		frappe_call("delete_assignments", names=[entry["name"] for entry in current["assignments"]])
		frappe.db.commit()

	if stored:
		for scan in (1, 2):
			append(rows[2], f"{LABEL}P{scan:03d}")

		second = next(row for row in status()["rows"] if row["row_key"] == rows[2])
		frappe_call("delete_assignments", names=[entry["name"] for entry in second["assignments"]])
		frappe.db.commit()

	prefix = "P" if stored else "R"

	for scan in (1, 2):
		append(rows[3], f"{LABEL}{prefix}{scan:03d}")

	recorder.note(
		f"row {rows[3]} invoices 3 and holds 2: one place left, two callers, "
		f"{'labels this site already has' if stored else 'labels that do not exist yet'}"
	)

	return rows[3], f"{LABEL}{prefix}"


def race_round(recorder, *, stored, warm=False):
	"""Two callers for one place, and what the row looks like afterwards.

	With `warm`, the second caller reads the invoice first and is released well after the first
	has finished. It cannot collide with anything — there is nothing left to collide with — so if
	the row ends over its quantity it ended there because a transaction acted on a count it had
	read before the other one committed, and nothing else. Without `warm`, both are released on the
	same instant, which is the collision the app is expected to answer with the retry sentence.
	"""
	row_key, prefix = race_setup(recorder, stored=stored)
	start = time.time() + 4
	script = os.path.abspath(__file__)
	releases = ((0.0, "cold"), (5.0 if warm else 0.0, "warm" if warm else "cold"))

	children = [
		subprocess.Popen(
			[
				sys.executable,
				script,
				"race-child",
				str(start + offset),
				row_key,
				f"{prefix}{index:03d}",
				mode,
			],
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
		)
		for index, (offset, mode) in zip((101, 102), releases, strict=True)
	]

	answers = []
	warmed = []

	for child in children:
		out, err = child.communicate(timeout=180)
		line = [row for row in out.splitlines() if row.startswith(RACE_PREFIX)]
		before = [row for row in out.splitlines() if row.startswith(RACE_WARM)]

		if before:
			warmed.append(json.loads(before[-1][len(RACE_WARM) :]))

		if line:
			answers.append(json.loads(line[-1][len(RACE_PREFIX) :]))
		else:
			answers.append({"ok": False, "error": (err or out).strip()[-400:], "no_result": True})

	frappe.db.rollback()

	for answer in answers:
		recorder.note(json.dumps(answer, ensure_ascii=False))

	winners = [answer for answer in answers if answer.get("ok")]
	losers = [answer for answer in answers if not answer.get("ok")]

	recorder.check(
		len(answers) == 2 and not any(answer.get("no_result") for answer in answers),
		"both callers reached the app and answered",
		str(answers),
	)
	recorder.check(
		len(winners) == 1,
		"exactly one of two simultaneous callers takes the last place",
		f"{len(winners)} of {len(answers)} succeeded",
	)

	if warm:
		recorder.check(
			len(warmed) == 1 and warmed[0]["registered"] == 2,
			"the late caller had read the row while it still held one place",
			str(warmed),
		)

	final = next(row for row in status()["rows"] if row["row_key"] == row_key)
	recorder.check(
		final["registered"] == 3 and final["excess"] == 0,
		"and the row ends at its quantity, never above it, whichever caller won",
		str({key: final[key] for key in ("registered", "capacity", "excess")}),
	)

	# The loser is refused by whichever guard got there first, and both are correct answers. A
	# caller that read the row after the winner committed is stopped by the cap, and carries
	# `[EXCESS_QUANTITY]`; a caller that collided with the winner on the invoice or on a serial row
	# is stopped by the framework's deadlock guard, and is told in words to send it again — that
	# refusal has no code on purpose, because nothing about the request was wrong
	# (`endpoints._contention`). What must never happen is a refused caller being answered with
	# silence, or with a success.
	recorder.check(
		len(losers) == 1 and _persian(losers[0].get("error") or ""),
		"and the caller that lost is told so, in words, with nothing written",
		str(losers),
	)

	# The retry the loser is invited to make. It reaches a row that is now full, so the cap — not
	# the collision — is what answers it.
	retried = refused(
		recorder,
		lambda: append(row_key, losers[0]["serial"]),
		"and the retry it was asked for meets the cap",
	)
	recorder.check(
		"[EXCESS_QUANTITY]" in retried,
		"with the same code the station classifies an excess by",
		retried,
	)


def stale_holder(argv):
	"""A caller that reads a row, waits while somebody else fills it, and then writes anyway.

	This is the interleaving a lock wait produces, without a race to arrange it: the reading and
	the writing are separated by a sleep, and the other write is committed in between by another
	process. Whether this caller is stopped depends entirely on whether the row's count is read
	when the decision is made or from the view its transaction took when it first looked — which
	is the question, and it is asked here rather than argued about.
	"""
	row_key = argv[2]
	serial = argv[3]
	wait = float(argv[4])

	connect()
	frappe.set_user("Administrator")

	before = next(row for row in status()["rows"] if row["row_key"] == row_key)
	print(RACE_WARM + json.dumps({"registered": before["registered"], "capacity": before["capacity"]}))

	time.sleep(wait)

	try:
		answer = frappe_call(
			"add_row_serial",
			sales_invoice=invoice_name(),
			sales_invoice_item=row_key,
			serial=serial,
		)
		frappe.db.commit()
		result = {"ok": True, "registered": answer["row"]["registered"], "serial": serial}
	except Exception as error:  # noqa: BLE001 — the refusal itself is the result
		frappe.db.rollback()
		result = {
			"ok": False,
			"error": str(error)[:400],
			"serial": serial,
			"kind": type(error).__name__,
			"safety": getattr(error, "code", None),
			"cause": (
				f"{type(error.__context__).__name__}: {str(error.__context__)[:300]}"
				if error.__context__ is not None
				else None
			),
		}

	print(RACE_PREFIX + json.dumps(result, ensure_ascii=False))

	return 0


def stale_round(recorder):
	"""One caller reads the row, another fills it, and the first one writes afterwards.

	Nothing is simultaneous here, so nothing can be excused as a collision: if the row ends over
	its quantity, it ended there because the write was decided on a count that was already stale.
	"""
	row_key, prefix = race_setup(recorder, stored=True)
	holder_serial = f"{prefix}201"
	other_serial = f"{prefix}202"
	script = os.path.abspath(__file__)

	holder = subprocess.Popen(
		[sys.executable, script, "stale-holder", row_key, holder_serial, "8"],
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
		text=True,
	)

	# Long enough that the holder has read the row and is sleeping, short enough that it is still
	# inside the same transaction when the row is filled.
	time.sleep(4)
	filled = append(row_key, other_serial)

	recorder.check(
		filled["row"]["registered"] == 3 and filled["row"]["is_complete"],
		"the second caller takes the last place while the first one is waiting",
		str(filled["row"]["registered"]),
	)

	out, err = holder.communicate(timeout=180)
	line = [row for row in out.splitlines() if row.startswith(RACE_PREFIX)]
	before = [row for row in out.splitlines() if row.startswith(RACE_WARM)]
	answer = (
		json.loads(line[-1][len(RACE_PREFIX) :])
		if line
		else {"ok": False, "error": (err or out).strip()[-400:], "no_result": True}
	)

	recorder.note(json.dumps(answer, ensure_ascii=False))
	frappe.db.rollback()

	recorder.check(
		before and json.loads(before[-1][len(RACE_WARM) :])["registered"] == 2,
		"the waiting caller had read the row while it still held one place",
		str(before),
	)

	final = next(row for row in status()["rows"] if row["row_key"] == row_key)
	recorder.check(
		final["registered"] == 3 and final["excess"] == 0,
		"and the row still ends at its quantity, read when it was written rather than when it was read",
		str({key: final[key] for key in ("registered", "capacity", "excess")}),
	)
	recorder.check(
		not answer.get("ok"),
		"because the waiting caller's write was refused, not accepted on a count taken before",
		str(answer),
	)

	# Which guard refuses it depends on MariaDB, and both answers are the right one. The count is
	# read as a locking read, and the server raises `1020 Record has changed since last read`
	# rather than hand back rows this transaction has already seen change — which is the write
	# being stopped by the read itself. When nothing changed between the two reads there is
	# nothing to detect, and the cap refuses it on the count as it now stands.
	recorder.check(
		"[EXCESS_QUANTITY]" in (answer.get("error") or "")
		or (answer.get("cause") or "").startswith("QueryDeadlockError: (1020,"),
		"and it is refused either by the cap, or by the server refusing a count it knows is stale",
		str({key: answer.get(key) for key in ("error", "cause")}),
	)

	# The retry both refusals ask for. The row is full by then, so the cap is what meets it.
	retried = refused(
		recorder,
		lambda: append(row_key, holder_serial),
		"and the retry the waiting caller is asked for meets the cap",
	)
	recorder.check(
		"[EXCESS_QUANTITY]" in retried,
		"with the code the station classifies an excess by",
		retried,
	)


def race(recorder):
	"""Two requests at the same instant for the last place in a row, in both of its forms.

	The queue in the window makes this rare — one column sends one label at a time — but the cap
	is enforced for every path into the app, and two operators, two windows or a script are all
	paths. What is asserted is the invariant rather than a schedule: however the two calls
	interleave, the row ends at its quantity and never above it.

	Two rounds, because they fail differently. With labels that do not exist yet, the loser is
	aborted on the serial index. With labels the site already holds, no serial row is written and
	nothing but the row's own count stands in the way — which is the case that decides whether the
	count is read under the invoice lock or from a view taken before it.
	"""
	race_round(recorder, stored=False)
	race_round(recorder, stored=True)
	stale_round(recorder)
	restore_round(recorder)


# --- restore -----------------------------------------------------------------------------


def restore_document(row_key, labels):
	"""An export document, built by the app's own builder, naming these labels on this row.

	Hand-written rows rather than a file taken from the site: what is under test is the restore
	endpoint's own writing, so the document has to name labels the site does not have yet and one
	row it is about to fill.
	"""
	from smartprocess_logistics.core import export

	item = item_code()
	company = frappe.db.get_value("Sales Invoice", invoice_name(), "company")
	item_name = frappe.db.get_value("Item", item, "item_name")
	stamp = frappe.utils.now()

	serials = []
	assignments = []

	for index, label in enumerate(labels, start=1):
		name = f"LS-RESTORE-{index}"
		normalized = label.lower()

		serials.append(
			{
				"name": name,
				"serial_no": label,
				"normalized_serial": normalized,
				"item_code": item,
				"item_name": item_name,
				"status": "Active",
				"first_registered_on": stamp,
				"normalization_version": 2,
				"remarks": "",
			}
		)
		assignments.append(
			{
				"name": f"LSA-RESTORE-{index}",
				"logistics_serial": name,
				"item_code": item,
				"normalized_serial": normalized,
				"sales_invoice": invoice_name(),
				"company": company,
				"sales_invoice_item": row_key,
				"invoice_item_idx": 3,
				"remarks": "",
			}
		)

	return export.build(
		serials,
		assignments,
		app_version="0.3.0",
		site=SITE,
		exported_by="Administrator",
		exported_on=stamp,
		normalization_version=2,
	)


def restore_child(argv):
	"""One of two restores of the same file, released at the same instant."""
	path = argv[2]
	deadline = float(argv[3])

	connect()
	frappe.set_user("Administrator")

	with open(path, encoding="utf-8") as opened:
		content = opened.read()

	from smartprocess_logistics.core import export

	token = export.read(content).get("digest")

	while time.time() < deadline:
		time.sleep(0.001)

	try:
		answer = frappe_call("apply_serial_restore", content=content, token=token)
		frappe.db.commit()
		result = {
			"ok": True,
			"created": answer["counts"]["created"],
			"conflicts": answer["counts"]["conflicts"],
			"by_reason": answer["counts"]["by_reason"],
		}
	except Exception as error:  # noqa: BLE001 — the refusal itself is the result
		frappe.db.rollback()
		result = {
			"ok": False,
			"error": str(error)[:300],
			"kind": type(error).__name__,
			"cause": (
				f"{type(error.__context__).__name__}: {str(error.__context__)[:200]}"
				if error.__context__ is not None
				else None
			),
		}

	print(RACE_PREFIX + json.dumps(result, ensure_ascii=False))

	return 0


def restore_round(recorder):
	"""Two restores of one file at the same instant, for the one place a row has left.

	A restore is a write like any other, and the capacity rule has to hold for it too. It used to
	be the one write that took no invoice lock: it read the site, planned, and wrote — so two of
	them could each place up to the quantity the row held when it was read, and the row would end
	over it. Measured here, that is the difference between a row of three and a row of four.
	"""
	from smartprocess_logistics.core import export

	row_key, prefix = race_setup(recorder, stored=False)
	document = restore_document(row_key, (f"{LABEL}S001", f"{LABEL}S002"))
	path = "/tmp/sp-restore-race.json"

	with open(path, "w", encoding="utf-8") as opened:
		opened.write(export.as_json(document))

	recorder.note(f"one file naming {len(document['assignments'])} labels on row {row_key}: one place")

	deadline = time.time() + 4
	script = os.path.abspath(__file__)

	children = [
		subprocess.Popen(
			[sys.executable, script, "restore-child", path, str(deadline)],
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
		)
		for _ in (1, 2)
	]

	answers = []

	for child in children:
		out, err = child.communicate(timeout=180)
		line = [row for row in out.splitlines() if row.startswith(RACE_PREFIX)]

		if line:
			answers.append(json.loads(line[-1][len(RACE_PREFIX) :]))
		else:
			answers.append({"ok": False, "error": (err or out).strip()[-300:], "no_result": True})

	frappe.db.rollback()

	for answer in answers:
		recorder.note(json.dumps(answer, ensure_ascii=False))

	placed = sum(answer.get("created", {}).get("assignments", 0) for answer in answers)
	final = next(row for row in status()["rows"] if row["row_key"] == row_key)

	recorder.check(
		len(answers) == 2 and not any(answer.get("no_result") for answer in answers),
		"both restores reached the app and answered",
		str(answers),
	)
	recorder.check(
		placed == 1,
		"the two of them place exactly the one place the row had",
		f"{placed} assignments placed",
	)
	recorder.check(
		final["registered"] == 3 and final["excess"] == 0,
		"and the row ends at its quantity, never above it",
		str({key: final[key] for key in ("registered", "capacity", "excess")}),
	)
	recorder.check(
		sum(answer.get("by_reason", {}).get(export.REASON_EXCESS_QUANTITY, 0) for answer in answers) == 1,
		"the other restore reports the row as one it cannot place, with the same reason code",
		str([answer.get("by_reason") for answer in answers]),
	)


# --- status ------------------------------------------------------------------------------


def status_phase():
	name = invoice_name()

	if not name:
		print(json.dumps({"invoice": None}, ensure_ascii=False))

		return 1

	print(
		json.dumps(
			{
				"invoice": name,
				"company": COMPANY,
				"item_code": item_code(),
				"rows": [
					{"row_key": key, "idx": idx, "qty": ROWS[idx - 1]}
					for idx, key in sorted(row_keys().items())
				],
				"url": f"http://127.0.0.1:8095/app/sales-invoice/{name}",
			},
			ensure_ascii=False,
			indent=1,
		)
	)

	return 0


# --- disarm ------------------------------------------------------------------------------


def sweep_orphan_labels():
	"""Labels a scan created that nothing points at any more.

	Two ways a label outlives its assignment: a request that was refused after it created one, and
	a deleted assignment — the app never deletes a serial with the last assignment that referenced
	it, deliberately, because the label is the record and the assignment is only the link. Either
	one inflates the count the other lab script asserts. Only names this script creates are
	touched, and only when nothing at all points at them.
	"""
	removed = 0

	for serial in frappe.get_all(SERIAL, filters={"serial_no": ("like", f"{LABEL}%")}, pluck="name"):
		if frappe.db.exists(ASSIGNMENT, {"logistics_serial": serial}):
			continue

		frappe.delete_doc(SERIAL, serial, force=True)
		removed += 1

	return removed


def disarm(recorder):
	"""Remove what the scans wrote, then the invoice — in that order, and only for this invoice."""
	frappe.set_user("Administrator")

	name = invoice_name()
	labels = set()
	removed = {"assignments": 0, "serials": 0, "invoice": name}

	if name:
		for assignment in frappe.get_all(
			ASSIGNMENT, filters={"sales_invoice": name}, fields=["name", "logistics_serial"]
		):
			if assignment.logistics_serial:
				labels.add(assignment.logistics_serial)

			frappe.delete_doc(ASSIGNMENT, assignment.name, force=True)
			removed["assignments"] += 1

		for label in labels:
			if not frappe.db.exists(SERIAL, label):
				continue

			# Only what a scan created: a label the fixture owns is not this script's to remove.
			if not (frappe.db.get_value(SERIAL, label, "serial_no") or "").startswith(LABEL):
				continue

			if frappe.db.exists(ASSIGNMENT, {"logistics_serial": label}):
				continue

			frappe.delete_doc(SERIAL, label, force=True)
			removed["serials"] += 1

		frappe.delete_doc("Sales Invoice", name, force=True)

	frappe.db.commit()
	removed["orphans"] = sweep_orphan_labels()
	frappe.db.commit()

	recorder.note(json.dumps(removed, ensure_ascii=False))


# --- entry point -------------------------------------------------------------------------


def main(argv):
	phase = (argv[1] if len(argv) > 1 else "status").lower()

	if phase == "race-child":
		return race_child(argv)

	if phase == "stale-holder":
		return stale_holder(argv)

	if phase == "restore-child":
		return restore_child(argv)

	if phase not in ("arm", "check", "race", "status", "disarm", "run"):
		print(f"unknown phase {phase!r}: expected arm, check, race, status, disarm or run")

		return 2

	connect()

	print(f"site: {frappe.local.site}  |  invoice: {invoice_name() or '(none)'}\n")

	recorder = Recorder()

	if phase == "status":
		return status_phase()

	try:
		if phase in ("arm", "run"):
			print("arm")
			arm(recorder)
			print()

		if phase in ("check", "run"):
			print("check")
			check(recorder)
			print()

		if phase in ("race", "run"):
			print("race")
			race(recorder)
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
