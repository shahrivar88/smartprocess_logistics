// The Logistics Desk integration for Sales Invoice.
//
// One form hook, two dialogs, and no arithmetic. Every count, every shortage and every refusal
// comes from the server: the endpoints answer with `core.quantities` coverage and with messages
// that are already written for an operator, and this file only draws them. A rule implemented
// here would be a rule the server tests could not reach.
//
// Assets are served as plain files from the app's `public` directory; see `hooks.py`.

frappe.provide("smartprocess_logistics");

smartprocess_logistics.ENDPOINT = "smartprocess_logistics.api.endpoints";
smartprocess_logistics.SETTINGS = "Logistics Settings";

smartprocess_logistics.ROLE = {
	viewer: "Logistics Serial Viewer",
	editor: "Logistics Serial Editor",
	manager: "Logistics Serial Manager",
};

/** Whether the acting user holds any of the roles an action needs. */
smartprocess_logistics.allowed = function (...roles) {
	if (frappe.user.has_role("System Manager")) {
		return true;
	}

	return roles.some((role) => frappe.user.has_role(role));
};

/** The Logistics Settings single, read once per page. */
smartprocess_logistics.settings = function () {
	if (!smartprocess_logistics._settings) {
		smartprocess_logistics._settings = frappe.db.get_single_value(
			smartprocess_logistics.SETTINGS,
			"enable_serial_registration"
		);
	}

	return smartprocess_logistics._settings;
};

/** Whether the acting user's company permissions reach this invoice's company. */
smartprocess_logistics.reaches_company = function (company) {
	if (!smartprocess_logistics._permissions) {
		// `get_user_permissions` answers with the object itself on this framework version and
		// is documented as answering with a promise on others. `Promise.resolve` takes either —
		// it flattens a promise and wraps a value — so the same code runs on both. Calling
		// `.then` on the plain object threw inside the form's `refresh` handler and the buttons
		// were never added, which is the failure this line exists to prevent.
		smartprocess_logistics._permissions = Promise.resolve(
			frappe.defaults.get_user_permissions()
		);
	}

	return smartprocess_logistics._permissions.then((permissions) => {
		const allowed = permissions && permissions.Company;

		if (!allowed || !allowed.length) {
			return true; // No company permission means every company, which is how the server reads it.
		}

		return allowed.some((entry) => entry.doc === company);
	});
};

// The verb each endpoint declares in its own whitelist. The two reads are GET-only and the
// four writes are POST-only, so the verb is stated here once rather than guessed at each call
// site: a POST to a GET-only endpoint is refused by the framework with 403 before the function
// runs, which is a failure that looks like a permission problem and is not one.
smartprocess_logistics.METHOD = {
	get_invoice_serial_status: "GET",
	get_serial_assignments: "GET",
	set_row_serials: "POST",
	delete_assignments: "POST",
	prepare_import_preview: "POST",
	confirm_import: "POST",
};

/** Call an endpoint and resolve with its message. */
smartprocess_logistics.call = function (method, args) {
	const type = smartprocess_logistics.METHOD[method];

	if (!type) {
		throw new Error(`unknown endpoint: ${method}`);
	}

	const path = `${smartprocess_logistics.ENDPOINT}.${method}`;

	return frappe
		.call({ method: path, args: args, type: type })
		.then((response) => response.message)
		.catch((error) => {
			// The server's message is written for the operator and carries the stable code.
			const message = (error && (error.message || error._server_messages)) || "";

			frappe.msgprint({
				indicator: "red",
				message: message || __("درخواست انجام نشد."),
				title: __("ثبت سریال"),
			});

			return Promise.reject(error);
		});
};

/** Read a chosen file as the data URL the endpoints accept for both text and workbooks. */
smartprocess_logistics.read_file = function (file) {
	return new Promise((resolve, reject) => {
		const reader = new FileReader();

		reader.onload = () => resolve(reader.result);
		reader.onerror = () => reject(new Error(__("فایل خوانده نشد.")));
		reader.readAsDataURL(file);
	});
};

// --- the form -------------------------------------------------------------------------

