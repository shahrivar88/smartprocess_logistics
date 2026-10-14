"""Verify the export / uninstall / reinstall / import cycle on the isolated lab site.

Run this **inside the lab's backend container**, where the app and a Frappe runtime are present:

    docker compose -p sp-logistics-isolation-lab \\
      --env-file deployment/lab/.env.lab \\
      -f deployment/lab/compose.isolation-lab.yaml \\
      exec -w /home/frappe/frappe-bench/sites backend /home/frappe/frappe-bench/env/bin/python \\
      apps/smartprocess_logistics/deployment/lab/verify_isolation_cycle.py <phase> --site sp_logistics_lab

Phases, in the order the rehearsal uses them:

    seed             create realistic data — a company, an item, a customer, two invoices, and
                     serials registered through the app's own endpoint
    export           take the export this app offers, and keep it as a file
    after-removal    the app is gone: prove the export is the only copy left, and that the live
                     tables went with the app
    restore          import the export and prove the history came back exactly once

Every phase exits non-zero on the first failed check and prints one line per check. Evidence is
written to `<site>/private/files/lab-cycle-<phase>-<timestamp>.json` — those files are *evidence*,
not the preservation mechanism. The export is the preservation mechanism, and it is a file an
operator keeps somewhere safe; a full site backup covers the rest.

The phases call the app's own endpoint functions rather than re-implementing them, so what is
verified is the code the Desk buttons call.

Safety
------
This script refuses to run against anything but the lab site by name **and** by database name, so
a mistyped `--site` cannot point it at a site that matters. It writes nothing outside the lab.
"""

import json
import os
import sys

import frappe

LAB_SITE = "sp_logistics_lab"
PROTECTED_SITES = {"SmartProcessERP", "ptm_clean_test", "ptm_clean_test2", "jalali_ui_test"}
PROTECTED_DATABASES = {"_939af57aabbacf4d"}

ITEM = "SP-LAB-WARRANTY-ITEM"
CUSTOMER = "SP LAB CUSTOMER"
COMPANY = "SP LAB COMPANY"

SERIALS_A = ("LAB-W-0001", "LAB-W-0002", "LAB-W-0003")
SERIALS_B = ("LAB-W-0004", "LAB-W-0005")

EXPORT_PREFIX = "lab-serial-export"


class Failed(AssertionError):
	"""A check that did not hold. The phase stops here."""


def check(condition, description, detail=""):
	if not condition:
		raise Failed(f"{description}{': ' + str(detail) if detail else ''}")

	print(f"  ok  {description}")


def say(message):
	print(f"      {message}")


def as_text(value):
	"""A datetime, a number or a string, as the text an export stores it as."""
	return "" if value in (None, "") else str(value)


def guard():
	"""Refuse to run anywhere but the lab, before a single read."""
	site = frappe.local.site

	check(site not in PROTECTED_SITES, f"site {site} is not a protected site")
	check(site == LAB_SITE, f"site is the lab site ({LAB_SITE})", site)
	check(frappe.conf.db_name not in PROTECTED_DATABASES, "database is not the protected database")

	say(f"site={site} database={frappe.conf.db_name}")


def evidence(phase, payload):
	stamp = frappe.utils.now().replace(":", "").replace(" ", "-")
	path = frappe.get_site_path("private", "files", f"lab-cycle-{phase}-{stamp}.json")

	with open(path, "w", encoding="utf-8") as handle:
		json.dump(payload, handle, ensure_ascii=False, indent=1, default=str)

	say(f"evidence: {path}")

	return path


def export_path():
	directory = frappe.get_site_path("private", "files")

	names = sorted(name for name in os.listdir(directory) if name.startswith(EXPORT_PREFIX))

	return os.path.join(directory, names[-1]) if names else None


# --- the app's own state ---------------------------------------------------------------


def live_counts():
	return {
		"serials": frappe.db.count("Logistics Serial"),
		"assignments": frappe.db.count("Logistics Serial Assignment"),
	}


def relationships():
	"""Every stored assignment as `(invoice, invoice row, normalized serial)`."""
	return sorted(
		(row.get("sales_invoice"), row.get("sales_invoice_item"), row.get("normalized_serial"))
		for row in frappe.get_all(
			"Logistics Serial Assignment",
			fields=["sales_invoice", "sales_invoice_item", "normalized_serial"],
			limit_page_length=0,
		)
	)


def endpoints():
	from smartprocess_logistics.api import endpoints as module

	return module


