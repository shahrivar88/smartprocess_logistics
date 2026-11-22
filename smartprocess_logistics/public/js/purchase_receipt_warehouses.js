// Purchase Receipt: the warehouse selectors, with the warehouse's type on a second line.
//
// The server owns the list: leaf, enabled warehouses of the document's company, searched and
// paged the way the native Link searches them. This script only asks for that query on each
// warehouse field; the value stored is still the warehouse's name, so nothing downstream —
// stock, valuation, accounting, the receipt's own validation — sees a different warehouse.
//
// The type line is the server's answer too. The setting on Logistics Settings turns it on or
// off, and with it off the same query returns the same names without the second line.

(function () {
	const WAREHOUSE_QUERY =
		"smartprocess_logistics.warehouse.physical_warehouse_query.purchase_receipt_warehouses";

	function warehouse_query(frm) {
		return {
			query: WAREHOUSE_QUERY,
			filters: { company: frm.doc.company || "" },
		};
	}

	function install(frm) {
		if (!frm.fields_dict) {
			return;
		}

		if (frm.fields_dict.set_warehouse) {
			frm.set_query("set_warehouse", function () {
				return warehouse_query(frm);
			});
		}

		["warehouse", "rejected_warehouse", "from_warehouse"].forEach(function (fieldname) {
			frm.set_query(fieldname, "items", function () {
				return warehouse_query(frm);
			});
		});
	}

	frappe.ui.form.on("Purchase Receipt", {
		onload: install,
		refresh: install,
		company: install,
	});
})();
