"""Pure virtual-warehouse rules. No Frappe import, no database.

A virtual warehouse is a real ERPNext warehouse: it has a Bin, a stock value and Stock Ledger
Entries, and it is part of company-owned inventory. What it cannot do is supply a sale, a
delivery or a reservation. The standard `Warehouse Type` is the classification label; the
server-side handlers in this package enforce the rule.
"""

from dataclasses import dataclass

PHYSICAL = "physical"
TRANSIT = "transit"
VIRTUAL = "virtual"

# Three kinds share two lists. `Virtual` and its Persian name mean "unusable stock" — lost,
# damaged, warranty, written off; `Transit` means goods on their way. Both are unsellable, which
# is why `DEFAULT_VIRTUAL_TYPES` still holds the union: every existing sales guard asks one
# question ("may this supply a sale?") and must keep answering it the same way.
VIRTUAL_TYPES = ("Virtual", "مجازی")
TRANSIT_TYPES = ("Transit",)

# A site can override the unsellable list with `sp_virtual_warehouse_types`; a new unsellable
# warehouse is then just a Warehouse with one of these types, not a new configuration entry.
#
# Everything else is physical: an empty type (every warehouse created before this app shipped a
# type) and the explicit `Physical` type the app now ships (`fixtures/warehouse_type.json`). The
# rule is a deny-list on purpose — a site that never labels a warehouse keeps its stock
# sellable, and a label this app has never seen cannot silently make a warehouse unsellable.
# `Physical` and `فیزیکی` are deliberately absent: they name the physical case.
DEFAULT_VIRTUAL_TYPES = (*VIRTUAL_TYPES, *TRANSIT_TYPES)


def kind(warehouse_type, virtual_types=DEFAULT_VIRTUAL_TYPES):
	"""Which of the three kinds a standard Warehouse Type means.

	`Transit` is its own kind even though it is also unsellable: an operational report must be
	able to show goods on their way without calling them lost. A type a site added to
	`sp_virtual_warehouse_types` is treated as virtual — it is unsellable and this app knows
	nothing else about it, so it is never counted as usable stock.

	`virtual_types` decides membership and nothing else does. The hardcoded names choose between
	the two unsellable kinds; they cannot make a type unsellable that the configured list leaves
	out, because every caller must answer "may this supply a sale?" the same way.
	"""
	name = str(warehouse_type or "").strip()
	if not name or name not in {str(value) for value in (virtual_types or ())}:
		return PHYSICAL
	return TRANSIT if name in TRANSIT_TYPES else VIRTUAL


@dataclass(frozen=True)
class Problem:
	code: str
	message: str
	row: int | None = None


def is_virtual_type(warehouse_type, virtual_types=DEFAULT_VIRTUAL_TYPES):
	"""Whether a standard Warehouse Type is classified as unsellable."""
	return kind(warehouse_type, virtual_types) != PHYSICAL


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
