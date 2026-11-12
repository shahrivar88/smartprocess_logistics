"""Pure invoice-first purchase rules, independent of warehouse routing."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Problem:
	code: str
	message: str
	row: int | None = None


def stock_receipt_problems(rows, *, invoice_first=True):
	"""Every stock receipt row must name its submitted Purchase Invoice.

	`rows` snapshots: idx, item_code, is_stock_item, is_managed, has_serial_no, qty,
	purchase_invoice. Non-stock and zero-quantity rows are left to ERPNext. A return keeps
	ERPNext's standard return rules and is skipped by the caller.

	The custom sales-only serial mechanism is unchanged: a managed item that is also standard
	serial-controlled (`has_serial_no = 1`) is refused, because purchasing must not generate
	sales serials. An unmanaged standard-serial item remains ERPNext's normal serial workflow.
	"""
	problems = []

	for row in rows:
		if not row.get("is_stock_item"):
			continue

		idx = row.get("idx")
		rejected_qty = abs(float(row.get("rejected_qty") or 0))

		if rejected_qty > 0:
			if not row.get("rejected_warehouse"):
				problems.append(
					Problem(
						"PR_REJECTED_WAREHOUSE_REQUIRED",
						f"ردیف {idx}: برای مقدار ردشده باید انبار ردشده انتخاب شود.",
						idx,
					)
				)
			elif row.get("rejected_warehouse_kind") != "virtual":
				problems.append(
					Problem(
						"PR_REJECTED_WAREHOUSE_NOT_VIRTUAL",
						f"ردیف {idx}: مقدار ردشده فقط به انبار با نوع Virtual مجاز است.",
						idx,
					)
				)

		if not invoice_first:
			continue

		if abs(float(row.get("qty") or 0)) <= 0:
			continue

		if not row.get("purchase_invoice"):
			problems.append(
				Problem(
					"PR_PURCHASE_INVOICE_REQUIRED",
					f"ردیف {idx}: رسید خرید کالای انباری باید به یک فاکتور خرید ثبت‌شده وصل باشد؛ "
					"رسید بدون فاکتور در سیاست خرید invoice-first مجاز نیست.",
					idx,
				)
			)
			continue

		if row.get("purchase_invoice_docstatus") != 1:
			problems.append(
				Problem(
					"PR_PURCHASE_INVOICE_NOT_SUBMITTED",
					f"ردیف {idx}: فاکتور خرید مرجع باید ثبت‌شده باشد؛ فاکتور پیش‌نویس یا لغوشده "
					"برای رسید خرید invoice-first معتبر نیست.",
					idx,
				)
			)
			continue

		if row.get("is_managed") and row.get("has_serial_no"):
			problems.append(
				Problem(
					"PR_SERIAL_PURCHASE_UNSUPPORTED",
					f"ردیف {idx}: کالای مدیریت‌شده در ERPNext سریال‌دار است. "
					"در جریان خرید سریال گرفته نمی‌شود؛ برای این کالا باید has_serial_no=0 باشد "
					"یا مالک تنظیمات کالا را جداگانه تأیید کند.",
					idx,
				)
			)

	return tuple(problems)


def invoice_cancel_problems(receipts, company, shown_limit=10):
	"""A submitted invoice cannot be cancelled while a submitted receipt still names it.

	`receipts` snapshots every Purchase Receipt that references the invoice through either
	item-level link or a header link a site added: `name`, `company` and `docstatus`. Only a
	submitted receipt blocks. A draft receipt has no stock or ledger effect to unwind, and a
	cancelled receipt no longer depends on the invoice. A return is a receipt too: a submitted
	purchase return keeps its rows pointed at the invoice's own item rows.

	A receipt of another company never blocks: it is not a dependency of this invoice, and
	naming it would disclose another company's document. A Link that crosses companies is
	still refused by the framework's back-link check when the invoice is cancelled.
	"""
	blocking = sorted(
		{
			row.get("name")
			for row in receipts
			if row.get("docstatus") == 1
			and row.get("name")
			and (not company or row.get("company") == company)
		}
	)
	if not blocking:
		return ()

	shown = "، ".join(blocking[:shown_limit])
	if len(blocking) > shown_limit:
		shown = f"{shown} و {len(blocking) - shown_limit} رسید دیگر"

	return (
		Problem(
			"PI_CANCEL_BLOCKED_BY_PURCHASE_RECEIPT",
			f"فاکتور خرید لغو نمی‌شود: {len(blocking)} رسید خرید ثبت‌شده به آن وابسته است ({shown}). "
			"ابتدا رسیدهای خرید را لغو کنید، سپس فاکتور خرید را لغو کنید.",
		),
	)
