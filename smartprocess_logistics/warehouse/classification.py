"""The one place that answers "what kind of warehouse is this?".

ERPNext's `Warehouse Type` is a label with no availability, permission or ledger meaning of its
own. Standard ERPNext does read one value of it — a Stock Entry whose warehouses are `Transit`
takes the transit path — and this app leaves that alone.

What this app adds is the operational rule: stock in a warehouse that cannot serve a customer is
not available to a sale, a delivery or a reservation. That used to be a two-way question (virtual
or physical), and it is now a three-way one (`physical`, `virtual`, `transit`) so a report can
tell goods in transit from stock held in a virtual warehouse, while the guards keep asking the
same binary question they always asked. Both answers come from here:

* `classification_of_type(type)` is the three-way answer the reports show, as an English code.
  Persian labels belong to the display layer (`policy.CLASSIFICATION_LABELS`,
  `translations/fa.csv`) and are never stored or compared.
* `virtual_types()` is the effective tuple of non-physical names, and every guard in this app
  asks membership of it. Falling back to it means a stored mapping reaches all of them without
  each one learning about the settings screen.

The effective policy is the standard type names or `sp_virtual_warehouse_types`, adjusted by the
administrator's mapping in Logistics Settings. It is computed, never written: an empty table is
the install state, and it reproduces the behaviour the app shipped before the table existed.
"""

import json

import frappe

from smartprocess_logistics.warehouse import policy

CONFIG_OVERRIDE = "sp_virtual_warehouse_types"

SETTINGS_DOCTYPE = "Logistics Settings"
SETTINGS_TABLE = "warehouse_classifications"
CLASSIFICATION_DOCTYPE = "Logistics Warehouse Classification"
WAREHOUSE_TYPE_DOCTYPE = "Warehouse Type"

# `frappe.local` is the request (or console session) this module is answering in. The mapping is
# a handful of rows and it is read on every row of every report, so it is read once per request
# and dropped by `forget_mapping()` when a settings write changes it.
_MAPPING_KEY = "sp_logistics_warehouse_classification"


def _configured_types():
	value = frappe.local.conf.get(CONFIG_OVERRIDE)
	if not value:
		return None

	if isinstance(value, str):
		try:
			value = json.loads(value)
		except ValueError:
			value = [part.strip() for part in value.split(",") if part.strip()]

	if isinstance(value, str):
		value = [value]

	if not isinstance(value, (list, tuple, set)):
		frappe.throw(
			"تنظیم sp_virtual_warehouse_types باید فهرستی از نام‌های Warehouse Type باشد.",
			title="جریان انبار — طبقه‌بندی انبار",
		)

	types = tuple(str(item).strip() for item in value if str(item).strip())
	if not types:
		frappe.throw(
			"تنظیم sp_virtual_warehouse_types خالی است؛ برای جلوگیری از طبقه‌بندی ناامن متوقف شد.",
			title="جریان انبار — طبقه‌بندی انبار",
		)
	return types


def policy_types():
	"""The type names the effective policy treats as non-physical, before any stored mapping."""
	return _configured_types() or policy.DEFAULT_VIRTUAL_TYPES


def stored_mapping():
	"""The mapping an administrator saved, as {type name: classification code}.

	Read once per request. A site that has not migrated yet has no such field and no such table,
	which is why the metadata is checked before anything is queried.
	"""
	cached = getattr(frappe.local, _MAPPING_KEY, None)
	if cached is not None:
		return cached

	mapping = _read_mapping()
	setattr(frappe.local, _MAPPING_KEY, mapping)
	return mapping


def forget_mapping():
	"""Drop the request's copy. The settings hook calls this when the table it caches is saved."""
	if hasattr(frappe.local, _MAPPING_KEY):
		delattr(frappe.local, _MAPPING_KEY)


def stored_rows():
	"""The saved table in document order, exactly as it was written.

	`stored_mapping()` reduces the same rows to the dict the guards ask; the settings form needs
	the order and the blank key, so it reads them whole. Neither one writes anything.
	"""
	meta = frappe.get_meta(SETTINGS_DOCTYPE)
	if SETTINGS_TABLE not in {field.fieldname for field in meta.fields}:
		return ()

	return tuple(
		frappe.get_all(
			CLASSIFICATION_DOCTYPE,
			filters={
				"parent": SETTINGS_DOCTYPE,
				"parenttype": SETTINGS_DOCTYPE,
				"parentfield": SETTINGS_TABLE,
			},
			fields=["warehouse_type", "classification"],
			order_by="idx asc",
			limit_page_length=0,
		)
	)


def _read_mapping():
	rows = stored_rows()

	mapping = {}
	for row in rows:
		classification = row.get("classification")
		if classification in policy.CLASSIFICATIONS:
			mapping[policy.type_key(row.get("warehouse_type"))] = classification

	return mapping


def known_types():
	"""Every `Warehouse Type` on the site, which is what the mapping table must cover."""
	return tuple(
		row.get("name")
		for row in frappe.get_all(
			WAREHOUSE_TYPE_DOCTYPE, fields=["name"], order_by="name", limit_page_length=0
		)
	)


def classification_of_type(warehouse_type):
	"""The three-way classification of a standard Warehouse Type, as `physical`/`virtual`/`transit`."""
	return policy.classification_of(warehouse_type, policy_types(), stored_mapping())


def virtual_types():
	"""The type names that are not physical, after the administrator's mapping is applied.

	This is the form the operational guards consume, and the reason a stored mapping reaches them
	without any of them being edited: a type maps to `physical` by leaving this tuple, and to
	`virtual` or `transit` by joining it. The blank-type bucket travels as the empty string.
	"""
	return policy.non_physical_names(policy_types(), stored_mapping())


def is_virtual(warehouse):
	"""Whether this warehouse's stock is unavailable to sales, deliveries and reservations."""
	if not warehouse:
		return False
	warehouse_type = frappe.get_cached_value("Warehouse", warehouse, "warehouse_type")
	return policy.is_virtual_type(warehouse_type, virtual_types())


def physical_warehouses(company):
	"""Leaf warehouses of a company whose type is not virtual."""
	types = virtual_types()
	rows = frappe.get_list(
		"Warehouse",
		filters={"company": company, "is_group": 0},
		fields=["name", "warehouse_type"],
		limit_page_length=0,
	)
	return tuple(
		row.get("name") for row in rows if not policy.is_virtual_type(row.get("warehouse_type"), types)
	)
