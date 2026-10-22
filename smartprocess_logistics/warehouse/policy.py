"""Pure virtual-warehouse rules. No Frappe import, no database.

A virtual warehouse is a real ERPNext warehouse: it has a Bin, a stock value and Stock Ledger
Entries, and it is part of company-owned inventory. What it cannot do is supply a sale, a
delivery or a reservation. The standard `Warehouse Type` is the classification label; the
server-side handlers in this package enforce the rule.
"""

from dataclasses import dataclass

# The type names that mean "virtual" out of the box. A site can override the list with
# `sp_virtual_warehouse_types`; a new virtual warehouse is then just a Warehouse with one of
# these types, not a new configuration entry.
DEFAULT_VIRTUAL_TYPES = ("Virtual", "مجازی", "Transit")


@dataclass(frozen=True)
class Problem:
	code: str
	message: str
	row: int | None = None


def is_virtual_type(warehouse_type, virtual_types=DEFAULT_VIRTUAL_TYPES):
	"""Whether a standard Warehouse Type is classified as virtual."""
	if not warehouse_type:
		return False
	allowed = {str(value) for value in (virtual_types or ())}
	return str(warehouse_type) in allowed


def stock_entry_problem(purpose, source_virtual, target_virtual, row=None):
	"""Only a Material Transfer may move stock into, out of or between virtual warehouses.

	Standard ERPNext already decides company, disabled/group warehouse, permissions, negative
	stock and valuation. This function adds only the classification rule; it has no destination
	whitelist and no purchase-receipt link.
	"""
	if not source_virtual and not target_virtual:
		return None
	if purpose == "Material Transfer":
		return None

	where = f"ردیف {row}: " if row else ""
	return Problem(
		"VIRTUAL_MATERIAL_TRANSFER_ONLY",
		f"{where}جابه‌جایی موجودی انبار مجازی فقط با نوع سند «انتقال مواد» مجاز است.",
		row,
	)


def sales_source_problem(warehouse, is_virtual, row=None):
	"""Virtual stock is never a sales, delivery or reservation source."""
	if not is_virtual:
		return None

	where = f"ردیف {row}: " if row else ""
	return Problem(
		"VIRTUAL_NOT_SELLABLE",
		f"{where}کالای موجود در انبار مجازی «{warehouse}» قابل فروش، ارسال یا رزرو نیست؛ "
		"ابتدا با «انتقال مواد» به انبار واقعی منتقل کنید.",
		row,
	)
