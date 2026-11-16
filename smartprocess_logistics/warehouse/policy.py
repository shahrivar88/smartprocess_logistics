"""Pure warehouse-classification rules. No Frappe import, no database.

A virtual warehouse is a real ERPNext warehouse: it has a Bin, a stock value and Stock Ledger
Entries, and it is part of company-owned inventory. What it cannot do is supply a sale, a
delivery or a reservation. The standard `Warehouse Type` is the classification label; the
server-side handlers in this package enforce the rule.

The business-logic identifiers of the three classes are English and never translated:
`physical`, `virtual`, `transit`. The Persian words are display labels and live at the UI and
report boundary (`CLASSIFICATION_LABELS`, `translations/fa.csv`), so a translated string can
never become a stored value or a calculation input.
"""

from dataclasses import dataclass

PHYSICAL = "physical"
TRANSIT = "transit"
VIRTUAL = "virtual"

CLASSIFICATIONS = (PHYSICAL, VIRTUAL, TRANSIT)
NON_PHYSICAL = (VIRTUAL, TRANSIT)

# Three kinds share two lists. `Virtual` and its Persian name mean "unusable stock" — lost,
# damaged, warranty, written off; `Transit` means goods on their way. Both are unsellable, which
# is why `DEFAULT_VIRTUAL_TYPES` still holds the union: every existing sales guard asks one
# question ("may this supply a sale?") and must keep answering it the same way.
VIRTUAL_TYPES = ("Virtual", "مجازی")
TRANSIT_TYPES = ("Transit",)

# ERPNext's own name for the type it puts on the warehouse goods rest in between.
TRANSIT_TYPE = "Transit"

# A site can override the unsellable list with `sp_virtual_warehouse_types`; a new unsellable
# warehouse is then just a Warehouse with one of these types, not a new configuration entry.
#
# Everything else is physical: an empty type (every warehouse created before this app shipped a
# type) and the explicit `Physical` type the app now ships (`fixtures/warehouse_type.json`). The
# rule is a deny-list on purpose — a site that never labels a warehouse keeps its stock
# sellable, and a label this app has never seen cannot silently make a warehouse unsellable.
# `Physical` and `فیزیکی` are deliberately absent: they name the physical case.
DEFAULT_VIRTUAL_TYPES = (*VIRTUAL_TYPES, *TRANSIT_TYPES)

# The mapping key for the warehouses that carry no type at all. A stored row spells it as a blank
# type; `type_key` normalises both spellings, so `""` is the only one the rest of this module sees.
UNTYPED = ""

# The labels are Persian because they are displayed, never compared. Nothing stores or calculates
# with them.
CLASSIFICATION_LABELS = {PHYSICAL: "فیزیکی", VIRTUAL: "مجازی", TRANSIT: "در راه"}

CODE_CLASSIFICATION_UNKNOWN_VALUE = "WAREHOUSE_CLASSIFICATION_UNKNOWN_VALUE"
CODE_CLASSIFICATION_DUPLICATE = "WAREHOUSE_CLASSIFICATION_DUPLICATE"
CODE_CLASSIFICATION_UNKNOWN_TYPE = "WAREHOUSE_CLASSIFICATION_UNKNOWN_TYPE"
CODE_CLASSIFICATION_INCOMPLETE = "WAREHOUSE_CLASSIFICATION_INCOMPLETE"


def type_key(warehouse_type):
	"""The mapping key for a type name: stripped, and the empty string for no type."""
	return str(warehouse_type or "").strip()


def _names(virtual_types):
	return {str(value).strip() for value in (virtual_types or ())}


def kind(warehouse_type, virtual_types=DEFAULT_VIRTUAL_TYPES):
	"""Which of the three kinds a standard Warehouse Type means under the effective policy.

	`Transit` is its own kind even though it is also unsellable: an operational report must be
	able to show goods on their way without calling them lost. A type a site added to
	`sp_virtual_warehouse_types` is treated as virtual — it is unsellable and this app knows
	nothing else about it, so it is never counted as usable stock.

	`virtual_types` decides membership and nothing else does. The hardcoded names choose between
	the two unsellable kinds; they cannot make a type unsellable that the configured list leaves
	out, because every caller must answer "may this supply a sale?" the same way.
	"""
	name = type_key(warehouse_type)
	if not name or name not in _names(virtual_types):
		return PHYSICAL
	return TRANSIT if name in TRANSIT_TYPES else VIRTUAL


def default_classification(warehouse_type, virtual_types=DEFAULT_VIRTUAL_TYPES):
	"""What the site's effective policy says, before any stored mapping is consulted.

	This is exactly `kind`, kept under the name the classification layer reads. A site that
	overrides `sp_virtual_warehouse_types` with its own names has already said which types mean
	"not for sale"; this function does not add a second opinion.
	"""
	return kind(warehouse_type, virtual_types)


def classification_of(warehouse_type, virtual_types=DEFAULT_VIRTUAL_TYPES, mapping=None):
	"""The one answer to "which of the three is this warehouse?".

	An explicit mapping wins; everything else falls back to the effective policy. `mapping` is a
	plain dict of type name to classification code, with `UNTYPED` for the blank-type bucket, so
	this module stays free of the framework and of the settings screen.
	"""
	explicit = (mapping or {}).get(type_key(warehouse_type))

	if explicit in CLASSIFICATIONS:
		return explicit

	return default_classification(warehouse_type, virtual_types)


