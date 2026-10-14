"""The transfer record's schema, and the promises it makes to `core/transfer.py`.

The two DocTypes that carry a transfer are read by two different kinds of reader: an operator in
the Desk, and a reviewer asking what happened to a serial six months later. Both are served by the
same rows, and the only thing that keeps those rows honest is that they cannot disagree with the
vocabulary the decision was made in. That is what this module checks — the `Select` that stores a
refusal reason is the refusal vocabulary itself, the immutability is a permission and a docstatus
rather than a convention, and the fields that name the source are shaped so that history cannot
break them.

Nothing here needs a site. The DocType JSON is read from disk and the vocabulary is imported from
`core/transfer.py`, which is why the two can be compared at all.
"""

import json

from smartprocess_logistics.core import transfer
from smartprocess_logistics.tests import schema_checks
from smartprocess_logistics.tests.base import UnitTestCase

HEADER = "Logistics Serial Transfer"
CHILD = "Logistics Serial Transfer Item"

# Roles this machine installs and grants on this app's DocTypes; a DocPerm outside this set is a
# privilege nobody asked for and no test below would otherwise notice.
ROLES = set(schema_checks.APP_ROLES) | set(schema_checks.OTHER_ROLES)

# The reasons a *record* can hold, as the vocabulary itself lists them. `UNKNOWN_SELECTION` is
# deliberately absent from that list: it says the operator's view was stale and it names no source
# assignment, so it reaches no row.

# A planner count, and the header field it is written to. The two names differ because the count
# is about the plan and the field is about the record, and neither reads well under the other's
# name.
FIELD_FOR = {
	"source": "source_total",
	"moved": "moved_count",
	"refused": "refused_count",
	"unselected": "unselected_count",
	"acknowledged": "acknowledged_count",
}


def definition(name):
	for path in schema_checks.doctype_paths():
		document = json.loads(path.read_text(encoding="utf-8"))

		if document.get("name") == name:
			return document

	raise AssertionError(f"{name} is not one of this app's DocTypes")


def fieldnames(document):
	"""Every fieldname the DocType declares, layout breaks included."""
	return {entry.get("fieldname") for entry in document.get("fields") or []}


def field(document, fieldname):
	for entry in document.get("fields") or []:
		if entry.get("fieldname") == fieldname:
			return entry

	raise AssertionError(f"{document['name']} has no field {fieldname!r}")


def options(document, fieldname):
	return [option for option in (field(document, fieldname).get("options") or "").split("\n") if option]


class TheRefusalVocabularyTest(UnitTestCase):
	"""The stored reason is the vocabulary, or the record cannot be read back."""

	def test_the_reason_select_offers_exactly_the_recordable_reasons(self):
		"""Both directions: a reason with no option is unstorable, an option with no reason is a
		value nobody can render."""
		self.assertEqual(options(definition(CHILD), "reason"), list(transfer.RECORDABLE_REASONS))

	def test_the_stale_selection_reason_is_not_storable(self):
		"""It names no source assignment, so a row carrying it would be a row about nothing."""
		self.assertNotIn(transfer.REASON_UNKNOWN_SELECTION, options(definition(CHILD), "reason"))

	def test_every_reason_the_select_offers_has_a_sentence_to_show(self):
		"""Driven by the stored options, not by the vocabulary they are built from.

		Iterating `RECORDABLE_REASONS` here would make this test compare the vocabulary with
		itself: that tuple *is* `sorted(REASON_MESSAGES)` minus one, so deleting a sentence
		deletes the case that checks it and the loop passes over what is left. Measured — the
		control that removed `ACKNOWLEDGEMENT_MISSING` from `REASON_MESSAGES` was not caught until
		this test read the DocType instead.
		"""
		for code in options(definition(CHILD), "reason"):
			with self.subTest(reason=code):
				self.assertTrue(transfer.REASON_MESSAGES.get(code, "").strip())

	def test_the_recordable_reasons_are_the_whole_vocabulary_but_one(self):
		self.assertEqual(
			set(transfer.RECORDABLE_REASONS),
			set(transfer.REASON_MESSAGES) - {transfer.REASON_UNKNOWN_SELECTION},
		)

	def test_the_result_select_offers_the_two_outcomes(self):
		self.assertEqual(options(definition(CHILD), "result"), ["Moved", "Refused"])


