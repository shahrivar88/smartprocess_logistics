"""How many labels a row needs, and how many it is short of or over.

A row's requirement is the size of its invoiced quantity, because one label is registered per
unit sold. Three decisions are worth stating plainly, because each one is observable:

- **Sign is ignored.** A credit note carries a negative quantity and still needs a label per
  unit returned, so the requirement is the absolute value.
- **Fractions round up.** A row of 2.5 units needs three labels, never two. Rounding down
  would silently leave a sold unit unlabelled. The rounding uses a small tolerance so that a
  quantity stored as 2.0000000001 is read as 2 rather than as 3.
- **A row may still be over its requirement in stored data.** Registration refuses it — the cap
  is the row's invoiced quantity, and it is not a site preference — but a row written before
  that rule, or by something outside the app, can hold more. This module reports the number
  rather than hiding it, because a count that is too high is what tells an operator where to
  look.
"""

import math
from dataclasses import dataclass

QUANTITY_EPSILON = 1e-9
MAX_UNITS = 100_000

CODE_NOT_A_NUMBER = "QUANTITY_NOT_A_NUMBER"
CODE_NOT_FINITE = "QUANTITY_NOT_FINITE"
CODE_TOO_LARGE = "QUANTITY_TOO_LARGE"
CODE_NEGATIVE_REGISTERED = "REGISTERED_COUNT_NEGATIVE"


class QuantityError(ValueError):
	"""A quantity or a count that cannot be used. `code` is stable."""

	def __init__(self, code, message):
		super().__init__(message)
		self.code = code
		self.message = message


def check_quantity(quantity):
	"""Refuse anything that is not a finite number."""
	if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
		raise QuantityError(CODE_NOT_A_NUMBER, "تعداد باید عدد باشد.")

	if not math.isfinite(quantity):
		raise QuantityError(CODE_NOT_FINITE, "تعداد باید عددی متناهی باشد.")

	return float(quantity)


def required_units(invoiced_qty):
	"""How many labels a row needs. Zero for a row that invoices nothing."""
	quantity = check_quantity(invoiced_qty)

	units = math.ceil(abs(quantity) - QUANTITY_EPSILON)
	units = max(0, int(units))

	if units > MAX_UNITS:
		raise QuantityError(CODE_TOO_LARGE, f"تعداد واحد بیش از {MAX_UNITS} است.")

	return units


def quantity_is_integral(invoiced_qty):
	"""Whether a quantity is a whole number, within the tolerance.

	A row that is not whole still gets a requirement, but a caller may want to warn about it.
	"""
	quantity = check_quantity(invoiced_qty)
	return abs(quantity - round(quantity)) <= QUANTITY_EPSILON


@dataclass(frozen=True)
class RowCoverage:
	"""How one invoice item row stands against its requirement."""

	row_key: str
	item_code: str
	invoiced_qty: float
	required: int
	registered: int
	shortage: int
	excess: int

	@property
	def is_complete(self):
		return self.shortage == 0

	@property
	def is_over(self):
		return self.excess > 0


@dataclass(frozen=True)
class CoverageSummary:
	"""Totals across several rows."""

	rows: int
	required: int
	registered: int
	shortage: int
	excess: int
	incomplete_rows: int
	over_rows: int

	@property
	def is_complete(self):
		return self.shortage == 0


def row_coverage(row_key, item_code, invoiced_qty, registered):
	"""Build the coverage of one row."""
	if isinstance(registered, bool) or not isinstance(registered, int):
		raise QuantityError(CODE_NOT_A_NUMBER, "تعداد سریال ثبت‌شده باید عدد صحیح باشد.")

	if registered < 0:
		raise QuantityError(CODE_NEGATIVE_REGISTERED, "تعداد سریال ثبت‌شده نمی‌تواند منفی باشد.")

	required = required_units(invoiced_qty)

	return RowCoverage(
		row_key=row_key,
		item_code=item_code,
		invoiced_qty=check_quantity(invoiced_qty),
		required=required,
		registered=registered,
		shortage=max(0, required - registered),
		excess=max(0, registered - required),
	)


def coverage_summary(coverages):
	"""Totals across rows, for a footer or a report."""
	coverages = tuple(coverages)

	return CoverageSummary(
		rows=len(coverages),
		required=sum(coverage.required for coverage in coverages),
		registered=sum(coverage.registered for coverage in coverages),
		shortage=sum(coverage.shortage for coverage in coverages),
		excess=sum(coverage.excess for coverage in coverages),
		incomplete_rows=sum(1 for coverage in coverages if not coverage.is_complete),
		over_rows=sum(1 for coverage in coverages if coverage.is_over),
	)
