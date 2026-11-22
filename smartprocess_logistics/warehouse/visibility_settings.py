"""The eight display switches behind the Desk's stock lines and the receipt's warehouse type.

They decide what the Desk draws; they are not an access-control boundary. Every endpoint they
gate applies the same company scope and permission checks with a switch on or off, so a client
that calls an endpoint directly sees only what its own permissions already allow.

Each is a Check on `Logistics Settings`. A switch that is **absent** from the site's singles row
reads as its default, which is enabled: `frappe.db.get_single_value` casts a missing Check row
to zero, so it cannot tell "never saved" from "switched off", and treating a field added by a
migrate as switched off would hide a feature the operator never touched. `get_singles_dict`
answers the presence question; the field default answers the value one.

The read is cached per request, because a Link search runs on every keystroke.
"""

import frappe
from frappe.utils import cint

SETTINGS_DOCTYPE = "Logistics Settings"

SHOW_SI_ITEM_STOCK = "sp_show_si_item_stock"
SHOW_SI_WAREHOUSE_STOCK = "sp_show_warehouse_stock_in_selector"
SI_PHYSICAL_WAREHOUSES_ONLY = "sp_si_physical_warehouses_only"
SHOW_QUOTATION_ITEM_STOCK = "sp_show_quotation_item_stock"
SHOW_QUOTATION_WAREHOUSE_STOCK = "sp_show_quotation_warehouse_stock"
SHOW_SO_ITEM_STOCK = "sp_show_so_item_stock"
SHOW_SO_WAREHOUSE_STOCK = "sp_show_so_warehouse_stock"
SHOW_PR_WAREHOUSE_TYPE = "sp_show_pr_warehouse_type"

FIELDS = (
	SHOW_SI_ITEM_STOCK,
	SHOW_SI_WAREHOUSE_STOCK,
	SI_PHYSICAL_WAREHOUSES_ONLY,
	SHOW_QUOTATION_ITEM_STOCK,
	SHOW_QUOTATION_WAREHOUSE_STOCK,
	SHOW_SO_ITEM_STOCK,
	SHOW_SO_WAREHOUSE_STOCK,
	SHOW_PR_WAREHOUSE_TYPE,
)

CACHE_KEY = "sp_visibility_settings"


def _switch(field, stored):
	if field is None:
		# The code is ahead of the schema: keep the feature visible rather than silently off.
		return True
	if stored is None:
		return bool(cint(field.default if field.default not in (None, "") else 1))
	return bool(cint(stored))


def load():
	"""All eight switches, defaults applied, cached for this request."""
	cached = getattr(frappe.local, CACHE_KEY, None)
	if cached is None:
		meta = frappe.get_meta(SETTINGS_DOCTYPE)
		stored = frappe.db.get_singles_dict(SETTINGS_DOCTYPE)
		cached = {name: _switch(meta.get_field(name), stored.get(name)) for name in FIELDS}
		setattr(frappe.local, CACHE_KEY, cached)

	return cached
