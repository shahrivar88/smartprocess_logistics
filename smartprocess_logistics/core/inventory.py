"""Read-only inventory totals and deterministic stock allocation.

Nothing here imports Frappe. The facts come from the caller as snapshots of `Warehouse` and
`Bin`; this module decides how they add up and how a limited physical quantity is allocated to
competing Sales Order lines. It never writes, reserves or changes a document.

Stock quantities are always in the item's Stock UOM. A Sales Order line carries the quantity
in its own UOM and a conversion factor; callers convert at the boundary with
`stock_quantity` rather than mixing units here.

The virtual/physical distinction is the one this application already uses: a warehouse is
virtual when its standard `Warehouse Type` is in `DEFAULT_VIRTUAL_TYPES`. A group warehouse,
a disabled warehouse, another company's warehouse and a warehouse the caller may not read are
not stock this caller may count.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from smartprocess_logistics.warehouse.policy import DEFAULT_VIRTUAL_TYPES, is_virtual_type

# Frappe stores and displays stock quantities with this precision. Calculations keep their
# Decimal values until a result is returned; the public result is rounded once, here.
QTY_PRECISION = Decimal("0.000001")
ZERO = Decimal("0")

OLDEST_ORDER = "oldest_order"
EARLIEST_DELIVERY = "earliest_delivery"
ORDER_THEN_DELIVERY = "order_then_delivery"

ALLOCATION_STRATEGIES = (OLDEST_ORDER, EARLIEST_DELIVERY, ORDER_THEN_DELIVERY)
DEFAULT_ALLOCATION_STRATEGY = ORDER_THEN_DELIVERY

Quantity = Decimal | float | int | str | None


def as_decimal(value, field="value"):
	"""A finite Decimal, or a clear refusal for a value that is not a number.

	`None` and the empty string mean "not supplied" and read as zero, which is how a missing
	optional child field arrives. Anything else that is not finite raises rather than becoming
	a silent zero: a report that turns a bad quantity into zero is a report that hides a data
	problem instead of showing it.
	"""
	if value is None or value == "":
		return ZERO
	if isinstance(value, bool):
		raise ValueError(f"{field} must be a number, not a boolean")

	try:
		result = value if isinstance(value, Decimal) else Decimal(str(value))
	except (InvalidOperation, ValueError, TypeError) as error:
		raise ValueError(f"{field} must be a number") from error

	if not result.is_finite():
		raise ValueError(f"{field} must be a finite number")

	return result


def quantity(value, field="quantity"):
	"""A stock quantity rounded to Frappe's six-place precision."""
	return as_decimal(value, field).quantize(QTY_PRECISION, rounding=ROUND_HALF_UP)


def positive_factor(value, field="conversion_factor"):
	"""A UOM conversion factor that can divide and multiply safely."""
	factor = as_decimal(value, field)
	if factor <= 0:
		raise ValueError(f"{field} must be greater than zero")
	return factor


def max_zero(value):
	return value if value > ZERO else ZERO


def stock_quantity(quantity_in_line_uom, conversion_factor):
	"""A Sales Order or Purchase Order quantity converted to Stock UOM."""
	return quantity(as_decimal(quantity_in_line_uom, "quantity") * positive_factor(conversion_factor))


def _date_key(value):
	"""Missing dates sort after real ones, so a row without a date never jumps the queue."""
	return "9999-12-31" if value in (None, "") else str(value)


def _index_key(value):
	"""A malformed row index falls back to zero rather than aborting an allocation."""
	try:
		return int(value or 0)
	except (TypeError, ValueError):
		return 0


@dataclass(frozen=True)
class WarehouseStock:
	"""One `Bin` row, as much of it as stock planning needs."""

	company: str
	item_code: str
	warehouse: str
	warehouse_type: str | None
	actual_qty: Quantity
	is_group: bool = False
	disabled: bool = False
	reserved_qty: Quantity = ZERO


@dataclass(frozen=True)
class ItemStock:
	"""The three quantities a warehouse snapshot produces for one item."""

	physical: Decimal = ZERO
	virtual: Decimal = ZERO
	reserved: Decimal = ZERO

	@property
	def total(self):
		return quantity(self.physical + self.virtual)


@dataclass(frozen=True)
class StockTotals:
	"""Company-level totals of the same three quantities."""

	physical: Decimal = ZERO
	virtual: Decimal = ZERO
	reserved: Decimal = ZERO

	@property
	def total(self):
		return quantity(self.physical + self.virtual)


@dataclass(frozen=True)
class AllocationItem:
	"""One demand line waiting for a physical or virtual quantity."""

	key: str
	item_code: str
	demand: Quantity
	transaction_date: object = None
	delivery_date: object = None
	sales_order: str = ""
	idx: int = 0


@dataclass(frozen=True)
class AllocationResult:
	allocations: dict[str, Decimal]
	remaining: dict[str, Decimal]
	strategy: str


