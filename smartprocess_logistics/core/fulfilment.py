"""Operational fulfilment: sellable stock, goods on the way, and the demand still to serve.

The report built on this module answers one question per Item: how much can be sold now, how
much is on its way, how much is still being asked for, and how much of that is short.

Nothing here imports Frappe. The caller supplies snapshots; this module decides how they add
up. Three rules keep the arithmetic honest:

* Virtual stock is not stock. It is lost, damaged, warranty or written off, so it is never
  summarised, subtracted or displayed here.
* A converted quotation is not demand. Only the remainder after `ordered_qty` counts, so the
  quantity that became a Sales Order is counted once — in the Sales Order.
* Open purchase orders are supply, not coverage. They appear as their own column and net the
  purchase suggestion, but they never reduce the shortage, which stays against physical and
  transit stock only, as the product rule requires.
"""

from dataclasses import dataclass
from decimal import Decimal

from smartprocess_logistics.core.inventory import ZERO, as_decimal, max_zero, quantity
from smartprocess_logistics.core.procurement import recommended_purchase
from smartprocess_logistics.warehouse import policy

# A quotation in one of these states is not demand any more. Everything else with
# `docstatus = 1` is: `Open`, `Replied` and `Partially Ordered` all still carry an un-ordered
# remainder, and the remainder is what this module counts.
CLOSED_QUOTATION_STATUSES = frozenset({"Ordered", "Lost", "Cancelled", "Expired"})


@dataclass(frozen=True)
class OperationalStock:
	"""Physical and transit stock for one Item, in Stock UOM."""

	physical: Decimal = ZERO
	transit: Decimal = ZERO


@dataclass(frozen=True)
class QuotationLine:
	"""One Quotation Item row, as much of it as fulfilment needs."""

	name: str
	quotation: str
	company: str
	item_code: str
	qty: Decimal
	stock_qty: Decimal
	ordered_qty: Decimal
	conversion_factor: Decimal
	status: str
	docstatus: int
	valid_till: object = None


@dataclass(frozen=True)
class FulfilmentRow:
	"""One report line."""

	company: str
	item_code: str
	physical: Decimal
	transit: Decimal
	quotation_demand: Decimal
	sales_order_demand: Decimal
	total_demand: Decimal
	shortage: Decimal
	related_quotations: tuple
	related_sales_orders: tuple
	open_purchase_supply: Decimal = ZERO
	suggested_purchase: Decimal = ZERO


def _text(value):
	return str(value or "").strip()


def is_open_quotation(docstatus, status, valid_till=None, today=None):
	"""Whether a quotation still carries demand.

	Mirrors ERPNext v16: only a submitted quotation is demand, a closed status is not, and an
	expired `valid_till` is not — the status field catches up in the daily job, which by design
	leaves a partially ordered quotation alone, so the date is checked here as well.
	"""
	try:
		submitted = int(docstatus) == 1
	except (TypeError, ValueError):
		return False

	if not submitted:
		return False
	if _text(status) in CLOSED_QUOTATION_STATUSES:
		return False
	if valid_till and today and str(valid_till) < str(today):
		return False
	return True


def quotation_remaining(line, today=None):
	"""The un-ordered quantity of one Quotation Item, in Stock UOM."""
	if not is_open_quotation(line.docstatus, line.status, line.valid_till, today):
		return ZERO

	stock_qty = line.stock_qty
	if stock_qty in (None, ""):
		factor = as_decimal(line.conversion_factor or 1, "conversion_factor")
		stock_qty = as_decimal(line.qty, "qty") * factor

	return quantity(max_zero(as_decimal(stock_qty, "stock_qty") - as_decimal(line.ordered_qty, "ordered_qty")))


def quotation_demand_by_item(lines, company, today=None):
	"""Un-ordered quotation quantity per Item, in Stock UOM."""
	company = _text(company)
	result = {}

	for line in lines:
		if _text(line.company) != company:
			continue
		code = _text(line.item_code)
		if not code:
			continue
		result[code] = result.get(code, ZERO) + quotation_remaining(line, today)

	return {code: quantity(value) for code, value in sorted(result.items())}


def stock_by_item(bins, virtual_types=policy.DEFAULT_VIRTUAL_TYPES):
	"""Physical and transit stock per Item from `Bin` rows.

	A virtual warehouse contributes nothing at all — not a total, not a shortage, not a column.
	"""
	result = {}

	for row in bins:
		item_kind = policy.kind(row.get("warehouse_type"), virtual_types)
		if item_kind == policy.VIRTUAL:
			continue

		code = _text(row.get("item_code"))
		if not code:
			continue

		current = result.get(code, OperationalStock())
		actual = as_decimal(row.get("actual_qty"), "actual_qty")
		if item_kind == policy.TRANSIT:
			result[code] = OperationalStock(current.physical, current.transit + actual)
		else:
			result[code] = OperationalStock(current.physical + actual, current.transit)

	return {
		code: OperationalStock(quantity(value.physical), quantity(value.transit))
		for code, value in sorted(result.items())
	}


