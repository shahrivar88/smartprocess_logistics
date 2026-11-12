// Quotation: stock-aware Item suggestions and warehouse selectors.
//
// A quotation plans; it does not reserve, deliver or post. The Item Link shows physical and
// transit stock side by side, and the warehouse selector offers physical and transit warehouses
// with the item's own stock in each; Virtual is not offered. The stock is the only description
// line, because the Link already draws the name.
//
// The server decides what each of those carries, from `Logistics Settings`, on every request.
// The Desk always asks these queries and never branches on a setting itself: the boot payload
// is cached per session, so a switch saved a moment ago would otherwise still read as it was.

(function () {
	const ITEM_QUERY = "smartprocess_logistics.warehouse.sales_item_search.items_for_quotation";
	const WAREHOUSE_QUERY =
		"smartprocess_logistics.warehouse.physical_warehouse_query.quotation_warehouses";

	function child_item_code(cdt, cdn) {
		const row = locals[cdt] && locals[cdt][cdn];
		return (row && row.item_code) || "";
	}

	function install(frm) {
		if (!frm.fields_dict) {
			return;
		}

		frm.set_query("item_code", "items", function () {
			return {
				query: ITEM_QUERY,
				filters: {
					company: frm.doc.company || "",
					customer: frm.doc.quotation_to === "Customer" ? frm.doc.party_name || "" : "",
				},
			};
		});

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

	frappe.ui.form.on("Quotation", {
		onload: install,
		refresh: install,
		company: install,
	});
})();