# --- phases ---------------------------------------------------------------------------


def phase_seed(_arguments):
	"""Realistic data, created and registered through the app's own endpoint."""
	module = endpoints()

	frappe.set_user("Administrator")

	if not frappe.db.exists("Warehouse Type", "Transit"):
		# A site that has had ERPNext installed but never set up has no fixtures at all: no Item
		# Groups, no UOMs, no Warehouse Types, no Territories. Creating a Company needs them —
		# measured: the Company's own default warehouse failed with `Could not find Warehouse
		# Type: Transit`. `install_fixtures.install` is what ERPNext's setup wizard calls to load
		# them, so this is the framework's own path rather than a list of documents written here.
		from erpnext.setup.setup_wizard.operations.install_fixtures import install as install_fixtures

		install_fixtures(country="Iran")
		say("installed ERPNext's default fixtures")

	if not frappe.db.exists("Company", COMPANY):
		company = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": COMPANY,
				"country": "Iran",
				"default_currency": "IRR",
				"create_chart_of_accounts_based_on": "Standard Template",
				"chart_of_accounts": "Standard",
			}
		).insert()
		say(f"company: {company.name}")

	if not frappe.db.exists("Item", ITEM):
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": ITEM,
				"item_name": "کالای آزمایشی گارانتی",
				"item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "Products",
				"stock_uom": "Nos",
				"is_stock_item": 0,
				"is_sales_item": 1,
				"sp_serial_required": 1,
			}
		).insert()
		say(f"item: {item.name}")

	if not frappe.db.exists("Price List", "Standard Selling"):
		# The other half of a setup a fresh site has never had: a default currency, the standard
		# price lists in that currency, and the global defaults that point at this company.
		# Measured: without them an invoice failed with `Exchange Rate is mandatory. Maybe Currency
		# Exchange record is not created for INR to IRR` — the site defaulted to INR while the
		# company is in IRR. This is the same function the setup wizard calls.
		from erpnext.setup.setup_wizard.operations.install_fixtures import install_defaults

		install_defaults(frappe._dict(company_name=COMPANY, currency="IRR", country="Iran"))
		say("installed ERPNext's default currency, price lists and global defaults")

	if not frappe.db.exists("Fiscal Year", str(frappe.utils.getdate().year)):
		# A third thing a site without a setup wizard has none of. Measured: an invoice failed
		# with `Posting Date ... is not in any active Fiscal Year for SP LAB COMPANY`. ERPNext's
		# own yearly job normally creates this; here it is created for the year the rehearsal
		# runs in, through the ordinary document API.
		today = frappe.utils.getdate()

		frappe.get_doc(
			{
				"doctype": "Fiscal Year",
				"year": str(today.year),
				"year_start_date": f"{today.year}-01-01",
				"year_end_date": f"{today.year}-12-31",
			}
		).insert()
		say(f"fiscal year {today.year} created")

	if not frappe.db.exists("Customer", CUSTOMER):
		customer = frappe.get_doc(
			{"doctype": "Customer", "customer_name": CUSTOMER, "customer_type": "Individual"}
		).insert()
		say(f"customer: {customer.name}")

	invoices = []

	for serials in (SERIALS_A, SERIALS_B):
		# Named by ERPNext rather than by this script: an explicitly passed name is cleared by
		# `model/naming.py` unless `frappe.flags.in_import` is set, and a rehearsal that forced
		# names would be exercising a path the Desk never takes.
		invoice = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"company": COMPANY,
				"customer": CUSTOMER,
				"posting_date": frappe.utils.today(),
				"items": [{"item_code": ITEM, "qty": len(serials), "rate": 1000}],
			}
		)
		invoice.insert()

		submitted = True

		try:
			invoice.submit()
		except Exception as error:
			# A submitted invoice is the realistic case and it posts to the ledger. A fresh site
			# with no chart of accounts cannot post, and that must not stop the rehearsal: the
			# rows and the serials are what this script is about.
			submitted = False
			frappe.db.rollback()
			invoice = frappe.get_doc("Sales Invoice", invoice.name)
			say(f"invoice {invoice.name} stays a draft: {type(error).__name__}")

		row = invoice.items[0].name

		# The app's own write path: the same endpoint the Desk button calls. It authorizes, it
		# normalizes, it applies the coverage rules and writes through the ORM and the gateway.
		frappe.local.form_dict = frappe._dict()
		module.set_row_serials(invoice.name, row, "، ".join(serials))

		invoices.append({"name": invoice.name, "serials": list(serials), "submitted": submitted})
		say(f"invoice: {invoice.name} ({'submitted' if submitted else 'draft'}) with {len(serials)} serials")

	counts = live_counts()
	expected = len(SERIALS_A) + len(SERIALS_B)

	check(counts["serials"] >= expected, "every seeded serial exists", counts)
	check(counts["assignments"] >= expected, "every seeded serial is assigned", counts)

	evidence("seed", {"invoices": invoices, "counts": counts, "relationships": relationships()})
	frappe.db.commit()


