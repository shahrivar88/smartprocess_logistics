"""Read-only Sales Order demand, procurement planning and invoice readiness.

The module works from snapshots the caller supplies: Sales Order item rows, Purchase Order item
rows, Sales Invoice item links and Stock UOM stock balances. It has no Frappe import and writes
nothing, so every formula can be tested without a site.

Two boundaries are deliberate:

* Invoicing and delivery are separate. A line can be ready to invoice because goods were
  already delivered-and-unbilled, or because physical stock is available; delivery is not a
  prerequisite for invoicing.
* Native reservations are not subtracted a second time. `Bin.reserved_qty` is the same Sales
  Order demand the allocation already sees, so callers must not pass it as an additional
  demand. `available_for_new_sales` in `core.inventory` exposes that distinction explicitly.

All stock figures are in Stock UOM. Sales Order and Purchase Order rows keep their own UOM and
carry a conversion factor; only the stock-side result is converted.
"""

from dataclasses import dataclass
from decimal import Decimal

from smartprocess_logistics.core.inventory import (
	DEFAULT_ALLOCATION_STRATEGY,
	ZERO,
	AllocationItem,
	allocate,
	as_decimal,
	max_zero,
	positive_factor,
	quantity,
)

OPEN_SALES_ORDER_EXCLUSIONS = frozenset(
	{"cancelled", "canceled", "closed", "stopped", "on hold", "completed"}
)
OPEN_PURCHASE_ORDER_EXCLUSIONS = frozenset(
	{"cancelled", "canceled", "closed", "on hold", "completed", "delivered"}
)

READY = "ready"
INCOMING = "incoming"
WAITING = "waiting"

# The operational status model. It is derived, never stored: native `status`, `docstatus`,
# `per_delivered` and `per_billed` stay the source of truth, and nothing here writes a document.
ORDER_OPEN = "open"
ORDER_PARTIALLY_INVOICED = "partially_invoiced"
ORDER_PARTIALLY_DELIVERED = "partially_delivered"
ORDER_FULLY_DELIVERED = "fully_delivered"
ORDER_CLOSED = "closed"
ORDER_CANCELLED = "cancelled"

OPERATIONAL_STATUSES = (
	ORDER_OPEN,
	ORDER_PARTIALLY_INVOICED,
	ORDER_PARTIALLY_DELIVERED,
	ORDER_FULLY_DELIVERED,
	ORDER_CLOSED,
	ORDER_CANCELLED,
)

# States that still carry work. Delivery is what demand depends on, so a fully delivered order
# carries no demand even when it is still waiting to be billed.
CLOSED_DELIVERY_STATUSES = frozenset({"closed", "stopped", "on hold"})
CANCELLED_STATUSES = frozenset({"cancelled", "canceled"})

READINESS_VALUES = (READY, INCOMING, WAITING)

CREDIT_NOTE_VALUE_ONLY = "value_only_credit_note"
CREDIT_NOTE_POSITIVE_RETURN = "positive_return_quantity"
CREDIT_NOTE_NEGATIVE_WITHOUT_RETURN = "negative_quantity_without_return"

Quantity = Decimal | float | int | str | None


def _normalized_status(status):
	return str(status or "").strip().casefold()


def _docstatus(value):
	try:
		return int(value)
	except (TypeError, ValueError):
		return None


def _truthy(value):
	return str(value).strip().casefold() in {"1", "true", "yes", "y"}


def is_open_sales_order(docstatus, status):
	"""A submitted Sales Order that still contributes demand."""
	return _docstatus(docstatus) == 1 and _normalized_status(status) not in OPEN_SALES_ORDER_EXCLUSIONS


def is_open_purchase_order(docstatus, status):
	"""A submitted Purchase Order whose remaining quantity is still expected."""
	return _docstatus(docstatus) == 1 and _normalized_status(status) not in OPEN_PURCHASE_ORDER_EXCLUSIONS


def _percent(value):
	"""A native percentage field. `None`/blank reads as "not filled", not as zero."""
	if value in (None, ""):
		return None
	try:
		return float(value)
	except (TypeError, ValueError):
		return None