frappe.ui.form.on("Sales Invoice", {
	refresh(frm) {
		if (frm.doc.docstatus === 2) {
			return; // A cancelled invoice takes no registrations.
		}

		smartprocess_logistics.settings().then((enabled) => {
			if (!enabled) {
				return; // The switch is documented as removing this button and nothing else.
			}

			return smartprocess_logistics.reaches_company(frm.doc.company).then((reaches) => {
				if (!reaches) {
					return;
				}

				if (smartprocess_logistics.allowed(smartprocess_logistics.ROLE.editor)) {
					frm.add_custom_button(
						__("ثبت سریال"),
						() => smartprocess_logistics.open_serial_dialog(frm),
						__("سریال‌ها")
					);
				}

				if (smartprocess_logistics.allowed(smartprocess_logistics.ROLE.viewer)) {
					frm.add_custom_button(
						__("جزئیات سریال‌ها"),
						() => smartprocess_logistics.open_details_dialog(frm),
						__("سریال‌ها")
					);
				}
			});
		});
	},
});

// --- shared drawing ------------------------------------------------------------------

/** The coverage summary chips, drawn from the server's own totals. */
smartprocess_logistics.summary_html = function (summary) {
	if (!summary) {
		return "";
	}

	const chips = [
		`<span class="sp-logistics-chip">${__("ردیف‌ها")}: ${summary.rows}</span>`,
		`<span class="sp-logistics-chip">${__("نیاز")}: ${summary.required}</span>`,
		`<span class="sp-logistics-chip">${__("ثبت‌شده")}: ${summary.registered}</span>`,
	];

	if (summary.shortage) {
		chips.push(
			`<span class="sp-logistics-chip is-short">${__("کسری")}: ${summary.shortage}</span>`
		);
	}

	if (summary.excess) {
		chips.push(
			`<span class="sp-logistics-chip is-short">${__("مازاد")}: ${summary.excess}</span>`
		);
	}

	if (summary.is_complete) {
		chips.push(
			`<span class="sp-logistics-chip is-complete">${__("کامل")}</span>`
		);
	}

	return `<div class="sp-logistics-summary">${chips.join("")}</div>`;
};

/** The rows table. The server sends the numbers; this only lays them out. */
smartprocess_logistics.rows_html = function (status) {
	const head = [
		__("ردیف"),
		__("کالا"),
		__("تعداد فاکتور"),
		__("نیاز"),
		__("ثبت‌شده"),
		__("کسری"),
		__("ثبت سریال"),
	];

	const body = (status.rows || [])
		.map((row) => {
			const state = row.is_complete
				? `<span class="sp-logistics-chip is-complete">${__("کامل")}</span>`
				: `<span class="sp-logistics-chip is-short">${row.shortage}</span>`;

			// The box starts with the serials the row already has, so correcting one of them is
			// an edit. The text is the server's, in registration order, and the value the box is
			// compared against before anything is written.
			const current = (row.serials || []).join(", ");

			return `<tr data-row-key="${frappe.utils.escape_html(row.row_key)}">
				<td>${row.row_index}</td>
				<td>${frappe.utils.escape_html(row.item_code)}</td>
				<td>${row.invoiced_qty}</td>
				<td>${row.required}</td>
				<td>${row.registered}</td>
				<td>${state}</td>
				<td>
					<input class="form-control input-xs sp-logistics-row-input"
						data-row-key="${frappe.utils.escape_html(row.row_key)}"
						data-current="${frappe.utils.escape_html(current)}"
						value="${frappe.utils.escape_html(current)}"
						placeholder="${__("شماره سریال‌ها، جدا شده با کاما یا نقطه‌ویرگول")}">
				</td>
			</tr>`;
		})
		.join("");

	if (!body) {
		return `<div class="sp-logistics-empty">${__("این فاکتور ردیف قابل ثبت ندارد.")}</div>`;
	}

	return `<table class="table table-bordered sp-logistics-rows">
			<thead><tr>${head.map((label) => `<th>${label}</th>`).join("")}</tr></thead>
			<tbody>${body}</tbody>
		</table>`;
};

/** The issues a preview reported, coloured by severity. */
smartprocess_logistics.issues_html = function (issues) {
	if (!issues || !issues.length) {
		return "";
	}

	const items = issues
		.map(
			(issue) => `<div class="sp-issue is-${issue.severity === "error" ? "error" : "warning"}">
				<div>${frappe.utils.escape_html(issue.message)}</div>
				<div class="sp-issue-code">${frappe.utils.escape_html(issue.code)}</div>
			</div>`
		)
		.join("");

	return `<div class="sp-logistics-issues">${items}</div>`;
};

// --- the serial registration dialog ----------------------------------------------------

