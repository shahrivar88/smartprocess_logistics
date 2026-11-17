"""The rules a `Logistics Serial` must obey, and the invariant that keeps them true.

`Logistics Serial` holds its own identity. `serial_no` is the text exactly as the operator typed
it, and `normalized_serial` is the single form this app treats as the identity, derived on the
server from `serial_no`. The unique constraint is `(item_code, normalized_serial)`; see
`core.normalization.py` for why the two forms differ and which rule set builds the identity.

Two rules, and only the first two are enforceable from application code:

1. **Derived, never supplied.** On create the server builds `normalized_serial` and
   `normalization_version` from `serial_no` through `derive_serial_identity`, and it is the only
   place that builds them. The field is read-only in the DocType, and a caller that sends it cannot
   decide an identity: the server overwrites it.
2. **Immutable after creation.** `serial_no` and `item_code` are never edited, because editing
   either moves the identity onto a different serial — a different text, or the same text under a
   different item. A correction is an explicit replacement: remove the serial and register it
   again, exactly as `core.assignment.py` states for an assignment.
3. **Checked on every write.** The `Logistics Serial` document event
   (`smartprocess_logistics/serial_link.py`, registered in `hooks_composition.py`) derives the
   identity on create and, on update, refuses a change to `serial_no` or `item_code` and reasserts
   the stored `normalized_serial` and `normalization_version`. `RUNTIME_ENFORCEMENT` records exactly
   what that event does.
4. **Not enforceable against raw SQL.** An `INSERT` or `UPDATE` issued directly against
   `tabLogistics Serial` bypasses the controller and can write any identity it likes. Nothing in
   Frappe intercepts that, and a database trigger would add schema the framework does not manage.
   This is the remaining hole; it needs database access and is recorded here.
"""

from smartprocess_logistics.core.normalization import normalize_serial

CODE_IDENTITY_CHANGED = "SERIAL_IDENTITY_CHANGED"

# The fields whose change means the row is a different serial, not an edited one. `normalized_serial`
# and `normalization_version` are deliberately absent: they are server-derived, so the server
# reasserts them rather than refusing them, and a caller cannot be said to have changed what it was
# never allowed to set.
SERIAL_IDENTITY_FIELDS = ("serial_no", "item_code")

# What the `Logistics Serial` document event must do.
RUNTIME_ENFORCEMENT = (
	"in `validate`, if the document is new and not a restore: derive `normalized_serial` and "
	"`normalization_version` from `serial_no` with `derive_serial_identity`, and refuse an empty "
	"or un-normalizable `serial_no`; never read either field from the request",
	"in `validate`, if the document is not new: refuse a change to `serial_no` or `item_code`, "
	"and set `normalized_serial` and `normalization_version` back to the stored values, so a value "
	"written by a path that skipped the derivation is caught on the next write",
	"in `validate`, skip the derivation on a restore (`flags.in_import`): the imported row already "
	"carries the identity it was first written with, and a restore preserves history rather than "
	"re-normalizing it under the current rule set",
)

# What only the database could enforce, and does not.
DIRECT_SQL_LIMITATION = (
	"a direct INSERT or UPDATE on `tabLogistics Serial` can write a `normalized_serial` that "
	"disagrees with `serial_no`, and no application code sees it. Reachable only with database "
	"access. A trigger would close it and would also add schema the framework does not manage, so "
	"the hole is documented instead."
)


def derive_serial_identity(serial_no, policy=None):
	"""The identity a serial is stored with, derived from the text as typed.

	Raises `SerialTextError` (from `core.normalization`) for a value that is not text, is empty,
	carries a control character, or is too long. The policy defaults to the current rule set; a
	stored serial written under an older set keeps the version it was written with and is
	re-normalized by a migration, not by a write.
	"""
	return normalize_serial(serial_no, policy=policy)


def changed_identity_fields(stored, incoming):
	"""The identity fields whose value differs between the stored row and the incoming document.

	Returns a tuple, empty when the identity is unchanged. Both arguments are read through `get`,
	so a stored dict and a live document compare alike, and a missing value and an empty value are
	treated as the same.
	"""
	changed = []

	for field in SERIAL_IDENTITY_FIELDS:
		if (incoming.get(field) or None) != (stored.get(field) or None):
			changed.append(field)

	return tuple(changed)