def operational_status(docstatus, status, per_delivered=None, per_billed=None):
	"""The operational state of a Sales Order, from native fields only.

	Precedence, because one order can be in two states at once: Cancelled, Closed, Fully
	Delivered, Partially Delivered, Partially Invoiced, Open. Delivery outranks billing on
	purpose — an order that has shipped half its quantity is "partially delivered" whether or
	not an invoice exists, and an order that shipped everything is "fully delivered" even when
	billing is still pending.
	"""
	code = _docstatus(docstatus)
	normalized = _normalized_status(status)

	if code == 2 or normalized in CANCELLED_STATUSES:
		return ORDER_CANCELLED
	if normalized in CLOSED_DELIVERY_STATUSES:
		return ORDER_CLOSED

	delivered = _percent(per_delivered)
	billed = _percent(per_billed)

	if delivered is not None and delivered >= 100:
		return ORDER_FULLY_DELIVERED
	if delivered is not None and delivered > 0:
		return ORDER_PARTIALLY_DELIVERED
	if billed is not None and billed > 0:
		return ORDER_PARTIALLY_INVOICED

	return ORDER_OPEN


def contributes_to_demand(docstatus, status, per_delivered=None):
	"""Whether an order still carries operational demand.

	Submitted, not closed and not cancelled — and with a delivery balance left. A fully
	delivered order contributes nothing even if it is not billed yet: demand is about shipping.
	"""
	if not is_open_sales_order(docstatus, status):
		return False

	delivered = _percent(per_delivered)
	return delivered is None or delivered < 100


def _company(company):
	value = str(company or "").strip()
	if not value:
		raise ValueError("company is required")
	return value


def _company_lines(lines, company):
	company = _company(company)
	return [line for line in lines if str(line.company or "").strip() == company]


def _open_sales_lines(lines, company):
	return [
		line for line in _company_lines(lines, company) if is_open_sales_order(line.docstatus, line.status)
	]


@dataclass(frozen=True)
class SalesOrderLine:
	"""One Sales Order Item row, as a planning snapshot."""

	name: str
	sales_order: str
	company: str
	item_code: str
	qty: Quantity
	delivered_qty: Quantity = ZERO
	returned_qty: Quantity = ZERO
	invoiced_qty: Quantity = ZERO
	conversion_factor: Quantity = 1
	transaction_date: object = None
	delivery_date: object = None
	status: str = "To Deliver and Bill"
	docstatus: int = 1
	idx: int = 0


@dataclass(frozen=True)
class PurchaseOrderLine:
	"""One Purchase Order Item row, as a supply snapshot."""

	name: str
	company: str
	item_code: str
	qty: Quantity
	received_qty: Quantity = ZERO
	returned_qty: Quantity = ZERO
	conversion_factor: Quantity = 1
	status: str = "To Receive and Bill"
	docstatus: int = 1


@dataclass(frozen=True)
class InvoiceLine:
	"""One submitted Sales Invoice Item row, for the standard SO → SI link."""

	so_detail: str
	qty: Quantity
	docstatus: int = 1
	is_return: bool = False
	amount: Quantity = ZERO
	update_stock: bool = False
	parent: str = ""


@dataclass(frozen=True)
class InvoiceReadiness:
	"""The quantities a ready-to-invoice report needs for one Sales Order Item."""

	key: str
	sales_order: str
	so_detail: str
	company: str
	item_code: str
	ordered_qty: Decimal
	invoiced_qty: Decimal
	remaining_uninvoiced_qty: Decimal
	remaining_uninvoiced_stock_qty: Decimal
	delivered_qty: Decimal
	delivered_unbilled_qty: Decimal
	allocated_physical_qty: Decimal
	allocated_virtual_qty: Decimal
	suggested_invoice_qty: Decimal
	incoming_qty: Decimal
	readiness: str


@dataclass(frozen=True)
class OperationalReadiness:
	"""Operational stock coverage when the caller cannot read Sales Invoices.

	It deliberately carries no invoiced or remaining quantity: those cannot be derived
	exactly without the invoice rows, and reporting them as zero would be a false statement.
	"""

	key: str
	sales_order: str
	so_detail: str
	company: str
	item_code: str
	ordered_qty: Decimal
	delivered_qty: Decimal
	allocated_physical_qty: Decimal
	allocated_virtual_qty: Decimal
	incoming_qty: Decimal
	readiness: str


def open_delivery_demand(line):
	"""Remaining physical delivery demand for one Sales Order line, in Stock UOM.

	`delivered_qty` is ERPNext's delivered quantity after its status updater has seen the
	submitted delivery rows, including returns. The quantity that still has to be delivered is
	therefore `qty - delivered_qty`; subtracting `returned_qty` again would double-count it.
	"""
	if not is_open_sales_order(line.docstatus, line.status):
		return ZERO

	ordered = as_decimal(line.qty, "qty")
	delivered = as_decimal(line.delivered_qty, "delivered_qty")
	factor = positive_factor(line.conversion_factor)

	return quantity(max_zero(ordered - delivered) * factor)


