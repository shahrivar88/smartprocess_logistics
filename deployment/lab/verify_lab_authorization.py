"""Who may do what, asked of a real session on a real site.

`docs/integration-test-plan.md` phase 2. The database-free suite asserts the *tables* — which
level each endpoint needs and which named role sits beside it — and the static tests assert the
DocPerm JSON. What neither can show is the half that belongs to Frappe: that `frappe.session.user`
reaches `core.authz` as a principal with that user's roles and `User Permission` rows, and that a
refused endpoint refuses *that caller* rather than everybody.

The phase's own note says which expectation is the load-bearing one: a Manager without the
Archivist role must be refused by the historical restore. If that gate is open, the role is
decoration and the historical path is reachable by anybody who can administer the app.

    arm      the three users this phase needs, with the roles the app actually requires
    check    the assertions, as each caller in turn
    disarm   report the users this leaves behind. Nothing is deleted: a user with roles is not a
             test fixture the next run can do without, and removing one would change the site
    run      arm, check, disarm

Run it inside the lab's backend container, from the sites directory:

    docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab \\
      -f deployment/lab/compose.isolation-lab.yaml \\
      exec -T -w /home/frappe/frappe-bench/sites backend \\
      /home/frappe/frappe-bench/env/bin/python \\
      /home/frappe/frappe-bench/apps/smartprocess_logistics/deployment/lab/verify_lab_authorization.py run
"""

import json
import sys

import frappe

from smartprocess_logistics.api import endpoints
from smartprocess_logistics.core import authz

SITE = "sp_logistics_lab"
SITES_PATH = "/home/frappe/frappe-bench/sites"

COMPANY = "SP LAB COMPANY"
OTHER_COMPANY = "SP LAB OTHER"
INVOICE = "Sales Invoice"

EDITOR_USER = "lab-editor@example.test"
MANAGER_USER = "lab-manager@example.test"
ARCHIVIST_USER = "lab-archivist@example.test"
RESTRICTED_USER = "lab-restricted@example.test"

# `Accounts User` is what lets a caller read a Sales Invoice on this site, and the gateway asks the
# framework for that rather than for a Logistics role — see `verify_scan_station.py`, where the
# prerequisite was measured rather than assumed.
BASE_ROLES = ("Accounts User",)

USERS = {
	EDITOR_USER: ("Logistics Serial Editor",),
	# The Manager the transfer phase plants too. Manager and nothing more: this phase's 2.4 is
	# about what that level *cannot* reach.
	MANAGER_USER: ("Logistics Serial Manager",),
	ARCHIVIST_USER: ("Logistics Serial Manager", "Logistics Serial Archivist"),
	RESTRICTED_USER: ("Logistics Serial Manager",),
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
		callable_(*args, **kwargs)
	except Exception as error:  # noqa: BLE001 — the message is the evidence
		return error

	return None


def as_user(email, work):
	"""Run one call as one user, and put the session back whatever happens."""
	frappe.set_user(email)

	try:
		return work()
	finally:
		frappe.set_user("Administrator")


# --- arm ---------------------------------------------------------------------------------


def arm(recorder):
	frappe.set_user("Administrator")

	for email, logistics_roles in USERS.items():
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": email.split("@")[0],
					"send_welcome_email": 0,
					"roles": [{"role": role} for role in BASE_ROLES + logistics_roles],
				}
			).insert()

			recorder.note(f"created {email} with {', '.join(logistics_roles)}")
			continue

		user = frappe.get_doc("User", email)
		held = {row.role for row in user.roles}

		for role in BASE_ROLES + logistics_roles:
			if role not in held:
				user.append("roles", {"role": role})

		user.save()

	# The other three are restricted to this company so that 2.6 has something to be restricted
	# *from*; the Archivist is restricted too, and that is deliberate: the role governs the
	# cancelled-invoice path and says nothing about which companies the caller may see.
	for email in (EDITOR_USER, MANAGER_USER, ARCHIVIST_USER, RESTRICTED_USER):
		if frappe.db.exists("User Permission", {"user": email, "allow": "Company"}):
			continue

		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": email,
				"allow": "Company",
				"for_value": COMPANY,
			}
		).insert()

		recorder.note(f"restricted {email} to {COMPANY}")

	plant_other_company(recorder)

	frappe.set_user("Administrator")
	frappe.db.commit()


# --- fixtures ----------------------------------------------------------------------------


