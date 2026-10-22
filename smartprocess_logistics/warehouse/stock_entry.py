"""Server-side classification guard for Stock Entry.

Standard ERPNext owns the movement: company, disabled/group warehouse, permissions, negative
stock, valuation and the ledger. This handler adds one rule only: a movement that touches a
virtual warehouse must be a Material Transfer. It does not require a Purchase Receipt, a
destination whitelist or a custom arrival call.
"""

import frappe

from smartprocess_logistics.warehouse import classification, policy


def _skip(doc):
	if getattr(frappe.flags, "in_install", False) or getattr(frappe.flags, "in_patch", False):
		return True
	if getattr(frappe.flags, "in_migrate", False):
		return True
	return doc.docstatus != 0 and getattr(doc, "_action", None) != "submit"


def validate_stock_entry(doc, method=None):
	if _skip(doc):
		return

	for row in doc.items:
		source = row.get("s_warehouse") or doc.get("from_warehouse")
		target = row.get("t_warehouse") or doc.get("to_warehouse")
		source_virtual = classification.is_virtual(source)
		target_virtual = classification.is_virtual(target)
		if not source_virtual and not target_virtual:
			continue

		problem = policy.stock_entry_problem(
			doc.purpose,
			source_virtual,
			target_virtual,
			row.idx,
		)
		if problem:
			frappe.throw(problem.message, title="جریان انبار — انبار مجازی")
