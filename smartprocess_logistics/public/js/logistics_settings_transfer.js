// The two buttons that hand this app's records to a file, and take them back.
//
// Why the records travel through a file at all: Frappe's uninstaller removes the table behind
// every DocType of the app being removed, so nothing this app stores can outlive its own
// removal. A full site backup covers disaster recovery; this covers the other case — the app
// goes away and its serial history has to come back with it.
//
// Two rules this file follows, both of them the app's own:
//
//   * It decides nothing. The server validates the file, plans the import and authorizes the
//     write; this file collects a file, shows what the server said, and asks for confirmation.
//   * It never writes to the site's disk. The export is turned into a download in the browser
//     and the import reads a file the operator picked, so neither path needs a writable app
//     directory — which this deployment does not have.

// One apply at a time, for the whole form. A second confirmation while the first request is in
// flight would be a duplicate submission, and the server's answer to that would be an idempotent
// no-op rather than an error — which is the right answer to a duplicate, and the wrong thing to
// send from a button that was pressed twice.
let applying = false;

frappe.ui.form.on("Logistics Settings", {
	refresh(frm) {
		// A convenience, not a permission: the endpoints check the level themselves and refuse
		// with their own sentence. Hiding the buttons only keeps a Viewer from pressing one that
		// cannot work.
		if (!is_manager()) {
			return;
		}

		frm.add_custom_button(__("خروجی کامل اطلاعات سریال‌ها"), () => export_everything(frm));
		frm.add_custom_button(__("بازیابی اطلاعات سریال‌ها"), () => restore_everything(frm));
	},
});

function is_manager() {
	return (
		frappe.user.has_role("Logistics Serial Manager") || frappe.user.has_role("System Manager")
	);
}

function export_everything(frm) {
	frappe.dom.freeze(__("در حال آماده‌سازی خروجی…"));

	frappe.call({
		method: "smartprocess_logistics.api.endpoints.export_serials",
		type: "GET",
		callback: (response) => {
			frappe.dom.unfreeze();

			const payload = response.message || {};

			download(payload.filename, JSON.stringify(payload.document, null, 1));

			const counts = payload.counts || {};

			frappe.show_alert({
				message: __("خروجی گرفته شد: {0} سریال و {1} ردیف.", [
					counts.serials || 0,
					counts.assignments || 0,
				]),
				indicator: "green",
			});
		},
		error: () => frappe.dom.unfreeze(),
	});
}

function download(filename, text) {
	const blob = new Blob([text], { type: "application/json;charset=utf-8" });
	const url = URL.createObjectURL(blob);
	const link = document.createElement("a");

	link.href = url;
	link.download = filename || "smartprocess-logistics-serials.json";
	document.body.appendChild(link);
	link.click();
	document.body.removeChild(link);
	URL.revokeObjectURL(url);
}

function restore_everything(frm) {
	pick_file()
		.then((file) => preview(file))
		.catch(() => {});
}

function pick_file() {
	return new Promise((resolve) => {
		const input = document.createElement("input");

		input.type = "file";
		input.accept = ".json,application/json";
		input.onchange = () => {
			const file = input.files && input.files[0];

			if (!file) {
				return;
			}

			const reader = new FileReader();

			reader.onload = () => resolve({ name: file.name, content: reader.result });
			reader.readAsText(file, "utf-8");
		};
		input.click();
	});
}

function preview(file) {
	frappe.dom.freeze(__("در حال بررسی فایل…"));

	return frappe
		.call({
			method: "smartprocess_logistics.api.endpoints.preview_serial_restore",
			type: "POST",
			args: { content: file.content },
		})
		.then((response) => {
			frappe.dom.unfreeze();

			// `frappe.call` resolves with the response it received, whose payload is under
			// `message`. Treating that response as the payload is what made the first version of
			// this dialog show nothing and send `token: undefined` — a preview that never reached
			// the operator, and an apply the server was right to refuse. The return value of a
			// `callback:` option is discarded, so unwrapping has to happen here.
			const preview = (response && response.message) || {};

			if (!preview.token) {
				report_problem(
					__("پیش‌نمایش کامل نرسید. فایل را دوباره انتخاب کنید."),
					__("پیش‌نمایش بازیابی")
				);

				return;
			}

			show_preview(preview, file);
		})
		.catch(() => {
			frappe.dom.unfreeze();
		});
}

