"""The two view filters a Sales Invoice row offers.

They narrow a Link's result list; they never change a saved value, and nothing here reads a
session or a database. The server applies both, so a user sees exactly the rows the query
returned — and a user who cannot read `Bin` is given the unfiltered list rather than an empty
one, because hiding every row on missing evidence reads as "nothing is available".
"""

from smartprocess_logistics.core.inventory import quantity

TRUE_WORDS = ("1", "true", "yes", "on")


def wants(value):
	"""A Link filter arrives as `"0"`/`"1"`; a direct caller may pass a boolean."""
	if isinstance(value, str):
		return value.strip().lower() in TRUE_WORDS
	return bool(value)


def keep_item(physical_qty, hide_without_stock):
	"""Whether an Item row survives «عدم نمایش کالاهای بدون موجودی»."""
	if not hide_without_stock:
		return True
	return quantity(physical_qty) > 0


def keep_warehouse(actual_qty, hide_empty):
	"""Whether a Warehouse row survives «عدم نمایش انبارهای بدون موجودی کالای انتخاب‌شده»."""
	if not hide_empty:
		return True
	return quantity(actual_qty) > 0
