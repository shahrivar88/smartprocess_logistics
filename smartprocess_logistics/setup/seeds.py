"""The values written once, at install time.

Delimiters are stored as literal text. `\\r`, `\\n` and `\\t` are stored as the two characters
backslash plus letter and are interpreted when a file is read, because a literal newline or
tab inside a grid cell cannot be typed or seen in the Desk.

Two independent guards keep this idempotent, and they answer two different questions:

1. `installed_on` empty means the install has not completed. Once it is written, no later
   migrate touches the tables, so a delimiter an administrator deliberately removed stays
   removed. That is the guard the install relies on.
2. Each child table is filled only when it is **empty**. That is the guard for the state the
   first one cannot see: `installed_on` empty *and* the tables already populated, which happens
   when an earlier attempt wrote the tables and then failed before writing the timestamp, or
   when an administrator configured the tables on a half-installed site. Without it, the retry
   would append a second copy of every default.

`Logistics Settings.installed_on` is also declared `set_only_once`, so the framework refuses a
later change to it even if this module were rewritten to try. The two layers are deliberate:
the early return says what this module intends, and the field flag is what the database
enforces.
"""

import frappe

from smartprocess_logistics.core.defaults import DEFAULT_DELIMITERS, DEFAULT_ITEM_MARKERS

SETTINGS_DOCTYPE = "Logistics Settings"


def is_installed(settings=None):
	"""Whether the installation has completed, judged by the timestamp."""
	settings = settings if settings is not None else frappe.get_single(SETTINGS_DOCTYPE)

	return bool(settings.installed_on)


def seed_settings():
	"""Record the install timestamp, and fill the seed tables on the first install only.

	Safe to run repeatedly, and safe to run after a failure that left the site half-installed.
	"""
	settings = frappe.get_single(SETTINGS_DOCTYPE)

	if settings.installed_on:
		return False

	seeded = False

	if not settings.get("delimiters"):
		for label, delimiter in DEFAULT_DELIMITERS:
			settings.append("delimiters", {"delimiter": delimiter, "enabled": 1, "label": label})
		seeded = True

	if not settings.get("item_markers"):
		for label, pattern in DEFAULT_ITEM_MARKERS:
			settings.append("item_markers", {"enabled": 1, "label": label, "pattern": pattern})
		seeded = True

	# Written last, on purpose. It is the marker that says the install finished, so a failure
	# anywhere above leaves it empty and the next `after_migrate` repairs the site instead of
	# treating a half-written settings row as a finished one.
	settings.installed_on = frappe.utils.now()
	settings.save()

	return True
