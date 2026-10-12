"""The values written once, at install time.

Delimiters are stored as literal text. `\\r`, `\\n` and `\\t` are stored as the two characters
backslash plus letter and are interpreted when a file is read, because a literal newline or
tab inside a grid cell cannot be typed or seen in the Desk.

Seeding is tied to the first install: it runs only while `installed_on` is still empty. A
later migrate therefore never puts back a delimiter or a marker an administrator deliberately
removed, and re-running the same install twice writes nothing the second time.
"""

import frappe

from smartprocess_logistics.core.defaults import DEFAULT_DELIMITERS, DEFAULT_ITEM_MARKERS

SETTINGS_DOCTYPE = "Logistics Settings"


def seed_settings():
	"""Record the install timestamp, and fill the seed tables on the first install only."""
	settings = frappe.get_single(SETTINGS_DOCTYPE)

	if settings.installed_on:
		return

	settings.installed_on = frappe.utils.now()

	for label, delimiter in DEFAULT_DELIMITERS:
		settings.append("delimiters", {"delimiter": delimiter, "enabled": 1, "label": label})

	for label, pattern in DEFAULT_ITEM_MARKERS:
		settings.append("item_markers", {"enabled": 1, "label": label, "pattern": pattern})

	settings.save()
