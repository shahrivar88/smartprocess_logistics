"""The values written once, at install time — and one that follows the code instead.

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

`sync_normalization_version` is deliberately outside both guards and outside `seed_settings`.
It records a fact about the **code** rather than about this site's first day, so a value written
once at install goes stale by design; see its docstring for why that was the mismatch worth
closing.

`Logistics Settings.installed_on` used to be declared `set_only_once` as a second layer, on the
theory that the framework would refuse any later change. That was wrong, and it stopped the
installation three times. `set_only_once` is honoured only when the document is **inserted**
(`Document.validate_set_only_once` returns immediately unless `_doc_before_save` exists), and
`installer.init_singles` inserts the `Logistics Settings` row before `after_install` ever runs.
So the first write this module made was already an *update*, and the framework refused it with
`CannotChangeConstantError`. The flag belongs on a value chosen at creation time; this value is
chosen after creation, which is exactly what the early return above is for. It is now the only
guard, and it is the one that was doing the work all along.
"""

import frappe

from smartprocess_logistics.core import scan
from smartprocess_logistics.core.defaults import (
	DEFAULT_DELIMITERS,
	DEFAULT_ITEM_MARKERS,
	DEFAULT_NEXT_ROW_COMMAND,
)
from smartprocess_logistics.core.normalization import NORMALIZATION_VERSION
from smartprocess_logistics.core.policies import DEFAULT_POLICY

SETTINGS_DOCTYPE = "Logistics Settings"


def is_installed(settings=None):
	"""Whether the installation has completed, judged by the timestamp."""
	settings = settings if settings is not None else frappe.get_single(SETTINGS_DOCTYPE)

	return bool(settings.installed_on)


def sync_normalization_version():
	"""Record the rule set the app normalizes with, so the screen agrees with the code.

	This is the one value in this module that is **not** written once. It follows
	`core.normalization.NORMALIZATION_VERSION`, the single source of truth for the rules in
	force, and it is the only place that writes the field. Written only at install it drifted:
	the site was installed while rule set 1 was current, the code then moved to 2, and the field
	went on describing rules the app no longer applies.

	It says nothing about the serials already stored. Which rule set produced each of those is a
	different fact, recorded on the serial itself as `Logistics Serial.normalization_version`
	when it was created, and never rewritten here. Rewriting this value therefore cannot change
	an identity, a stored copy or a report row; it only stops the screen from contradicting the
	code.

	Returns whether it wrote, so a caller can log the transition. Idempotent.
	"""
	settings = frappe.get_single(SETTINGS_DOCTYPE)

	if int(settings.get("normalization_version") or 0) == int(NORMALIZATION_VERSION):
		return False

	settings.normalization_version = int(NORMALIZATION_VERSION)
	settings.save()

	return True


def sync_next_row_command():
	"""Give a site the scan window's next-column command, if it does not have one yet.

	This exists for one moment: the migrate that adds the field to a site that already has a
	settings row. A new column arrives empty — Frappe does not write a field's default into rows
	that already exist — and the command is a **required** value with a rule behind it, so without
	this a site would migrate into a settings row that its own validation refuses to save.

	It is written only when empty, so the two things it must never do are both impossible: it
	cannot overwrite a command an administrator chose, and it cannot put `**` back after somebody
	deliberately changed it. An administrator cannot empty the field through the form — the rule in
	`core.scan` refuses that — so "empty" only ever means "this column was just added".

	Returns whether it wrote, so a caller can log the transition. Idempotent.
	"""
	settings = frappe.get_single(SETTINGS_DOCTYPE)

	if (settings.get(scan.FIELDNAME) or "").strip():
		return False

	settings.next_row_command = DEFAULT_NEXT_ROW_COMMAND
	settings.save()

	return True


def sync_inventory_policy_defaults():
	"""Write the approved inventory defaults once into an existing Settings row.

	A new Check column arrives as 0 for an old row, which is indistinguishable from an
	administrator's explicit off. The version marker is written with the defaults, so the next
	migrate leaves an explicit 0 alone. A fake or pre-migration Settings object without the
	marker returns without writing.
	"""
	settings = frappe.get_single(SETTINGS_DOCTYPE)

	if not hasattr(settings, "sp_inventory_policy_version"):
		return False
	if int(settings.get("sp_inventory_policy_version") or 0) >= 1:
		return False

	settings.sp_allocation_priority = DEFAULT_POLICY.allocation_priority
	settings.sp_virtual_stock_covers_procurement = (
		1 if DEFAULT_POLICY.virtual_stock_covers_procurement else 0
	)
	settings.sp_recommended_purchase_net_of_open_po = (
		1 if DEFAULT_POLICY.recommended_purchase_net_of_open_po else 0
	)
	settings.sp_inventory_policy_version = 1
	settings.save()

	return True


def seed_settings():
	"""Record the install timestamp, and fill the seed tables on the first install only.

	Safe to run repeatedly, and safe to run after a failure that left the site half-installed.
	"""
	settings = frappe.get_single(SETTINGS_DOCTYPE)

	if settings.installed_on:
		return False

	if not settings.get("delimiters"):
		for label, delimiter in DEFAULT_DELIMITERS:
			settings.append("delimiters", {"delimiter": delimiter, "enabled": 1, "label": label})

	if not settings.get("item_markers"):
		for label, pattern in DEFAULT_ITEM_MARKERS:
			settings.append("item_markers", {"enabled": 1, "label": label, "pattern": pattern})

	# Written last, on purpose. It is the marker that says the install finished, so a failure
	# anywhere above leaves it empty and the next `after_migrate` repairs the site instead of
	# treating a half-written settings row as a finished one.
	settings.installed_on = frappe.utils.now()
	settings.save()

	return True
