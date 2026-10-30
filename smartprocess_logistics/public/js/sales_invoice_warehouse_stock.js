// Sales Invoice: the warehouse selectors and the stock-aware Item suggestions.
//
// The server still owns the rule that a virtual warehouse cannot be a source or target. This
// script only chooses which query the Desk asks: the physical-only selector while that switch
// is on, the stock-bearing selector while its own switch is on, and the native selector when
// both are off. It does not add a stock-availability validation.

frappe.provide("smartprocess_logistics");

(function () {
	const WAREHOUSE_QUERY =
		"smartprocess_logistics.warehouse.physical_warehouse_query.physical_warehouses";
	const ITEM_QUERY = "smartprocess_logistics.warehouse.sales_item_search.items_for_sales_invoice";
	const visibility = smartprocess_logistics.visibility;

	const PHYSICAL_ONLY = "sp_si_physical_warehouses_only";
	const WAREHOUSE_STOCK = "sp_show_warehouse_stock_in_selector";
	const ITEM_STOCK = "sp_show_si_item_stock";

	function warehouse_query(frm, item_code) {
		return {
			query: WAREHOUSE_QUERY,
			filters: {
				company: frm.doc.company || "",
				item_code: item_code || "",
			},
		};
	}

	function child_item_code(cdt, cdn) {
		const row = locals[cdt] && locals[cdt][cdn];
		return (row && row.item_code) || "";
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

		if (visibility.enabled(PHYSICAL_ONLY) || visibility.enabled(WAREHOUSE_STOCK)) {
			["set_warehouse", "set_target_warehouse"].forEach(function (fieldname) {
				if (frm.fields_dict[fieldname]) {
					frm.set_query(fieldname, function () {
						return warehouse_query(frm);
					});
				}
			});

			["warehouse", "target_warehouse"].forEach(function (fieldname) {
				frm.set_query(fieldname, "items", function (doc, cdt, cdn) {
					return warehouse_query(frm, child_item_code(cdt, cdn));
				});
				if (frm.fields_dict.packed_items) {
					frm.set_query(fieldname, "packed_items", function (doc, cdt, cdn) {
						return warehouse_query(frm, child_item_code(cdt, cdn));
					});
				}
			});
		}

		if (visibility.enabled(ITEM_STOCK)) {
			frm.set_query("item_code", "items", function () {
				return item_query(frm);
			});
		}
	}

	frappe.ui.form.on("Sales Invoice", {
		onload: install,
		refresh: install,
		company: install,
	});

	frappe.ui.form.on("Sales Invoice Item", {
		item_code: function (frm) {
			install(frm);
		},
	});
})();
