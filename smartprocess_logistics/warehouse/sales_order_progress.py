"""Read-only Sales Order billing progress for the form panel."""

import frappe

from smartprocess_logistics.core import procurement
from smartprocess_logistics.core.inventory import quantity

QUERY_METHOD = "smartprocess_logistics.warehouse.sales_order_progress.sales_order_progress"
MAX_INVOICE_ROWS = 10000


def _invoice_lines(doc, item_names):
	if not frappe.has_permission("Sales Invoice", "read"):
		return None
	if not item_names:
		return []

	headers = frappe.get_list(
		"Sales Invoice",
		filters={"company": doc.company, "docstatus": 1},
		fields=["name", "is_return", "update_stock"],
		limit_page_length=MAX_INVOICE_ROWS + 1,
	)
	if len(headers) > MAX_INVOICE_ROWS:
		frappe.throw(
			"تعداد فاکتورهای فروش از سقف مجاز بیشتر است؛ نمایش پیشرفت فاکتور ممکن نیست.",
			frappe.ValidationError,
		)
	parent_map = {row.get("name"): row for row in headers}
	if not parent_map:
		return []

	rows = frappe.get_all(
		"Sales Invoice Item",
		filters={
			"parent": ("in", tuple(parent_map)),
			"so_detail": ("in", tuple(item_names)),
			"docstatus": 1,
		},
		fields=["parent", "so_detail", "qty", "amount"],
		limit_page_length=MAX_INVOICE_ROWS + 1,
	)
	if len(rows) > MAX_INVOICE_ROWS:
		frappe.throw(
			"تعداد ردیف‌های فاکتور فروش از سقف مجاز بیشتر است؛ نمایش پیشرفت فاکتور ممکن نیست.",
			frappe.ValidationError,
		)
	return [
		procurement.InvoiceLine(
			so_detail=row.get("so_detail"),
			qty=row.get("qty"),
			docstatus=1,
			is_return=parent_map[row.get("parent")].get("is_return"),
			update_stock=parent_map[row.get("parent")].get("update_stock"),
			amount=row.get("amount"),
			parent=row.get("parent"),
		)
		for row in rows
	]


@frappe.whitelist(methods=["GET"])
def sales_order_progress(sales_order=None):
	"""Ordered, invoiced, remaining and delivered quantities for one Sales Order.

	Also returns the derived operational status, the invoice names behind each invoiced
	quantity (for drill-down) and the totals the panel prints. Read-only: it reads submitted
	Sales Invoices, and it never writes to the Sales Order or to the invoices.
	"""
	name = str(sales_order or "").strip()
	if not name:
		frappe.throw("شناسهٔ سفارش فروش مشخص نشده است.")

	if not frappe.has_permission("Sales Order", "read", doc=name):
		frappe.throw("این سفارش فروش برای شما قابل دسترسی نیست.", frappe.PermissionError)

	doc = frappe.get_doc("Sales Order", name)
	item_names = [row.name for row in doc.items if row.name]
	invoice_lines = _invoice_lines(doc, item_names)
	billing_available = invoice_lines is not None
	invoiced = procurement.invoiced_quantities_by_so_detail(invoice_lines or [])
	reviews = procurement.credit_note_reviews_by_so_detail(invoice_lines or [])

	invoices_by_so_detail = {}
	for line in invoice_lines or ():
		invoices_by_so_detail.setdefault(line.so_detail, set()).add(line.parent)

	rows = []
	if billing_available:
		for item in doc.items:
			line = procurement.SalesOrderLine(
				name=item.name,
				sales_order=doc.name,
				company=doc.company,
				item_code=item.item_code,
				qty=item.qty,
				delivered_qty=item.delivered_qty,
				returned_qty=item.returned_qty,
				invoiced_qty=invoiced.get(item.name, 0),
				conversion_factor=item.conversion_factor,
				transaction_date=doc.transaction_date,
				delivery_date=item.delivery_date or doc.delivery_date,
				status=doc.status,
				docstatus=doc.docstatus,
				idx=item.idx,
			)
			billable = procurement.billable_quantity(line)
			remaining = procurement.remaining_uninvoiced(line)
			progress = (line.invoiced_qty / billable * 100) if billable else 0
			rows.append(
				{
					"so_detail": item.name,
					"item_code": item.item_code,
					"item_name": item.item_name,
					"ordered_qty": float(quantity(item.qty)),
					"invoiced_qty": float(quantity(line.invoiced_qty)),
					"remaining_qty": float(remaining),
					"delivered_qty": float(quantity(item.delivered_qty)),
					"progress": float(progress),
					"review": ", ".join(reviews.get(item.name, ())),
					"invoices": sorted(invoices_by_so_detail.get(item.name, ())),
				}
			)
	else:
		for item in doc.items:
			rows.append(
				{
					"so_detail": item.name,
					"item_code": item.item_code,
					"item_name": item.item_name,
					"ordered_qty": float(quantity(item.qty)),
					"invoiced_qty": None,
					"remaining_qty": None,
					"delivered_qty": float(quantity(item.delivered_qty)),
					"progress": None,
					"review": "",
					"invoices": [],
				}
			)

	return {
		"available": True,
		"billing_available": billing_available,
		"billing_note": ""
		if billing_available
		else "مقدار فاکتورشده برای نقش شما در دسترس نیست؛ فقط مقدار سفارش و تحویل نمایش داده می‌شود.",
		"operational_status": procurement.operational_status(
			doc.docstatus, doc.status, doc.per_delivered, doc.per_billed
		),
		"contributes_to_demand": procurement.contributes_to_demand(
			doc.docstatus, doc.status, doc.per_delivered
		),
		"totals": _totals(rows),
		"rows": rows,
	}


def _totals(rows):
	"""Ordered, invoiced, remaining and delivered totals, or `None` where billing is unreadable."""
	def total(field):
		values = [row[field] for row in rows if row.get(field) is not None]
		return float(round(sum(values), 6)) if values else None

	return {
		"ordered_qty": total("ordered_qty"),
		"invoiced_qty": total("invoiced_qty"),
		"remaining_qty": total("remaining_qty"),
		"delivered_qty": total("delivered_qty"),
	}
