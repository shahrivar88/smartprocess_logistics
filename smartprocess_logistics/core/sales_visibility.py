"""The visible stock text behind the Sales Invoice and Sales Order Item links.

The caller has already restricted warehouses to the ones the user may read and has aggregated
their `Bin` quantities. This module only formats the two parts a Frappe Link shows after the
Item Code: the item name and the authorized stock.
"""

from smartprocess_logistics.core.inventory import quantity
from smartprocess_logistics.core.stock_search import format_quantity

PHYSICAL_LABEL = "موجودی فیزیکی"
VIRTUAL_LABEL = "موجودی مجازی"


def stock_text(physical, virtual=None, uom="", include_virtual=False):
	"""`موجودی فیزیکی: 10 Nos` and, when asked, a separate virtual part."""
	physical = quantity(physical)
	parts = [f"{PHYSICAL_LABEL}: {format_quantity(physical)} {uom}".strip()]

	if include_virtual:
		virtual = quantity(virtual or 0)
		parts.append(f"{VIRTUAL_LABEL}: {format_quantity(virtual)} {uom}".strip())

	return parts


def item_suggestion(item_code, item_name, physical, virtual=None, uom="", include_virtual=False):
	"""A Link row: Item Code as the value, Item Name and stock as the description."""
	parts = [item_name or ""]
	parts.extend(stock_text(physical, virtual, uom, include_virtual))

	return (item_code, *parts)
