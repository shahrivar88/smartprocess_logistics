"""The server side of the inter-app registry on the Settings form.

The registry itself is static metadata in `core/interop.py`, and this module does one thing with
it: answer its checks from the site, read-only. Nothing here writes an app, a record or a
setting, and nothing here decides what "applied" means — that word is computed in core from
facts, so the same answer can be reproduced without a bench.
"""

import frappe

from smartprocess_logistics.core import interop as registry
from smartprocess_logistics.warehouse.classification import SETTINGS_DOCTYPE

TITLE = "وابستگی‌ها و تغییرات بین‌ماژولی"


@frappe.whitelist(methods=["GET"])
def dependency_rows():
	"""Every declared dependency and applied change, with the status the site actually shows."""
	if not frappe.has_permission(SETTINGS_DOCTYPE, "read"):
		frappe.throw("نمایش وابستگی‌ها و تغییرات بین‌ماژولی برای این کاربر مجاز نیست.", title=TITLE)

	return registry.rows(_facts())


def _facts():
	checks = registry.required_checks()
	return {
		"installed": _installed_versions(),
		"records": _record_facts(checks),
		"fields": _field_facts(checks),
	}


def _installed_versions():
	"""Only the apps the registry declares, and only those the site reports as installed."""
	installed = set(frappe.get_installed_apps())
	return {
		dependency.app: _version_of(dependency.app)
		for dependency in registry.DEPENDENCIES
		if dependency.app in installed
	}


def _version_of(app):
	try:
		return str(frappe.get_attr(f"{app}.__version__"))
	except (AttributeError, ImportError):
		return ""


def _record_facts(checks):
	pairs = set()
	for check in checks:
		if check[0] == registry.CHECK_RECORD:
			pairs.add((check[1], check[2]))
		elif check[0] == registry.CHECK_RECORDS:
			pairs.update((check[1], name) for name in check[2])

	return {pair: bool(frappe.db.exists(*pair)) for pair in sorted(pairs)}


def _field_facts(checks):
	names_by_doctype = {}
	for check in checks:
		if check[0] == registry.CHECK_FIELDS:
			names_by_doctype.setdefault(check[1], set()).update(check[2])

	facts = {}
	for doctype, names in names_by_doctype.items():
		if not frappe.db.exists("DocType", doctype):
			facts[doctype] = frozenset()
			continue
		meta = frappe.get_meta(doctype)
		facts[doctype] = frozenset(name for name in names if meta.get_field(name))

	return facts