smartprocess_logistics.open_serial_dialog = function (frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("ثبت سریال برای {0}", [frm.doc.name]),
		size: "large",
		fields: [{ fieldname: "body", fieldtype: "HTML" }],
		primary_action_label: __("بستن"),
		primary_action() {
			dialog.hide();
		},
	});

	dialog.$wrapper.addClass("sp-logistics-dialog");

	const state = { preview: null, session: null };

	const draw = function (status) {
		const preview_html = state.preview
			? `<div class="sp-logistics-summary">
					<span class="sp-logistics-chip">${__("پذیرفته‌شده")}: ${state.preview.accepted.length}</span>
					<span class="sp-logistics-chip">${__("ردیف‌ها")}: ${state.preview.candidates}</span>
				</div>
				${smartprocess_logistics.issues_html(state.preview.issues)}`
			: "";

		dialog.fields_dict.body.$wrapper.html(`
			<div class="sp-logistics-toolbar">
				${smartprocess_logistics.summary_html(status && status.summary)}
				<button class="btn btn-xs btn-default" data-action="refresh">
					${__("به‌روزرسانی")}
				</button>
			</div>
			${smartprocess_logistics.rows_html(status)}

			<hr>
			<div class="sp-logistics-toolbar">
				<div>
					<input type="file" class="sp-logistics-file" accept=".txt,.xlsx,.xlsm">
					<div class="sp-logistics-file-hint">
						${__("فایل متنی یا XLSX. پیش‌نمایش چیزی ذخیره نمی‌کند.")}
					</div>
				</div>
				<button class="btn btn-xs btn-default" data-action="preview">
					${__("بارگذاری و پیش‌نمایش")}
				</button>
				<button class="btn btn-xs btn-primary" data-action="confirm"
					${state.session ? "" : "disabled"}>
					${__("تأیید و ثبت")}
				</button>
			</div>
			${preview_html}
			<div class="sp-logistics-file-hint">
				${__("برای ثبت دستی، سریال‌های یک ردیف را در ستون «ثبت سریال» بنویسید.")}
			</div>
		`);
	};

	const load_status = function () {
		return smartprocess_logistics
			.call("get_invoice_serial_status", { sales_invoice: frm.doc.name })
			.then((status) => {
				draw(status);

				return status;
			});
	};

	const with_working = function (message, work) {
		frappe.dom.freeze(message);

		return work().finally(() => frappe.dom.unfreeze());
	};

	dialog.fields_dict.body.$wrapper.on("click", "[data-action]", function () {
		const action = $(this).attr("data-action");

		if (action === "refresh") {
			load_status();
		}

		if (action === "preview") {
			const input = dialog.fields_dict.body.$wrapper.find(".sp-logistics-file")[0];
			const file = input && input.files && input.files[0];

			if (!file) {
				frappe.msgprint(__("فایلی انتخاب نشده است."));

				return;
			}

			with_working(__("در حال خواندن فایل..."), () =>
				smartprocess_logistics
					.read_file(file)
					.then((content) =>
						smartprocess_logistics.call("prepare_import_preview", {
							sales_invoice: frm.doc.name,
							content: content,
							filename: file.name,
						})
					)
					.then((preview) => {
						state.preview = preview;
						state.session = preview.session || null;

						if (preview.requires_row_mapping_confirmation) {
							frappe.msgprint(
								__("نگاشت ردیف‌ها قطعی نیست؛ لطفاً پیش‌نمایش را بررسی کنید.")
							);
						}

						return load_status();
					})
					.catch(() => {})
			);
		}

		if (action === "confirm") {
			if (!state.session) {
				return;
			}

			with_working(__("در حال ثبت..."), () =>
				smartprocess_logistics
					.call("confirm_import", { token: state.session })
					.then((outcome) => {
						frappe.show_alert({
							indicator: "green",
							message: outcome.message || __("سریال‌ها ثبت شدند."),
						});

						state.preview = null;
						state.session = null;

						frm.reload_doc();

						return load_status();
					})
					.catch(() => {})
			);
		}
	});

	// Manual entry: the row's whole set is replaced, which is what the endpoint does. The box
	// arrives holding the serials the row already has, so one of them can be corrected without
	// retyping the rest, and the text goes to the server as it stands — which characters separate
	// serials is the job of the delimiters configured in `Logistics Settings`.
	const write_row = function (row_key, text) {
		with_working(__("در حال ثبت..."), () =>
			smartprocess_logistics
				.call("set_row_serials", {
					sales_invoice: frm.doc.name,
					sales_invoice_item: row_key,
					serials: text,
				})
				.then(() => {
					frappe.show_alert({ indicator: "green", message: __("ثبت شد.") });

					frm.reload_doc();

					return load_status();
				})
				.catch(() => load_status())
		);
	};

	dialog.fields_dict.body.$wrapper.on("change", ".sp-logistics-row-input", function () {
		const input = $(this);
		const row_key = input.attr("data-row-key");
		const current = input.attr("data-current") || "";
		const text = (input.val() || "").trim();

		// Blurring an unchanged box is not a request to write anything. Without this the prefill
		// would make every glance at a row a write of the set it already holds.
		if (text === current) {
			return;
		}

		// Emptying a row that has serials removes registrations, so it is asked about. The set is
		// replaced wholesale, which is what makes a correction possible and also what makes this
		// one keystroke destructive.
		if (!text && current) {
			frappe.confirm(
				__("همهٔ سریال‌های این ردیف حذف شوند؟"),
				() => write_row(row_key, text),
				() => load_status()
			);

			return;
		}

		write_row(row_key, text);
	});

	load_status().then(() => dialog.show());
};

