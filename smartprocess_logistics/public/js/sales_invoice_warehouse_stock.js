// Sales Invoice: physical-only warehouse selectors and stock-aware Item suggestions.
//
// The server still owns the rule that a virtual warehouse cannot be a source or target. This
// script only removes those warehouses from the picker and adds the authorized physical stock
// to the Item Link description. It does not add a stock-availability validation.

frappe.provide("smartprocess_logistics");

(function () {
	const WAREHOUSE_QUERY =
		"smartprocess_logistics.warehouse.physical_warehouse_query.physical_warehouses";
	const ITEM_QUERY = "smartprocess_logistics.warehouse.sales_item_search.items_for_sales_invoice";

	function warehouse_query(frm) {
		return {
			query: WAREHOUSE_QUERY,
			filters: { company: frm.doc.company || "" },
		};
	}

	function item_query(frm) {
		return {
			query: ITEM_QUERY,
			filters: {
				company: frm.doc.company || "",
				customer: frm.doc.customer || "",
			},
		};
	}

	function install(frm) {
		if (!frm.fields_dict) {
			return;
		}

		["set_warehouse", "set_target_warehouse"].forEach(function (fieldname) {
			if (frm.fields_dict[fieldname]) {
				frm.set_query(fieldname, function () {
					return warehouse_query(frm);
				});
			}
		});

		["warehouse", "target_warehouse"].forEach(function (fieldname) {
			frm.set_query(fieldname, "items", function () {
				return warehouse_query(frm);
			});
			if (frm.fields_dict.packed_items) {
				frm.set_query(fieldname, "packed_items", function () {
					return warehouse_query(frm);
				});
			}
		});

		frm.set_query("item_code", "items", function () {
			return item_query(frm);
		});
	}

	frappe.ui.form.on("Sales Invoice", {
		onload: install,
		refresh: install,
	});
})();
