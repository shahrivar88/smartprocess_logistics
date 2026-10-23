"""Read the approved inventory policies from Logistics Settings.

The fields may not exist yet during an upgrade; `frappe.get_meta` is checked before any read,
and a missing field keeps its approved default. `frappe.db.get_single_value` reads the Single
without making a report depend on the Settings DocPerm. An explicitly saved `0` is returned as
false and is never replaced by the default.
"""

import frappe

from smartprocess_logistics.core.policies import DEFAULT_POLICY, POLICY_FIELDS, policy_from_values


def inventory_policy():
	meta = frappe.get_meta("Logistics Settings")
	present = {field.fieldname for field in meta.fields if field.fieldname in POLICY_FIELDS}
	if not present:
		return DEFAULT_POLICY

	values = {field: frappe.db.get_single_value("Logistics Settings", field) for field in present}
	return policy_from_values(values, present)