function show_preview(preview, file) {
	const rows = preview.to_create || {};
	const skipped = preview.to_skip || {};

	const lines = [
		__("فایل: {0}", [file.name]),
		__("سایت مبدأ: {0}", [preview.source_site || "—"]),
		__("زمان خروجی: {0}", [preview.exported_on || "—"]),
		"",
		__("برای ثبت: {0} سریال و {1} ردیف.", [rows.serials || 0, rows.assignments || 0]),
		__("قبلاً موجود: {0} سریال و {1} ردیف.", [skipped.serials || 0, skipped.assignments || 0]),
		__("قابل ثبت نیست: {0} ردیف.", [preview.conflicts || 0]),
	];

	const dialog = new frappe.ui.Dialog({
		title: __("پیش‌نمایش بازیابی"),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "summary" }],
		primary_action_label: __("ثبت کن"),
		primary_action: () => {
			dialog.hide();
			apply(preview.token, file);
		},
		secondary_action_label: __("انصراف"),
	});

	let html = `<div style="white-space: pre-line; margin-bottom: 12px;">${frappe.utils.escape_html(
		lines.join("\n")
	)}</div>`;

	if ((preview.sample || []).length) {
		html += `<table class="table table-bordered"><thead><tr>
			<th>${__("ردیف")}</th><th>${__("دلیل")}</th><th>${__("جزئیات")}</th>
			</tr></thead><tbody>`;

		(preview.sample || []).forEach((row) => {
			html += `<tr><td>${frappe.utils.escape_html(row.row || "")}</td>
				<td>${frappe.utils.escape_html(row.message || "")}</td>
				<td>${frappe.utils.escape_html(row.detail || "")}</td></tr>`;
		});

		html += "</tbody></table>";
	}

	dialog.fields_dict.summary.$wrapper.html(html);
	dialog.show();
}

function apply(token, file) {
	// The token is what ties this write to the file the operator actually saw, and the server
	// requires it. Sending the request without one would be asking the server to weaken that
	// check; refusing here says the same thing to the operator in a sentence they can act on.
	if (!token) {
		report_problem(
			__("نشانهٔ تأیید پیش‌نمایش در دسترس نیست. فایل را دوباره انتخاب کنید."),
			__("بازیابی سریال‌ها")
		);

		return;
	}

	// One apply at a time, however many times the button is pressed. The dialog closes on the
	// first press, but a reopened preview could be confirmed twice, and the second request would
	// be a duplicate submission rather than an idempotent one.
	if (applying) {
		return;
	}

	applying = true;
	frappe.dom.freeze(__("در حال بازیابی…"));

	return frappe
		.call({
			method: "smartprocess_logistics.api.endpoints.apply_serial_restore",
			type: "POST",
			args: { content: file.content, token: token },
		})
		.then((response) => {
			const result = (response && response.message) || {};
			const counts = result.counts || {};
			const created = counts.created || {};

			frappe.msgprint({
				title: __("بازیابی سریال‌ها"),
				indicator: "green",
				message: [
					__("ثبت‌شده: {0} سریال و {1} ردیف.", [created.serials || 0, created.assignments || 0]),
					__("رد شده (از قبل موجود): {0} ردیف.", [
						(counts.skipped && counts.skipped.assignments) || 0,
					]),
					__("قابل ثبت نبود: {0} ردیف.", [counts.conflicts || 0]),
				].join("<br>"),
			});
		})
		.catch(() => {
			// The server's own refusal has already been shown to the operator by Frappe; the
			// freeze is what this has to undo.
		})
		.always(() => {
			applying = false;
			frappe.dom.unfreeze();
		});
}

function report_problem(message, title) {
	frappe.msgprint({ title: title, indicator: "red", message: message });
}
