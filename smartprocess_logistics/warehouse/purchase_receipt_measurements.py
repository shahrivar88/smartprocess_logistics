"""Read-only weight and volume summary for the Purchase Receipt form panel."""

import frappe

from smartprocess_logistics.warehouse import measurement_summary

PURCHASE_RECEIPT = "Purchase Receipt"


@frappe.whitelist(methods=["GET"])
def purchase_receipt_measurements(purchase_receipt=None, target_uom=None):
	"""Net/gross weight and volume of one receipt, with coverage and warnings.

	The panel only reads the receipt and its Item specifications. It never writes the document,
	a ledger or the native totals.
	"""
	name = str(purchase_receipt or "").strip()
	if not name:
		frappe.throw("شناسهٔ رسید خرید مشخص نشده است.")

	if not frappe.has_permission(PURCHASE_RECEIPT, "read", doc=name):
		frappe.throw(
			"این رسید خرید برای شما قابل دسترسی نیست.",
			frappe.PermissionError,
		)

	doc = frappe.get_doc(PURCHASE_RECEIPT, name)
	return measurement_summary.purchase_receipt_summary(doc, target_uom=target_uom)
