"""The rules that keep the invoice-first logistics workflow inside ERPNext's own accounting.

Everything here is a pure function over plain values: no Frappe import, no database, no site.
`smartprocess_logistics/integrity.py` reads the documents and calls these; the tests call them
directly. The split is the one the rest of the app keeps.

Scope
-----
Most rules are scoped to a **logistics-managed** document: at least one Item row names an Item
whose `sp_serial_required` flag is on. That flag already exists on every site this app runs on,
so those rules need no custom field, no DocType and no migration. A document with no marked row
is left to ERPNext.

The Purchase Invoice rule is deliberately broader. It applies to **every domestic and foreign
stock purchase**, whether or not the item is marked, and is decided from the Item's
`is_stock_item`/`is_fixed_asset` fields. It refuses a Purchase Invoice that would post
inventory; the Purchase Receipt is the inventory movement.

The rules do not post, reverse or adjust anything. They refuse a save that would make ERPNext
post twice, value stock at the wrong rate, or link a row to the wrong invoice. When they allow a
save, ERPNext's own controller does the posting exactly as it does without this app.
"""

from dataclasses import dataclass

# Conversion rates carry nine decimals in Frappe. A difference smaller than this is the same
# rate written two ways, not a different market rate.
RATE_TOLERANCE = 1e-6

# Stock value per stock unit is money in the company currency. A cent is the smallest difference
# worth refusing; anything smaller is rounding in a two-decimal ledger.
VALUE_TOLERANCE = 0.01


@dataclass(frozen=True)
class Problem:
	"""One refusal, in the language the owner will see, with a stable code for the tests."""

	code: str
	message: str
	row: int | None = None


def is_managed(item_flags):
	"""Whether a document is in this app's workflow: any row's Item is marked."""
	return any(bool(flag) for flag in item_flags)


def sales_invoice_problems(update_stock, docstatus, managed):
	"""A managed Sales Invoice is a request to deliver, not a stock movement.

	The rule is about documents that have not been submitted (`docstatus = 0`), so a historical
	submitted invoice is never inspected and never changed. It applies on every draft save, not
	only on insert, because a draft can be saved once with the box on and submitted later.
	"""
	if docstatus != 0 or not managed or not update_stock:
		return ()

	return (
		Problem(
			"SI_UPDATE_STOCK",
			"این فاکتور کالای نیازمند سریال دارد؛ در جریان لجستیک، «به‌روزرسانی موجودی» باید خاموش باشد. "
			"کسر انبار فقط با یادداشت تحویل انجام می‌شود.",
		),
	)


def purchase_invoice_problems(
	update_stock,
	docstatus,
	rows,
	is_return=False,
	is_opening=False,
	is_internal_transfer=False,
):
	"""An inventory Purchase Invoice is a bill, not a receipt.

	The rule applies to every domestic and foreign stock purchase, not only to items marked
	`sp_serial_required`. It refuses `update_stock = 1` when a stock row with a non-zero quantity
	would post inventory; the goods must arrive through a standard Purchase Receipt.

	Opening entries, internal inter-company transfers and fixed-asset rows keep their standard
	ERPNext workflow. Service and non-stock rows never move inventory and are left alone.

	A Purchase Invoice Return / Debit Note may be posted without stock (`update_stock = 0`);
	a stock-affecting return must use a Purchase Receipt Return. The return flag is therefore
	not a blanket exception.
	"""
	if is_opening or is_internal_transfer or docstatus != 0 or not update_stock:
		return ()

	for row in rows:
		if not row.get("is_stock_item") or row.get("is_fixed_asset"):
			continue
		if abs(float(row.get("qty") or 0)) <= 0:
			continue

		idx = row.get("idx")
		where = f"ردیف {idx}: " if idx else ""
		if is_return:
			return (
				Problem(
					"PI_UPDATE_STOCK",
					f"{where}برگشت فاکتور خرید / Debit Note نباید موجودی را تغییر دهد. "
					"برگشت موجودی فقط با «رسید خرید برگشتی» انجام می‌شود.",
					idx,
				),
			)

		return (
			Problem(
				"PI_UPDATE_STOCK",
				f"{where}فاکتور خرید باید بدون «به‌روزرسانی موجودی» ثبت شود. "
				"موجودی فقط با رسید خرید افزایش می‌یابد؛ این قاعده برای خرید داخلی و خارجی یکسان است.",
				idx,
			),
		)

	return ()