def open_delivery_demand_by_item(lines, company):
	"""Open delivery demand for one company, in Stock UOM, grouped by Item."""
	result = {}

	for line in _open_sales_lines(lines, company):
		item_code = str(line.item_code or "").strip()
		if not item_code:
			continue
		result[item_code] = result.get(item_code, ZERO) + open_delivery_demand(line)

	return {key: quantity(value) for key, value in sorted(result.items())}


def open_delivery_demand_total(lines, company):
	"""Sum of the open delivery demand for one company, in Stock UOM."""
	return quantity(sum(open_delivery_demand_by_item(lines, company).values(), ZERO))


def billable_quantity(line):
	"""The native quantity against which a Sales Order line may be invoiced.

	This mirrors ERPNext's own `get_qty_net_of_returns`:
	`min(qty, max(qty - returned_qty, delivered_qty))`.
	"""
	if not is_open_sales_order(line.docstatus, line.status):
		return ZERO

	ordered = as_decimal(line.qty, "qty")
	returned = as_decimal(line.returned_qty, "returned_qty")
	delivered = as_decimal(line.delivered_qty, "delivered_qty")

	return quantity(max_zero(min(ordered, max(ordered - returned, delivered))))


def remaining_uninvoiced(line):
	"""Outstanding quantity against which an invoice may still be raised, in line UOM.

	Delivered quantity is not used as a cap here. A line that has not been delivered can still
	be invoiced when the business allows billing before delivery; delivery is a separate fact.
	"""
	if not is_open_sales_order(line.docstatus, line.status):
		return ZERO

	billable = billable_quantity(line)
	invoiced = as_decimal(line.invoiced_qty, "invoiced_qty")

	return quantity(max_zero(billable - invoiced))


def remaining_uninvoiced_stock(line):
	"""`remaining_uninvoiced` converted to Stock UOM."""
	return quantity(remaining_uninvoiced(line) * positive_factor(line.conversion_factor))


def remaining_uninvoiced_stock_by_item(lines, company):
	"""Remaining uninvoiced quantity, in Stock UOM, grouped by Item."""
	result = {}

	for line in _open_sales_lines(lines, company):
		item_code = str(line.item_code or "").strip()
		if not item_code:
			continue
		result[item_code] = result.get(item_code, ZERO) + remaining_uninvoiced_stock(line)

	return {key: quantity(value) for key, value in sorted(result.items())}


def delivered_unbilled_quantity(line):
	"""Delivered quantity that has not yet been invoiced, in line UOM."""
	if not is_open_sales_order(line.docstatus, line.status):
		return ZERO

	billable = billable_quantity(line)
	delivered = min(as_decimal(line.delivered_qty, "delivered_qty"), billable)
	invoiced = as_decimal(line.invoiced_qty, "invoiced_qty")

	return quantity(max_zero(delivered - invoiced))


def invoiced_quantities_by_so_detail(rows):
	"""Sum submitted Sales Invoice quantities by `so_detail`.

	Returns are negative quantities and reduce the total. Draft and cancelled invoices are
	ignored; a row without a Sales Order link is not part of this calculation. A zero-quantity
	value-only credit note contributes nothing and therefore cannot create demand by itself.
	"""
	totals = {}

	for row in rows:
		if _docstatus(row.docstatus) != 1:
			continue
		so_detail = str(row.so_detail or "").strip()
		if not so_detail:
			continue
		totals[so_detail] = totals.get(so_detail, ZERO) + as_decimal(row.qty, "qty")

	return {key: quantity(value) for key, value in sorted(totals.items())}


def credit_note_review(line):
	"""A review reason when a credit note's quantity effect is ambiguous.

	A zero-quantity return is a value-only correction: it does not change the quantity demand
	and must not be read as a reason to re-invoice. A positive quantity on a return, or a
	negative quantity without the return flag, is reported for a person to inspect rather than
	guessed at.
	"""
	if _docstatus(line.docstatus) != 1:
		return None

	quantity = as_decimal(line.qty, "qty")
	amount = as_decimal(getattr(line, "amount", ZERO), "amount")
	is_return = _truthy(getattr(line, "is_return", False))

	if is_return:
		if quantity == ZERO and amount != ZERO:
			return CREDIT_NOTE_VALUE_ONLY
		if quantity > ZERO:
			return CREDIT_NOTE_POSITIVE_RETURN
		return None

	if quantity < ZERO:
		return CREDIT_NOTE_NEGATIVE_WITHOUT_RETURN

	return None


