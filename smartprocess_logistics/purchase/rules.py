"""Pure invoice-first purchase rules, independent of warehouse routing."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Problem:
	code: str
	message: str
	row: int | None = None


def stock_receipt_problems(rows):
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
		if not row.get("is_stock_item") or abs(float(row.get("qty") or 0)) <= 0:
			continue

		idx = row.get("idx")

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