class TheRecordCannotBeRewrittenTest(UnitTestCase):
	"""Immutability is the framework's, not this app's good manners."""

	def test_the_record_is_submittable(self):
		"""`_validate_update_after_submit` is what refuses a changed field after submission.

		Verified in the source rather than assumed: `base_document._validate_update_after_submit`
		compares every field against the stored document and throws for any that is not
		`allow_on_submit`, and `delete_doc.check_permission_and_not_submitted` refuses a submitted
		document to every user, the Administrator included.
		"""
		self.assertEqual(definition(HEADER).get("is_submittable"), 1)

	def test_no_role_may_edit_a_record(self):
		for row in definition(HEADER)["permissions"]:
			with self.subTest(role=row.get("role")):
				self.assertFalse(row.get("write"), "an editor of the record could rewrite history")
				self.assertFalse(
					row.get("cancel"), "a cancelled record would read as one that never happened"
				)

	def test_no_role_may_delete_a_record(self):
		for row in definition(HEADER)["permissions"]:
			with self.subTest(role=row.get("role")):
				self.assertFalse(row.get("delete"))

	def test_no_role_may_amend_a_record(self):
		for row in definition(HEADER)["permissions"]:
			with self.subTest(role=row.get("role")):
				self.assertFalse(row.get("amend"))

	def test_every_role_that_can_create_can_also_submit(self):
		"""A creator that cannot submit would leave a draft nobody can submit, and a draft is
		editable — the one state this record must never be in."""
		for row in definition(HEADER)["permissions"]:
			if row.get("create"):
				with self.subTest(role=row.get("role")):
					self.assertTrue(row.get("submit"))

	def test_the_roles_are_the_ones_this_app_installs(self):
		granted = {row.get("role") for row in definition(HEADER)["permissions"]}

		self.assertEqual(granted, ROLES)

	def test_the_viewer_may_read_and_nothing_else(self):
		viewer = next(
			row for row in definition(HEADER)["permissions"] if row.get("role") == "Logistics Serial Viewer"
		)

		self.assertTrue(viewer.get("read"))
		for flag in ("create", "write", "delete", "submit", "cancel", "amend"):
			with self.subTest(flag=flag):
				self.assertFalse(viewer.get(flag))


class TheRecordNamesWhatItMovedTest(UnitTestCase):
	"""Every row points at a source serial, and nothing can erase that pointer."""

	def test_the_child_requires_the_serial_the_item_and_the_identity(self):
		for fieldname in ("logistics_serial", "item_code", "normalized_serial"):
			with self.subTest(field=fieldname):
				self.assertTrue(field(definition(CHILD), fieldname).get("reqd"))

	def test_the_source_assignment_is_a_plain_name_and_not_a_link(self):
		"""A Link would tie a historical fact to a row that may not exist later.

		The cancelled invoice is frozen — a cancelled document refuses writes — but an assignment
		on it is still an ordinary document an Editor may delete, and this record must survive
		that.
		"""
		self.assertEqual(field(definition(CHILD), "source_assignment")["fieldtype"], "Data")

	def test_the_serial_text_and_the_item_are_read_from_the_serial(self):
		"""So the row cannot show a serial or an item that disagrees with the serial it names."""
		for fieldname, source in (("serial_no", "serial_no"), ("item_code", "item_code")):
			with self.subTest(field=fieldname):
				self.assertEqual(
					field(definition(CHILD), fieldname).get("fetch_from"),
					f"logistics_serial.{source}",
				)

	def test_the_target_row_is_recorded_by_name(self):
		"""A row index moves when a row is added; the row's own name does not."""
		self.assertEqual(field(definition(CHILD), "target_row")["fieldtype"], "Data")

	def test_the_header_holds_the_child_table(self):
		self.assertEqual(field(definition(HEADER), "items").get("options"), CHILD)
		self.assertEqual(field(definition(HEADER), "items")["fieldtype"], "Table")


class TheSourceInvoiceIsANameTest(UnitTestCase):
	"""A submitted DocType may not link a cancelled document, and the source is always cancelled.

	Verified in the source, because the shape of this field looks like a mistake otherwise:
	`base_document.get_invalid_links` collects any link to a `docstatus = 2` document when both
	the DocType and the linked DocType are submittable, `_validate_links` raises
	`CancelledLinkError` for the set, and the only exemption in the whole function is a field
	whose name is exactly `amended_from` — which this record cannot use, because a document with
	that field set is an amendment and the framework then requires the linked document to be of
	the same DocType and cancelled.
	"""

	def test_the_source_invoice_is_a_name_and_not_a_link(self):
		self.assertEqual(field(definition(HEADER), "source_invoice")["fieldtype"], "Data")

	def test_it_is_still_required_and_searchable(self):
		source = field(definition(HEADER), "source_invoice")

		self.assertTrue(source.get("reqd"))
		self.assertTrue(source.get("search_index"))

	def test_the_target_invoice_stays_a_link(self):
		"""The other end is a live document an operator may want to open from here."""
		self.assertEqual(field(definition(HEADER), "target_invoice")["fieldtype"], "Link")


