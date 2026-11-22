"""What a report opens on: one company, and the site's current fiscal year.

Two questions, both answerable from what the caller's own records already say.

**Which company.** The reader's default company is used when their permissions reach it;
otherwise the first company they may read. A caller who may read none gets `None`, and no
company they cannot read is ever named.

**Which dates.** `from_date` is the start of the `Fiscal Year` that contains today and
`to_date` is today. Nothing here assumes a calendar: a `Fiscal Year` record stores real start
and end dates, so a Jalali year is read exactly like a Gregorian one. Overlapping records are a
site configuration error, and the latest start wins so the answer does not depend on row order.

No Frappe import, no database. `api.gateway` reads the records; this file decides.
"""


def _iso(value):
	"""A date as `YYYY-MM-DD`, whether it arrived as a date, a datetime or text."""
	if value is None:
		return ""
	if hasattr(value, "isoformat"):
		return value.isoformat()[:10]

	return str(value)[:10]


def choose_company(user_default, permitted, visible=None):
	"""The company a report should open on, or `None`.

	`permitted` is what the framework's own permission-checked list query returned, so it is
	already the set the caller may read. `visible` is the app's own company scope: `None` means
	unrestricted, and any other value narrows the permitted set further.
	"""
	allowed = [name for name in (permitted or ()) if name and (visible is None or name in visible)]
	default = str(user_default or "").strip()

	if default and default in allowed:
		return default

	return allowed[0] if allowed else None


def _year_dates(year):
	"""One `Fiscal Year` record's start and end date, as a mapping or as a row."""
	if isinstance(year, dict):
		return (year.get("year_start_date"), year.get("year_end_date"))

	_name, start, end = year

	return (start, end)


def fiscal_year_window(today, years):
	"""The start and end date of the fiscal year that contains `today`.

	`years` is an iterable of `(name, year_start_date, year_end_date)` rows or of mappings with
	those keys — the shape the gateway's own read returns. When no record contains today — a
	site with no fiscal year yet — both dates are empty and the caller leaves the filter unset
	rather than inventing a boundary.
	"""
	day = _iso(today)
	if not day:
		return ("", "")

	containing = []

	for year in years or ():
		start, end = _year_dates(year)
		start, end = _iso(start), _iso(end)

		if start and end and start <= day <= end:
			containing.append((start, end))

	if not containing:
		return ("", "")

	start, end = max(containing, key=lambda window: window[0])

	return (start, end)