def delivery_identity_problems(company, row):
	"""The row-link, item, invoice and company checks that do not depend on Commitment.

	Returns the problems and whether the caller must stop checking this row. The first three
	cases — no invoice row, no invoice reference, or a reference that does not resolve — leave
	nothing further to judge, which is why the original validator continued to the next row.
	"""
	idx = row.get("idx")
	problems = []

	if not row.get("si_detail"):
		if row.get("against_sales_invoice"):
			problems.append(
				Problem(
					"DN_DETAIL_MISSING",
					f"ردیف {idx}: «فاکتور فروش مرجع» انتخاب شده اما ردیف فاکتور مشخص نیست.",
					idx,
				)
			)
		else:
			problems.append(
				Problem(
					"DN_NOT_LINKED",
					f"ردیف {idx}: هر ردیف یادداشت تحویل مدیریت‌شده باید به ردیف فاکتور فروش ثبت‌شده وصل باشد.",
					idx,
				)
			)
		return problems, True

	if not row.get("against_sales_invoice"):
		problems.append(
			Problem(
				"DN_INVOICE_MISSING",
				f"ردیف {idx}: ردیف فاکتور انتخاب شده اما فاکتور فروش مرجع خالی است.",
				idx,
			)
		)
		return problems, True

	if row.get("si_item_parent") is None:
		problems.append(
			Problem(
				"DN_DETAIL_NOT_FOUND",
				f"ردیف {idx}: ردیف فاکتور انتخاب‌شده وجود ندارد.",
				idx,
			)
		)
		return problems, True

	if row.get("si_item_parent") != row.get("against_sales_invoice"):
		problems.append(
			Problem(
				"DN_CROSS_INVOICE",
				f"ردیف {idx}: ردیف فاکتور انتخاب‌شده به فاکتور مرجع تعلق ندارد.",
				idx,
			)
		)

	if row.get("si_item_code") != row.get("item_code"):
		problems.append(
			Problem(
				"DN_ITEM_MISMATCH",
				f"ردیف {idx}: کالای ردیف با کالای ردیف فاکتور یکسان نیست.",
				idx,
			)
		)

	if row.get("si_docstatus") != 1:
		problems.append(
			Problem(
				"DN_INVOICE_NOT_SUBMITTED",
				f"ردیف {idx}: فاکتور فروش مرجع ثبت‌نشده یا لغوشده است.",
				idx,
			)
		)

	if row.get("si_company") != company:
		problems.append(
			Problem(
				"DN_COMPANY_MISMATCH",
				f"ردیف {idx}: فاکتور فروش و یادداشت تحویل از دو شرکت متفاوت‌اند.",
				idx,
			)
		)

	if row.get("warehouse_company") and row["warehouse_company"] != company:
		problems.append(
			Problem(
				"DN_WAREHOUSE_COMPANY",
				f"ردیف {idx}: انبار از شرکت سند نیست.",
				idx,
			)
		)

	if row.get("target_warehouse_company") and row["target_warehouse_company"] != company:
		problems.append(
			Problem(
				"DN_TARGET_WAREHOUSE_COMPANY",
				f"ردیف {idx}: انبار مقصد از شرکت سند نیست.",
				idx,
			)
		)

	return problems, False


def delivery_source_problem(row):
	"""The approved stock-row source check, independent of a committed warehouse."""
	if row.get("is_stock_item") and not row.get("warehouse"):
		return Problem(
			"DN_SOURCE_WAREHOUSE_MISSING",
			f"ردیف {row.get('idx')}: انبار منبع برای کالای انباری مشخص نیست.",
			row.get("idx"),
		)
	return None


def delivery_second_deduction_problem(is_return, row):
	"""A managed invoice that already moved stock must not be deducted by a Delivery Note again."""
	if not is_return and row.get("si_update_stock") == 1 and row.get("si_managed"):
		return Problem(
			"DN_SECOND_DEDUCTION",
			f"ردیف {row.get('idx')}: این فاکتور فروش قبلاً موجودی را کسر کرده است؛ "
			"ثبت یادداشت تحویل یک کسر دوم می‌سازد.",
			row.get("idx"),
		)
	return None


def delivery_note_problems(company, is_return, rows, managed):
	"""The approved managed-Delivery-Note rule (I30), independent of the committed warehouse.

	This is the half of the original validator that does not read a commitment: every managed
	row names its own submitted invoice row, item, company and warehouse company; a stock row
	names a source; and a managed invoice that already moved stock is not deducted twice. The
	committed-warehouse checks are not part of this release and are added only by the
	development-only extension.
	"""
	if not managed or is_return:
		return ()

	problems = []

	for row in rows:
		identity, stop = delivery_identity_problems(company, row)
		problems.extend(identity)
		if stop:
			continue

		source = delivery_source_problem(row)
		if source:
			problems.append(source)

		second = delivery_second_deduction_problem(is_return, row)
		if second:
			problems.append(second)

	return tuple(problems)