def stock_by_item(
	rows,
	company,
	*,
	permitted_warehouses=None,
	include_disabled=False,
	virtual_types=DEFAULT_VIRTUAL_TYPES,
):
	"""Physical and virtual stock for one company, by item.

	`rows` are `WarehouseStock` snapshots. `permitted_warehouses` is the set the caller may
	read; `None` means the caller supplied an already-permission-filtered set. An empty set
	means no warehouse is readable and therefore no stock is visible.
	"""
	company = str(company or "").strip()
	if not company:
		raise ValueError("company is required")

	permitted = None if permitted_warehouses is None else frozenset(permitted_warehouses)
	if permitted is not None and not permitted:
		return {}

	result = {}

	for row in rows:
		if str(row.company or "").strip() != company:
			continue
		if row.is_group:
			continue
		if row.disabled and not include_disabled:
			continue
		if permitted is not None and row.warehouse not in permitted:
			continue

		item_code = str(row.item_code or "").strip()
		if not item_code:
			continue

		actual = as_decimal(row.actual_qty, "actual_qty")
		reserved = as_decimal(row.reserved_qty, "reserved_qty")
		current = result.get(item_code, ItemStock())
		virtual = is_virtual_type(row.warehouse_type, virtual_types)

		result[item_code] = ItemStock(
			physical=current.physical + (ZERO if virtual else actual),
			virtual=current.virtual + (actual if virtual else ZERO),
			reserved=current.reserved + reserved,
		)

	return dict(sorted(result.items()))


def stock_totals(
	rows,
	company,
	*,
	permitted_warehouses=None,
	include_disabled=False,
	virtual_types=DEFAULT_VIRTUAL_TYPES,
):
	"""The company totals of the same snapshot."""
	by_item = stock_by_item(
		rows,
		company,
		permitted_warehouses=permitted_warehouses,
		include_disabled=include_disabled,
		virtual_types=virtual_types,
	)

	return StockTotals(
		physical=quantity(sum((stock.physical for stock in by_item.values()), ZERO)),
		virtual=quantity(sum((stock.virtual for stock in by_item.values()), ZERO)),
		reserved=quantity(sum((stock.reserved for stock in by_item.values()), ZERO)),
	)


def physically_deliverable_stock(stock):
	"""Physical on-hand stock. Open Sales Orders are not subtracted from this headline."""
	return quantity(stock.physical)


def available_for_new_sales(
	stock,
	allocated_physical,
	*,
	native_reserved=ZERO,
	reserved_in_open_demand=True,
):
	"""Physical stock left after the deterministic allocation.

	Native `Bin.reserved_qty` describes the same Sales Order demand when a site uses native
	reservations. It is therefore not subtracted again by default; doing so would count the
	same units twice. A caller that knows its native reservations are *not* represented in the
	allocation can pass `reserved_in_open_demand=False`.
	"""
	allocated = max_zero(as_decimal(allocated_physical, "allocated_physical"))
	available = max_zero(as_decimal(stock.physical, "physical") - allocated)

	if not reserved_in_open_demand:
		available = max_zero(available - max_zero(as_decimal(native_reserved, "native_reserved")))

	return quantity(available)


def _allocation_key(item, strategy):
	transaction = _date_key(item.transaction_date)
	delivery = _date_key(item.delivery_date)
	sales_order = str(item.sales_order or "")
	idx = _index_key(item.idx)
	key = str(item.key)

	if strategy == OLDEST_ORDER:
		return transaction, sales_order, idx, key
	if strategy == EARLIEST_DELIVERY:
		return delivery, transaction, sales_order, idx, key
	if strategy == ORDER_THEN_DELIVERY:
		return transaction, delivery, sales_order, idx, key

	raise ValueError(f"unknown allocation strategy: {strategy}")


def allocate(items, stock_by_item, strategy=DEFAULT_ALLOCATION_STRATEGY):
	"""Allocate a quantity of each item once, in a deterministic order.

	`stock_by_item` maps `item_code` to a Stock UOM quantity. Each line receives at most its
	demand; no item's stock is used twice. Negative stock is not available for allocation, but
	it remains visible to the shortage calculation in `core.procurement`.
	"""
	strategy = strategy or DEFAULT_ALLOCATION_STRATEGY
	if strategy not in ALLOCATION_STRATEGIES:
		raise ValueError(f"unknown allocation strategy: {strategy}")

	available = {}
	for item_code, value in stock_by_item.items():
		code = str(item_code or "").strip()
		if not code:
			continue
		available[code] = max_zero(as_decimal(value, "stock_by_item"))

	prepared = []
	seen = set()

	for item in items:
		key = str(item.key or "").strip()
		code = str(item.item_code or "").strip()
		if not key or not code:
			raise ValueError("allocation items need a key and an item_code")
		if key in seen:
			raise ValueError(f"duplicate allocation key: {key}")
		seen.add(key)
		prepared.append(item)

	prepared.sort(key=lambda item: _allocation_key(item, strategy))

	allocations = {}
	for item in prepared:
		key = str(item.key)
		code = str(item.item_code)
		demand = max_zero(as_decimal(item.demand, "demand"))
		stock = available.get(code, ZERO)
		take = demand if demand < stock else stock
		allocations[key] = quantity(take)
		available[code] = stock - take

	remaining = {code: quantity(max_zero(value)) for code, value in sorted(available.items())}

	return AllocationResult(allocations=allocations, remaining=remaining, strategy=strategy)
