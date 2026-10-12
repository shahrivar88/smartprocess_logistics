"""The composite unique constraints a DocType JSON cannot declare.

`frappe.db.add_unique` is Frappe's own helper for this. It is idempotent: it checks
`information_schema.TABLE_CONSTRAINTS` for the constraint name before issuing the ALTER, so
running it on every migrate is safe. It commits before the ALTER, which is why it is called
from `after_migrate` and never from a request handler.

The DocType schema sync may rebuild a table, so these are declared again on every migrate
rather than created once at install.

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


def _constraint_columns(doctype, constraint_name):
	"""The columns a constraint name currently covers, in order, or `()` when absent.

	Read from `information_schema`, parameterized, and touching no application table.
	"""
	rows = frappe.db.sql(
		"""
		select COLUMN_NAME
		from information_schema.STATISTICS
		where TABLE_SCHEMA = database()
		  and TABLE_NAME = %(table)s
		  and INDEX_NAME = %(constraint)s
		order by SEQ_IN_INDEX
		""",
		{"table": f"tab{doctype}", "constraint": constraint_name},
	)

	return tuple(row[0] for row in rows)


def ensure_unique_constraints():
	"""Create the composite unique constraints. Safe to run repeatedly.

	A constraint whose name already exists over *different* columns stops the migration
	instead of being silently accepted, because the name would then claim to enforce a rule it
	does not enforce.
	"""
	for doctype, fields, constraint_name in UNIQUE_CONSTRAINTS:
		if not frappe.db.exists("DocType", doctype):
			continue

		existing = _constraint_columns(doctype, constraint_name)

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
				title=_("Constraint name is taken by a different rule"),
			)

		if not existing:
			frappe.db.add_unique(doctype, list(fields), constraint_name)