def purchase_receipt_problems(header, rows, is_return=False):
	"""A linked stock Purchase Receipt keeps the invoice's identity, rate and item link.

	`header` carries the Purchase Receipt's company, currency, supplier and conversion rate.
	Each row is a snapshot carrying the Purchase Receipt row and the linked Purchase Invoice row
	and header. The comparison is on the **stock-value rate** (rate per stock unit in company
	currency), so a receipt in a different UOM is allowed when its conversion makes the same
	value; a different item rate is not.

	This runs for every linked stock row, not only `sp_serial_required` rows. Non-stock rows are
	left to ERPNext. A return is a reversal, not a second receipt: the rate, value, link and
	company checks stay in force for a return.

	`purchase_receipt_rate_set_problems` is the companion for the document-level question: a
	receipt has one conversion rate, so linked invoices with different rates cannot all keep
	their own.
	"""
	problems = []

	for row in rows:
		if not row.get("is_stock_item"):
			continue
		if not row.get("purchase_invoice"):
			continue

		idx = row.get("idx")

		if not row.get("purchase_invoice_item"):
			problems.append(
				Problem(
					"PR_MISSING_PI_ITEM",
					f"ردیف {idx}: ردیف فاکتور خرید مرجع مشخص نیست.",
					idx,
				)
			)
			continue

		if row.get("pi_item_parent") is None:
			problems.append(
				Problem(
					"PR_PI_ITEM_NOT_FOUND",
					f"ردیف {idx}: ردیف فاکتور خرید انتخاب‌شده وجود ندارد.",
					idx,
				)
			)
			continue

		if row.get("pi_item_parent") != row.get("purchase_invoice"):
			problems.append(
				Problem(
					"PR_CROSS_INVOICE",
					f"ردیف {idx}: ردیف فاکتور خرید به فاکتور مرجع تعلق ندارد.",
					idx,
				)
			)

		if row.get("pi_item_code") != row.get("item_code"):
			problems.append(
				Problem(
					"PR_ITEM_MISMATCH",
					f"ردیف {idx}: کالای رسید با کالای ردیف فاکتور خرید یکسان نیست.",
					idx,
				)
			)

		if row.get("pi_item_uom") and row.get("uom") != row.get("pi_item_uom"):
			problems.append(
				Problem(
					"PR_UOM_MISMATCH",
					f"ردیف {idx}: واحد رسید باید با واحد فاکتور خرید یکسان باشد "
					f"({row.get('pi_item_uom')} در برابر {row.get('uom')}).",
					idx,
				)
			)

		if (
			row.get("pi_item_conversion_factor")
			and row.get("conversion_factor")
			and abs(float(row.get("pi_item_conversion_factor")) - float(row.get("conversion_factor"))) > 1e-9
		):
			problems.append(
				Problem(
					"PR_UOM_MISMATCH",
					f"ردیف {idx}: ضریب تبدیل واحد رسید باید با فاکتور خرید یکسان باشد.",
					idx,
				)
			)

		if row.get("pi_docstatus") != 1:
			problems.append(
				Problem(
					"PR_PI_NOT_SUBMITTED",
					f"ردیف {idx}: فاکتور خرید مرجع ثبت‌نشده یا لغوشده است.",
					idx,
				)
			)

		if row.get("pi_company") != header.get("company"):
			problems.append(
				Problem(
					"PR_COMPANY_MISMATCH",
					f"ردیف {idx}: فاکتور خرید و رسید خرید از دو شرکت متفاوت‌اند.",
					idx,
				)
			)

		if row.get("pi_currency") != header.get("currency"):
			problems.append(
				Problem(
					"PR_CURRENCY_MISMATCH",
					f"ردیف {idx}: ارز فاکتور خرید و رسید خرید یکسان نیست.",
					idx,
				)
			)

		if row.get("pi_supplier") != header.get("supplier"):
			problems.append(
				Problem(
					"PR_SUPPLIER_MISMATCH",
					f"ردیف {idx}: تأمین‌کنندهٔ فاکتور خرید و رسید خرید یکسان نیست.",
					idx,
				)
			)

		if row.get("pi_update_stock") == 1 and not is_return:
			problems.append(
				Problem(
					"PR_PI_UPDATED_STOCK",
					f"ردیف {idx}: فاکتور خرید مرجع قبلاً اثر انبار داشته است؛ "
					"رسید خرید یک کسر/ورود دوم می‌سازد.",
					idx,
				)
			)

		pi_rate = row.get("pi_conversion_rate")
		pr_rate = header.get("conversion_rate")

		if pi_rate is None or pr_rate is None:
			problems.append(
				Problem(
					"PR_RATE_MISSING",
					f"ردیف {idx}: نرخ تبدیل فاکتور یا رسید خرید خالی است.",
					idx,
				)
			)
		elif abs(pr_rate - pi_rate) > RATE_TOLERANCE:
			problems.append(
				Problem(
					"PR_RATE_MISMATCH",
					f"ردیف {idx}: نرخ تبدیل رسید خرید باید برابر نرخ فاکتور خرید باشد "
					f"({pi_rate} در برابر {pr_rate}).",
					idx,
				)
			)

		pi_factor = row.get("pi_item_conversion_factor") or 0
		pr_factor = row.get("conversion_factor") or 0

		if not pi_factor or not pr_factor:
			problems.append(
				Problem(
					"PR_UOM_INVALID",
					f"ردیف {idx}: ضریب تبدیل واحد کالا صفر یا خالی است.",
					idx,
				)
			)
		else:
			pi_stock_rate = abs(row.get("pi_item_rate") or 0) * pi_rate / pi_factor
			pr_stock_rate = abs(row.get("rate") or 0) * pr_rate / pr_factor
			tolerance = max(VALUE_TOLERANCE, abs(pi_stock_rate) * 1e-6)

			if abs(pi_stock_rate - pr_stock_rate) > tolerance:
				problems.append(
					Problem(
						"PR_VALUE_MISMATCH",
						f"ردیف {idx}: ارزش هر واحد انباری در رسید با فاکتور یکسان نیست (نرخ/UOM).",
						idx,
					)
				)

	return tuple(problems)


