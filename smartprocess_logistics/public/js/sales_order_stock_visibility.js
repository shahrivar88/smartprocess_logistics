// Sales Order: stock-aware Item suggestions, stock-aware warehouse selectors, and a read-only
// billing-progress panel.
//
// The Item Link shows the standard item name plus authorized physical and virtual stock, and
// the warehouse selector offers both kinds of warehouse with their own stock, because a Sales
// Order only plans. A virtual warehouse is labelled as one in that list: it is real stock, but
// this app refuses it as a sales source, so a reservation of it is refused at submit and the
// Delivery Note built from the row will not save. The panel reads the standard Sales Invoice
// links; it never creates an invoice, reserves stock or blocks submission when stock is zero.

frappe.provide("smartprocess_logistics");

(function () {
	const ITEM_QUERY = "smartprocess_logistics.warehouse.sales_item_search.items_for_sales_order";
	const WAREHOUSE_QUERY =
		"smartprocess_logistics.warehouse.physical_warehouse_query.sales_order_warehouses";
	const PROGRESS_METHOD = "smartprocess_logistics.warehouse.sales_order_progress.sales_order_progress";
	const PANEL_CLASS = "sp-sales-order-billing-progress";
	const visibility = smartprocess_logistics.visibility;

	const ITEM_STOCK = "sp_show_so_item_stock";
	const WAREHOUSE_STOCK = "sp_show_so_warehouse_stock";

	function child_item_code(cdt, cdn) {
		const row = locals[cdt] && locals[cdt][cdn];
		return (row && row.item_code) || "";
	}

	function install(frm) {
		if (visibility.enabled(ITEM_STOCK)) {
			frm.set_query("item_code", "items", function () {
				return {
					query: ITEM_QUERY,
					filters: {
						company: frm.doc.company || "",
						customer: frm.doc.customer || "",
					},
				};
			});
		}

		if (visibility.enabled(WAREHOUSE_STOCK)) {
			["warehouse", "target_warehouse"].forEach(function (fieldname) {
				frm.set_query(fieldname, "items", function (doc, cdt, cdn) {
					return {
						query: WAREHOUSE_QUERY,
						filters: {
							company: frm.doc.company || "",
							item_code: child_item_code(cdt, cdn),
						},
					};
				});
			});
		}
	}

	function clear_panel(frm) {
		if (frm.dashboard && frm.dashboard.parent) {
			frm.dashboard.parent.find("." + PANEL_CLASS).remove();
		}
	}

	function display_value(value, fieldtype) {
		return value === null || value === undefined ? "—" : frappe.format(value, { fieldtype: fieldtype });
	}

	function add_panel(frm, rows, available, billingAvailable, billingNote) {
		if (!frm.dashboard) {
			return;
		}
		clear_panel(frm);
		const section = frm.dashboard.add_section("", __("پیشرفت صورت‌حساب"));
		section.addClass(PANEL_CLASS);

		if (!available) {
			$("<p class='text-muted small'></p>")
				.text("برای نمایش پیشرفت، دسترسی خواندن سفارش فروش لازم است.")
				.appendTo(section);
			return;
		}
		if (!billingAvailable) {
			$("<p class='text-muted small'></p>").text(billingNote || "اطلاعات فاکتور در دسترس نیست.").appendTo(section);
		}
		if (!rows.length) {
			$("<p class='text-muted small'></p>").text("ردیفی برای این سفارش وجود ندارد.").appendTo(section);
			return;
		}

		const table = $("<table class='table table-bordered table-sm'></table>").appendTo(section);
		const head = $("<thead></thead>").appendTo(table);
		const head_row = $("<tr></tr>").appendTo(head);
		["کالا", "سفارش‌شده", "فاکتورشده", "باقی‌ماندهٔ فاکتور", "تحویل‌شده", "پیشرفت"]
			.forEach(function (label) {
				$("<th></th>").text(label).appendTo(head_row);
			});

		const body = $("<tbody></tbody>").appendTo(table);
		rows.forEach(function (row) {
			const tr = $("<tr></tr>").appendTo(body);
			$("<td></td>").text(row.item_name || row.item_code).appendTo(tr);
			$("<td></td>").text(display_value(row.ordered_qty, "Float")).appendTo(tr);
			$("<td></td>").text(display_value(row.invoiced_qty, "Float")).appendTo(tr);
			$("<td></td>").text(display_value(row.remaining_qty, "Float")).appendTo(tr);
			$("<td></td>").text(display_value(row.delivered_qty, "Float")).appendTo(tr);
			$("<td></td>").text(display_value(row.progress, "Percent")).appendTo(tr);
			if (row.review) {
				$("<div class='text-muted small'></div>").text(row.review).appendTo(tr.find("td").first());
			}
		});
	}

	function load_progress(frm) {
		if (!frm.doc.name || frm.is_new()) {
			return;
		}
		frappe
			.xcall(PROGRESS_METHOD, { sales_order: frm.doc.name }, "GET")
			.then(function (data) {
				add_panel(
					frm,
					(data && data.rows) || [],
					data && data.available !== false,
					data && data.billing_available !== false,
					(data && data.billing_note) || ""
				);
			})
			.catch(function () {
				// A failed read is not a billing statement; leave the form untouched.
			});
	}

	frappe.ui.form.on("Sales Order", {
		onload: install,
		refresh: function (frm) {
			install(frm);
			load_progress(frm);
		},
	});
})();
