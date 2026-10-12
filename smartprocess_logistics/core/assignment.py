"""The values an assignment stores, and the invariant that keeps them true.

`Logistics Serial Assignment` carries `normalized_serial`, a copy of the referenced serial's
identity. The copy exists because a unique constraint cannot reach through a link field: a
constraint on `(sales_invoice, logistics_serial)` forbids the same *serial record* twice on one
invoice, but not the same *serial text* twice when the two rows name different items, because
those are two different serial records. The copy is what
`UNIQUE(sales_invoice, normalized_serial)` can be declared on.

A copy can drift, and no unique constraint can detect that it did. Four rules keep it true,
and only the first three are enforceable from application code:

1. **Derived, never supplied.** The value is built on the server from the referenced
   `Logistics Serial`. `derive_assignment_values` is the only place that builds it, and
   `reject_supplied_identity` refuses a request that carries the field at all. A caller that
   sends `normalized_serial` is a caller that is trying to decide an identity, and that is not
   a decision it gets to make.
2. **Immutable after registration.** A serial's `normalized_serial` is never edited. The
   `Logistics Serial` controller refuses a change to it, and a correction is an explicit,
   authorized replacement: the registration is removed and a new one is added. Because the
   referenced value cannot change, a stored copy cannot go stale through use.
3. **Checked on every allowed write.** The future `Logistics Serial Assignment` controller
   re-derives the copy from the linked serial and refuses a mismatch, so a document edited
   through the Desk, through `frappe.client`, or through the REST API cannot introduce one.
   `RUNTIME_ENFORCEMENT` records exactly what that controller has to do, so the requirement
   survives until the code exists.
4. **Not enforceable against raw SQL.** An `INSERT` or `UPDATE` issued directly against
   `tabLogistics Serial Assignment` bypasses the controller and can write any copy it likes.
   Nothing in Frappe intercepts that, and adding a database trigger would introduce schema the
   framework does not manage. This is the remaining hole, it needs database access to reach,
   and it is recorded here rather than papered over.

Consequence worth stating: because the copy is derived and the identity is immutable, the
report and the dialog can trust `normalized_serial` for duplicate detection and for matching
without joining back to `Logistics Serial`. That is the reason for the copy, and it is only
sound while rules 1 to 3 hold.
"""

from dataclasses import dataclass

from smartprocess_logistics.core.normalization import NormalizedSerial

CODE_IDENTITY_MISMATCH = "ASSIGNMENT_IDENTITY_MISMATCH"
CODE_IDENTITY_SUPPLIED = "ASSIGNMENT_IDENTITY_SUPPLIED"
CODE_SERIAL_MISSING = "ASSIGNMENT_SERIAL_MISSING"

# The field a request must never carry, because the server derives it.
DERIVED_FIELDS = ("normalized_serial",)

# What the `Logistics Serial Assignment` controller has to do once runtime code is allowed.
RUNTIME_ENFORCEMENT = (
	"in `validate`, if the document is new: read the linked `Logistics Serial` and set "
	"`normalized_serial` to its `normalized_serial`; never read it from the request",
	"in `validate`, if the document is not new: refuse an edit to `logistics_serial`, "
	"`sales_invoice`, `sales_invoice_item` or `normalized_serial`, because an assignment is "
	"created or replaced, never edited",
	"in `validate`, compare the stored `normalized_serial` with the linked serial's and refuse "
	"a mismatch, so a value written by a path that skipped the derivation is caught on the "
	"next write",
	"in `on_trash` and in the replacement API, do not touch `Logistics Serial`; removing a "
	"registration must not remove the serial, which other invoices may still reference",
)

# What only the database could enforce, and does not.
DIRECT_SQL_LIMITATION = (
	"a direct INSERT or UPDATE on `tabLogistics Serial Assignment` can write a "
	"`normalized_serial` that disagrees with the linked serial, and no application code sees "
	"it. Reachable only with database access. A trigger would close it and would also add "
	"schema the framework does not manage, so the hole is documented instead."
)


class AssignmentIdentityError(ValueError):
	"""An assignment value that cannot be built or that disagrees. `code` is stable."""

	def __init__(self, code, message):
		super().__init__(message)
		self.code = code
		self.message = message


@dataclass(frozen=True)
class AssignmentValues:
	"""Exactly the fields a new assignment is written with.

	Building them in one place is what makes "derived on the server" a fact rather than an
	intention.
	"""

	logistics_serial: str
	sales_invoice: str
	sales_invoice_item: str
	company: str
	item_code: str
	normalized_serial: str
	invoice_item_idx: int | None


def derive_assignment_values(
	*,
	serial,
	serial_name,
	item_code,
	sales_invoice,
	sales_invoice_item,
	company,
	row_index=None,
):
	"""Build the stored values for a new assignment.

	`serial` is the `NormalizedSerial` of the referenced `Logistics Serial`, which the caller
	has already read back from the database, and `serial_name` and `item_code` are that
	document's own values. Nothing here is read from a request.
	"""
	if not isinstance(serial, NormalizedSerial):
		raise AssignmentIdentityError(CODE_SERIAL_MISSING, "سریال مرجع داده نشده است.")

	for value, label in (
		(serial_name, "نام سریال"),
		(item_code, "کالا"),
		(sales_invoice, "فاکتور فروش"),
		(sales_invoice_item, "ردیف فاکتور"),
		(company, "شرکت"),
	):
		if not isinstance(value, str) or not value.strip():
			raise AssignmentIdentityError(CODE_SERIAL_MISSING, f"{label} باید مقدار داشته باشد.")

	if row_index is not None:
		if isinstance(row_index, bool) or not isinstance(row_index, int) or row_index < 0:
			raise AssignmentIdentityError(CODE_SERIAL_MISSING, "شمارهٔ ردیف نامعتبر است.")

	return AssignmentValues(
		logistics_serial=serial_name,
		sales_invoice=sales_invoice,
		sales_invoice_item=sales_invoice_item,
		company=company,
		item_code=item_code,
		normalized_serial=serial.normalized,
		invoice_item_idx=row_index,
	)


def reject_supplied_identity(payload):
	"""Refuse a request that carries a server-derived field.

	The check is on the payload a caller sent, before any document is built, so a caller that
	sends `normalized_serial` gets a refusal rather than having it silently ignored.
	"""
	if not isinstance(payload, dict):
		return

	for field in DERIVED_FIELDS:
		if field in payload:
			raise AssignmentIdentityError(
				CODE_IDENTITY_SUPPLIED,
				f"فیلد {field} سمت سرور ساخته می‌شود و نباید در درخواست فرستاده شود.",
			)


def identity_mismatch(stored_normalized, serial_normalized):
	"""Whether a stored copy disagrees with the serial it was copied from.

	Returns a reason code, or `None` when the two agree.
	"""
	if stored_normalized != serial_normalized:
		return CODE_IDENTITY_MISMATCH

	return None


def is_consistent(stored_normalized, serial_normalized):
	"""Whether a stored copy still matches the serial it names."""
	return identity_mismatch(stored_normalized, serial_normalized) is None
