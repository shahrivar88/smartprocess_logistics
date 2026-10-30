"""The visible stock text behind the Sales Invoice and Sales Order Item links.

The caller has already restricted warehouses to the ones the user may read and has aggregated
their `Bin` quantities. This module only formats the two parts a Frappe Link shows after the
Item Code: the item name and the authorized stock.
"""

from smartprocess_logistics.core.inventory import quantity
from smartprocess_logistics.core.stock_search import format_quantity

PHYSICAL_LABEL = "موجودی فیزیکی"
VIRTUAL_LABEL = "موجودی مجازی"
WAREHOUSE_STOCK_LABEL = "موجودی"
STOCK_SEPARATOR = " | "


def warehouse_stock_text(actual_qty):
	"""The secondary line under a warehouse in a sales selector."""
	return f"{WAREHOUSE_STOCK_LABEL}: {format_quantity(quantity(actual_qty))}"


def stock_text(physical, uom=""):
	"""`موجودی فیزیکی: 10 Nos` — the Sales Invoice Item line, physical stock only."""
	return f"{PHYSICAL_LABEL}: {format_quantity(quantity(physical))} {(uom or '').strip()}".strip()


def combined_stock_text(physical, virtual=None):
	"""`موجودی فیزیکی: 24 | موجودی مجازی: 15` — the one line Quotation and Sales Order show."""
	physical = format_quantity(quantity(physical))
	virtual = format_quantity(quantity(virtual or 0))

	return STOCK_SEPARATOR.join([f"{PHYSICAL_LABEL}: {physical}", f"{VIRTUAL_LABEL}: {virtual}"])


def item_suggestion(item_code, item_name, physical, virtual=None, uom="", include_virtual=False):
	"""A Link row: Item Code as the value, Item Name and stock as the description."""
	parts = [item_name or ""]
	if include_virtual:
		parts.append(combined_stock_text(physical, virtual))
	else:
		parts.append(stock_text(physical, uom))

	return (item_code, *parts)