def live_invoice(company=COMPANY):
	return frappe.db.get_value(
		INVOICE, {"company": company, "docstatus": 1}, "name", order_by="creation desc"
	)


def other_company_invoice():
	"""An invoice in the company the restricted users may not see, with assignments on it."""
	found = frappe.db.get_value(INVOICE, {"company": OTHER_COMPANY, "docstatus": ["!=", 2]}, "name")

	if found:
		return found

	return None


def plant_other_company(recorder):
	"""One invoice of the other company with one registration on it, which is what 2.6 needs.

	A draft, like the company-isolation script's: what the restriction decides on is the stored
	invoice's company and nothing here needs a ledger effect. It stays behind afterwards, and it
	has to — a user restricted to one company is a standing fact about this site, so the row it is
	restricted *from* is not a fixture the next run can do without.
	"""
	if other_company_invoice():
		return

	abbr = frappe.db.get_value("Company", OTHER_COMPANY, "abbr")
	source = frappe.get_doc(INVOICE, frappe.db.get_value(INVOICE, {"company": COMPANY}, "name"))
	item = frappe.db.get_value("Logistics Serial", {"item_code": ("is", "set")}, "item_code")
	stamp = frappe.utils.now().replace(" ", "-").replace(":", "")

	invoice = frappe.get_doc(
		{
			"doctype": INVOICE,
			"company": OTHER_COMPANY,
			"customer": source.customer,
			"posting_date": frappe.utils.nowdate(),
			"debit_to": f"Debtors - {abbr}",
			"remarks": f"planted by verify_lab_authorization.py — {OTHER_COMPANY}",
			"items": [
				{
					"item_code": item,
					"qty": 1,
					"rate": 1,
					"income_account": f"Sales - {abbr}",
					"cost_center": frappe.db.get_value("Company", OTHER_COMPANY, "cost_center"),
				}
			],
		}
	).insert()

	answer = call(
		"add_row_serials",
		sales_invoice=invoice.name,
		sales_invoice_item=invoice.items[0].name,
		serials=[f"LAB-AUTH-{stamp}"],
	)
	frappe.db.commit()

	recorder.note(f"planted {invoice.name} in {OTHER_COMPANY} with {answer['accepted']} label(s)")


# --- check -------------------------------------------------------------------------------


def check_the_editor(recorder):
	"""2.3 — an Editor works the invoice and does not administer the app."""
	invoice = live_invoice()

	status = as_user(EDITOR_USER, lambda: call("get_invoice_serial_status", sales_invoice=invoice))
	recorder.check(
		isinstance(status, dict) and "rows" in status,
		"an Editor may read the invoice's serial status",
		str(status)[:120],
	)

	refused = as_user(EDITOR_USER, lambda: thrown(call, "export_serials"))
	recorder.check(
		refused is not None and authz.CODE_NOT_PERMITTED in str(refused),
		"and may not export the site's records, which is the Settings page's first button",
		str(refused)[:160],
	)

	refused = as_user(
		EDITOR_USER,
		lambda: thrown(call, "preview_cancelled_serial_history", content="{}"),
	)
	recorder.check(
		refused is not None,
		"and may not open the historical path at all",
		str(refused)[:160],
	)

	# The same write the window behind the Sales Invoice button makes: refused for want of a row
	# that exists, and *not* for want of a role. The distinction is the whole point of asking.
	refused = as_user(
		EDITOR_USER,
		lambda: thrown(
			call,
			"add_row_serial",
			sales_invoice=invoice,
			sales_invoice_item="no-such-row",
			serial="LAB-AUTH-0001",
		),
	)
	recorder.check(
		refused is not None and authz.CODE_NOT_PERMITTED not in str(refused),
		"an Editor's registration is refused by the row, not by the role",
		str(refused)[:160],
	)


def check_the_manager_without_the_role(recorder):
	"""2.4 — the gate this phase exists for."""
	file = as_user(
		"Administrator", lambda: json.dumps(call("export_serials")["document"], ensure_ascii=False)
	)

	allowed = as_user(MANAGER_USER, lambda: thrown(call, "export_serials"))
	recorder.check(
		allowed is None,
		"a Manager may export the site's records",
		str(allowed)[:160],
	)

	refused = as_user(
		MANAGER_USER,
		lambda: thrown(call, "preview_cancelled_serial_history", content=file),
	)
	recorder.check(
		refused is not None and authz.ROLE_ARCHIVIST in str(refused),
		"a Manager **without** the Archivist role is refused by name",
		str(refused)[:200],
	)
	recorder.check(
		refused is not None and "نقش" in str(refused),
		"and the sentence says a role is what is missing",
		str(refused)[:200],
	)

	return file