def total_demand(quotation_demand, sales_order_demand):
	"""Open quotation remainder plus undelivered Sales Order quantity.

	The two never overlap: an item ordered from a quotation carries its ordered quantity inside
	`ordered_qty`, which `quotation_remaining` subtracts, and the Sales Order then counts its own
	undelivered quantity.
	"""
	return quantity(
		max_zero(as_decimal(quotation_demand, "quotation_demand"))
		+ max_zero(as_decimal(sales_order_demand, "sales_order_demand"))
	)


def order_shortage(total, physical, transit):
	"""`max(0, demand - physical - transit)`; virtual stock is never part of the answer."""
	return quantity(
		max_zero(
			max_zero(as_decimal(total, "total"))
			- as_decimal(physical, "physical")
			- as_decimal(transit, "transit")
		)
	)


def is_empty_row(row):
	"""Whether every operational column of this row is zero.

	Row 2 of the report's «عدم نمایش اقلام صفر»: an Item is hidden only when it has nothing to
	show anywhere — no physical stock, no transit stock, no quotation demand, no Sales Order
	demand, no open purchase supply and no purchase suggestion, and therefore no total and no
	shortage. A negative balance is not zero and keeps the row, because a negative quantity is a
	fact an operator has to see.
	"""
	return all(
		as_decimal(getattr(row, field), field) == ZERO
		for field in (
			"physical",
			"transit",
			"quotation_demand",
			"sales_order_demand",
			"total_demand",
			"shortage",
			"open_purchase_supply",
			"suggested_purchase",
		)
	)


def fulfilment_rows(
	company,
	stock,
	quotation_lines,
	sales_order_demand_by_item,
	*,
	sales_orders_by_item=None,
	purchase_supply_by_item=None,
	today=None,
	only_shortage=False,
	hide_zero=False,
	net_open_supply=True,
):
	"""One row per Item, sorted by shortage descending, then by Item Code.

	`stock` maps an Item Code to `OperationalStock`; `sales_order_demand_by_item` is the
	undelivered quantity per Item already computed by `core.procurement`, and
	`sales_orders_by_item` names the Sales Orders it came from, for drill-down.
	`purchase_supply_by_item` is open Purchase Order quantity in Stock UOM. It is reported
	beside the shortage and nets the suggestion; it never enters the shortage itself.
	`net_open_supply` is the site policy for that netting, so this report and the other two
	read `sp_recommended_purchase_net_of_open_po` the same way.
	"""
	sales_orders_by_item = sales_orders_by_item or {}
	purchase_supply_by_item = purchase_supply_by_item or {}
	company = _text(company)
	quotation_demand = quotation_demand_by_item(quotation_lines, company, today)
	related = {}
	for line in quotation_lines:
		if _text(line.company) == company and _text(line.status) not in CLOSED_QUOTATION_STATUSES:
			if quotation_remaining(line, today) > ZERO:
				related.setdefault(_text(line.item_code), set()).add(line.quotation)

	codes = sorted(
		set(stock)
		| set(quotation_demand)
		| set(sales_order_demand_by_item)
		| set(related)
		| set(purchase_supply_by_item)
	)
	rows = []

	for code in codes:
		if not code:
			continue
		item_stock = stock.get(code, OperationalStock())
		quote_demand = quotation_demand.get(code, ZERO)
		so_demand = quantity(sales_order_demand_by_item.get(code, ZERO))
		demand = total_demand(quote_demand, so_demand)
		shortage = order_shortage(demand, item_stock.physical, item_stock.transit)
		supply = quantity(purchase_supply_by_item.get(code, ZERO))

		if only_shortage and shortage <= ZERO:
			continue

		row = FulfilmentRow(
			company=company,
			item_code=code,
			physical=item_stock.physical,
			transit=item_stock.transit,
			quotation_demand=quote_demand,
			sales_order_demand=so_demand,
			total_demand=demand,
			shortage=shortage,
			related_quotations=tuple(sorted(related.get(code, ()))),
			related_sales_orders=tuple(sorted(sales_orders_by_item.get(code, ()))),
			open_purchase_supply=supply,
			suggested_purchase=recommended_purchase(
				shortage, supply, net_open_supply=net_open_supply
			),
		)
		if hide_zero and is_empty_row(row):
			continue

		rows.append(row)

	rows.sort(key=lambda row: (-row.shortage, row.item_code))
	return rows
