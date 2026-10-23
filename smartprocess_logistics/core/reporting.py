"""Row shaping for the management reports.

The calculations stay in `core.inventory`, `core.procurement` and `core.measurements`; this
module only joins their results into report rows. It has no Frappe import, so the report
modules can be thin and the row arithmetic can be tested without a site.
"""

from smartprocess_logistics.core import inventory, measurements, procurement
from smartprocess_logistics.core.inventory import ItemStock, max_zero, quantity
from smartprocess_logistics.warehouse.policy import DEFAULT_VIRTUAL_TYPES, is_virtual_type


def _value(row, key, default=None):
	if isinstance(row, dict):
		return row.get(key, default)
	return getattr(row, key, default)


def warehouse_inventory_rows(rows, item_details, virtual_types=None):
	"""One row per Bin snapshot, with Item metadata and physical/virtual classification."""
	virtual_types = virtual_types or DEFAULT_VIRTUAL_TYPES
	result = []

	for row in rows:
		item_code = str(_value(row, "item_code", "") or "")
		if not item_code:
			continue

		item = item_details.get(item_code) or {}
		warehouse_type = _value(row, "warehouse_type")
		result.append(
			{
				"company": _value(row, "company", ""),
				"item_code": item_code,
				"item_name": _value(item, "item_name", "") or "",
				"item_group": _value(item, "item_group", "") or "",
				"warehouse": _value(row, "warehouse", ""),
				"warehouse_type": warehouse_type or "",
				"classification": "virtual" if is_virtual_type(warehouse_type, virtual_types) else "physical",
				"actual_qty": quantity(_value(row, "actual_qty", 0)),
				"stock_uom": _value(row, "stock_uom", "") or _value(item, "stock_uom", "") or "",
				"stock_value": quantity(_value(row, "stock_value", 0)),
				"valuation_rate": quantity(_value(row, "valuation_rate", 0)),
			}
		)

	return result


def _allocated_physical_by_item(lines, stock_by_item, company, strategy):
	physical = {code: stock.physical for code, stock in stock_by_item.items()}
	allocation = procurement.allocate_physical_for_demand(lines, physical, company, strategy=strategy)
	allocated = {}

	for line in lines:
		item_code = str(line.item_code or "").strip()
		if not item_code:
			continue
		allocated[item_code] = allocated.get(item_code, inventory.ZERO) + allocation.allocations.get(
			line.name, inventory.ZERO
		)

	return {code: quantity(value) for code, value in allocated.items()}


def _metric_row(
	company, item_code, stock, demand, allocated, open_supply, *, virtual_covers, net_open_supply
):
	physical = stock.physical
	virtual = stock.virtual
	available = inventory.available_for_new_sales(stock, allocated)
	immediate = quantity(max_zero(demand - physical))
	shortage = procurement.procurement_shortage(
		demand,
		physical,
		virtual,
		virtual_stock_covers=virtual_covers,
	)
	recommended = procurement.recommended_purchase(
		shortage,
		open_supply,
		net_open_supply=net_open_supply,
	)

	return {
		"company": company,
		"item_code": item_code,
		"physical_stock": quantity(physical),
		"virtual_stock": quantity(virtual),
		"total_stock": quantity(physical + virtual),
		"open_demand": quantity(demand),
		"physically_deliverable": quantity(physical),
		"available_for_new_sales": available,
		"immediate_shortage": immediate,
		"procurement_shortage": shortage,
		"open_purchase_supply": quantity(open_supply),
		"recommended_purchase": recommended,
	}


def inventory_dashboard_rows(
	company,
	stock_by_item,
	lines,
	open_purchase_supply_by_item,
	*,
	strategy=inventory.DEFAULT_ALLOCATION_STRATEGY,
	virtual_stock_covers=True,
	net_open_supply=True,
):
	"""One row per Item for the inventory/procurement dashboard."""
	demand = procurement.open_delivery_demand_by_item(lines, company)
	allocated = _allocated_physical_by_item(lines, stock_by_item, company, strategy)
	codes = sorted(set(stock_by_item) | set(demand) | set(open_purchase_supply_by_item))
	result = []

	for item_code in codes:
		stock = stock_by_item.get(item_code, ItemStock())
		result.append(
			_metric_row(
				company,
				item_code,
				stock,
				demand.get(item_code, inventory.ZERO),
				allocated.get(item_code, inventory.ZERO),
				open_purchase_supply_by_item.get(item_code, inventory.ZERO),
				virtual_covers=virtual_stock_covers,
				net_open_supply=net_open_supply,
			)
		)

	return result


