// Sales Order: stock-aware Item suggestions, stock-aware warehouse selectors, and a read-only
// billing-progress panel.
//
// The Item Link shows the standard item name plus authorized physical and transit stock, and
// the warehouse selector offers physical and transit warehouses with their own stock, because a
// Sales Order only plans; Virtual is not offered. The stock is the only description line: the
// Link already draws the name. The server applies both switches on every request; this script
// always asks its queries. A Virtual warehouse is never a sales source, so a reservation or
// delivery from one is refused at submit. The panel reads standard Sales Invoice links; it
// never creates an invoice, reserves stock or blocks submission when stock is zero.

frappe.provide("smartprocess_logistics");

(function () {
	const ITEM_QUERY = "smartprocess_logistics.warehouse.sales_item_search.items_for_sales_order";
	const WAREHOUSE_QUERY =
		"smartprocess_logistics.warehouse.physical_warehouse_query.sales_order_warehouses";
	const PROGRESS_METHOD = "smartprocess_logistics.warehouse.sales_order_progress.sales_order_progress";
	const PANEL_CLASS = "sp-sales-order-billing-progress";

	function child_item_code(cdt, cdn) {
		const row = locals[cdt] && locals[cdt][cdn];
		return (row && row.item_code) || "";
	}

	// The server reads the two switches for this form on every request. The Desk does not
	// branch on them: the boot payload is cached per session and would keep the old values.
	function install(frm) {
		frm.set_query("item_code", "items", function () {
			return {
				query: ITEM_QUERY,
				filters: {
					company: frm.doc.company || "",
					customer: frm.doc.customer || "",
				},
			};
		});

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

	function clear_panel(frm) {
		if (frm.dashboard && frm.dashboard.parent) {
			frm.dashboard.parent.find("." + PANEL_CLASS).remove();
		}
	}

	function display_value(value, fieldtype) {
		return value === null || value === undefined ? "—" : frappe.format(value, { fieldtype: fieldtype });
	}

	const STATUS_LABELS = {
		open: "باز / معتبر",
		partially_invoiced: "بخشی فاکتورشده",
		partially_delivered: "بخشی تحویل‌شده",
		fully_delivered: "کاملاً تحویل‌شده",
		closed: "بسته",
		cancelled: "لغوشده",
	};

	function invoice_cell(row) {
		const cell = $("<td></td>");
		(row.invoices || []).forEach(function (name) {
			if (cell.children().length) {
				cell.append(", ");
			}
			cell.append(frappe.utils.get_form_link("Sales Invoice", name, true));
		});
		return cell;
	}

	function add_invoice_button(frm, row, cell) {
		if (!row.so_detail || !(row.remaining_qty > 0) || frm.doc.docstatus !== 1) {
			return;
		}
		if (!frappe.model.can_create("Sales Invoice")) {
			return;
		}
		$("<button class='btn btn-xs btn-default'></button>")
			.text("فاکتور")
			.on("click", function () {
				// The native mapper, limited to this row. It maps the remaining invoiceable
				// quantity and ERPNext's own validation refuses anything above it.
				frappe.model.open_mapped_doc({
					method: "erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice",
					frm: frm,
					args: { filtered_children: [row.so_detail] },
				});
			})
			.appendTo(cell);
	}

	function add_panel(frm, rows, available, billingAvailable, billingNote, status, totals, inDemand) {
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
		if (status) {
			const label = "وضعیت عملیاتی: " + (STATUS_LABELS[status] || status);
			$("<p class='small'></p>")
				.text(label + " — در تقاضای عملیاتی: " + (inDemand ? "بله" : "خیر"))
				.appendTo(section);
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
		["کالا", "سفارش‌شده", "فاکتورشده", "باقی‌ماندهٔ فاکتور", "تحویل‌شده", "پیشرفت", "فاکتورها", ""]
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
			tr.append(invoice_cell(row));
			const action = $("<td></td>").appendTo(tr);
			add_invoice_button(frm, row, action);
			if (row.review) {
				$("<div class='text-muted small'></div>").text(row.review).appendTo(tr.find("td").first());
			}
		});

		if (totals) {
			const foot = $("<tfoot></tfoot>").appendTo(table);
			const tr = $("<tr class='strong'></tr>").appendTo(foot);
			$("<td></td>").text("جمع").appendTo(tr);
			$("<td></td>").text(display_value(totals.ordered_qty, "Float")).appendTo(tr);
			$("<td></td>").text(display_value(totals.invoiced_qty, "Float")).appendTo(tr);
			$("<td></td>").text(display_value(totals.remaining_qty, "Float")).appendTo(tr);
			$("<td></td>").text(display_value(totals.delivered_qty, "Float")).appendTo(tr);
			$("<td></td>").appendTo(tr);
			$("<td></td>").appendTo(tr);
			$("<td></td>").appendTo(tr);
		}
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
					(data && data.billing_note) || "",
					(data && data.operational_status) || "",
					(data && data.totals) || null,
					!!(data && data.contributes_to_demand)
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
