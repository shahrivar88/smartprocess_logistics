"""Accounting receipt and physical arrival for one Purchase Invoice Item row.

ERPNext already answers "how much of this line has been received". The Purchase Receipt status
updater rewrites `Purchase Invoice Item.received_qty` on every submit and cancel as the sum of
the linked `Purchase Receipt Item.received_qty` where `docstatus = 1`. This module reads that
same figure, so the report cannot disagree with the document's own status, and computes the one
ERPNext does not: how much of it physically arrived.

Those are different questions. A receipt into a transit or a virtual warehouse is an accounting
receipt and not goods on the floor. A report that shows one number for both either counts goods
still with the carrier as sellable stock or hides stock that was really received. Where the
linked rows cannot be attributed to this line exactly, the physical answer is `None` —
«قابل تعیین نیست» — and never zero.

Two choices worth stating, because both could be made the other way:

- The accounting receipt is `received_qty`, but the physical arrival counts `qty`. ERPNext
  adds rejected units to `received_qty` (`received_qty = qty + rejected_qty`) while those units
  never become stock in the target warehouse. The rejection is reported as a note, so the
  difference between the two columns is explained rather than hidden.
- A Purchase Invoice that sets Update Stock receives its own quantity; there is no separate
  receipt row to attribute, and its warehouse's classification is what decides.

`Bin` is never consulted. A Bin balance is a company-wide total and says nothing about which
invoice row a unit arrived against.

No Frappe import.
"""

from dataclasses import dataclass
from decimal import Decimal

from smartprocess_logistics.core.inventory import ZERO, as_decimal, max_zero, quantity
from smartprocess_logistics.warehouse.policy import PHYSICAL, TRANSIT

ONE = Decimal("1")

# Stock quantities are stored and compared at six places; a smaller difference is rounding.
TOLERANCE = Decimal("0.000000001")

NOT_RECEIVED = "not_received"
PARTIALLY_RECEIVED = "partially_received"
FULLY_RECEIVED = "fully_received"
OVER_RECEIVED = "over_received"
PURCHASE_RETURN = "purchase_return"

STATUSES = (NOT_RECEIVED, PARTIALLY_RECEIVED, FULLY_RECEIVED, OVER_RECEIVED, PURCHASE_RETURN)

# "Outstanding" is everything that still owes goods, which is the report's default.
OUTSTANDING = (NOT_RECEIVED, PARTIALLY_RECEIVED)

STATUS_LABELS = {
	NOT_RECEIVED: "دریافت‌نشده",
	PARTIALLY_RECEIVED: "دریافت ناقص",
	FULLY_RECEIVED: "دریافت کامل",
	OVER_RECEIVED: "بیش از مقدار خرید",
	PURCHASE_RETURN: "برگشت خرید",
}

# The report's receipt-status filter: the value sent to the server, and the statuses it keeps.
# The values are stable codes rather than the Persian labels, so a label can be reworded without
# moving a filter or breaking a saved bookmark.
FILTER_ALL = "all"
FILTER_OUTSTANDING = "outstanding"
FILTER_NOT_RECEIVED = NOT_RECEIVED
FILTER_PARTIALLY_RECEIVED = PARTIALLY_RECEIVED
FILTER_FULLY_RECEIVED = FULLY_RECEIVED
FILTER_OVER_RECEIVED = OVER_RECEIVED
FILTER_PURCHASE_RETURN = PURCHASE_RETURN

STATUS_FILTERS = {
	FILTER_ALL: STATUSES,
	FILTER_OUTSTANDING: OUTSTANDING,
	FILTER_NOT_RECEIVED: (NOT_RECEIVED,),
	FILTER_PARTIALLY_RECEIVED: (PARTIALLY_RECEIVED,),
	FILTER_FULLY_RECEIVED: (FULLY_RECEIVED,),
	FILTER_OVER_RECEIVED: (OVER_RECEIVED,),
	FILTER_PURCHASE_RETURN: (PURCHASE_RETURN,),
}

