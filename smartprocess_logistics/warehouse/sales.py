"""Keep virtual inventory out of sales, delivery and reservation paths.

The rule is enforced server-side for every caller. A return keeps ERPNext's standard workflow;
only an outbound sale, dispatch or reservation is refused. The paused Sales Invoice panel and
the existing sales fulfilment code are not changed.
"""

import frappe

from smartprocess_logistics.warehouse import classification, policy


def _skip(doc):
	if getattr(frappe.flags, "in_install", False) or getattr(frappe.flags, "in_patch", False):
		return True
	if getattr(frappe.flags, "in_migrate", False):
		return True
	return doc.docstatus != 0 and getattr(doc, "_action", None) != "submit"


def _refuse(problem):
	if problem:
		frappe.throw(problem.message, title="جریان انبار — انبار مجازی")


def _virtual_rows(doc):
	stock_cache = {}
	parents = {row.name: row for row in doc.items if row.get("name")}

	def rows():
		for row in doc.items:
			yield row, row.get("warehouse"), row.get("target_warehouse")

		# A product bundle posts its components from `packed_items`, and ERPNext falls back to
		# the parent item's warehouse when the component does not name one. Both fields must be
		# checked or a bundle can ship stock out of a virtual warehouse.
		for row in doc.get("packed_items") or ():
			parent = parents.get(row.get("parent_detail_docname")) or {}
			yield (
				row,
				row.get("warehouse") or parent.get("warehouse"),
				row.get("target_warehouse") or parent.get("target_warehouse"),
			)

	for row, warehouses in ((row, (source, target)) for row, source, target in rows()):
		item_code = row.get("item_code")
		if not item_code:
			continue
		if item_code not in stock_cache:
			stock_cache[item_code] = bool(frappe.get_cached_value("Item", item_code, "is_stock_item"))
		if not stock_cache[item_code]:
			continue

		for warehouse in warehouses:
			if warehouse and classification.is_virtual(warehouse):
				yield row, warehouse


def _validate_outbound(doc):
	if _skip(doc) or doc.is_return:
		return

	for row, warehouse in _virtual_rows(doc):
		_refuse(policy.sales_source_problem(warehouse, True, row.idx))


def validate_sales_invoice(doc, method=None):
	_validate_outbound(doc)


def validate_delivery_note(doc, method=None):
	_validate_outbound(doc)


def validate_stock_reservation_entry(doc, method=None):
	if _skip(doc):
		return

	warehouse = doc.get("warehouse")
	if classification.is_virtual(warehouse):
		_refuse(policy.sales_source_problem(warehouse, True))
