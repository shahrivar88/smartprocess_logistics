// Quotation: stock-aware Item suggestions and warehouse selectors.
//
// A quotation plans; it does not reserve, deliver or post. So the Item Link shows physical and
// virtual stock side by side, and the warehouse selector offers both kinds of warehouse with
// the item's own stock in each. A virtual warehouse is labelled as one, because this app
// refuses it as a sales source once the quotation becomes an invoice or a delivery.
//
// Each switch is independent: with it off, the native selector is left exactly as ERPNext set it.

frappe.provide("smartprocess_logistics");

(function () {
	const ITEM_QUERY = "smartprocess_logistics.warehouse.sales_item_search.items_for_quotation";
	const WAREHOUSE_QUERY =
		"smartprocess_logistics.warehouse.physical_warehouse_query.quotation_warehouses";
	const visibility = smartprocess_logistics.visibility;

	const ITEM_STOCK = "sp_show_quotation_item_stock";
	const WAREHOUSE_STOCK = "sp_show_quotation_warehouse_stock";

	function child_item_code(cdt, cdn) {
		const row = locals[cdt] && locals[cdt][cdn];
		return (row && row.item_code) || "";
	}

	function install(frm) {
		if (!frm.fields_dict) {
			return;
		}

		if (visibility.enabled(ITEM_STOCK)) {
			frm.set_query("item_code", "items", function () {
				return {
					query: ITEM_QUERY,
					filters: {
						company: frm.doc.company || "",
						customer: frm.doc.quotation_to === "Customer" ? frm.doc.party_name || "" : "",
					},
				};
			});
		}

		if (visibility.enabled(WAREHOUSE_STOCK)) {
			frm.set_query("warehouse", "items", function (doc, cdt, cdn) {
				return {
					query: WAREHOUSE_QUERY,
					filters: {
						company: frm.doc.company || "",
						item_code: child_item_code(cdt, cdn),
					},
				};
			});
		}
	}

	frappe.ui.form.on("Quotation", {
		onload: install,
		refresh: install,
		company: install,
	});
})();
