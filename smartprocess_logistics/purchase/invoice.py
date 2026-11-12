"""Purchase Invoice handler: a submitted receipt blocks the invoice's cancellation.

This is a pre-check. The framework's own back-link check already refuses a Link that points at
the invoice, but only after `on_cancel` has run and the whole cancel has to roll back. Running
here fails earlier, with a message that names the blocking receipts, and it also covers the
`purchase_invoice_item` Data field, which no Link check can see. The framework check stays the
backstop.

The invoice row is locked before its receipts are read, and the receipt validator locks the
invoice it reads, so a receipt submitted and an invoice cancelled at the same moment cannot
both succeed.
"""

import frappe

from smartprocess_logistics.purchase import rules

TITLE = "جریان خرید — فاکتور خرید"

# Standard v16 keeps the invoice link on the receipt row. A site that adds the same link to
# the receipt header is honoured too, so a header-only reference cannot bypass the guard.
HEADER_FIELD = "purchase_invoice"


def _skip(doc):
	if getattr(frappe.flags, "in_install", False) or getattr(frappe.flags, "in_patch", False):
		return True
	if getattr(frappe.flags, "in_migrate", False):
		return True
	# The framework's back-link check honours this flag; a documented data repair keeps working.
	if getattr(doc.flags, "ignore_links", False):
		return True
	# `_cancel()` sets docstatus before saving, so the action is the only signal left.
	return getattr(doc, "_action", None) != "cancel"


def _item_link_names(doc):
	"""Receipts whose submitted rows name this invoice or one of its item rows."""
	names = {
		row.get("parent")
		for row in frappe.get_all(
			"Purchase Receipt Item",
			filters=[["purchase_invoice", "=", doc.name], ["docstatus", "=", 1]],
			fields=["parent"],
			limit_page_length=0,
		)
	}

	item_names = tuple(sorted({row.name for row in doc.items if row.name}))
	if item_names:
		names |= {
			row.get("parent")
			for row in frappe.get_all(
				"Purchase Receipt Item",
				filters=[["purchase_invoice_item", "in", item_names], ["docstatus", "=", 1]],
				fields=["parent"],
				limit_page_length=0,
			)
		}

	return names


def _header_link_names(doc):
	field = frappe.get_meta("Purchase Receipt").get_field(HEADER_FIELD)
	if not field or field.fieldtype != "Link" or field.options != "Purchase Invoice":
		return set()

	return {
		row.get("name")
		for row in frappe.get_all(
			"Purchase Receipt",
			filters=[[HEADER_FIELD, "=", doc.name], ["docstatus", "=", 1]],
			fields=["name"],
			limit_page_length=0,
		)
	}


def _submitted_receipts(names):
	names = tuple(sorted(name for name in names if name))
	if not names:
		return []

	return [
		{"name": row.get("name"), "company": row.get("company"), "docstatus": 1}
		for row in frappe.get_all(
			"Purchase Receipt",
			filters=[["name", "in", names], ["docstatus", "=", 1]],
			fields=["name", "company"],
			limit_page_length=0,
		)
	]


def before_cancel(doc, method=None):
	if _skip(doc):
		return

	# A cancel that races a receipt submit must see the committed receipt, and the receipt
	# validator takes the same lock before it reads the invoice.
	frappe.db.get_value("Purchase Invoice", doc.name, "name", for_update=True)

	receipts = _submitted_receipts(_item_link_names(doc) | _header_link_names(doc))
	problems = rules.invoice_cancel_problems(receipts, doc.company)
	if problems:
		frappe.throw(problems[0].message, title=TITLE)