def phase_export(_arguments):
	"""Take the export the app offers, and keep it exactly as the browser would."""
	module = endpoints()

	payload = module.export_serials()
	document = payload["document"]
	text = json.dumps(document, ensure_ascii=False, indent=1)

	counts = live_counts()

	check(
		document["counts"]["serials"] == counts["serials"],
		"the export holds every serial",
		document["counts"],
	)
	check(
		document["counts"]["assignments"] == counts["assignments"],
		"the export holds every assignment",
		document["counts"],
	)
	check(bool(document.get("digest")), "the export carries a digest")
	check(document.get("site") == frappe.local.site, "the export names the site it came from")

	path = frappe.get_site_path("private", "files", f"{EXPORT_PREFIX}-{frappe.utils.now()[:10]}.json")

	with open(path, "w", encoding="utf-8") as handle:
		handle.write(text)

	say(f"export written: {path} ({len(text)} bytes)")

	# What the import has to reproduce, kept beside the export so the later phase can compare
	# against the state *before* the removal rather than against itself.
	evidence(
		"export",
		{
			"path": path,
			"counts": counts,
			"document_counts": document["counts"],
			"relationships": relationships(),
		},
	)

	frappe.db.commit()


def phase_after_removal(_arguments):
	"""The app is gone. The export must be the only copy, and the tables must be gone with it."""
	from smartprocess_logistics.core import export as rules

	check(not frappe.db.exists("DocType", "Logistics Serial"), "the app's DocTypes are gone")
	check(
		not frappe.db.exists("DocType", "Logistics Serial Assignment"),
		"the assignment DocType is gone",
	)

	path = export_path()

	check(bool(path), "the export file is still there")

	with open(path, encoding="utf-8") as handle:
		document = rules.read(handle.read())

	check(document["counts"]["serials"] > 0, "the export still holds serials", document["counts"])

	# The removal takes this app's tables and nothing else. The invoices the serials were
	# registered on belong to ERPNext, and they must still be there, still submitted, with their
	# item rows — that is the difference between removing an app and removing a site.
	invoices = frappe.db.count("Sales Invoice")
	rows = frappe.db.count("Sales Invoice Item")
	submitted = frappe.db.count("Sales Invoice", {"docstatus": 1})

	check(invoices > 0, "the sales invoices are still there", invoices)
	check(rows >= invoices, "their item rows are still there", rows)
	check(submitted == invoices, "every invoice is still submitted", (submitted, invoices))

	say(f"invoices={invoices} rows={rows} submitted={submitted}")

	tables = frappe.db.sql(
		"select table_name from information_schema.tables where table_schema = database()"
		" and table_name like %s",
		("sp_logistics_%",),
	)

	check(not tables, "no leftover table of an older preservation scheme", tables)

	say(f"export readable after the removal: {document['counts']}")

	evidence("after-removal", {"path": path, "counts": document["counts"]})


