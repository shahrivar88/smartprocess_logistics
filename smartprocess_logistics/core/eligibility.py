"""Whether a serial is required for an invoice row, how many, and what state that row is in.

One rule, stated once, applied where it is decided.

**Mandatory is a property of the item, not of the invoice's age.** An item marked
`sp_serial_required` needs one label per unit sold, and a row short of them is a shortage. An item
that is *not* marked is **not a serial row at all**: it is not required — so it can never be short
— and it is not offered either, because the station shows the rows that can be worked on and a row
with no requirement is a row the report will never ask about.

That is the whole policy:

| item | labels | a shortage | in the report | in the scan station |
| ---- | ------ | ---------- | ------------- | ------------------- |
| `sp_serial_required = 1` | required, one per unit | possible | yes, when short | a row, and the rows a registration is refused on |
| `sp_serial_required = 0` | neither offered nor refused | never | never | no row: it is not in the dialog, and `*N` naming it is answered rather than obeyed |

`core.reports` applies it as `i.sp_serial_required = 1` in the statement, the station applies it by
serving only those rows, `core.validation` applies it by refusing a registration on any other, and
`is_eligible` below is the one predicate all three read. The three must agree: a row the report
calls short and a row the station does not offer would be the app contradicting itself, and a row
the station hides but the endpoints still write to would be a rule enforced in a window and
nowhere else.

What an unmarked row keeps
--------------------------
Registrations an unmarked row already carries are **not deleted, not hidden from the database and
not rewritten**: this module decides what is offered and what may be added, and nothing else. They
stay in `Logistics Serial Assignment` where the Desk list, the API and the export can read them,
and they can still be removed — a removal is not a registration, which is why the rule below is
about what may be *added*. Hiding a row is not a way of losing what is on it.

What this module used to be
---------------------------
It decided which invoices the report *might* cover by comparing `Sales Invoice.creation` with
`Logistics Settings.installed_on`, and left out every invoice older than the app. The reasoning
was that an invoice predating the install cannot be judged — but that is the wrong input. What
decides a shortage is the item's **current** flag, read when the report runs, which says nothing
about when the invoice was written. The date rule therefore excluded real shortages for no reason
and for as long as the site kept its invoices.

So the install timestamp no longer reaches this module, or the report, at all. It keeps the one
job it always had and that this module never had anything to do with: `setup/seeds.py` writes it
and reads it back to know whether the app's own seeding finished, so a half-finished install is
completed on the next run and a finished one is left alone. An upgrade, a reinstall, a restore, a
move to another server or a change to that timestamp cannot change which rows are audited,
because none of them is an input to the rule below.
"""

from smartprocess_logistics.core.quantities import required_units

MANDATORY = "MANDATORY"
OPTIONAL = "OPTIONAL"

# The four states one serial row can be in, as names rather than as colours: the window paints them
# and the server decides them, and neither has to guess what the other means by a number.
INCOMPLETE = "INCOMPLETE"  # fewer labels than the row requires
STAGED = "STAGED"  # exactly its quantity, with labels not yet written
SAVED = "SAVED"  # exactly its quantity, every label in the database
CONFLICT = "CONFLICT"  # more labels than the invoice allows


def kind(serial_required):
	"""Whether a row is a required one or merely permitted one."""
	return MANDATORY if serial_required else OPTIONAL


def requirement(serial_required, invoiced_qty):
	"""How many labels this row *must* carry.

	Zero for an optional item, whatever its quantity: an item nobody marked as needing a serial
	cannot be short of one, and a number here would turn every optional registration into a
	shortage. For a required item it is the invoiced quantity, through the same function the rest
	of the app counts with — magnitude, so a credit note still needs a label per unit returned,
	and fractions round up.
	"""
	if not serial_required:
		return 0

	return required_units(invoiced_qty)


def is_reported(serial_required):
	"""Whether a row may appear in the shortage report at all.

	Only a required item can be short, which is why the report's own statement carries
	`i.sp_serial_required = 1` rather than comparing a requirement of zero with a count.
	"""
	return bool(serial_required)


def is_eligible(serial_required):
	"""Whether a row may be offered for serials, and may be given one.

	The one predicate the three surfaces read: the station serves only these rows, `core.validation`
	refuses a registration anywhere else, and the report audits the same set. An unmarked row is not
	a serial row — which is a decision about *adding* a label, not about the ones already there. The
	registrations an unmarked row holds are neither removed nor rewritten by this rule.
	"""
	return bool(serial_required)


def row_state(required, capacity, persisted, staged=0):
	"""The one state a serial row is in: `INCOMPLETE`, `STAGED`, `SAVED` or `CONFLICT`.

	Four inputs and one answer, and every surface that draws or reports a row reads it from here —
	the window's card, the endpoint beside it, and the tests — so there is no second opinion about
	what "done" means.

	- `required` is how many labels the row must carry, `capacity` the most the invoice allows
	  (the same number for every row this app offers, and two parameters rather than one because
	  they are two different questions: a shortage and an excess).
	- `persisted` is what the **database** holds on the row, `staged` what an operator has in hand
	  and unsaved. The window passes both; the server passes `staged=0`, because it cannot know
	  about a label that has not been sent to it yet — which is exactly why the state is computed
	  where the numbers are, and not inferred from one side by the other.

	The order of the answers is part of the rule. **More than the invoice allows is a conflict
	first**: a row that has to be fixed is not also "nearly done". Then a shortage, then — with
	exactly the required count — whether everything is written (`SAVED`) or something is still in
	hand (`STAGED`). The last distinction is the one an operator acts on, and it is the reason
	`STAGED` is a state of its own rather than a shade of done: a green row is finished work that
	has not been sent, and the blue one is the answer the server gave back.

	`core.quantities.RowCoverage.is_complete` answers a different question and keeps its meaning:
	it is "nothing is missing", which a row over its quantity also satisfies, and the report's
	totals are built on it. This function answers "what is this row", which an over-filled row
	does not satisfy — so an over-filled row is `RowCoverage.is_complete` **and** `CONFLICT`, and
	that is not a contradiction but the two questions being asked.
	"""
	held = persisted + staged

	if held > capacity:
		return CONFLICT

	if held < required:
		return INCOMPLETE

	return STAGED if staged else SAVED