def check_the_archivist(recorder, file):
	"""2.5 — the same call with the role, which must get past the gate."""
	refused = as_user(
		ARCHIVIST_USER,
		lambda: thrown(call, "preview_cancelled_serial_history", content=file),
	)
	recorder.check(
		refused is None or authz.ROLE_ARCHIVIST not in str(refused),
		"the Archivist reaches the historical path: no role refusal comes back",
		str(refused)[:200],
	)
	recorder.check(
		refused is None or authz.CODE_NOT_PERMITTED not in str(refused),
		"and is not refused for want of a level either",
		str(refused)[:200],
	)

	# The records restore, the other half of the same role.
	refused = as_user(
		ARCHIVIST_USER,
		lambda: thrown(call, "preview_restore_audit_records", content=file),
	)
	recorder.check(
		refused is None or authz.ROLE_ARCHIVIST not in str(refused),
		"the same role opens the audit-record restore",
		str(refused)[:200],
	)


def check_the_company_restriction(recorder):
	"""2.6 — a file full of another company's rows, read by a caller restricted to one.

	The comparison is against the same file read by an unrestricted caller, because a count on its
	own says nothing here: this file was exported *from this site*, so every row in it already
	exists and the unrestricted preview places nothing and refuses nothing. What the restriction
	changes is exactly that — the rows in companies the caller cannot reach stop being rows this
	site already has and become rows the caller cannot place, and the count of the difference is
	the count of the file's rows in the other company.
	"""
	document = as_user("Administrator", lambda: call("export_serials")["document"])
	file = json.dumps(document, ensure_ascii=False)

	if not other_company_invoice():
		recorder.note("no invoice exists in the other company, so 2.6 is not asserted")

		return

	others = [row for row in document["assignments"] if row.get("company") == OTHER_COMPANY]

	admin = as_user("Administrator", lambda: call("preview_serial_restore", content=file))
	restricted = as_user(RESTRICTED_USER, lambda: call("preview_serial_restore", content=file))

	recorder.note(f"file: {len(document['assignments'])} assignments, {len(others)} in {OTHER_COMPANY}")
	recorder.note(f"unrestricted preview: {admin['conflicts']} conflicts {admin['by_reason']}")
	recorder.note(f"restricted preview:   {restricted['conflicts']} conflicts {restricted['by_reason']}")
	recorder.note(f"sample: {[row['row'] for row in restricted['sample']][:4]}")

	recorder.check(
		bool(others),
		"the exported file does carry rows of the other company, so the restriction has a subject",
		str(len(others)),
	)
	recorder.check(
		admin["conflicts"] == 0,
		"an unrestricted caller refuses none of it: every row is already on this site",
		str(admin["by_reason"]),
	)
	recorder.check(
		restricted["conflicts"] == len(others),
		"a caller restricted to one company is refused exactly that company's rows",
		f"{restricted['conflicts']} conflicts for {len(others)} rows",
	)
	recorder.check(
		restricted["by_reason"].get("INVOICE_MISSING") == len(others)
		or restricted["by_reason"].get("COMPANY_MISSING") == len(others),
		"and the reason is that the invoice is not one they can see",
		str(restricted["by_reason"]),
	)
	recorder.check(
		restricted["to_create"]["assignments"] == 0,
		"nothing of the other company's is offered as something to create",
		str(restricted["to_create"]),
	)


# --- disarm -------------------------------------------------------------------------------


def disarm(recorder):
	users = sorted(USERS)
	roles = sorted(
		row.name
		for row in frappe.get_all(
			"Role", filters={"name": ["like", "Logistics Serial%"]}, fields=["name"], limit_page_length=0
		)
	)

	recorder.note(f"users left in place: {', '.join(users)}")
	recorder.note(f"roles on the site: {', '.join(roles)}")
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
	print(f"session user at connect: {frappe.session.user}\n")

	if phase in ("arm", "run"):
		print("arm")
		arm(recorder)
		print()

	try:
		if phase in ("check", "run"):
			print("check — what each caller may do (plan phase 2)")
			check_the_editor(recorder)
			print()
			file = check_the_manager_without_the_role(recorder)
			print()
			check_the_archivist(recorder, file)
			print()
			check_the_company_restriction(recorder)
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