def phase_after_reinstall(_arguments):
	"""Import the export, twice, and prove the history came back exactly once."""
	module = endpoints()

	path = export_path()

	check(bool(path), "the export file is still there after the reinstall")

	with open(path, encoding="utf-8") as handle:
		text = handle.read()

	before = live_counts()

	check(
		before["serials"] == 0 and before["assignments"] == 0,
		"the reinstall brought back empty tables, so the import is what fills them",
		before,
	)

	preview = module.preview_serial_restore(text)

	say(f"preview: {preview['to_create']} to create, {preview['to_skip']}, {preview['conflicts']} conflicts")

	check(preview["conflicts"] == 0, "nothing this site cannot place", preview.get("sample"))
	check(preview["to_create"]["serials"] > 0, "the preview offers serials to create")
	check(bool(preview["token"]), "the preview returned the token the import needs")

	first = module.apply_serial_restore(text, preview["token"])
	second = module.apply_serial_restore(text, preview["token"])

	check(
		first["counts"]["created"]["serials"] == preview["to_create"]["serials"],
		"every offered serial was created",
		first["counts"],
	)
	check(
		first["counts"]["created"]["assignments"] == preview["to_create"]["assignments"],
		"every offered assignment was created",
		first["counts"],
	)
	check(
		second["counts"]["created"] == {"serials": 0, "assignments": 0},
		"a second import creates nothing at all",
		second["counts"],
	)

	after = live_counts()

	check(after["serials"] == preview["to_create"]["serials"], "the serials are live again", after)
	check(
		after["assignments"] == preview["to_create"]["assignments"], "the assignments are live again", after
	)

	# The relationships are the point of the whole exercise: which serial went on which invoice
	# row. Compared against the export itself, not against a number.
	from smartprocess_logistics.core import export as rules

	document = rules.read(text)
	expected = sorted(
		(row.get("sales_invoice"), row.get("sales_invoice_item"), row.get("normalized_serial"))
		for row in rules.assignment_rows(document)
	)

	check(relationships() == expected, "every invoice-to-serial relationship matches the export")

	serials = frappe.db.get_all(
		"Logistics Serial",
		fields=["name", "serial_no", "normalized_serial", "item_code"],
		limit_page_length=0,
	)
	expected_names = sorted(row.get("name") for row in rules.serial_rows(document))

	check(
		sorted(row.get("name") for row in serials) == expected_names,
		"the serials came back under their exported names",
	)
	check(
		sorted(row.get("serial_no") for row in serials)
		== sorted(row.get("serial_no") for row in rules.serial_rows(document)),
		"the original serial text came back unchanged",
	)

	# The four columns Frappe rewrites on an insert. The import writes them back with
	# `frappe.db.set_value(..., update_modified=False)`, and this is the check that the round trip
	# is lossless about *when and by whom*, not only about what.
	restored = {
		row.get("name"): (
			as_text(row.get("creation")),
			as_text(row.get("owner")),
			as_text(row.get("modified")),
			as_text(row.get("modified_by")),
		)
		for row in frappe.db.get_all(
			"Logistics Serial",
			fields=["name", "creation", "owner", "modified", "modified_by"],
			limit_page_length=0,
		)
	}
	exported = {
		row.get("name"): (
			as_text(row.get("creation")),
			as_text(row.get("owner")),
			as_text(row.get("modified")),
			as_text(row.get("modified_by")),
		)
		for row in rules.serial_rows(document)
	}

	check(
		restored == exported,
		"creation, owner, modified and modified_by came back as they were",
		{
			name: (restored.get(name), exported.get(name))
			for name in exported
			if restored.get(name) != exported.get(name)
		},
	)

	evidence(
		"after-reinstall",
		{
			"file": path,
			"preview": preview,
			"first": first["counts"],
			"second": second["counts"],
			"live": after,
			"relationships": relationships(),
			"provenance": restored,
		},
	)

	frappe.db.commit()


PHASES = {
	"seed": phase_seed,
	"export": phase_export,
	"after-removal": phase_after_removal,
	# Named for what it does rather than for when it runs: it restores an exported document and
	# verifies that what came back is what left.
	"restore": phase_after_reinstall,
}


def main(argv):
	arguments = [item for item in argv if not item.startswith("--")]
	phase = arguments[0] if arguments else ""

	if phase not in PHASES:
		print(f"usage: {os.path.basename(__file__)} {{{'|'.join(PHASES)}}} --site {LAB_SITE}")
		return 2

	site = LAB_SITE

	for index, item in enumerate(argv):
		if item == "--site" and index + 1 < len(argv):
			site = argv[index + 1]

	# The sites directory is given, not inferred: `frappe.init` resolves a bare site name against
	# the process's working directory, and a script started with `docker compose exec` does not
	# necessarily have the bench root as its cwd — which is what "site does not exist" meant the
	# first time this ran. A path belongs in the deployment wiring rather than in the app, and this
	# file is deployment wiring.
	frappe.init(site=site, sites_path=os.environ.get("SP_LAB_SITES_PATH", "/home/frappe/frappe-bench/sites"))
	frappe.connect()
	frappe.set_user("Administrator")

	print(f"phase: {phase}")

	try:
		guard()
		PHASES[phase](argv)
	except Failed as failure:
		print(f"  FAIL  {failure}")
		return 1

	print(f"phase {phase}: PASS")
	return 0


if __name__ == "__main__":
	sys.exit(main(sys.argv[1:]))