# Why a row needs a second look. The code is what a test asserts; the label is what the operator
# reads. Every code that `evaluate` can return has a label, and a test holds the two sets equal.
NOTE_UOM_MISMATCH = "uom_mismatch"
NOTE_UNKNOWN_WAREHOUSE = "unknown_warehouse"
NOTE_TRANSIT = "transit"
NOTE_VIRTUAL = "virtual"
NOTE_NO_PHYSICAL = "no_physical"
NOTE_STORED_DISAGREES = "stored_disagrees"
NOTE_INVOICE_STOCK = "invoice_stock"
NOTE_REJECTED = "rejected"
NOTE_RETURNED = "returned"
NOTE_CANCELLED = "cancelled"
NOTE_DRAFT = "draft"

NOTE_LABELS = {
	NOTE_UOM_MISMATCH: "واحد یا ضریب تبدیل ردیف رسید با ردیف فاکتور یکی نیست؛ ورود فیزیکی قابل تعیین نیست.",
	NOTE_UNKNOWN_WAREHOUSE: "انبار ردیف رسید در دسترس نیست؛ ورود فیزیکی قابل تعیین نیست.",
	NOTE_TRANSIT: "بخشی از دریافت در انبار در راه ثبت شده است و موجودی قابل فروش نیست.",
	NOTE_VIRTUAL: "بخشی از دریافت در انبار مجازی ثبت شده است و موجودی قابل فروش نیست.",
	NOTE_NO_PHYSICAL: "دریافت حسابداری ثبت شده است ولی کالا به انبار فیزیکی نرسیده است.",
	NOTE_STORED_DISAGREES: "مقدار دریافت‌شدهٔ ثبت‌شده با جمع رسیدهای پیوندی برابر نیست.",
	NOTE_INVOICE_STOCK: "این فاکتور خودش موجودی را ثبت کرده است و ردیف رسید جداگانه ندارد.",
	NOTE_REJECTED: "در رسید پیوندی، مقدار مردود ثبت شده است و در ورود فیزیکی نیامده است.",
	NOTE_RETURNED: "رسید برگشتی روی این ردیف ثبت شده است.",
	NOTE_CANCELLED: "رسید لغوشده روی این ردیف وجود دارد و در محاسبه نیامده است.",
	NOTE_DRAFT: "رسید پیش‌نویس روی این ردیف وجود دارد و در محاسبه نیامده است.",
}

# The status of a return line is decided by the document, not by comparing quantities.
_SUBMITTED = 1


@dataclass(frozen=True)
class ReceiptRow:
	"""One `Purchase Receipt Item` row linked to the invoice row, as the report reads it.

	`received_qty` is the figure ERPNext sums into the invoice row; `qty` is the accepted part
	that becomes stock in `warehouse`; `rejected_qty` never does. `classification` is the
	three-way answer for the row's warehouse, or `None` when the warehouse could not be resolved.
	"""

	qty: Decimal = ZERO
	rejected_qty: Decimal = ZERO
	received_qty: Decimal = ZERO
	uom: str = ""
	conversion_factor: Decimal = ONE
	classification: str | None = PHYSICAL
	docstatus: int = _SUBMITTED
	is_return: bool = False


@dataclass(frozen=True)
class InvoiceRow:
	"""One `Purchase Invoice Item` row and every receipt row ERPNext links to it."""

	purchased_qty: Decimal = ZERO
	received_qty: Decimal = ZERO
	uom: str = ""
	conversion_factor: Decimal = ONE
	is_return: bool = False
	update_stock: bool = False
	warehouse_classification: str | None = None
	receipts: tuple[ReceiptRow, ...] = ()
	cancelled_receipts: int = 0
	draft_receipts: int = 0


@dataclass(frozen=True)
class Receipt:
	"""What the report shows for one invoice row."""

	status: str
	purchased: Decimal
	received: Decimal
	outstanding: Decimal
	physical: Decimal | None
	notes: tuple[str, ...] = ()


