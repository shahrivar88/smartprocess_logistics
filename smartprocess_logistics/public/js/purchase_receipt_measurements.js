// Purchase Receipt: a read-only net/gross/volume panel computed by the server.
//
// The panel never edits native totals or item rows. It asks one GET endpoint for the document's
// measurement summary; missing values stay missing, and mixed weight units are listed instead
// of being summed.

frappe.provide("smartprocess_logistics");

(function () {
	const METHOD =
		"smartprocess_logistics.warehouse.purchase_receipt_measurements.purchase_receipt_measurements";
	const PANEL_CLASS = "sp-purchase-receipt-measurements";

	function clear_panel(frm) {
		if (frm.dashboard && frm.dashboard.parent) {
			frm.dashboard.parent.find("." + PANEL_CLASS).remove();
		}
	}

	function number(value) {
		if (value === null || value === undefined || value === "") {
			return "—";
		}
		const parsed = Number(value);
		return Number.isFinite(parsed) ? frappe.format(parsed, { fieldtype: "Float" }) : String(value);
	}

	function with_uom(value, uom) {
		const text = number(value);
		return text === "—" ? text : text + (uom ? " " + uom : "");
	}

	function group_text(groups) {
		const parts = (groups || [])
			.filter(function (group) {
				return group && group.value !== null && group.value !== undefined && group.value !== "";
			})
			.map(function (group) {
				return with_uom(group.value, group.uom);
			});
		return parts.length ? parts.join(" · ") : "—";
	}

	function line(section, label, value) {
		const row = $("<div class='row small'></div>").appendTo(section);
		$("<div class='col-sm-5 text-muted'></div>").text(label).appendTo(row);
		$("<div class='col-sm-7'></div>").text(value).appendTo(row);
	}

	function add_panel(frm, data) {
		if (!frm.dashboard) {
			return;
		}
		clear_panel(frm);
		const section = frm.dashboard.add_section("", __("وزن و حجم رسید"));
		section.addClass(PANEL_CLASS);

		if (!data || data.rows === 0) {
			$("<p class='text-muted small'></p>")
				.text("ردیفی برای محاسبه وزن و حجم وجود ندارد.")
				.appendTo(section);
			return;
		}

		const net = data.net_total !== null ? with_uom(data.net_total, data.weight_uom) : group_text(data.net_by_uom);
		const gross =
			data.gross_total !== null
				? with_uom(data.gross_total, data.weight_uom)
				: group_text(data.gross_by_uom);
		line(section, "وزن خالص پذیرفته‌شده", net);
		line(section, "وزن ناخالص پذیرفته‌شده", gross);
		line(
			section,
			"حجم پذیرفته‌شده",
			data.volume_cm3 === null ? "—" : number(data.volume_cm3) + " cm³ · " + number(data.volume_m3) + " m³"
		);

		if (data.rejected_rows) {
			line(
				section,
				"وزن خالص ردشده",
				data.rejected_net_total !== null
					? with_uom(data.rejected_net_total, data.weight_uom)
					: "—"
			);
			line(
				section,
				"وزن ناخالص ردشده",
				data.rejected_gross_total !== null
					? with_uom(data.rejected_gross_total, data.weight_uom)
					: "—"
			);
			line(
				section,
				"حجم ردشده",
				data.rejected_volume_cm3 === null
					? "—"
					: number(data.rejected_volume_cm3) + " cm³"
			);
		}

		$("<p class='text-muted small'></p>").text(data.coverage || "").appendTo(section);
		if (frm.doc.__unsaved) {
			$("<p class='text-muted small'></p>")
				.text("برای بازتاب تغییرات ذخیره‌نشده، ابتدا سند را ذخیره کنید.")
				.appendTo(section);
		}
		(data.warnings || []).forEach(function (warning) {
			$("<p class='text-warning small'></p>").text(warning).appendTo(section);
		});
	}

	function load(frm) {
		if (!frm.doc.name || frm.is_new()) {
			if (frm.dashboard) {
				clear_panel(frm);
				const section = frm.dashboard.add_section("", __("وزن و حجم رسید"));
				section.addClass(PANEL_CLASS);
				$("<p class='text-muted small'></p>")
					.text("برای محاسبهٔ وزن و حجم، ابتدا سند را ذخیره کنید.")
					.appendTo(section);
			}
			return;
		}
		frappe
			.xcall(METHOD, { purchase_receipt: frm.doc.name }, "GET")
			.then(function (data) {
				add_panel(frm, data);
			})
			.catch(function () {
				// A failed read is not a measurement; leave the form untouched.
			});
	}

	frappe.ui.form.on("Purchase Receipt", {
		refresh: function (frm) {
			load(frm);
		},
	});
})();
