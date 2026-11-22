// Report defaults and display, shared by the eight reports this app ships.
//
// The company and the date window come from the server, which is the only place that knows the
// reader's permitted companies and the site's own `Fiscal Year` — including a Jalali one, whose
// real start date is what the default must be. Nothing here is a rule.
//
// A default is written only into a filter the reader has left empty, and only while the report
// is opening. A value the reader typed is never replaced, and an answer that arrives after they
// started typing is dropped rather than applied over them.
//
// The same file marks the report page so this app's stylesheet can style its data without
// reaching into another app's report, and formats quantity columns: a whole number carries no
// decimal places, and a fractional one keeps every digit it really has. That is display only —
// no stored quantity is rounded or rewritten.

frappe.provide("smartprocess_logistics.report_defaults");

(function () {
	const METHOD = "smartprocess_logistics.api.endpoints.report_filter_defaults";
	const PAGE_CLASS = "sp-logistics-report";
	const DEFAULTED = ["company", "from_date", "to_date"];

	function has_value(value) {
		return value !== null && value !== undefined && value !== "";
	}

	function missing_filters(report) {
		return DEFAULTED.filter(function (fieldname) {
			return report.get_filter(fieldname) && !has_value(report.get_filter_value(fieldname));
		});
	}

	function apply(report) {
		if (!report || !report.page || !report.page.wrapper) {
			return;
		}

		report.page.wrapper.addClass(PAGE_CLASS);

		const missing = missing_filters(report);
		if (!missing.length) {
			return;
		}

		frappe
			.xcall(METHOD, {}, "GET")
			.then(function (defaults) {
				missing.forEach(function (fieldname) {
					const value = defaults && defaults[fieldname];

					// The reader may have filled this filter while the answer was on its way.
					if (value && !has_value(report.get_filter_value(fieldname))) {
						report.set_filter_value(fieldname, value);
					}
				});
			})
			.catch(function () {
				// No default is not a failure the reader can act on: the filters stay empty.
			});
	}

	function decimals_of(number) {
		const text = String(number);
		const parts = text.split(".");

		return parts.length > 1 ? Math.min(parts[1].length, 6) : 0;
	}

	// A quantity is shown as the number it is. `5.000` is `5`; `1.5` stays `1.5`, and a value
	// with more real digits keeps all of them. Nothing is rounded, and nothing is padded.
	function quantity(value, row, column, data, default_formatter) {
		if (!has_value(value)) {
			return "";
		}

		const number = Number(value);
		if (!Number.isFinite(number) || /[eE]/.test(String(value))) {
			return default_formatter(value, row, column, data);
		}

		return frappe.format(number, { fieldtype: "Float", precision: decimals_of(number) });
	}

	smartprocess_logistics.report_defaults.apply = apply;
	smartprocess_logistics.report_defaults.quantity = quantity;
})();
