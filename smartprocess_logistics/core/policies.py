"""Pure parsing of the configurable inventory policies.

The settings are read by `warehouse.policies`; this module only turns the stored values into a
small immutable policy object. Missing fields during an upgrade fall back to the approved
defaults. An explicitly saved `0` for a Check remains false; it is not replaced by the default.
"""

from dataclasses import dataclass

from smartprocess_logistics.core.inventory import ALLOCATION_STRATEGIES, DEFAULT_ALLOCATION_STRATEGY

ALLOCATION_FIELD = "sp_allocation_priority"
VIRTUAL_STOCK_FIELD = "sp_virtual_stock_covers_procurement"
NET_OPEN_PO_FIELD = "sp_recommended_purchase_net_of_open_po"

POLICY_FIELDS = (ALLOCATION_FIELD, VIRTUAL_STOCK_FIELD, NET_OPEN_PO_FIELD)


@dataclass(frozen=True)
class InventoryPolicy:
	allocation_priority: str = DEFAULT_ALLOCATION_STRATEGY
	virtual_stock_covers_procurement: bool = True
	recommended_purchase_net_of_open_po: bool = True


DEFAULT_POLICY = InventoryPolicy()


def _bool_or_none(value):
	if value is None:
		return None
	if isinstance(value, bool):
		return value
	if isinstance(value, (int, float)):
		return bool(value)
	text = str(value).strip().casefold()
	if not text:
		return None
	return text not in {"0", "false", "no", "off"}


def policy_from_values(values, fields_present):
	"""Parse only fields that exist; an absent field is not an explicit false."""
	values = values or {}
	present = set(fields_present or ())

	allocation = values.get(ALLOCATION_FIELD) if ALLOCATION_FIELD in present else None
	if allocation not in ALLOCATION_STRATEGIES:
		allocation = DEFAULT_POLICY.allocation_priority

	virtual = _bool_or_none(values.get(VIRTUAL_STOCK_FIELD)) if VIRTUAL_STOCK_FIELD in present else None
	net_open_po = _bool_or_none(values.get(NET_OPEN_PO_FIELD)) if NET_OPEN_PO_FIELD in present else None

	return InventoryPolicy(
		allocation_priority=allocation,
		virtual_stock_covers_procurement=(
			DEFAULT_POLICY.virtual_stock_covers_procurement if virtual is None else virtual
		),
		recommended_purchase_net_of_open_po=(
			DEFAULT_POLICY.recommended_purchase_net_of_open_po if net_open_po is None else net_open_po
		),
	)