// --- the invoice serial details dialog -------------------------------------------------

smartprocess_logistics.open_details_dialog = function (frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("سریال‌های {0}", [frm.doc.name]),
		size: "large",
		fields: [{ fieldname: "body", fieldtype: "HTML" }],
		primary_action_label: __("بستن"),
		primary_action() {
			dialog.hide();
		},
	});

	dialog.$wrapper.addClass("sp-logistics-dialog");

	const can_delete = smartprocess_logistics.allowed(smartprocess_logistics.ROLE.manager);

	const draw = function (status, page) {
		const rows = (page && page.assignments) || [];

		const table = rows.length
			? `<table class="table table-bordered">
					<thead><tr>
						<th>${__("کالا")}</th>
						<th>${__("سریال")}</th>
						${can_delete ? `<th>${__("حذف")}</th>` : ""}
					</tr></thead>
					<tbody>${rows
						.map(
							(row) => `<tr>
								<td>${frappe.utils.escape_html(row.item_code)}</td>
								<td>${frappe.utils.escape_html(row.serial_no || row.normalized_serial)}</td>
								${
									can_delete
										? `<td><input type="checkbox" class="sp-delete" value="${frappe.utils.escape_html(row.name)}"></td>`
										: ""
								}
							</tr>`
						)
						.join("")}</tbody>
				</table>`
			: `<div class="sp-logistics-empty">${__("سریالی ثبت نشده است.")}</div>`;

		dialog.fields_dict.body.$wrapper.html(`
			<div class="sp-logistics-toolbar">
				${smartprocess_logistics.summary_html(status && status.summary)}
				${
					can_delete
						? `<button class="btn btn-xs btn-danger" data-action="delete">
								${__("حذف انتخاب‌شده‌ها")}
							</button>`
						: ""
				}
			</div>
			${table}
		`);
	};

	const load = function () {
		return Promise.all([
			smartprocess_logistics.call("get_invoice_serial_status", {
				sales_invoice: frm.doc.name,
			}),
			smartprocess_logistics.call("get_serial_assignments", {
				sales_invoice: frm.doc.name,
				limit: 500,
			}),
		]).then(([status, page]) => draw(status, page));
	};

	dialog.fields_dict.body.$wrapper.on("click", "[data-action='delete']", function () {
		const names = dialog.fields_dict.body.$wrapper
			.find(".sp-delete:checked")
			.map(function () {
				return $(this).val();
			})
			.get();

		if (!names.length) {
			frappe.msgprint(__("هیچ ارجاعی انتخاب نشده است."));

			return;
		}

		frappe.confirm(__("سریال‌های انتخاب‌شده حذف شوند؟"), () => {
			frappe.dom.freeze(__("در حال حذف..."));

			smartprocess_logistics
				.call("delete_assignments", { names: names })
				.then(() => {
					frappe.show_alert({ indicator: "green", message: __("حذف شد.") });

					frm.reload_doc();

					return load();
				})
				.catch(() => {})
				.finally(() => frappe.dom.unfreeze());
		});
	});

	load().then(() => dialog.show());
};
