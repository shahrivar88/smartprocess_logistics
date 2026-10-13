"""The composite unique constraints a DocType JSON cannot declare.

`frappe.db.add_unique` is Frappe's own helper for this. It is idempotent: it checks
`information_schema.TABLE_CONSTRAINTS` for the constraint name before issuing the ALTER, so
running it on every migrate is safe. It commits before the ALTER, which is why it is called
from `after_migrate` and never from a request handler.

These are asserted at install and again on every migrate. Not because v16's schema sync drops
them — it does not, for columns the DocType metadata still declares with `search_index` — but
because the check is one parameterized read of `information_schema`, which makes re-asserting
free, and because a site restored from an older dump or left half-installed would otherwise
never get them.

Why the assignment constraint is on a stored copy
-------------------------------------------------
`logistics_serial` is a link to a record whose identity is `(item_code, normalized_serial)`. A
constraint on it forbids the same serial *record* twice on one invoice, but not the same serial
*text* twice when two rows name different items, because those are two records. The approved
rule is about the text, and only the copy carries the text, so the constraint is declared on
`(sales_invoice, normalized_serial)`.

A third constraint on `(sales_invoice, sales_invoice_item, normalized_serial)` is deliberately
absent: it is implied by the one above.

What no unique constraint can do
--------------------------------
A constraint on the copy cannot keep the copy equal to the serial it was copied from. That is
an application invariant, enforced by deriving the value on the server and refusing to edit an
identity after registration. `core.assignment` holds the rules and the remaining limitation.

Why there is no concatenated key column
---------------------------------------
`Data` columns are `varchar(140)`. A combined `item_code + "::" + normalized_serial` key
reaches 282 characters, so it would be truncated by the database and would then report
unrelated rows as duplicates, blocking a legitimate serial. The constraints below are enforced
on the real columns instead.

Index size: the largest constraint covers two `varchar(140)` columns, which is 1120 bytes
under utf8mb4. Both tables are created with `row_format: Dynamic`, whose index limit is 3072
bytes, so the constraint fits everywhere Frappe runs on MariaDB.

Collation note: the tables are `utf8mb4_unicode_ci`, which compares without regard to case and
without regard to accents. The normalization rule set was chosen to be at least as coarse as
that collation, so the application never accepts two serials the database would then call one.
`tests/test_collation_agreement.py` pins that property against verdicts measured on this
MariaDB build.
"""

import frappe
from frappe import _

UNIQUE_CONSTRAINTS = (
	("Logistics Serial", ("item_code", "normalized_serial"), "unique_item_serial"),
	("Logistics Serial Assignment", ("sales_invoice", "normalized_serial"), "unique_invoice_serial"),
)


def _index_state(doctype, constraint_name):
	"""The columns an index name covers, in order, and whether that index is unique.

	Read from `information_schema`, parameterized, and touching no application table.

	The uniqueness flag is read as well as the columns because two different catalogues are
	involved: this reads `STATISTICS`, which lists indexes, while `frappe.db.add_unique` looks
	the name up in `TABLE_CONSTRAINTS`, which lists unique constraints. A plain, non-unique
	index carrying one of these names would therefore look like a constraint here and be
	invisible there — the guard would skip it and the uniqueness would never be applied, while
	`add_unique` would have failed loudly on the duplicate name. Reading the flag closes that
	gap instead of leaving it to chance.
	"""
	rows = frappe.db.sql(
		"""
		select COLUMN_NAME, NON_UNIQUE
		from information_schema.STATISTICS
		where TABLE_SCHEMA = database()
		  and TABLE_NAME = %(table)s
		  and INDEX_NAME = %(constraint)s
		order by SEQ_IN_INDEX
		""",
		{"table": f"tab{doctype}", "constraint": constraint_name},
	)

	if not rows:
		return (), False

	return tuple(row[0] for row in rows), not any(row[1] for row in rows)


def ensure_unique_constraints():
	"""Create the composite unique constraints. Safe to run repeatedly.

	Three states stop the migration rather than being accepted silently, because in each the
	name would claim to enforce a rule it does not enforce:

	- the name exists over **different** columns;
	- the name exists over the right columns but as a **non-unique** index;
	- `add_unique` returned without the constraint being there afterwards.

	The third is not hypothetical. `frappe.db.add_unique` looks its name up in
	`TABLE_CONSTRAINTS` filtered by **table name only**, while this guard reads
	`information_schema.STATISTICS` filtered by schema *and* table. On a bench hosting a second
	site that runs this app, the two tables share a name across two schemas: the guard sees no
	constraint here, and `add_unique` finds the other site's row, concludes the name is taken and
	returns without altering anything — leaving this site's table without the rule and every later
	migrate repeating the same silent no-op. So the constraint is created and then **looked for**:
	what the migration believes is enforced is read back from the catalogue that enforces it.
	"""
	for doctype, fields, constraint_name in UNIQUE_CONSTRAINTS:
		if not frappe.db.exists("DocType", doctype):
			continue

		existing, is_unique = _index_state(doctype, constraint_name)

		if existing and not is_unique:
			frappe.throw(
				_(
					"{0} on {1} is an index but not a unique constraint, so it does not enforce"
					" what its name claims. Drop it and run migrate again."
				).format(frappe.bold(constraint_name), frappe.bold(doctype)),
				title=_("نام قید توسط یک ایندکس ساده اشغال شده است"),
			)

		if existing and existing != tuple(fields):
			frappe.throw(
				_(
					"Constraint {0} on {1} covers {2} instead of {3}. Drop it and run migrate again;"
					" leaving it would silently enforce a different rule."
				).format(
					frappe.bold(constraint_name),
					frappe.bold(doctype),
					", ".join(existing),
					", ".join(fields),
				),
				title=_("نام قید توسط قاعدهٔ دیگری اشغال شده است"),
			)

		if not existing:
			frappe.db.add_unique(doctype, list(fields), constraint_name)

			created, created_is_unique = _index_state(doctype, constraint_name)

			if not created or not created_is_unique:
				frappe.throw(
					_(
						"{0} on {1} could not be created as a unique constraint. The name may"
						" already exist in another schema of the same database server, which"
						" `frappe.db.add_unique` treats as a duplicate. Give the constraint another"
						" name and run migrate again."
					).format(frappe.bold(constraint_name), frappe.bold(doctype)),
					title=_("قید ساخته نشد"),
				)