def procurement_shortage_rows(
	company,
	stock_by_item,
	lines,
	open_purchase_supply_by_item,
	*,
	related_orders=None,
	strategy=inventory.DEFAULT_ALLOCATION_STRATEGY,
	virtual_stock_covers=True,
	net_open_supply=True,
):
	"""One row per Item with the demand, supply and recommended purchase transparently shown."""
	related_orders = related_orders or {}
	rows = inventory_dashboard_rows(
		company,
		stock_by_item,
		lines,
		open_purchase_supply_by_item,
		strategy=strategy,
		virtual_stock_covers=virtual_stock_covers,
		net_open_supply=net_open_supply,
	)

	for row in rows:
		row["related_orders"] = ", ".join(sorted(related_orders.get(row["item_code"], ())))
		row["suggested_purchase"] = row.pop("recommended_purchase")

	return rows


def ready_invoicing_rows(readiness, metadata=None, reviews=None):
	"""One row per ready/incoming/waiting Sales Order Item.

	Physical readiness and incoming (virtual) quantity are separate fields. They are never
	added into one recommendation.
	"""
	metadata = metadata or {}
	reviews = reviews or {}
	result = []

	for row in readiness:
		info = metadata.get(row.key) or {}
		review = ", ".join(reviews.get(row.so_detail, ()))
		result.append(
			{
				"sales_order": row.sales_order,
				"customer": info.get("customer", ""),
				"order_date": info.get("order_date"),
				"age_days": info.get("age_days"),
				"item_code": row.item_code,
				"ordered_qty": row.ordered_qty,
				"invoiced_qty": row.invoiced_qty,
				"remaining_uninvoiced_qty": row.remaining_uninvoiced_qty,
				"delivered_qty": row.delivered_qty,
				"allocated_physical_qty": row.allocated_physical_qty,
				"allocated_virtual_qty": row.allocated_virtual_qty,
				"suggested_invoice_qty": row.suggested_invoice_qty,
				"incoming_qty": row.incoming_qty,
				"readiness": row.readiness,
				"review": review,
				"invoiced_qty_available": True,
				"billing_note": "",
			}
		)

	return result


def operational_ready_invoicing_rows(readiness, metadata=None, billing_note=""):
	"""Operational rows for a caller who cannot read Sales Invoices.

	Invoiced and remaining quantities are deliberately `None`; the note says why. Ordered,
	delivered, physical and incoming quantities remain useful operational facts.
	"""
	metadata = metadata or {}
	result = []

	for row in readiness:
		info = metadata.get(row.key) or {}
		result.append(
			{
				"sales_order": row.sales_order,
				"customer": info.get("customer", ""),
				"order_date": info.get("order_date"),
				"age_days": info.get("age_days"),
				"item_code": row.item_code,
				"ordered_qty": row.ordered_qty,
				"invoiced_qty": None,
				"remaining_uninvoiced_qty": None,
				"delivered_qty": row.delivered_qty,
				"allocated_physical_qty": row.allocated_physical_qty,
				"allocated_virtual_qty": row.allocated_virtual_qty,
				"suggested_invoice_qty": None,
				"incoming_qty": row.incoming_qty,
				"readiness": row.readiness,
				"review": "",
				"invoiced_qty_available": False,
				"billing_note": billing_note,
			}
		)

	return result


def weight_volume_row(
	quantity_stock_uom,
	*,
	measurement=None,
	native_net_weight=None,
	native_weight_uom=None,
	gross_available=False,
	volume_available=False,
	measurement_source=None,
):
	"""One document or warehouse line's net/gross/volume values.

	`native_net_weight` is the standard transaction-row snapshot. Gross weight and volume are
	only returned when the caller says the corresponding historical values exist; otherwise
	they are `None` and the source column says why.
	"""
	quantity_stock_uom = quantity(quantity_stock_uom)
	net = quantity(native_net_weight) if native_net_weight is not None else None
	gross = None
	volume = None
	volume_m3 = None
	weight_uom = native_weight_uom
	net_source = "native_snapshot" if native_net_weight is not None else "unavailable"
	source = measurement_source or "unavailable"

	if measurement is not None:
		totals = measurements.totals(quantity_stock_uom, measurement)
		if net is None:
			net = totals.net_weight
			net_source = measurement.source
		weight_uom = weight_uom or totals.weight_uom
		if gross_available and measurement.gross_weight not in (None, ""):
			gross = totals.gross_weight
		if volume_available and all(
			value not in (None, "")
			for value in (measurement.length_cm, measurement.width_cm, measurement.height_cm)
		):
			volume = totals.volume_cm3
			volume_m3 = totals.volume_m3
		if source == "unavailable":
			source = measurement.source

	return {
		"quantity": quantity_stock_uom,
		"net_weight": net,
		"gross_weight": gross,
		"volume_cm3": volume,
		"volume_m3": volume_m3,
		"weight_uom": weight_uom or "",
		"measurement_source": source,
		"net_source": net_source,
		"gross_source": measurement.source if gross is not None else "unavailable",
		"volume_source": measurement.source if volume is not None else "unavailable",
	}
