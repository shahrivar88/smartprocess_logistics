"""`add_row_serials` against a real site: one column's labels, whole or not at all.

The station sends a column's waiting scans in one request now. Everything that makes that safe is
a property of the *database*, not of the window, and this script is where those properties are
measured rather than argued:

    arm      a draft invoice with four rows (5, 2, 1 and 3 units), a donor invoice for the reuse
             case, a submitted-then-cancelled invoice for the lifecycle case, and four operators
             — an Editor, a Viewer, a user holding no Logistics role, and one restricted to
             another company
    check    one process: atomic acceptance, whole-batch refusal for each reason, what a refusal
             leaves behind, identity reuse against a duplicate assignment, the ceiling on a
             request, and every refusal being written for an operator rather than handed over as a
             server error
    race     two processes released at the same instant, each sending a batch of two labels into a
             row that has room for exactly two — twice: once with labels the site has never seen,
             and once with labels it already holds
    retry    what a caller does next: the same batch again, and the same batch with the surplus
             removed after a place is freed
    disarm   remove this script's rows and nothing else

Run it inside the lab's backend container, from the sites directory:

    docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab \\
      -f deployment/lab/compose.isolation-lab.yaml \\
      exec -T -w /home/frappe/frappe-bench/sites backend \\
      /home/frappe/frappe-bench/env/bin/python \\
      /home/frappe/frappe-bench/apps/smartprocess_logistics/deployment/lab/verify_batch_intake.py run

The cancelled invoice is **cancelled and kept**: cancelling is the only way to reach `docstatus 2`,
and a cancelled invoice cannot be deleted without removing the reversed `GL Entry` rows the
framework keeps. It is a lab record, it holds no serials, and `disarm` says so rather than
pretending it removed it.
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
OTHER_COMPANY = "SP LAB OTHER"

REMARK = "verify_batch_intake draft"
DONOR_REMARK = "verify_batch_intake donor"
CANCELLED_REMARK = "verify_batch_intake cancelled"

LABEL = "BATCH-"
ROWS = (5, 2, 1, 3)
RACE_ROW = 4

EDITOR_USER = "lab-batch-editor@example.test"
VIEWER_USER = "lab-batch-viewer@example.test"
NOROLE_USER = "lab-batch-norole@example.test"
RESTRICTED_USER = "lab-batch-restricted@example.test"

INVOICE = "Sales Invoice"
SERIAL = "Logistics Serial"
ASSIGNMENT = "Logistics Serial Assignment"

RACE_PREFIX = "BATCH_RACE_RESULT "
RACE_WARM = "BATCH_RACE_WARM "


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


# --- the fixture --------------------------------------------------------------------------


def invoice_name(remark=REMARK):
	return frappe.db.get_value(INVOICE, {"remarks": remark}, "name")


def item_code():
	# An item the lab already sells, so this script plants no Item.
	return frappe.db.get_value(SERIAL, {"item_code": ("is", "set")}, "item_code")


def row_keys(name=None):
	return {row.idx: row.name for row in frappe.get_doc(INVOICE, name or invoice_name()).items}


def status(name=None):
	return frappe_call("get_invoice_serial_status", sales_invoice=name or invoice_name())


def row_of(row_key, name=None):
	return next(row for row in status(name)["rows"] if row["row_key"] == row_key)


def snapshot(name=None):
	"""What a write of this invoice would change, so "nothing was written" is a comparison."""
	return {
		"assignments": frappe.db.count(ASSIGNMENT, {"sales_invoice": name or invoice_name()}),
		"serials": frappe.db.count(SERIAL),
	}


def assignments_of(row_key, name=None):
	return frappe.db.count(
		ASSIGNMENT,
		{"sales_invoice": name or invoice_name(), "sales_invoice_item": row_key},
	)


def serials_named(text):
	return frappe.get_all(SERIAL, filters={"serial_no": text}, pluck="name")


def begin_request():
	"""End whatever this script's own reads left open, the way a request boundary does.

	Each call below stands in for one HTTP request, and a request begins on a fresh transaction.
	Without this the process sits inside a `REPEATABLE-READ` snapshot taken by an earlier read while
	other processes commit, and the next locking read inside the app meets MariaDB's `1020 Record
	has changed since last read` on `tabSeries` — the framework's own naming-series compare-and-set.
	That is the app behaving correctly, and it is what `_contention` exists for; this script was
	the thing pretending to be one long request.
	"""
	frappe.db.commit()


def frappe_call(method, **arguments):
	from smartprocess_logistics.api import endpoints

	begin_request()

	return getattr(endpoints, method)(**arguments)


def send(row_key, labels, name=None):
	"""One batch, committed — because that is what one save is."""
	answer = frappe_call(
		"add_row_serials",
		sales_invoice=name or invoice_name(),
		sales_invoice_item=row_key,
		serials=list(labels),
	)
	frappe.db.commit()

	return answer


def _persian(message):
	return any("؀" <= character <= "ۿ" for character in message)


def code_in(message):
	"""The stable code the window classifies on, out of the message an operator reads."""
	start = message.rfind("[")

	if start < 0 or not message.rstrip().endswith("]"):
		return ""

	return message[start + 1 : message.rindex("]")]


def refused(recorder, work, description, code=None):
	try:
		work()
	except Exception as error:  # noqa: BLE001 — what kind it is, is the next thing asserted
		frappe.db.rollback()

		message = str(error)

		recorder.check(_persian(message), description, message)

		if code is not None:
			recorder.check(
				code_in(message) == code,
				f"  … with the code the window branches on: {code}",
				f"got {code_in(message)!r} from {message!r}",
			)

		return message

	recorder.check(False, description, "nothing was refused")

	return ""


def ensure_users(recorder):
	"""Four operators, with the roles the feature actually needs and one each of the ways it says no.

	`Accounts User` is a prerequisite rather than a detail: every call ends at
	`gateway.assert_can_read_invoice`, which asks the framework, and on this site `Sales Invoice`
	does not grant read to `Sales User`.
	"""
	plan = (
		(EDITOR_USER, "Logistics Serial Editor", COMPANY),
		(VIEWER_USER, "Logistics Serial Viewer", COMPANY),
		(NOROLE_USER, None, COMPANY),
		(RESTRICTED_USER, "Logistics Serial Editor", OTHER_COMPANY),
	)

	for email, logistics_role, company in plan:
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": email.split("@")[0],
					"send_welcome_email": 0,
					"roles": [{"role": logistics_role}] if logistics_role else [{"role": "Accounts User"}],
				}
			).insert()

			recorder.note(f"created user {email}")

		user = frappe.get_doc("User", email)
		held = {row.role for row in user.roles}

		for role in ("Accounts User", logistics_role):
			if role and role not in held:
				user.append("roles", {"role": role})

		user.save()

		if not frappe.db.exists("User Permission", {"user": email, "allow": "Company"}):
			frappe.get_doc(
				{
					"doctype": "User Permission",
					"user": email,
					"allow": "Company",
					"for_value": company,
				}
			).insert()


def plant_invoice(remark, *, submit=False, cancel=False, donor=False):
	abbr = frappe.db.get_value("Company", COMPANY, "abbr")
	customer = frappe.db.get_value(INVOICE, {"company": COMPANY}, "customer")
	# The donor row is wide: it is where the identities a "stored" race needs are created, and it
	# must be able to hold them without reaching its own quantity.
	quantities = ROWS if not donor else (8,)

	invoice = frappe.get_doc(
		{
			"doctype": INVOICE,
			"company": COMPANY,
			"customer": customer,
			"posting_date": frappe.utils.nowdate(),
			"debit_to": f"Debtors - {abbr}",
			"remarks": remark,
			"items": [
				{
					"item_code": item_code(),
					"qty": quantity,
					"rate": 1,
					"income_account": f"Sales - {abbr}",
				}
				for quantity in quantities
			],
		}
	).insert()

	if submit:
		invoice.submit()

	if cancel:
		invoice.cancel()

	frappe.db.commit()

	return invoice


def arm(recorder):
	frappe.set_user("Administrator")
	ensure_users(recorder)

	for remark, options in (
		(REMARK, {}),
		(DONOR_REMARK, {"donor": True}),
		(CANCELLED_REMARK, {"submit": True, "cancel": True}),
	):
		if not invoice_name(remark):
			planted = plant_invoice(remark, **options)
			recorder.note(f"created {remark}: {planted.name} (docstatus {planted.docstatus})")

	frappe.set_user("Administrator")
	frappe.db.commit()


# --- check --------------------------------------------------------------------------------


def check_atomic_acceptance(recorder, rows):
	"""A batch that is accepted is written whole, and the answer says what it made."""
	before = snapshot()
	answer = send(rows[1], (f"{LABEL}A1", f"{LABEL}A2", f"{LABEL}A3"))

	recorder.check(answer["accepted"] == 3, "a batch of three is accepted as three", str(answer["accepted"]))
	recorder.check(
		len(answer["created_serials"]) == 3,
		"and reports the three serial identities it created",
		str(answer["created_serials"]),
	)
	recorder.check(answer["row"]["registered"] == 3, "the row holds three afterwards", str(answer["row"]))
	recorder.check(
		assignments_of(rows[1]) == 3, "three assignments exist for the row", str(assignments_of(rows[1]))
	)
	recorder.check(
		snapshot()["serials"] - before["serials"] == 3,
		"and three serial rows were created",
		f"{before['serials']} → {snapshot()['serials']}",
	)
	recorder.check(
		isinstance(answer.get("summary"), dict) and "registered" in answer["summary"],
		"the answer carries the invoice's counts, the object the window draws from",
		str(answer.get("summary")),
	)
	recorder.check(
		answer["row"]["row_key"] == rows[1] and answer["sales_invoice_item"] == rows[1],
		"and says which row it wrote",
		f"{answer.get('sales_invoice_item')} / {answer['row']['row_key']}",
	)


def check_refusals_write_nothing(recorder, rows):
	"""Each way a batch can be refused, and the database after each one.

	This is the property the whole change is for. A per-label loop writes the labels it can and
	reports the ones it could not; a batch either goes in or does not, so a row can never hold
	half of what an operator asked for.
	"""
	# A label repeated inside the batch.
	before = snapshot()
	message = refused(
		recorder,
		lambda: send(rows[1], (f"{LABEL}DUP", f"{LABEL}DUP")),
		"a label twice in one batch is refused",
		"DUPLICATE_IN_ROW",
	)
	recorder.check(message.count("DUP") >= 1, "  … and the refusal names the label", message)
	recorder.check(snapshot() == before, "  … and nothing at all was written", f"{before} → {snapshot()}")
	recorder.check(serials_named(f"{LABEL}DUP") == [], "  … not even the serial identity")

	# A label the invoice already holds, beside a new one. The new one is the point: an
	# implementation that wrote label by label would have created it.
	send(rows[2], (f"{LABEL}KEEP",))
	before = snapshot()
	message = refused(
		recorder,
		lambda: send(rows[2], (f"{LABEL}FRESH", f"{LABEL}KEEP")),
		"a label the invoice already holds refuses the batch it is in",
		"ALREADY_REGISTERED",
	)
	recorder.check(
		serials_named(f"{LABEL}FRESH") == [],
		"  … including the new label beside it, which was not created",
		str(serials_named(f"{LABEL}FRESH")),
	)
	recorder.check(snapshot() == before, "  … and nothing was written", f"{before} → {snapshot()}")

	# More labels than the row has places.
	before = snapshot()
	message = refused(
		recorder,
		lambda: send(rows[3], (f"{LABEL}Q1", f"{LABEL}Q2")),
		"a batch past the row's quantity is refused",
		"EXCESS_QUANTITY",
	)
	recorder.check(
		f"{LABEL}Q2" in message and f"{LABEL}Q1" not in message,
		"  … naming the label that does not fit rather than the whole batch",
		message,
	)
	recorder.check(snapshot() == before, "  … and nothing was written", f"{before} → {snapshot()}")

	# The correction the window offers: the same row, one label.
	answer = send(rows[3], (f"{LABEL}Q1",))
	recorder.check(answer["accepted"] == 1, "the row's one place takes one label", str(answer["accepted"]))
	refused(
		recorder,
		lambda: send(rows[3], (f"{LABEL}Q2",)),
		"and a further batch into a row that is now full is refused",
		"EXCESS_QUANTITY",
	)


def check_identity_reuse(recorder, rows):
	"""A serial the catalogue already knows is reused; the same serial twice on one invoice is not.

	These are the two facts the review asked to keep apart, and they look alike from the outside:
	both are "this label already exists somewhere".
	"""
	donor = invoice_name(DONOR_REMARK)
	donor_rows = row_keys(donor)

	before_serials = frappe.db.count(SERIAL)
	answer = send(donor_rows[1], (f"{LABEL}KEEP",), donor)

	recorder.check(
		answer["accepted"] == 1, "a serial the site already knows is accepted", str(answer["accepted"])
	)
	recorder.check(
		list(answer["created_serials"]) == [],
		"nothing new was created in the catalogue",
		str(answer["created_serials"]),
	)
	recorder.check(
		answer["serials_reused"] >= 1, "and the answer says it was reused", str(answer["serials_reused"])
	)
	recorder.check(frappe.db.count(SERIAL) == before_serials, "the catalogue is the same size as before")
	recorder.check(len(serials_named(f"{LABEL}KEEP")) == 1, "one identity, whatever it is registered on")

	refused(
		recorder,
		lambda: send(donor_rows[1], (f"{LABEL}KEEP",), donor),
		"the same label twice on one invoice is refused, reuse or not",
		"ALREADY_REGISTERED",
	)


def check_request_ceilings(recorder, rows):
	"""A malformed or oversized request is a sentence, not a server error.

	`_parse_serials` bounds a batch by the ceiling the validator applies to a request, which is the
	one that matters: a list between that and the file ceiling used to be accepted here and refused
	during validation, where the refusal is a `ValueError` no endpoint catches.
	"""
	before = snapshot()

	refused(recorder, lambda: send(rows[1], ()), "an empty batch is refused")
	refused(recorder, lambda: send(rows[1], "{not json"), "a batch that is not a list is refused")
	refused(recorder, lambda: send(rows[1], (1,)), "a batch whose member is not text is refused")
	refused(
		recorder,
		lambda: frappe_call(
			"add_row_serials",
			sales_invoice=invoice_name(),
			sales_invoice_item=rows[1],
			serials=["X"] * 5001,
		),
		"a batch beyond the request ceiling is refused",
	)

	recorder.check(snapshot() == before, "none of them wrote anything", f"{before} → {snapshot()}")


def check_identity_cannot_be_supplied(recorder, rows):
	"""The server derives the identity, so a request that carries one is refused outright."""
	before = getattr(frappe.local, "form_dict", None)
	frappe.local.form_dict = {"normalized_serial": "smuggled"}

	try:
		refused(
			recorder,
			lambda: send(rows[1], (f"{LABEL}SMUGGLE",)),
			"a request carrying the server-derived identity is refused",
		)
	finally:
		frappe.local.form_dict = before or {}

	recorder.check(serials_named(f"{LABEL}SMUGGLE") == [], "and the label was not written either")


def check_permissions(recorder, rows):
	"""Who may send a batch, decided on the server."""
	frappe.set_user(EDITOR_USER)
	answer = send(rows[2], (f"{LABEL}PERM",))
	recorder.check(answer["accepted"] == 1, "an Editor may add a batch to their company's invoice")

	frappe.set_user(VIEWER_USER)
	refused(recorder, lambda: send(rows[2], (f"{LABEL}VIEW",)), "a Viewer may not", "AUTH_NOT_PERMITTED")

	frappe.set_user(NOROLE_USER)
	refused(
		recorder,
		lambda: send(rows[2], (f"{LABEL}NONE",)),
		"a user with no Logistics role may not",
		"AUTH_NOT_PERMITTED",
	)

	if frappe.db.exists("Company", OTHER_COMPANY):
		frappe.set_user(RESTRICTED_USER)
		refused(
			recorder,
			lambda: send(rows[2], (f"{LABEL}OTHER",)),
			"an Editor restricted to another company may not reach this one",
			"AUTH_COMPANY_NOT_ALLOWED",
		)
	else:
		recorder.note(f"{OTHER_COMPANY} does not exist on this site; the company check is not run here")

	frappe.set_user(EDITOR_USER)
	refused(
		recorder,
		lambda: frappe_call(
			"add_row_serials",
			sales_invoice="IN-DOES-NOT-EXIST",
			sales_invoice_item=rows[2],
			serials=[f"{LABEL}GHOST"],
		),
		"an invoice that does not exist is refused, not raised",
		"AUTH_INVOICE_NOT_FOUND",
	)

	frappe.set_user("Administrator")


def check_cancelled_invoice(recorder, rows):
	"""A cancelled invoice may be read and removed from, and never added to."""
	name = invoice_name(CANCELLED_REMARK)

	if not name:
		recorder.check(False, "the cancelled invoice exists", "arm did not create it")

		return

	frappe.set_user(EDITOR_USER)
	refused(
		recorder,
		lambda: send(rows[1], (f"{LABEL}CANCEL",), name),
		"a batch into a cancelled invoice is refused",
		"AUTH_INVOICE_CANCELLED",
	)
	recorder.check(serials_named(f"{LABEL}CANCEL") == [], "and nothing was written to the catalogue")

	# The half that must keep working: the registrations a cancelled invoice holds are real, and
	# somebody has to be able to see them in order to remove them.
	read = status(name)

	recorder.check(
		read["sales_invoice"] == name and isinstance(read.get("rows"), list),
		"and the same invoice can still be read",
	)

	frappe.set_user("Administrator")


def check(recorder):
	rows = row_keys()

	check_atomic_acceptance(recorder, rows)
	check_refusals_write_nothing(recorder, rows)
	check_identity_reuse(recorder, rows)
	check_request_ceilings(recorder, rows)
	check_identity_cannot_be_supplied(recorder, rows)
	check_permissions(recorder, rows)
	check_cancelled_invoice(recorder, rows)


# --- race ---------------------------------------------------------------------------------


def race_child(argv):
	"""One caller, released at an instant the parent chooses.

	It waits on a wall-clock instant rather than on a queue, so the process is already connected
	and already inside the endpoint when the clock reaches it. Two calls issued one after the other
	would prove nothing: the cap would simply have been applied twice.

	`warm` makes it read the invoice before waiting and then call. That is the interleaving a lock
	wait produces and a simultaneous start hides — this process has already read the row, then it
	blocks on the invoice, then it proceeds after the other caller has committed.
	"""
	deadline = float(argv[2])
	row_key = argv[3]
	labels = argv[4].split(",")
	warm = len(argv) > 5 and argv[5] == "warm"

	connect()
	frappe.set_user(EDITOR_USER)

	if warm:
		before = row_of(row_key)
		print(RACE_WARM + json.dumps({"registered": before["registered"]}))

	while time.time() < deadline:
		time.sleep(0.001)

	# The serial rows are created before the assignments are inserted, so a loser that got that far
	# leaves evidence of what a rollback has to undo.
	created = [serial for label in labels for serial in serials_named(label)]

	try:
		answer = frappe_call(
			"add_row_serials",
			sales_invoice=invoice_name(),
			sales_invoice_item=row_key,
			serials=labels,
		)
		frappe.db.commit()
		result = {"ok": True, "registered": answer["row"]["registered"], "labels": labels}
	except Exception as error:  # noqa: BLE001 — the refusal itself is the result
		frappe.db.rollback()
		result = {
			"ok": False,
			"error": str(error)[:400],
			"code": code_in(str(error)),
			"labels": labels,
			"kind": type(error).__name__,
			"wrote_serial_rows_before_failing": bool(created),
			"where": [line.strip() for line in traceback.format_exc().splitlines() if "/apps/" in line],
		}

	print(RACE_PREFIX + json.dumps(result, ensure_ascii=False))

	return 0


def run_children(row_key, labels, *, warm=False):
	"""Two processes, one instant, and what each of them said."""
	deadline = time.time() + 3.0
	python = sys.executable
	script = os.path.abspath(__file__)
	children = [
		subprocess.Popen(
			[python, script, "race-child", str(deadline), row_key, ",".join(pair)]
			+ (["warm"] if warm else []),
			stdout=subprocess.PIPE,
			stderr=subprocess.STDOUT,
			text=True,
			env=os.environ.copy(),
		)
		for pair in labels
	]

	results = []

	for child in children:
		output, _ = child.communicate(timeout=300)

		for line in output.splitlines():
			if line.startswith(RACE_PREFIX):
				results.append(json.loads(line[len(RACE_PREFIX) :]))
			elif line.startswith(RACE_WARM):
				results.append({"warm": json.loads(line[len(RACE_WARM) :])})

	return [result for result in results if "ok" in result]


def empty_row(row_key):
	current = row_of(row_key)

	if current["assignments"]:
		frappe_call("delete_assignments", names=[entry["name"] for entry in current["assignments"]])
		frappe.db.commit()


def race_round(recorder, row_key, *, warm, stored):
	"""Two callers for the last two places in one row, and what the row holds afterwards.

	`stored` decides whether the labels both callers use already exist on the site. They have to,
	for the second round: a caller writing a **new** serial row also inserts into the index the
	other caller is holding a range lock on, and MariaDB aborts one of them as a deadlock — a real
	answer, but a different one, arriving before the cap is consulted. Labels the site already
	holds leave the cap as the only thing between two simultaneous callers and an over-filled row.
	"""
	frappe.set_user("Administrator")
	empty_row(row_key)

	prefix = f"{LABEL}{'S' if stored else 'N'}{'W' if warm else ''}"

	if stored:
		# Registered on the donor invoice, so the identities exist in the catalogue and are not on
		# this invoice: a serial the site already knows, arriving at a row that does not hold it.
		donor = invoice_name(DONOR_REMARK)
		donor_row = row_keys(donor)[1]
		held = {entry["serial_no"].lower() for entry in row_of(donor_row, donor)["assignments"]}

		for index in (1, 2, 3, 4):
			label = f"{prefix}{index}"

			if label.lower() not in held:
				send(donor_row, (label,), donor)

	# One place filled, two free, two callers, two labels each.
	send(row_key, (f"{LABEL}RACEHOLD",))
	frappe.db.commit()

	frappe.set_user(EDITOR_USER)

	current = row_of(row_key)

	recorder.note(
		f"row {row_key} invoices 3 and holds {current['registered']}: two places, two callers, "
		f"{'labels this site already has' if stored else 'labels that do not exist yet'}"
		+ (", the second after a read" if warm else "")
	)

	# Two callers, two places, and **different** labels: with the same labels the loser would be
	# refused by the duplicate rule, which would leave the quantity limit untested under
	# concurrency — and the quantity limit is the rule this round exists to put under pressure.
	results = run_children(row_key, [(f"{prefix}1", f"{prefix}2"), (f"{prefix}3", f"{prefix}4")], warm=warm)

	winners = [result for result in results if result["ok"]]
	losers = [result for result in results if not result["ok"]]

	frappe.set_user("Administrator")
	after = row_of(row_key)

	recorder.check(len(results) == 2, "both callers answered", str(len(results)))
	recorder.check(len(winners) == 1, "exactly one of them won", json.dumps(results, ensure_ascii=False))
	recorder.check(
		after["registered"] <= after["capacity"],
		"the row never exceeds its quantity",
		f"{after['registered']} of {after['capacity']}",
	)

	if losers:
		loser = losers[0]

		recorder.check(_persian(loser["error"]), "the loser was refused with a sentence", loser["error"])
		# Two answers are correct and they mean different things: the cap refused it (the row is
		# full), or the write lost the lock race and the endpoint said so in words that ask for a
		# retry. Anything else — a raw MariaDB error, a traceback, a `does not exist` — is a defect.
		recorder.check(
			loser["code"] == "EXCESS_QUANTITY" or "دوباره تلاش کنید" in loser["error"],
			"  … because the row is full, or because the write raced and says to try again",
			f"code={loser['code']!r} kind={loser['kind']} where={loser['where']}",
		)
		recorder.check(
			assignments_of(row_key) == after["registered"],
			"  … and left no assignment of its own behind",
			f"assignments={assignments_of(row_key)} registered={after['registered']}",
		)

		if not stored:
			recorder.check(
				all(serials_named(label) == [] for label in loser["labels"]),
				"  … and the serial rows it created were rolled back with it",
				json.dumps({label: serials_named(label) for label in loser["labels"]}, ensure_ascii=False),
			)

	return prefix


def race(recorder):
	"""Twice, because the two rounds ask different questions of the same lock."""
	row_key = row_keys()[RACE_ROW]

	race_round(recorder, row_key, warm=False, stored=False)
	race_round(recorder, row_key, warm=True, stored=True)


# --- retry --------------------------------------------------------------------------------


def check_retry(recorder):
	"""What a caller does after a refusal: the same batch again, and the same batch with less in it.

	A retry is safe because one request is one transaction, so a request that was refused wrote
	nothing. The two things that would make it unsafe are a partial write — tested above — and a
	request that succeeded being indistinguishable from one that did not, which is what the first
	half of this checks.
	"""
	row_key = row_keys()[RACE_ROW]
	frappe.set_user("Administrator")
	empty_row(row_key)

	capacity = row_of(row_key)["capacity"]

	answer = send(row_key, tuple(f"{LABEL}FILL{index}" for index in range(1, capacity + 1)))
	frappe.db.commit()

	recorder.check(
		answer["row"]["registered"] == capacity,
		f"the row is filled to its {capacity} units in one request",
		str(answer["row"]),
	)

	# The same batch again: a completed request, sent twice. It must be refused rather than
	# doubled, and the row must be exactly as it was.
	before = snapshot()
	refused(
		recorder,
		lambda: send(row_key, tuple(f"{LABEL}FILL{index}" for index in range(1, capacity + 1))),
		"re-sending a batch that was written is refused",
		"ALREADY_REGISTERED",
	)
	recorder.check(
		snapshot() == before and row_of(row_key)["registered"] == capacity,
		"  … and writes nothing a second time",
		f"{before} → {snapshot()}",
	)

	# A retry that no longer fits, then the same retry with the row given a place.
	refused(
		recorder,
		lambda: send(row_key, (f"{LABEL}RETRY",)),
		"a retry into a full row is refused",
		"EXCESS_QUANTITY",
	)

	freed = row_of(row_key)["assignments"][0]["name"]

	frappe_call("delete_assignments", names=[freed])
	frappe.db.commit()

	answer = send(row_key, (f"{LABEL}RETRY",))
	frappe.db.commit()

	recorder.check(
		answer["accepted"] == 1,
		"and the same label is accepted once a place is free",
		json.dumps(answer["row"], ensure_ascii=False),
	)
	recorder.check(
		row_of(row_key)["registered"] <= row_of(row_key)["capacity"],
		"the row is inside its quantity after the retry",
		str(row_of(row_key)),
	)


# --- disarm -------------------------------------------------------------------------------


def disarm(recorder):
	"""Remove this script's rows, and say exactly what it could not."""
	frappe.set_user("Administrator")
	removed = {"assignments": 0, "serials": 0, "invoices": [], "users": 0, "kept": []}

	for remark in (REMARK, DONOR_REMARK):
		name = invoice_name(remark)

		if not name:
			continue

		for assignment in frappe.get_all(ASSIGNMENT, filters={"sales_invoice": name}, pluck="name"):
			frappe.delete_doc(ASSIGNMENT, assignment, force=True)
			removed["assignments"] += 1

		frappe.delete_doc(INVOICE, name, force=True)
		removed["invoices"].append(name)

	frappe.db.commit()

	# Labels this script created that nothing points at any more.
	for serial in frappe.get_all(SERIAL, filters={"serial_no": ("like", f"{LABEL}%")}, pluck="name"):
		if frappe.db.exists(ASSIGNMENT, {"logistics_serial": serial}):
			continue

		frappe.delete_doc(SERIAL, serial, force=True)
		removed["serials"] += 1

	frappe.db.commit()

	cancelled = invoice_name(CANCELLED_REMARK)

	if cancelled:
		removed["kept"].append(
			f"{cancelled} ({CANCELLED_REMARK}, docstatus 2 — cancelling is the only way to reach that state and deleting it would mean removing reversed GL Entry rows)"
		)

	for email in (EDITOR_USER, VIEWER_USER, NOROLE_USER, RESTRICTED_USER):
		for permission in frappe.get_all("User Permission", filters={"user": email}, pluck="name"):
			frappe.delete_doc("User Permission", permission, force=True)

		if frappe.db.exists("User", email):
			frappe.delete_doc("User", email, force=True)
			removed["users"] += 1

	frappe.db.commit()
	recorder.note(json.dumps(removed, ensure_ascii=False, indent=1))


def status_phase():
	name = invoice_name()

	if not name:
		print(json.dumps({"invoice": None}))

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
				"donor": invoice_name(DONOR_REMARK),
				"cancelled": invoice_name(CANCELLED_REMARK),
				"url": f"http://127.0.0.1:8095/app/sales-invoice/{name}",
			},
			ensure_ascii=False,
			indent=1,
		)
	)

	return 0


# --- entry point ---------------------------------------------------------------------------


def main(argv):
	phase = (argv[1] if len(argv) > 1 else "status").lower()

	if phase == "race-child":
		return race_child(argv)

	if phase not in ("arm", "check", "race", "retry", "status", "disarm", "run"):
		print(f"unknown phase {phase!r}: expected arm, check, race, retry, status, disarm or run")

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

		if phase in ("retry", "run"):
			print("retry")
			check_retry(recorder)
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