class TheRecordThePlannerBuildsTest(UnitTestCase):
	"""What `core.transfer.record_of` writes is what the DocTypes have, key for key.

	This is the seam where the pure layer and the schema meet, and neither can check the other
	alone: the planner has no idea what a field is called on disk, and this module cannot tell
	what the planner will write. A key added to one and not the other is an insert that fails on
	a live site, or a column that stays empty forever.
	"""

	def sample(self):
		"""The smallest transfer that decides both ways: one moved, one refused."""
		serials = (
			transfer.SourceSerial(
				assignment="LSA-0001",
				serial="W-1",
				normalized="w-1",
				item_code="PR1",
				row_key="row-1",
				row_index=1,
				serial_ref="LS-1",
				version=2,
			),
			transfer.SourceSerial(
				assignment="LSA-0002",
				serial="W-2",
				normalized="w-2",
				item_code="PR2",
				row_key="row-2",
				row_index=2,
				serial_ref="LS-2",
				version=2,
			),
		)
		rows = (
			transfer.TargetRow(row_key="t-1", item_code="PR1", capacity=1, serial_required=True, index=1),
		)
		for_request = transfer.TransferRequest(
			source_invoice="IN-1",
			target_invoice="IN-1-1",
			source_company="C",
			target_company="C",
			source_cancelled=True,
			target_cancelled=False,
			target_amends_source=True,
			serials=serials,
			rows=rows,
		)

		return transfer.record_of(transfer.plan_transfer(for_request), for_request)

	def test_the_record_is_the_header_fields_and_its_items(self):
		self.assertEqual(
			sorted(self.sample()),
			[
				"acknowledged_count",
				"company",
				"confirmed_token",
				"items",
				"moved_count",
				"refused_count",
				"source_invoice",
				"source_total",
				"target_invoice",
				"unselected_count",
			],
		)

	def test_every_header_key_is_a_field_of_the_header_doctype(self):
		"""`items` is one of them: it is the name of the Table field, not a special key."""
		self.assertEqual(set(self.sample()) - fieldnames(definition(HEADER)), set())

	def test_both_outcomes_are_represented_so_the_checks_cover_both(self):
		self.assertEqual({item["result"] for item in self.sample()["items"]}, {"Moved", "Refused"})

	def test_the_item_keys_are_exactly_the_child_doctypes_fields(self):
		"""Both directions: a key with no field is a failed insert, a field no key fills is a
		column that reads as empty on every record this app ever writes."""
		written = set()

		for item in self.sample()["items"]:
			written |= set(item)

		self.assertEqual(written, fieldnames(definition(CHILD)))


class TheHeaderReportsWhatThePlannerDecidedTest(UnitTestCase):
	"""The five numbers an operator is shown are the five the record stores."""

	def test_every_count_the_planner_returns_has_a_field_to_land_in(self):
		"""`unresolved` is the one exception, and it is an exception on purpose: an apply refuses
		the whole request when it is not zero, so a stored record always has it at zero and a
		field that is always zero says nothing."""
		counts = transfer.TransferPlan().counts()

		self.assertEqual(
			sorted(counts),
			["acknowledged", "moved", "refused", "source", "unresolved", "unselected"],
		)

		for key, fieldname in FIELD_FOR.items():
			with self.subTest(count=key):
				self.assertEqual(
					field(definition(HEADER), fieldname)["fieldtype"], "Int", f"{key} has no field"
				)

	def test_no_count_can_be_typed_into(self):
		for fieldname in FIELD_FOR.values():
			with self.subTest(field=fieldname):
				self.assertTrue(field(definition(HEADER), fieldname).get("read_only"))

	def test_the_confirmed_selection_cannot_be_rewritten_either(self):
		self.assertTrue(field(definition(HEADER), "confirmed_token").get("read_only"))

	def test_the_record_is_scoped_to_one_company(self):
		"""Both invoices must be in it; the field is what a User Permission restricts."""
		company = field(definition(HEADER), "company")

		self.assertTrue(company.get("reqd"))
		self.assertEqual(company["options"], "Company")

	def test_the_record_is_searchable_by_the_two_invoices_and_the_company(self):
		self.assertEqual(definition(HEADER).get("search_fields"), "source_invoice,target_invoice,company")