def evaluate(line):
	"""The receipt status, the outstanding quantity and the physical arrival of one line."""
	notes = []
	purchased = quantity(line.purchased_qty, "purchased_qty")
	received = purchased if line.update_stock else quantity(line.received_qty, "received_qty")

	if line.update_stock:
		notes.append(NOTE_INVOICE_STOCK)

	physical = _physical(line, notes)
	if physical == ZERO and received > ZERO:
		notes.append(NOTE_NO_PHYSICAL)

	submitted = [row for row in line.receipts if row.docstatus == _SUBMITTED]
	if not line.update_stock and _disagrees(line, received, submitted):
		notes.append(NOTE_STORED_DISAGREES)
	if any(as_decimal(row.rejected_qty, "rejected_qty") > ZERO for row in submitted):
		notes.append(NOTE_REJECTED)
	if any(row.is_return for row in submitted):
		notes.append(NOTE_RETURNED)
	if line.cancelled_receipts:
		notes.append(NOTE_CANCELLED)
	if line.draft_receipts:
		notes.append(NOTE_DRAFT)

	return Receipt(
		status=_status(purchased, received, line.is_return),
		purchased=purchased,
		received=received,
		outstanding=ZERO if line.is_return else quantity(max_zero(purchased - received)),
		physical=physical,
		notes=tuple(dict.fromkeys(notes)),
	)


def _status(purchased, received, is_return):
	if is_return:
		return PURCHASE_RETURN
	if received <= TOLERANCE:
		return NOT_RECEIVED
	if received < purchased - TOLERANCE:
		return PARTIALLY_RECEIVED
	if received > purchased + TOLERANCE:
		return OVER_RECEIVED

	return FULLY_RECEIVED


def _same_unit(line_uom, line_factor, row):
	"""Whether a receipt row's quantities may be compared with the invoice row's at all."""
	if not str(row.uom or "").strip() or not str(line_uom or "").strip():
		return False

	return str(row.uom).strip() == str(line_uom).strip() and as_decimal(
		row.conversion_factor, "conversion_factor"
	) == as_decimal(line_factor, "conversion_factor")


def _physical(line, notes):
	"""The quantity that arrived in a warehouse a sale can be served from, or None.

	None means the linked rows exist but cannot be attributed to this line exactly. Returning
	zero there would report a false shortage of goods that may be on the floor, so the answer is
	"cannot be determined" and the reason is noted.
	"""
	if line.update_stock:
		if line.warehouse_classification is None:
			notes.append(NOTE_UNKNOWN_WAREHOUSE)
			return None

		return _classified(line.warehouse_classification, line.purchased_qty, notes)

	submitted = [row for row in line.receipts if row.docstatus == _SUBMITTED]
	if not submitted:
		return ZERO

	total = ZERO
	for row in submitted:
		if row.classification is None:
			notes.append(NOTE_UNKNOWN_WAREHOUSE)
			return None
		if not _same_unit(line.uom, line.conversion_factor, row):
			notes.append(NOTE_UOM_MISMATCH)
			return None

		total += _classified(row.classification, as_decimal(row.qty, "qty"), notes)

	return quantity(total)


def _classified(classification, value, notes):
	"""The part of a quantity that counts as physical arrival, noting where the rest went."""
	if classification == PHYSICAL:
		return as_decimal(value, "qty")

	notes.append(NOTE_TRANSIT if classification == TRANSIT else NOTE_VIRTUAL)
	return ZERO


def _disagrees(line, received, submitted):
	"""Whether the stored received quantity differs from the linked rows this report can see.

	Skipped when a row's unit does not match, because then the two figures are not comparable and
	a difference would say nothing about the data.
	"""
	if not submitted:
		return False
	if not all(_same_unit(line.uom, line.conversion_factor, row) for row in submitted):
		return False

	linked = sum((as_decimal(row.received_qty, "received_qty") for row in submitted), ZERO)
	return quantity(linked) != received