def credit_note_reviews_by_so_detail(rows):
	"""Review reasons grouped by Sales Order Item, without changing any quantity."""
	result = {}

	for row in rows:
		reason = credit_note_review(row)
		so_detail = str(getattr(row, "so_detail", "") or "").strip()
		if reason and so_detail:
			result.setdefault(so_detail, set()).add(reason)

	return {key: tuple(sorted(reasons)) for key, reasons in sorted(result.items())}


def outstanding_purchase_supply_by_item(lines, company):
	"""Open Purchase Order quantity, in Stock UOM, grouped by Item.

	`received_qty` is the standard receipt obligation already fulfilled; `returned_qty` is a
	separate return figure and is not subtracted again. Receipts already in `Bin` are not added
	again as virtual stock, so the same goods cannot be counted twice.
	"""
	company = _company(company)
	result = {}

	for line in lines:
		if str(line.company or "").strip() != company:
			continue
		if not is_open_purchase_order(line.docstatus, line.status):
			continue

		item_code = str(line.item_code or "").strip()
		if not item_code:
			continue

		ordered = as_decimal(line.qty, "qty")
		received = as_decimal(line.received_qty, "received_qty")
		factor = positive_factor(line.conversion_factor)
		result[item_code] = result.get(item_code, ZERO) + max_zero(ordered - received) * factor

	return {key: quantity(value) for key, value in sorted(result.items())}


def outstanding_purchase_supply(lines, company):
	"""Total open Purchase Order quantity, in Stock UOM, for one company."""
	return quantity(sum(outstanding_purchase_supply_by_item(lines, company).values(), ZERO))


def procurement_shortage(open_demand, physical_stock, virtual_stock, *, virtual_stock_covers=True):
	"""The quantity still needed after usable stock, in Stock UOM.

	Physical stock is used as supplied, so a negative physical balance increases the shortage
	rather than being silently treated as zero. Virtual stock is coverage only when the policy
	says so; a negative virtual balance never increases usable coverage.
	"""
	demand = max_zero(as_decimal(open_demand, "open_demand"))
	physical = as_decimal(physical_stock, "physical_stock")
	virtual = max_zero(as_decimal(virtual_stock, "virtual_stock")) if virtual_stock_covers else ZERO

	return quantity(max_zero(demand - physical - virtual))


def recommended_purchase(shortage, open_purchase_supply, *, net_open_supply=True):
	"""Suggested purchase quantity, optionally net of open Purchase Order supply."""
	shortage = max_zero(as_decimal(shortage, "shortage"))
	supply = max_zero(as_decimal(open_purchase_supply, "open_purchase_supply"))

	if net_open_supply:
		return quantity(max_zero(shortage - supply))
	return quantity(shortage)


def allocate_physical_for_demand(lines, stock_by_item, company, strategy=DEFAULT_ALLOCATION_STRATEGY):
	"""Allocate physical stock to open delivery demand, once per line."""
	items = [
		AllocationItem(
			key=line.name,
			item_code=line.item_code,
			demand=open_delivery_demand(line),
			transaction_date=line.transaction_date,
			delivery_date=line.delivery_date,
			sales_order=line.sales_order,
			idx=line.idx,
		)
		for line in _open_sales_lines(lines, company)
	]

	return allocate(items, stock_by_item, strategy=strategy)


def _allocation_item(line, demand):
	return AllocationItem(
		key=line.name,
		item_code=line.item_code,
		demand=demand,
		transaction_date=line.transaction_date,
		delivery_date=line.delivery_date,
		sales_order=line.sales_order,
		idx=line.idx,
	)


