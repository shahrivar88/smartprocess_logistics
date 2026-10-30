// The Desk-side view of the Logistics Settings display switches.
//
// `boot.py` hands the seven checks to every Desk session. A switch that this browser's boot
// payload does not carry reads as enabled, which is the same default the server applies.

frappe.provide("smartprocess_logistics.visibility");

smartprocess_logistics.visibility.enabled = function (fieldname) {
	const flags = (frappe.boot && frappe.boot.smartprocess_logistics) || {};
	return flags[fieldname] !== false;
};