# The Warehouse Inventory report's «نوع انبار» filter. Three of its values are the classification
# codes themselves and `untyped` is the raw question "does this warehouse carry no Warehouse Type
# at all?". That question is deliberately not the configurable blank-type bucket: the mapping
# says how to treat a warehouse with no type, not whether it has one.
FILTER_ALL = "all"
FILTER_UNTYPED = "untyped"
TYPE_FILTERS = (FILTER_ALL, FILTER_UNTYPED, *CLASSIFICATIONS)


def type_filter_matches(warehouse_type, selected, virtual_types=DEFAULT_VIRTUAL_TYPES, mapping=None):
	"""Whether a warehouse belongs in a report filtered to `selected`."""
	if selected in (None, "", FILTER_ALL):
		return True
	if selected == FILTER_UNTYPED:
		return not type_key(warehouse_type)

	return classification_of(warehouse_type, virtual_types, mapping) == selected


def is_virtual_type(warehouse_type, virtual_types=DEFAULT_VIRTUAL_TYPES, mapping=None):
	"""Whether a type's stock is unavailable to sales, deliveries and reservations.

	The name is the one the whole app already calls it by. `Transit` answers yes for the same
	reason `Virtual` does: it is not in a warehouse a customer can be served from.
	"""
	return classification_of(warehouse_type, virtual_types, mapping) in NON_PHYSICAL


def non_physical_names(virtual_types=DEFAULT_VIRTUAL_TYPES, mapping=None):
	"""The type names the effective policy treats as non-physical, as a sorted tuple.

	This is the form the binary guards take: they receive a tuple of names and ask membership, so
	the stored mapping is folded into that tuple once instead of being passed through every
	handler. The blank-type bucket travels as the empty string.
	"""
	explicit = mapping or {}
	names = {name for name in _names(virtual_types) if name}
	names.update(name for name, value in explicit.items() if value in NON_PHYSICAL)
	names.difference_update(name for name, value in explicit.items() if value == PHYSICAL)

	if explicit.get(UNTYPED) in NON_PHYSICAL:
		names.add(UNTYPED)

	return tuple(sorted(names))


@dataclass(frozen=True)
class Problem:
	code: str
	message: str
	row: int | None = None


def classification_mapping_problems(rows, known_types):
	"""What is wrong with a mapping before anybody stores it.

	`rows` is the table in document order, each `(warehouse_type, classification)`; a blank type
	is the bucket for warehouses with no type. `known_types` is every `Warehouse Type` on the
	site. An empty table is not a mapping at all — it is the install state, where the effective
	policy decides everything — so it is never refused. A non-empty table has to be complete:
	every known type and the untyped bucket, so a half-saved table cannot silently reclassify the
	warehouse somebody forgot.
	"""
	problems = []
	seen = {}
	known = {type_key(name) for name in (known_types or ()) if type_key(name)}

	for position, (warehouse_type, classification) in enumerate(rows, start=1):
		key = type_key(warehouse_type)
		where = f"ردیف {position}: "

		if classification not in CLASSIFICATIONS:
			label = CLASSIFICATION_LABELS.get(classification, classification)
			problems.append(
				Problem(
					CODE_CLASSIFICATION_UNKNOWN_VALUE,
					f"{where}طبقه‌بندی «{label}» شناخته نشده است؛ فیزیکی، مجازی یا در راه.",
					position,
				)
			)

		if key in seen:
			problems.append(
				Problem(
					CODE_CLASSIFICATION_DUPLICATE,
					f"{where}نوع انبار «{key or 'بدون نوع انبار'}» پیش‌تر در ردیف {seen[key]} آمده است.",
					position,
				)
			)
		else:
			seen[key] = position

		if key and key not in known:
			problems.append(
				Problem(
					CODE_CLASSIFICATION_UNKNOWN_TYPE,
					f"{where}نوع انبار «{key}» وجود ندارد.",
					position,
				)
			)

	if rows and seen.keys() != known | {UNTYPED}:
		missing = sorted(known - seen.keys())
		problems.append(
			Problem(
				CODE_CLASSIFICATION_INCOMPLETE,
				"جدول طبقه‌بندی ناقص است؛ هر نوع انبار و ردیف «بدون نوع انبار» باید حاضر باشد."
				+ (f" غایب: {'، '.join(missing)}." if missing else ""),
			)
		)

	return problems


def stock_entry_problem(purpose, source_virtual, target_virtual, row=None):
	"""Only a Material Transfer may move stock into, out of or between virtual warehouses.

	Standard ERPNext already decides company, disabled/group warehouse, permissions, negative
	stock and valuation. This function adds only the classification rule; it has no destination
	whitelist and no purchase-receipt link.
	"""
	if not source_virtual and not target_virtual:
		return None
	if purpose == "Material Transfer":
		return None

	where = f"ردیف {row}: " if row else ""
	return Problem(
		"VIRTUAL_MATERIAL_TRANSFER_ONLY",
		f"{where}جابه‌جایی موجودی انبار مجازی فقط با نوع سند «انتقال مواد» مجاز است.",
		row,
	)


def sales_source_problem(warehouse, is_virtual, row=None):
	"""Virtual stock is never a sales, delivery or reservation source."""
	if not is_virtual:
		return None

	where = f"ردیف {row}: " if row else ""
	return Problem(
		"VIRTUAL_NOT_SELLABLE",
		f"{where}کالای موجود در انبار مجازی «{warehouse}» قابل فروش، ارسال یا رزرو نیست؛ "
		"ابتدا با «انتقال مواد» به انبار واقعی منتقل کنید.",
		row,
	)