def ready_for_invoicing(
	lines,
	physical_stock_by_item,
	virtual_stock_by_item,
	company,
	strategy=DEFAULT_ALLOCATION_STRATEGY,
):
	"""Classify open Sales Order lines whose uninvoiced quantity has usable coverage.

	Physical stock is allocated first, then virtual stock to whatever physical stock did not
	cover. The same line is never allocated stock twice, and a delivered-but-unbilled quantity
	is ready without consuming stock.
	"""
	company = _company(company)
	prepared = []

	for line in _open_sales_lines(lines, company):
		remaining_line = remaining_uninvoiced(line)
		if remaining_line <= ZERO:
			continue

		factor = positive_factor(line.conversion_factor)
		delivered_unbilled = delivered_unbilled_quantity(line)
		remaining_stock = remaining_line * factor
		delivered_unbilled_stock = delivered_unbilled * factor
		need_stock = max_zero(remaining_stock - delivered_unbilled_stock)

		prepared.append(
			{
				"line": line,
				"remaining_line": remaining_line,
				"remaining_stock": remaining_stock,
				"delivered_unbilled": delivered_unbilled,
				"need_stock": need_stock,
				"factor": factor,
			}
		)

	physical = allocate(
		[_allocation_item(row["line"], row["need_stock"]) for row in prepared],
		physical_stock_by_item,
		strategy=strategy,
	)

	virtual_needs = [
		_allocation_item(
			row["line"],
			max_zero(row["need_stock"] - physical.allocations.get(row["line"].name, ZERO)),
		)
		for row in prepared
	]
	virtual = allocate(virtual_needs, virtual_stock_by_item, strategy=strategy)

	result = []
	for row in prepared:
		line = row["line"]
		allocated_physical = physical.allocations.get(line.name, ZERO)
		allocated_virtual = virtual.allocations.get(line.name, ZERO)
		physical_covered_line = row["delivered_unbilled"] + (allocated_physical / row["factor"])
		suggested = quantity(max_zero(min(row["remaining_line"], physical_covered_line)))
		virtual_covered_line = allocated_virtual / row["factor"]
		incoming = quantity(max_zero(min(max(row["remaining_line"] - suggested, ZERO), virtual_covered_line)))

		if row["delivered_unbilled"] > ZERO or allocated_physical > ZERO:
			readiness = READY
		elif allocated_virtual > ZERO:
			readiness = INCOMING
		else:
			readiness = WAITING

		result.append(
			InvoiceReadiness(
				key=line.name,
				sales_order=line.sales_order,
				so_detail=line.name,
				company=company,
				item_code=line.item_code,
				ordered_qty=quantity(as_decimal(line.qty, "qty")),
				invoiced_qty=quantity(as_decimal(line.invoiced_qty, "invoiced_qty")),
				remaining_uninvoiced_qty=row["remaining_line"],
				remaining_uninvoiced_stock_qty=quantity(row["remaining_stock"]),
				delivered_qty=quantity(as_decimal(line.delivered_qty, "delivered_qty")),
				delivered_unbilled_qty=row["delivered_unbilled"],
				allocated_physical_qty=quantity(allocated_physical),
				allocated_virtual_qty=quantity(allocated_virtual),
				suggested_invoice_qty=suggested,
				incoming_qty=incoming,
				readiness=readiness,
			)
		)

	return tuple(result)


def operational_readiness(
	lines,
	physical_stock_by_item,
	virtual_stock_by_item,
	company,
	strategy=DEFAULT_ALLOCATION_STRATEGY,
):
	"""Stock coverage for Sales Order lines when invoiced quantities are not readable.

	The allocation is against the ordered quantity, not an invented "remaining" quantity. The
	result is operational stock readiness only; the caller must show that invoicing detail is
	unavailable instead of presenting these numbers as an invoice recommendation.
	"""
	company = _company(company)
	prepared = []

	for line in _open_sales_lines(lines, company):
		ordered = as_decimal(line.qty, "qty")
		if ordered <= ZERO:
			continue
		factor = positive_factor(line.conversion_factor)
		prepared.append(
			{
				"line": line,
				"ordered": ordered,
				"need_stock": ordered * factor,
				"factor": factor,
			}
		)

	physical = allocate(
		[_allocation_item(row["line"], row["need_stock"]) for row in prepared],
		physical_stock_by_item,
		strategy=strategy,
	)
	virtual_needs = [
		_allocation_item(
			row["line"],
			max_zero(row["need_stock"] - physical.allocations.get(row["line"].name, ZERO)),
		)
		for row in prepared
	]
	virtual = allocate(virtual_needs, virtual_stock_by_item, strategy=strategy)

	result = []
	for row in prepared:
		line = row["line"]
		allocated_physical = physical.allocations.get(line.name, ZERO)
		allocated_virtual = virtual.allocations.get(line.name, ZERO)
		physical_covered = allocated_physical / row["factor"]
		incoming = max_zero(
			min(max_zero(row["ordered"] - physical_covered), allocated_virtual / row["factor"])
		)

		if allocated_physical > ZERO:
			readiness = READY
		elif allocated_virtual > ZERO:
			readiness = INCOMING
		else:
			readiness = WAITING

		result.append(
			OperationalReadiness(
				key=line.name,
				sales_order=line.sales_order,
				so_detail=line.name,
				company=company,
				item_code=line.item_code,
				ordered_qty=quantity(row["ordered"]),
				delivered_qty=quantity(as_decimal(line.delivered_qty, "delivered_qty")),
				allocated_physical_qty=quantity(allocated_physical),
				allocated_virtual_qty=quantity(allocated_virtual),
				incoming_qty=quantity(incoming),
				readiness=readiness,
			)
		)

	return tuple(result)