def purchase_receipt_rate_set_problems(linked_invoices):
	"""All invoices on one receipt must share a currency and a conversion rate.

	A Purchase Receipt has one `conversion_rate`; two invoices with different rates cannot both
	keep their own. Splitting the receipt is the safe answer. Two invoices with the **same**
	rate remain allowed, so a multi-invoice shipment is not prohibited.
	"""
	rates = set()

	for invoice in linked_invoices:
		if invoice.get("conversion_rate") is None:
			continue
		rates.add((invoice.get("currency"), round(float(invoice["conversion_rate"]), 9)))

	if len(rates) > 1:
		return (
			Problem(
				"PR_MIXED_RATES",
				"این رسید خرید چند فاکتور با ارز یا نرخ تبدیل متفاوت دارد؛ "
				"هر فاکتور را در رسید خرید جداگانه ثبت کنید.",
			),
		)

	return ()


def purchase_return_link_plan(return_rows, original_items, pi_items):
	"""Fill the missing `purchase_invoice_item` on an invoice-first Purchase Receipt return.

	ERPNext's own return mapper copies `purchase_invoice` but not `purchase_invoice_item`; the
	submission then fails in `make_stock_received_but_not_billed_entry` with a `KeyError`. The
	missing value is a link the original receipt already holds, so filling it changes no amount,
	no quantity and no ledger entry.

	Matching order: the original row named on the return row, then the only original row of the
	same item, then the row at the same position. If the chosen original row itself has no
	`purchase_invoice_item`, the unique invoice row of the same item is used; a missing or
	ambiguous invoice row is refused rather than guessed. The `name` of a Purchase Receipt Item
	is never written into the Purchase Invoice Item field.

	Returns `(plans, problems)`. Each plan is `{"idx", "purchase_invoice",
	"purchase_invoice_item"}` and is applied by the caller before ERPNext validates the return.
	"""
	plans = []
	problems = []

	for row in return_rows:
		if row.get("purchase_invoice_item"):
			continue

		idx = row.get("idx")
		candidates = [item for item in original_items if item.get("item_code") == row.get("item_code")]

		chosen = None
		named = row.get("purchase_receipt_item")

		if named:
			chosen = next((item for item in candidates if item.get("name") == named), None)

		if chosen is None and len(candidates) == 1:
			chosen = candidates[0]

		if chosen is None and candidates:
			at_position = [item for item in candidates if item.get("idx") == idx]
			if len(at_position) == 1:
				chosen = at_position[0]

		pi_name = (chosen or {}).get("purchase_invoice") or row.get("purchase_invoice")
		if not pi_name:
			# Neither the return row nor its original names a Purchase Invoice: this is a
			# standard, unmanaged return. It has no invoice link to fill and must stay with
			# ERPNext's own return workflow.
			continue

		pi_item = (chosen or {}).get("purchase_invoice_item")
		if not pi_item:
			items = pi_items.get(pi_name, [])
			matching = [item for item in items if item.get("item_code") == row.get("item_code")]
			if len(matching) != 1:
				problems.append(
					Problem(
						"RETURN_LINK_AMBIGUOUS",
						f"ردیف {idx}: ردیف فاکتور خرید متناظر برای برگشت پیدا نشد یا مبهم است.",
						idx,
					)
				)
				continue
			pi_item = matching[0].get("name")

		plans.append(
			{
				"idx": idx,
				"purchase_invoice": pi_name,
				"purchase_invoice_item": pi_item,
			}
		)

	return plans, tuple(problems)
