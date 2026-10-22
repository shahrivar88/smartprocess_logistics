"""Which Items a source warehouse can offer, and how one suggestion reads.

The Stock Entry Item Code field is a standard Frappe Link. Only its server query changes, so
the SELECT, the WHERE clause, the ordering and the paging are written here, where they are
tested without a site. The Frappe layer adds the Item permission condition and runs the
statement; nothing in this module reads a session, a permission or the database.

A suggestion exists only when the warehouse itself holds the item: the join is on `Bin`, the
quantity must be positive, and the Item must be a live, enabled stock item. Virtual warehouses
are not special — their stock is real stock; only its use is restricted elsewhere.
"""

DEFAULT_PAGE_LENGTH = 10

# A Link validation asks for a page long enough to contain the typed name; a normal dropdown
# asks for ten. Anything above this is a request we do not need to answer in full.
MAX_PAGE_LENGTH = 500

FILTER_KEYS = ("warehouse", "company", "item_code")


def filter_values(filters):
	"""The three documented Link filters, or `None` when one is not a string.

	The Link sends strings. A direct caller can send anything, and a list value such as
	`{"warehouse": ["=", "WH-001"]}` is not one of the documented shapes; the endpoint answers
	it with an empty list rather than letting a type error become a server error. A missing
	key and `None` both mean "not sent"; an empty string is its own value and is judged by the
	caller (no warehouse means no suggestions).
	"""
	if not isinstance(filters, dict):
		return None

	values = {}
	for key in FILTER_KEYS:
		value = filters.get(key)
		if value is None:
			value = ""
		elif not isinstance(value, str):
			return None
		values[key] = value.strip()

	return values


def normalize_page(start, page_len):
	"""Clamp the Link's paging to something one query can answer."""
	try:
		start = int(start or 0)
	except (TypeError, ValueError):
		start = 0
	try:
		page_len = int(page_len or DEFAULT_PAGE_LENGTH)
	except (TypeError, ValueError):
		page_len = DEFAULT_PAGE_LENGTH

	return max(0, start), max(1, min(page_len, MAX_PAGE_LENGTH))


def format_quantity(value):
	"""A quantity the way an operator reads it: no trailing zeros, no bare float."""
	try:
		number = round(float(value or 0), 3)
	except (TypeError, ValueError):
		number = 0.0

	text = f"{number:.3f}".rstrip("0").rstrip(".")
	return text or "0"


def stock_label(quantity, uom):
	"""The half of a suggestion that says how much is there, e.g. `Stock: 120 Nos`."""
	label = f"Stock: {format_quantity(quantity)}"
	uom = (uom or "").strip()
	return f"{label} {uom}" if uom else label


def build_query(warehouse, txt, start, page_len, today, match_condition="", item_code=None):
	"""The Item/Bin statement and its parameters.

	`match_condition` is the SQL fragment `frappe.desk.reportview.get_match_cond` returns for
	the current user: role permissions, Item user permissions, `if_owner` and the site's
	permission-query hooks. It is inserted as SQL, so the caller must pass nothing else.

	`item_code` is the exact-item lookup the form uses after a source warehouse changes. It is
	a filter rather than a separate query so both paths answer with the same rows.
	"""
	start, page_len = normalize_page(start, page_len)

	pattern = f"%{txt}%" if txt else "%"
	conditions = [
		"`tabBin`.`warehouse` = %(warehouse)s",
		"`tabBin`.`actual_qty` > 0",
		"`tabItem`.`is_stock_item` = 1",
		"`tabItem`.`disabled` = 0",
		"`tabItem`.`has_variants` = 0",
		"`tabItem`.`docstatus` < 2",
		"(`tabItem`.`end_of_life` is null or `tabItem`.`end_of_life` = '0000-00-00'"
		" or `tabItem`.`end_of_life` > %(today)s)",
	]
	params = {
		"warehouse": warehouse,
		"today": today,
		"txt": pattern,
		"_txt": txt or "",
		"start": start,
		"page_len": page_len,
	}

	if item_code:
		conditions.append("`tabItem`.`name` = %(item_code)s")
		params["item_code"] = item_code
	else:
		conditions.append("(`tabItem`.`name` like %(txt)s or `tabItem`.`item_name` like %(txt)s)")

	where = "\n\t\tand ".join(conditions)
	if match_condition:
		where += f"\n\t\t{match_condition}"

	sql = f"""select
			`tabItem`.`name`,
			`tabItem`.`item_name`,
			`tabBin`.`actual_qty`,
			`tabItem`.`stock_uom`
		from `tabItem`
		inner join `tabBin` on `tabBin`.`item_code` = `tabItem`.`name`
		where {where}
		order by
			if(locate(%(_txt)s, `tabItem`.`name`), locate(%(_txt)s, `tabItem`.`name`), 99999),
			if(locate(%(_txt)s, `tabItem`.`item_name`), locate(%(_txt)s, `tabItem`.`item_name`), 99999),
			`tabItem`.`idx` desc,
			`tabItem`.`name`
		limit %(start)s, %(page_len)s"""

	return sql, params


def suggestion(item_code, item_name, quantity, uom):
	"""One Link row: `(value, ...description parts)`.

	Frappe joins everything after the first column into the dropdown's description, so the
	operator reads `ITEM-001, Product Name, Stock: 120 Nos`.
	"""
	return (item_code, (item_name or "").strip(), stock_label(quantity, uom))
