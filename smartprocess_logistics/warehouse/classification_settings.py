"""The server side of the classification table, and nothing else.

The rules are in `policy.py` and the resolver is in `classification.py`; this module reads the
saved document, asks them, and refuses or invalidates. It never writes a Warehouse, a Bin, a
Stock Ledger Entry or a setting of its own — a mapping changes what this app lets you do with a
warehouse, not what the warehouse holds.

Why this is a document event and not the DocType controller: `Logistics Settings`'s controller is
one empty class, like every other controller in this app (a test holds it to that), and a
validation that belongs to a field set belongs in a hook anyway.
"""

import frappe

from smartprocess_logistics.warehouse import classification, policy

TITLE = "جریان انبار — طبقه‌بندی انبار"


@frappe.whitelist(methods=["GET"])
def classification_rows():
	"""Every row the settings table should show, with the classification that applies today.

	The saved rows come first and keep what they hold, so opening the form cannot rewrite a
	mapping. The types nobody has decided yet follow, carrying the classification the effective
	policy already gives them — which is why filling the table in and saving it snapshots today's
	behaviour instead of changing it, and why the rule is not repeated in JavaScript. The row for
	warehouses with no type is always last.

	A saved row whose type has since been deleted stays visible. The server refuses an unknown
	type on save, and hiding the row would leave an administrator with a table that cannot be
	saved and nothing on screen to remove.
	"""
	if not frappe.has_permission(classification.SETTINGS_DOCTYPE, "read"):
		frappe.throw("نمایش طبقه‌بندی انواع انبار برای این کاربر مجاز نیست.", title=TITLE)

	rows = []
	seen = set()

	def add(warehouse_type):
		key = policy.type_key(warehouse_type)
		# The blank key is the bucket for warehouses with no type; it is appended once, last, so a
		# saved table that lists it elsewhere still shows it where the field's help says it is.
		if not key or key in seen:
			return
		seen.add(key)
		rows.append({"warehouse_type": key, "classification": classification.classification_of_type(key)})

	for row in classification.stored_rows():
		add(row.get("warehouse_type"))

	for name in classification.known_types():
		add(name)

	rows.append(
		{"warehouse_type": policy.UNTYPED, "classification": classification.classification_of_type("")}
	)

	return rows


def validate_logistics_settings(doc, method=None):
	"""Refuse a mapping that is unauthorized, unknown or incomplete.

	The write permission is asked again here rather than trusted from the caller: the DocType's
	permission table gives write to `Logistics Serial Manager` and `System Manager` only, and a
	save that never asked would reclassify every warehouse in the site's operational rules.
	`Administrator` passes this check because Frappe treats it as the site's superuser, which is
	the same reason it passes every other permission check in this app.
	"""
	if not frappe.has_permission(classification.SETTINGS_DOCTYPE, "write"):
		frappe.throw(
			"تغییر طبقه‌بندی انواع انبار فقط برای نقش Logistics Serial Manager یا System Manager مجاز است.",
			title=TITLE,
		)

	rows = [
		(row.get("warehouse_type"), row.get("classification"))
		for row in (doc.get(classification.SETTINGS_TABLE) or [])
	]

	for problem in policy.classification_mapping_problems(rows, classification.known_types()):
		frappe.throw(problem.message, title=TITLE)


def forget_classification_mapping(doc, method=None):
	"""Drop the request's cached copy, after the table it was read from has been written."""
	classification.forget_mapping()
