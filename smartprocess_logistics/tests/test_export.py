"""The portable export and the import plan: what leaves the site, and what may come back.

The first test in this file is the one that matters. Everything else here checks a refusal or a
decision; that one checks that the format is **lossless** — by comparing its field list with the
DocType definitions in this repository, so a field added to `Logistics Serial` does not quietly
stop being exported. A promise in a docstring cannot do that.
"""

import json
import pathlib

from smartprocess_logistics.core import export, transfer
from smartprocess_logistics.tests.base import UnitTestCase

APP_ROOT = pathlib.Path(__file__).resolve().parents[2] / "smartprocess_logistics"
DOCTYPE_DIR = APP_ROOT / "smartprocess_logistics" / "doctype"

SKIPPED_FIELDTYPES = ("Section Break", "Column Break", "Tab Break")

# Columns every Frappe table has and no DocType lists.
FRAMEWORK_COLUMNS = frozenset({"name", "creation", "modified", "owner", "modified_by"})

# A child table is the one DocType where a field the definition carries stores nothing on the row:
# a `Table` field is a link to the children, whose own rows travel in their own section. It is the
# only such field, and it is named here rather than filtered silently so that a real column cannot
# escape the comparison by looking like structure.
STRUCTURAL_FIELDTYPES = ("Table",)


def doctype_fields(foldername):
	"""Every field name of one shipped DocType, read from its own definition."""
	path = next((DOCTYPE_DIR / foldername).glob("*.json"))
	document = json.loads(path.read_text(encoding="utf-8"))

	return tuple(
		field["fieldname"]
		for field in document["fields"]
		if field.get("fieldtype") not in SKIPPED_FIELDTYPES
		and field.get("fieldtype") not in STRUCTURAL_FIELDTYPES
	)


def live_serial(name="LS-2026-00001", **overrides):
	row = {
		"name": name,
		"serial_no": "W-0001",
		"normalized_serial": "w-0001",
		"item_code": "ITEM-1",
		"item_name": "کالای یک",
		"status": "Active",
		"first_registered_on": "2026-01-01 00:00:00",
		"normalization_version": 2,
		"remarks": "یادداشت",
		"creation": "2026-01-01 00:00:00",
		"modified": "2026-01-02 00:00:00",
		"owner": "Administrator",
		"modified_by": "Administrator",
	}

	row.update(overrides)

	return row


def live_assignment(name="LSA-2026-00001", **overrides):
	row = {
		"name": name,
		"logistics_serial": "LS-2026-00001",
		"item_code": "ITEM-1",
		"normalized_serial": "w-0001",
		"sales_invoice": "SINV-1",
		"company": "COMPANY-A",
		"sales_invoice_item": "row-1",
		"invoice_item_idx": 1,
		"remarks": "",
		"creation": "2026-01-01 00:00:00",
		"modified": "2026-01-02 00:00:00",
		"owner": "Administrator",
		"modified_by": "Administrator",
	}

	row.update(overrides)

	return row


def document(serials=None, assignments=None):
	return export.build(
		serials if serials is not None else [live_serial()],
		assignments if assignments is not None else [live_assignment()],
		app_version="0.3.0",
		site="a-site",
		exported_by="Administrator",
		exported_on="2026-10-14 09:00:00",
		normalization_version=2,
	)


def world(**overrides):
	defaults = {
		"serial_identities": frozenset(),
		"serials_by_identity": {},
		"items": frozenset({"ITEM-1"}),
		"companies": frozenset({"COMPANY-A"}),
		"invoices": {"SINV-1": "COMPANY-A"},
		"invoice_rows": frozenset({("SINV-1", "row-1")}),
		"assignments_by_identity": {},
	}

	defaults.update(overrides)

	return export.World(**defaults)


class LosslessFormatTest(UnitTestCase):
	"""The format covers every field of both DocTypes, and is checked rather than promised."""

	def test_every_serial_field_of_the_doctype_is_exported(self):
		self.assert_lossless("logistics_serial", export.SERIAL_FIELDS)

	def test_every_assignment_field_of_the_doctype_is_exported(self):
		self.assert_lossless("logistics_serial_assignment", export.ASSIGNMENT_FIELDS)

	def test_every_transfer_field_of_the_doctype_is_exported(self):
		"""The audit records are the app's own history and an uninstall drops their tables.

		They are carried in the file for that reason alone — an import never re-creates one — and a
		field left out of the format is a field an uninstall destroys.
		"""
		# `docstatus` is the framework's column on every submittable table, and it is carried on
		# purpose: the record's whole guarantee is that it is inserted submitted and never edited,
		# so a reader of the file should be able to see that rather than take it on trust.
		self.assert_lossless(
			"logistics_serial_transfer", export.TRANSFER_FIELDS, extra=FRAMEWORK_COLUMNS | {"docstatus"}
		)

	def test_every_transfer_item_field_of_the_doctype_is_exported(self):
		self.assert_lossless(
			"logistics_serial_transfer_item",
			export.TRANSFER_ITEM_FIELDS,
			extra=FRAMEWORK_COLUMNS | {"parent"},
		)

	def assert_lossless(self, foldername, fields, extra=FRAMEWORK_COLUMNS):
		"""Every field the DocType defines, and nothing beyond the columns it really has.

		The five extras are real columns on every Frappe table that the DocType JSON does not
		list: the row's name, its timestamps and its authorship. They are what makes an export
		lossless rather than merely complete, so they belong in the format — and their presence is
		asserted, not overlooked, so a sixth one cannot appear unnoticed.

		`extra` is what a child table adds to that set. A child row has a `parent` column, which is
		the only one of the four child-table columns the format needs: `parenttype` and `parentfield`
		follow from the parent, and `idx` is a display order the export does not preserve.
		"""
		defined = set(doctype_fields(foldername))

		self.assertTrue(defined <= set(fields), sorted(defined - set(fields)))
		self.assertEqual(set(fields) - defined, extra)

	def test_the_scan_reads_the_definitions_it_claims_to(self):
		"""A comparison that read nothing would pass for the wrong reason."""
		self.assertIn("serial_no", doctype_fields("logistics_serial"))
		self.assertIn("sales_invoice", doctype_fields("logistics_serial_assignment"))

	def test_a_built_document_holds_every_value_of_every_row(self):
		built = document()
		row = built["serials"][0]

		for field in export.SERIAL_FIELDS:
			with self.subTest(field=field):
				self.assertEqual(row[field], live_serial()[field])


class DocumentTest(UnitTestCase):
	def test_a_document_reads_back_as_what_was_written(self):
		built = document()

		parsed = export.read(export.as_json(built))

		self.assertEqual(parsed["digest"], built["digest"])
		self.assertEqual(len(parsed["serials"]), 1)
		self.assertEqual(parsed["site"], "a-site")

	def test_the_same_rows_produce_the_same_digest(self):
		self.assertEqual(document()["digest"], document()["digest"])

	def test_changing_a_value_changes_the_digest(self):
		built = document()
		built["serials"][0]["serial_no"] = "W-9999"

		with self.assertRaises(export.ExportError) as raised:
			export.read(built)

		self.assertEqual(raised.exception.code, export.CODE_DIGEST_MISMATCH)

	def test_a_file_that_is_not_json_is_refused(self):
		with self.assertRaises(export.ExportError) as raised:
			export.read("{ not json")

		self.assertEqual(raised.exception.code, export.CODE_NOT_JSON)

	def test_a_file_from_another_app_is_refused(self):
		with self.assertRaises(export.ExportError) as raised:
			export.read(json.dumps({"format": "something-else", "format_version": 1}))

		self.assertEqual(raised.exception.code, export.CODE_NOT_OURS)

	def test_a_file_from_a_newer_format_is_refused(self):
		built = document()
		built["format_version"] = export.FORMAT_VERSION + 1
		built["digest"] = export.document_digest(built)

		with self.assertRaises(export.ExportError) as raised:
			export.read(built)

		self.assertEqual(raised.exception.code, export.CODE_FORMAT_TOO_NEW)

	def test_a_file_missing_a_section_is_refused(self):
		built = document()
		del built["assignments"]
		built["digest"] = export.document_digest(built)

		with self.assertRaises(export.ExportError) as raised:
			export.read(built)

		self.assertEqual(raised.exception.code, export.CODE_BAD_SHAPE)

	def test_a_header_whose_counts_disagree_with_the_rows_is_refused(self):
		built = document()
		built["counts"]["serials"] = 5
		built["digest"] = export.document_digest(built)

		with self.assertRaises(export.ExportError) as raised:
			export.read(built)

		self.assertEqual(raised.exception.code, export.CODE_BAD_SHAPE)

	def test_a_row_without_its_identity_is_refused(self):
		built = document(serials=[live_serial(normalized_serial="")])

		with self.assertRaises(export.ExportError) as raised:
			export.read(export.as_json(built))

		self.assertEqual(raised.exception.code, export.CODE_ROW_INVALID)
		self.assertIn("normalized_serial", str(raised.exception))

	def test_two_rows_with_one_identity_are_refused(self):
		built = document(serials=[live_serial("LS-1"), live_serial("LS-2")])

		with self.assertRaises(export.ExportError) as raised:
			export.read(export.as_json(built))

		self.assertEqual(raised.exception.code, export.CODE_ROW_INVALID)

	def test_a_row_that_is_not_an_object_is_refused(self):
		built = document()
		built["serials"] = ["not a row"]
		built["digest"] = export.document_digest(built)

		with self.assertRaises(export.ExportError) as raised:
			export.read(built)

		self.assertEqual(raised.exception.code, export.CODE_ROW_INVALID)


class WorldNamesTest(UnitTestCase):
	"""The names the plan asks the site about come from the file, not from the site.

	A world assembled only from what is already stored answers about the wrong set — and an import
	into a freshly reinstalled site, whose tables are empty by definition, then calls every row a
	conflict. Measured in the lab, on a five-row export whose item was on the site throughout.
	"""

	def test_the_names_come_from_the_document(self):
		names = export.world_names(document())

		self.assertEqual(names["item_codes"], ("ITEM-1",))
		self.assertEqual(names["company_names"], ("COMPANY-A",))
		self.assertEqual(names["invoice_names"], ("SINV-1",))
		self.assertEqual(names["serial_names"], ("LS-2026-00001",))

	def test_every_name_is_unique_and_sorted(self):
		built = document(
			serials=[
				live_serial("LS-1"),
				live_serial("LS-2", serial_no="W-0002", normalized_serial="w-0002"),
			],
			assignments=[
				live_assignment("LSA-1"),
				live_assignment(
					"LSA-2",
					sales_invoice="SINV-2",
					normalized_serial="w-0002",
					logistics_serial="LS-2",
				),
			],
		)

		names = export.world_names(built)

		self.assertEqual(names["invoice_names"], ("SINV-1", "SINV-2"))
		self.assertEqual(len(names["item_codes"]), len(set(names["item_codes"])))

	def test_a_record_s_invoices_are_names_the_world_asks_about(self):
		"""The section that was left out, and the failure it produced on a real file.

		A record names the invoice it moved serials to, and the record mode asks whether that
		invoice is here — so a file whose assignment section is empty (a site whose registrations
		have since been removed, which is exactly what a recovery is for) named no invoice at all
		and every record came back `INVOICE_MISSING` with its amendment sitting on the site.
		"""
		built = export.build(
			[],
			[],
			[
				{
					"name": "LST-2026-00001",
					"source_invoice": "SINV-CANCELLED",
					"target_invoice": "SINV-1",
					"company": "COMPANY-A",
					"docstatus": 1,
				}
			],
			[],
			app_version="0.3.0",
			site="a-site",
			exported_by="Administrator",
			exported_on="2026-10-14 09:00:00",
			normalization_version=2,
		)

		names = export.world_names(built)

		self.assertEqual(names["invoice_names"], ("SINV-1", "SINV-CANCELLED"))
		self.assertEqual(names["company_names"], ("COMPANY-A",))

	def test_an_empty_document_names_nothing(self):
		self.assertEqual(
			export.world_names(export.build([], [])),
			{"item_codes": (), "company_names": (), "invoice_names": (), "serial_names": ()},
		)

	def test_the_endpoints_ask_about_the_documents_names(self):
		"""The bug this test exists for: the world was built from the site's own empty tables.

		Three calls, not two: the preview, the apply's first plan — which is what tells it which
		invoices to lock — and the apply's plan again, rebuilt under those locks.
		"""
		source = (APP_ROOT / "api" / "endpoints.py").read_text(encoding="utf-8")

		self.assertEqual(source.count("gateway.transfer_world(principal, **export.world_names(document))"), 3)


class TransferRequestContractTest(UnitTestCase):
	"""The contract between the two buttons and the two endpoints, pinned as text.

	A real browser test found the failure this class exists for: the preview's payload was read
	from the wrong level of the response — `frappe.call` resolves with the response it received,
	whose payload is under `message` — so the dialog had no numbers and the apply request carried
	`token: undefined`. The server refused it, correctly, with a `TypeError` about a missing
	argument. Neither half was wrong on its own; the seam between them was, and a seam nothing
	tests is a seam that breaks in a browser rather than in a suite.
	"""

	def js(self):
		return (APP_ROOT / "public" / "js" / "logistics_settings_transfer.js").read_text(encoding="utf-8")

	def endpoints_source(self):
		return (APP_ROOT / "api" / "endpoints.py").read_text(encoding="utf-8")

	def test_the_preview_reads_the_payload_from_the_response_message(self):
		source = self.js()

		self.assertIn("const preview = (response && response.message) || {};", source)

	def test_nothing_tries_to_unwrap_a_response_in_a_callback(self):
		"""A `callback:` option's return value is discarded, which is what made the first version
		look like it unwrapped the response and did not."""
		self.assertNotIn("callback: (response) => response.message", self.js())

	def test_the_apply_request_carries_the_file_and_the_token(self):
		source = self.js()

		self.assertIn("args: { content: file.content, token: token },", source)

	def test_the_apply_is_refused_in_the_browser_without_a_token(self):
		"""The server requires it; a UI that sends a request it knows will be refused is a bug."""
		source = self.js()

		self.assertIn("if (!token) {", source)
		self.assertIn("نشانهٔ تأیید پیش‌نمایش در دسترس نیست", source)

	def test_a_second_submission_while_one_is_in_flight_is_dropped(self):
		source = self.js()

		self.assertIn("let applying = false;", source)
		self.assertIn("if (applying) {", source)
		self.assertIn("applying = true;", source)
		self.assertIn("applying = false;", source)

	def test_the_dialog_shows_the_counts_and_asks_for_confirmation(self):
		source = self.js()

		self.assertIn("برای ثبت: {0} سریال و {1} ردیف.", source)
		self.assertIn("قبلاً موجود: {0} سریال و {1} ردیف.", source)
		self.assertIn("قابل ثبت نیست: {0} ردیف.", source)
		# The label is the mode's own, now that there are two restores: the ordinary one says
		# «ثبت کن» and the historical one «بازآوری کن», and neither is written where the dialog is.
		self.assertIn("primary_action_label: restore.confirm_label,", source)
		self.assertIn('confirm_label: __("ثبت کن")', source)
		self.assertIn('confirm_label: __("بازآوری کن")', source)

	def test_the_server_compares_the_token_with_the_documents_own_digest(self):
		source = self.endpoints_source()

		self.assertIn("def apply_serial_restore(content=None, token=None):", source)
		self.assertIn('if token != document.get("digest"):', source)

	def test_a_request_with_no_token_is_refused_in_words(self):
		"""Not with `missing 1 required positional argument`, which is what a browser saw."""
		source = self.endpoints_source()

		self.assertIn("if not token:", source)
		self.assertIn("این درخواست نشانهٔ تأیید پیش‌نمایش را همراه ندارد", source)

	def test_the_token_is_the_documents_digest_and_nothing_else(self):
		"""So it is bound to the content: change a byte of the file and the token stops matching."""
		built = document()
		token = built["digest"]
		tampered = dict(built)

		tampered["serials"] = [dict(built["serials"][0], serial_no="LAB-W-9999")]

		self.assertEqual(token, export.document_digest(built))
		self.assertNotEqual(token, export.document_digest(tampered))

		with self.assertRaises(export.ExportError):
			export.read(tampered)


class RestoreLockTest(UnitTestCase):
	"""The apply endpoint locks what it is about to write to, then plans again under the lock.

	A database-free test cannot make two restores race, so what is pinned here is the shape that
	makes the race safe — the same shape every other write in this app has. What it protects
	against is a rewrite that drops the second plan because it looks redundant.
	"""

	def source(self):
		return (APP_ROOT / "api" / "endpoints.py").read_text(encoding="utf-8")

	def body(self):
		marker = "def apply_serial_restore"
		start = self.source().index(marker)

		return self.source()[start : self.source().index("def _read_export", start)]

	def test_it_locks_the_invoices_it_is_about_to_write_to(self):
		body = self.body()

		self.assertIn("gateway.lock_invoice(locked)", body)
		# Every invoice either list names, and the loop runs over that one set: an assignment names
		# the invoice it is written on, a record names the one it points at, and a caller who may not
		# reach a record's target may not be told the record exists.
		self.assertIn("for locked in sorted(invoice for invoice in named if invoice):", body)
		self.assertIn('row.get("target_invoice") for row in plan.transfers', body)

	def test_a_taken_record_name_is_reported_as_a_taken_name(self):
		"""The records' insert has its own handler, and its sentence is about the name.

		Found in the phase-20 security review: a record name held by **another company** is invisible
		to the pre-flight, because `world.transfers` is company-scoped by design, so the collision
		reaches the insert — and the sentence there used to be the one for a concurrent request,
		which invites a retry that can never succeed. The same mistake the ordinary restore was cured
		of in phase 18, in the one place it was left.

		Asserted on the order of the two handlers rather than on either sentence alone: the record
		insert must come *after* the assignments' handler has closed, or the sentence for a serial
		collision would be the one an operator sees for a record name.
		"""
		body = self.body()
		records = body.index("created_records = gateway.insert_transfers(")
		assignments = body.index("created_assignments = gateway.insert_exported(")

		self.assertLess(assignments, records, "the records are written last, after the rows")

		after = body[records:]
		self.assertIn("نام یکی از سابقه‌های این فایل", after)
		self.assertNotIn("هم‌زمان درخواست دیگری همین سریال‌ها", after)

	def test_the_lock_comes_before_anything_is_written(self):
		body = self.body()

		self.assertLess(body.index("gateway.lock_invoice(locked)"), body.index("insert_exported"))

	def test_the_plan_is_computed_again_after_the_lock(self):
		"""The first plan only says which invoices to lock; the one that is written is the second."""
		body = self.body()

		self.assertLess(body.index("gateway.lock_invoice(locked)"), body.rindex("export.plan("))
		self.assertLess(body.rindex("export.plan("), body.index("insert_exported"))

	def test_each_restore_passes_its_own_mode_and_the_body_receives_it(self):
		"""The four endpoints differ in one value and one marker, and the shared body reads both.

		`MODE_LIVE` is the ordinary restore, and the tests above this one are about it: were it ever
		to pass another mode, a cancelled invoice would silently start accepting registrations
		through the endpoint an operator uses every day, or a file would start writing audit records
		nobody asked it to.
		"""
		source = self.source()

		for endpoint, mode in (
			("apply_serial_restore", "MODE_LIVE"),
			("restore_cancelled_serial_history", "MODE_HISTORY"),
			("restore_audit_records", "MODE_RECORDS"),
		):
			with self.subTest(endpoint=endpoint):
				self.assertIn(f'_restore_export("{endpoint}", export.{mode}, content, token)', source)

		body = self.body()

		self.assertEqual(body.count("mode=mode"), 2, "both plans must be told the mode")
		self.assertEqual(body.count("stamp=_stamp(principal, document)"), 2, "and the marker")

		# Scoped to the shared implementation: the body this reads runs from the first endpoint to
		# the next helper, so it contains the *other* endpoints — whose own bodies legitimately name
		# their own modes. Asserting over the whole span forbids them from doing so, which is the
		# same over-broad slice this test had to be narrowed out of once before.
		shared = body[body.index("def _restore_export(") :]

		self.assertNotIn("MODE_RECORDS", shared, "the shared body names no mode of its own")
		self.assertNotIn("MODE_HISTORY", shared)

	def test_the_record_writer_is_the_only_thing_that_places_a_record(self):
		"""A record and its rows are one document, so they are written by one call and not as rows."""
		source = self.source()

		self.assertIn(
			"created_records = gateway.insert_transfers(plan.transfers, plan.transfer_items)", source
		)
		self.assertIn("gateway.insert_exported(export.SERIAL_DOCTYPE, plan.serials)", source)
		self.assertIn("gateway.insert_exported(export.ASSIGNMENT_DOCTYPE, plan.assignments)", source)


class CancelledInvoiceTest(UnitTestCase):
	"""A cancelled invoice takes no registration, and a restored file is not a way around it.

	Every other write path applies `core.authz.require_live_invoice`, and this — the one that does
	not go through `core.validation` — applied nothing at all: a file naming a cancelled invoice
	was written to it, which is the state the app documents it never creates. The refusal is the
	same kind as any other conflict: the row is reported and left alone, so a file that mixes a
	cancelled invoice with live ones restores what it can and names what it cannot.
	"""

	def test_a_cancelled_invoice_is_refused(self):
		plan = export.plan(document(), world(cancelled_invoices=frozenset({"SINV-1"})))

		# The write this rule is about is the **assignment** — a registration on the invoice — and
		# none is planned. The catalogue row the file carries is planned as usual: a refused
		# assignment has never stopped a serial row from being created, for this reason or any
		# other, and that behaviour is unchanged here rather than special-cased.
		self.assertEqual(plan.assignments, ())
		self.assertEqual(len(plan.serials), 1)
		self.assertEqual([conflict.reason for conflict in plan.conflicts], [export.REASON_INVOICE_CANCELLED])

	def test_the_refusal_names_the_invoice_and_reads_as_a_sentence(self):
		plan = export.plan(document(), world(cancelled_invoices=frozenset({"SINV-1"})))
		conflict = plan.conflicts[0]

		self.assertEqual(conflict.detail, "SINV-1")
		self.assertIn("لغو", conflict.describe())

	def test_the_refusal_names_the_endpoint_that_can_place_the_row(self):
		"""An operator holding a file of cancelled invoices needs to know where to take it.

		The ordinary preview says these rows will not be placed; without the other half of the
		sentence the file is one they cannot use and nothing tells them why. The name here is the
		one the endpoint is registered under and the one its button carries.
		"""
		plan = export.plan(document(), world(cancelled_invoices=frozenset({"SINV-1"})))

		self.assertIn("بازآوری تاریخچهٔ فاکتورهای لغو‌شده", plan.conflicts[0].describe())

	def test_a_mixed_batch_places_the_live_rows_and_reports_the_cancelled_one(self):
		"""The case a whole-file refusal would get wrong: only the cancelled row is left alone."""
		document_with_two = document(
			serials=[
				live_serial(),
				live_serial(name="LS-2026-00002", serial_no="W-0002", normalized_serial="w-0002"),
			],
			assignments=[
				live_assignment(),
				live_assignment(
					name="LSA-2026-00002",
					sales_invoice="SINV-2",
					sales_invoice_item="row-2",
					logistics_serial="LS-2026-00002",
					normalized_serial="w-0002",
				),
			],
		)
		live = world(
			invoices={"SINV-1": "COMPANY-A", "SINV-2": "COMPANY-A"},
			invoice_rows=frozenset({("SINV-1", "row-1"), ("SINV-2", "row-2")}),
			cancelled_invoices=frozenset({"SINV-1"}),
		)

		plan = export.plan(document_with_two, live)

		self.assertEqual([row.get("sales_invoice") for row in plan.assignments], ["SINV-2"])
		self.assertEqual([conflict.reason for conflict in plan.conflicts], [export.REASON_INVOICE_CANCELLED])

	def test_a_live_invoice_is_untouched_by_the_rule(self):
		plan = export.plan(document(), world(cancelled_invoices=frozenset({"SINV-OTHER"})))

		self.assertEqual(len(plan.assignments), 1)
		self.assertEqual(plan.conflicts, ())


class PlanTest(UnitTestCase):
	def test_a_fresh_site_gets_everything(self):
		plan = export.plan(document(), world())

		self.assertEqual(len(plan.serials), 1)
		self.assertEqual(len(plan.assignments), 1)
		self.assertEqual(plan.conflicts, ())

	def test_a_second_import_creates_nothing(self):
		"""The idempotency the whole feature rests on, at the level of the plan."""
		live = world(
			serial_identities=frozenset({("ITEM-1", "w-0001")}),
			serials_by_identity={("ITEM-1", "w-0001"): "LS-2026-00001"},
			assignments_by_identity={("SINV-1", "w-0001"): "LS-2026-00001"},
		)

		plan = export.plan(document(), live)

		self.assertEqual(plan.serials, ())
		self.assertEqual(plan.assignments, ())
		self.assertEqual(len(plan.skipped_serials), 1)
		self.assertEqual(len(plan.skipped_assignments), 1)
		self.assertEqual(plan.conflicts, ())

	def test_a_serial_the_site_has_under_another_name_is_skipped_and_its_row_is_placed(self):
		live = world(
			serial_identities=frozenset({("ITEM-1", "w-0001")}),
			serials_by_identity={("ITEM-1", "w-0001"): "LS-RENAMED"},
		)

		plan = export.plan(document(), live)

		self.assertEqual(plan.serials, ())
		self.assertEqual(len(plan.skipped_serials), 1)
		self.assertEqual(len(plan.assignments), 1)

	def test_a_serial_whose_name_is_taken_is_skipped_although_its_identity_is_invisible(self):
		"""The lab's own case: a restricted caller, and a serial of another company's invoice.

		The identity is not in this world at all — the only assignment carrying it sits on an
		invoice the caller cannot see — so the plan would create the master, and the insert would
		collide with a primary key the world never showed. Measured: `DuplicateEntryError` on
		`Logistics Serial`, reported to the operator as somebody else's concurrent request, with
		the caller's own rows lost with it. A name is unique over the site and can be scoped to no
		company, so the name alone answers it.
		"""
		live = world(serial_names=frozenset({"LS-2026-00001"}))

		plan = export.plan(document(), live)

		self.assertEqual(plan.serials, ())
		self.assertEqual(len(plan.skipped_serials), 1)

	def test_a_taken_name_stops_the_insert_and_decides_nothing_else(self):
		"""I28's boundary, as the two facts it is made of.

		`World.serial_names` may only stop an insert. It never moves a placement, never renames
		one, and never decides anything about a row whose serial this site already resolves — the
		second half below is what says so, and it is the half that would break first if the check
		were ever asked to do more than exist.

		The first half is the consequence of stopping the insert, and it is a *refusal*, not a
		crash: a serial the world cannot resolve and the plan will not create leaves its row
		`SERIAL_UNRESOLVED`, named. That is the same answer the pre-existing path gives a serial
		the site has under another name (`test_a_serial_the_site_has_under_another_name_is_skipped…`),
		and it is what the lab's own `DuplicateEntryError` became after the fix.
		"""
		taken = world(serial_names=frozenset({"LS-2026-00001"}))
		plan = export.plan(document(), taken)

		self.assertEqual(plan.serials, (), "the taken name did not stop the insert")
		self.assertEqual(len(plan.skipped_serials), 1)
		self.assertEqual(plan.assignments, (), "a row was placed for a serial nothing will create")
		self.assertEqual(
			[conflict.reason for conflict in plan.conflicts],
			[export.REASON_SERIAL_UNRESOLVED],
			"the row is refused by name, with the identity it could not resolve",
		)
		self.assertIn("w-0001", plan.conflicts[0].detail)

	def test_a_taken_name_does_not_move_a_row_the_site_can_already_resolve(self):
		"""The other half: nothing about a resolvable row changes, which is the boundary itself."""
		# One world, and the same world with the name added: the identity is resolvable in both,
		# so the only difference the second may make is the skip itself. (The assignment's
		# `logistics_serial` is re-pointed at the site's own master in both — that is the reuse
		# rule, and holding it constant is what keeps this test about `serial_names`.)
		resolvable = {
			"serial_identities": frozenset({("ITEM-1", "w-0001")}),
			"serials_by_identity": {("ITEM-1", "w-0001"): "LS-RENAMED"},
		}

		before = export.plan(document(), world(**resolvable))
		after = export.plan(document(), world(**resolvable, serial_names=frozenset({"LS-2026-00001"})))

		self.assertEqual(before.assignments, after.assignments, "a name decided a placement")
		self.assertEqual(before.conflicts, after.conflicts)
		self.assertEqual(
			(len(before.skipped_serials), len(after.skipped_serials)), (1, 1), "the serial was not skipped"
		)

	def test_a_name_the_site_does_not_hold_creates_the_serial_as_usual(self):
		"""The check is about this name, not about names in general."""
		live = world(serial_names=frozenset({"LS-2026-00009"}))

		plan = export.plan(document(), live)

		self.assertEqual(len(plan.serials), 1)
		self.assertEqual(plan.skipped_serials, ())

	def test_a_reused_serial_re_points_the_assignment_at_the_live_row(self):
		"""The file names `LS-2026-00001`; this site holds that identity as `LS-RENAMED`.

		Inserting the file's name would name a document that does not exist here, and Frappe's
		link validation would refuse the insert — rolling the whole import back over one rename.
		"""
		live = world(
			serial_identities=frozenset({("ITEM-1", "w-0001")}),
			serials_by_identity={("ITEM-1", "w-0001"): "LS-RENAMED"},
		)

		plan = export.plan(document(), live)

		self.assertEqual(plan.assignments[0]["logistics_serial"], "LS-RENAMED")
		self.assertEqual(plan.assignments[0]["normalized_serial"], "w-0001")

	def test_a_serial_arriving_with_the_import_keeps_the_name_the_file_gives_it(self):
		plan = export.plan(document(), world())

		self.assertEqual(plan.assignments[0]["logistics_serial"], "LS-2026-00001")

	def test_an_assignment_whose_serial_is_in_the_file_is_placed(self):
		"""The serial arrives with the same import, so the row may name it."""
		plan = export.plan(document(serials=[live_serial()], assignments=[live_assignment()]), world())

		self.assertEqual(len(plan.serials), 1)
		self.assertEqual(len(plan.assignments), 1)

	def test_an_assignment_whose_serial_is_nowhere_is_a_conflict(self):
		plan = export.plan(document(serials=[], assignments=[live_assignment()]), world())

		self.assertEqual(plan.assignments, ())
		self.assertEqual(plan.conflicts[0].reason, export.REASON_SERIAL_UNRESOLVED)

	def test_a_missing_item_is_a_conflict(self):
		plan = export.plan(document(), world(items=frozenset()))

		self.assertEqual(plan.serials, ())
		self.assertIn(export.REASON_ITEM_MISSING, [c.reason for c in plan.conflicts])

	def test_a_missing_company_is_a_conflict(self):
		plan = export.plan(document(), world(companies=frozenset()))

		self.assertIn(export.REASON_COMPANY_MISSING, [c.reason for c in plan.conflicts])

	def test_a_missing_invoice_is_a_conflict(self):
		plan = export.plan(document(), world(invoices={}))

		self.assertIn(export.REASON_INVOICE_MISSING, [c.reason for c in plan.conflicts])

	def test_a_missing_invoice_row_is_a_conflict(self):
		plan = export.plan(document(), world(invoice_rows=frozenset()))

		self.assertIn(export.REASON_INVOICE_ROW_MISSING, [c.reason for c in plan.conflicts])

	def test_a_company_that_disagrees_with_the_invoice_is_a_conflict(self):
		"""The invoice that exists now decides, never the company the file claims."""
		plan = export.plan(document(), world(invoices={"SINV-1": "COMPANY-B"}))

		self.assertIn(export.REASON_COMPANY_MISMATCH, [c.reason for c in plan.conflicts])

	def test_an_invoice_that_already_carries_that_serial_as_something_else_is_a_conflict(self):
		live = world(
			serial_identities=frozenset({("ITEM-1", "w-0001")}),
			serials_by_identity={("ITEM-1", "w-0001"): "LS-2026-00001"},
			assignments_by_identity={("SINV-1", "w-0001"): "LS-OTHER"},
		)

		plan = export.plan(document(), live)

		self.assertEqual(plan.assignments, ())
		self.assertEqual(plan.conflicts[0].reason, export.REASON_LINK_CONFLICT)

	def test_a_conflict_is_reported_once_with_one_reason(self):
		plan = export.plan(
			document(),
			world(items=frozenset(), companies=frozenset(), invoices={}, invoice_rows=frozenset()),
		)

		by_row = {}

		for conflict in plan.conflicts:
			by_row.setdefault(conflict.row, []).append(conflict.reason)

		for row, reasons in by_row.items():
			with self.subTest(row=row):
				self.assertEqual(len(reasons), 1)

	def test_the_summary_counts_each_reason(self):
		plan = export.plan(document(), world(invoices={}))

		summary = plan.summary()

		self.assertEqual(summary["conflicts"], len(plan.conflicts))
		self.assertEqual(summary["by_reason"][export.REASON_INVOICE_MISSING], 1)

	def test_the_sample_never_exceeds_its_limit(self):
		conflicts = tuple(export.Conflict(f"ROW-{index}", export.REASON_ITEM_MISSING) for index in range(50))

		self.assertEqual(len(export.sample(conflicts, limit=10)), 10)
		self.assertEqual(len(export.sample(conflicts)), export.PREVIEW_SAMPLE)

	def test_every_reason_has_a_message_in_persian(self):
		for reason in export.REASON_MESSAGES:
			with self.subTest(reason=reason):
				self.assertTrue(any(ord(character) > 127 for character in export.REASON_MESSAGES[reason]))


class CapacityTest(UnitTestCase):
	"""A restored file may not fill an invoice row past its quantity.

	The cap is the one an operator meets while scanning, and a file is not a privileged way round
	it — but nothing is refused wholesale either: the rows that do not fit are reported, with the
	rows that do still coming back, because otherwise one full row would make a backup
	unrestorable. Nothing already stored is touched.
	"""

	def capacity(self, allowed, already):
		"""A world whose one invoice row may hold `allowed` serials and already holds `already`."""
		return world(
			row_capacity={("SINV-1", "row-1"): allowed},
			row_registered={("SINV-1", "row-1"): already} if already else {},
		)

	def test_a_row_with_room_takes_one_more(self):
		plan = export.plan(document(), self.capacity(3, 2))

		self.assertEqual(len(plan.assignments), 1)
		self.assertEqual(plan.conflicts, ())

	def test_a_row_that_holds_its_quantity_is_reported_instead_of_filled(self):
		plan = export.plan(document(), self.capacity(3, 3))

		self.assertEqual(plan.assignments, ())
		self.assertEqual([conflict.reason for conflict in plan.conflicts], [export.REASON_EXCESS_QUANTITY])

	def test_a_row_of_nothing_takes_nothing(self):
		"""A row that invoices no units has no place for a serial, and the file does not make one."""
		plan = export.plan(document(), self.capacity(0, 0))

		self.assertEqual(plan.assignments, ())
		self.assertEqual([conflict.reason for conflict in plan.conflicts], [export.REASON_EXCESS_QUANTITY])

	def test_the_file_is_counted_against_itself_as_it_is_placed(self):
		"""Five rows naming one place are measured one at a time, not five times against nothing."""
		rows = [
			live_assignment(
				name=f"LSA-{index}", logistics_serial=f"LS-{index}", normalized_serial=f"w-{index:04d}"
			)
			for index in range(1, 6)
		]
		serials = [
			live_serial(name=f"LS-{index}", serial_no=f"W-{index}", normalized_serial=f"w-{index:04d}")
			for index in range(1, 6)
		]

		plan = export.plan(document(serials=serials, assignments=rows), self.capacity(3, 0))

		self.assertEqual(len(plan.assignments), 3)
		self.assertEqual(
			[conflict.reason for conflict in plan.conflicts], [export.REASON_EXCESS_QUANTITY] * 2
		)
		self.assertEqual(len(plan.conflicts) + len(plan.assignments), len(rows))

	def test_a_full_row_does_not_stop_the_rest_of_the_file(self):
		"""One row over its quantity is one row's problem: the others are still restored."""
		documented = document(
			serials=[
				live_serial(),
				live_serial(name="LS-2026-00002", serial_no="W-0002", normalized_serial="w-0002"),
			],
			assignments=[
				live_assignment(name="LSA-1", normalized_serial="w-0001"),
				live_assignment(
					name="LSA-2",
					logistics_serial="LS-2026-00002",
					normalized_serial="w-0002",
					sales_invoice_item="row-2",
				),
			],
		)
		full = world(
			invoice_rows=frozenset({("SINV-1", "row-1"), ("SINV-1", "row-2")}),
			row_capacity={("SINV-1", "row-1"): 1, ("SINV-1", "row-2"): 1},
			row_registered={("SINV-1", "row-1"): 1},
		)

		plan = export.plan(documented, full)

		self.assertEqual([row.get("name") for row in plan.assignments], ["LSA-2"])
		self.assertEqual([conflict.row for conflict in plan.conflicts], ["LSA-1"])

	def test_a_row_the_world_does_not_describe_is_not_measured(self):
		"""A world without a capacity for a row is a world that was not asked — a test's, not a site's.

		`gateway.transfer_world` fills the capacity of every row it lists, from the same read that
		lists them, so this is the database-free case and not a hole in the deployed one.
		"""
		plan = export.plan(document(), world())

		self.assertEqual(len(plan.assignments), 1)
		self.assertEqual(plan.conflicts, ())

	def test_the_reason_is_the_same_code_the_endpoints_refuse_with(self):
		"""An operator reads one code for one rule, whichever path met it."""
		from smartprocess_logistics.core import validation

		self.assertEqual(export.REASON_EXCESS_QUANTITY, validation.CODE_EXCESS_QUANTITY)


class ProvenanceTest(UnitTestCase):
	"""`creation`, `owner`, `modified` and `modified_by` come back as they were, or not at all."""

	def test_a_row_carries_all_four_back(self):
		self.assertEqual(
			export.row_timestamps(live_serial()),
			{
				"creation": "2026-01-01 00:00:00",
				"owner": "Administrator",
				"modified": "2026-01-02 00:00:00",
				"modified_by": "Administrator",
			},
		)

	def test_a_row_that_says_nothing_about_a_field_does_not_write_a_blank_over_it(self):
		"""An export taken before a field existed must not clear what the insert just set."""
		trimmed = export.row_timestamps(live_serial(owner=None, modified_by=""))

		self.assertEqual(
			trimmed,
			{"creation": "2026-01-01 00:00:00", "modified": "2026-01-02 00:00:00"},
		)

	def test_a_row_with_no_timestamps_at_all_answers_with_nothing(self):
		empty = dict.fromkeys(export.TIMESTAMP_FIELDS)

		self.assertEqual(export.row_timestamps(empty), {})

	def test_the_four_fields_are_in_the_exported_formats(self):
		for field in export.TIMESTAMP_FIELDS:
			with self.subTest(field=field):
				self.assertIn(field, export.SERIAL_FIELDS)
				self.assertIn(field, export.ASSIGNMENT_FIELDS)


class ProvenanceWriteTest(UnitTestCase):
	"""How the provenance is written back is part of the contract, so it is asserted, not assumed."""

	def gateway_source(self):
		return (APP_ROOT / "api" / "gateway.py").read_text(encoding="utf-8")

	def test_the_import_restores_provenance_through_set_value(self):
		source = self.gateway_source()

		self.assertIn("export.row_timestamps(row)", source)
		self.assertIn("frappe.db.set_value(doctype, document.name, provenance", source)

	def test_it_does_not_rewrite_the_timestamp_it_is_restoring(self):
		"""`update_modified=True` is the default, and would replace `modified` with now()."""
		self.assertIn("update_modified=False", self.gateway_source())

	def test_no_framework_wide_flag_is_used_for_the_timestamps(self):
		"""`in_install` keeps `creation` and `owner` too, but it is read elsewhere in Frappe.

		A narrower mechanism that does one thing is easier to reason about than a switch whose
		other readers are not in this file — and this file cannot be the place that discovers them.
		"""
		self.assertNotIn("flags.in_install", self.gateway_source())


class ChoiceTest(UnitTestCase):
	def test_a_value_outside_a_select_is_reported(self):
		self.assertEqual(
			export.misplaced_choices({"status": "Active\nBlocked"}, {"status": "Deleted"}),
			(("status", "Deleted"),),
		)

	def test_a_value_inside_a_select_is_left_alone(self):
		self.assertEqual(export.misplaced_choices({"status": "Active\nBlocked"}, {"status": "Active"}), ())

	def test_an_empty_value_and_an_empty_option_list_are_not_checked(self):
		self.assertEqual(export.misplaced_choices({"status": "Active"}, {"status": None}), ())
		self.assertEqual(export.misplaced_choices({"status": ""}, {"status": "anything"}), ())


def live_transfer(name="LST-2026-00001", **overrides):
	row = {
		"name": name,
		"source_invoice": "SINV-CANCELLED",
		"target_invoice": "SINV-1",
		"company": "COMPANY-A",
		"confirmed_token": "a" * 64,
		# The counts a record's own rows add up to. The validator refuses a record that claims more
		# than it holds, and this fixture claimed ten moves and eight refusals while holding one
		# row — which is exactly the hand-edited archive the check exists for.
		"source_total": 18,
		"moved_count": 1,
		"refused_count": 1,
		"unselected_count": 16,
		"acknowledged_count": 0,
		"docstatus": 1,
		"remarks": "",
		"creation": "2026-10-14 09:00:00",
		"modified": "2026-10-14 09:00:00",
		"owner": "Administrator",
		"modified_by": "Administrator",
	}

	row.update(overrides)

	return row


def live_transfer_item(name="LSTI-0001", **overrides):
	row = {
		"name": name,
		"parent": "LST-2026-00001",
		"logistics_serial": "LS-2026-00001",
		"serial_no": "W-0001",
		"item_code": "ITEM-1",
		"normalized_serial": "w-0001",
		"result": "Moved",
		"reason": "",
		"reason_message": "",
		"source_assignment": "LSA-2026-00001",
		"source_row": "row-1",
		"source_index": 1,
		"target_row": "row-1",
		"target_index": 1,
		"acknowledged": 0,
		"creation": "2026-10-14 09:00:00",
		"modified": "2026-10-14 09:00:00",
		"owner": "Administrator",
		"modified_by": "Administrator",
	}

	row.update(overrides)

	return row


def live_refused_item(name="LSTI-0002", **overrides):
	"""A row the transfer could not place, and the reason it carries — what makes the counts add up."""
	row = live_transfer_item(
		name=name,
		result="Refused",
		reason="NO_ROOM",
		reason_message="ردیف‌های این کالا در فاکتور اصلاحی جا ندارند",
		normalized_serial="w-0002",
		serial_no="W-0002",
		target_row="",
		target_index=None,
	)

	row.update(overrides)

	return row


def transfer_records():
	"""One record and its two rows: the smallest archive that agrees with itself."""
	return [live_transfer()], [live_transfer_item(), live_refused_item()]


class TransferRecordsInTheFileTest(UnitTestCase):
	"""The audit records travel with the file, and nothing about them is ever re-created.

	They are the app's own account of moving a cancelled invoice's serials onto its amendment, they
	live in tables an uninstall drops, and an export is the thing that survives the removal. So
	they are carried — and an import places none of them, because a record of an act performed here
	by named people is not something a file from another site should be able to manufacture.
	"""

	def built(self):
		return export.build(
			[live_serial()],
			[live_assignment()],
			*transfer_records(),
			app_version="0.4.0",
			site="a-site",
			exported_by="Administrator",
			exported_on="2026-10-14 09:00:00",
			normalization_version=2,
		)

	def test_the_records_are_in_the_file_and_counted(self):
		built = self.built()

		self.assertEqual(built["counts"]["transfers"], 1)
		self.assertEqual(built["counts"]["transfer_items"], 2)
		self.assertEqual(len(export.transfer_rows(built)), 1)
		self.assertEqual(len(export.transfer_item_rows(built)), 2)

	def test_the_document_is_version_2(self):
		self.assertEqual(self.built()["format_version"], 2)
		self.assertEqual(export.FORMAT_VERSION, 2)

	def test_it_reads_back_as_what_was_written(self):
		read = export.read(self.built())

		self.assertEqual(export.transfer_rows(read)[0]["name"], "LST-2026-00001")
		self.assertEqual([row["result"] for row in export.transfer_item_rows(read)], ["Moved", "Refused"])

	def test_a_file_with_no_records_is_still_a_file_of_this_version(self):
		"""A site that has never transferred anything exports the two sections empty, not absent."""
		built = document()

		self.assertEqual(built["transfers"], [])
		self.assertEqual(built["transfer_items"], [])

	def test_a_version_1_file_is_read_as_it_always_was(self):
		"""The point of the version: an old export keeps working, and it is missing nothing."""
		old = document()
		old["format_version"] = 1
		old["counts"] = {"serials": 1, "assignments": 1}
		old.pop("transfers")
		old.pop("transfer_items")
		old["digest"] = export.document_digest(old)

		read = export.read(old)

		self.assertEqual(len(export.serial_rows(read)), 1)
		self.assertEqual(export.assignment_rows(read)[0]["name"], "LSA-2026-00001")
		self.assertEqual(export.transfer_rows(read), ())

	def test_a_version_1_file_missing_a_section_it_declares_is_still_refused(self):
		"""The tolerance is for sections the version did not have, not for a truncated file."""
		old = document()
		old["format_version"] = 1
		old.pop("assignments")
		old["digest"] = export.document_digest(old)

		with self.assertRaises(export.ExportError) as caught:
			export.read(old)

		self.assertEqual(caught.exception.code, export.CODE_BAD_SHAPE)

	def test_a_newer_version_is_refused(self):
		ahead = document()
		ahead["format_version"] = 3
		ahead["digest"] = export.document_digest(ahead)

		with self.assertRaises(export.ExportError) as caught:
			export.read(ahead)

		self.assertEqual(caught.exception.code, export.CODE_FORMAT_TOO_NEW)

	def test_an_import_counts_the_records_and_places_none_of_them(self):
		plan = export.plan(self.built(), world())

		self.assertEqual(plan.records_in_file, 1)
		self.assertEqual(plan.summary()["records_in_file"], 1)
		self.assertEqual(len(plan.assignments), 1)
		self.assertEqual([conflict.row for conflict in plan.conflicts], [])

	def test_no_conflict_is_raised_for_a_record_that_was_never_attempted(self):
		"""A site with a thousand transfers must not preview a thousand conflicts."""
		records = [
			live_transfer(
				f"LST-{index}",
				source_total=1,
				moved_count=1,
				refused_count=0,
				unselected_count=0,
			)
			for index in range(50)
		]
		rows = [
			live_transfer_item(f"LSTI-{index}", parent=f"LST-{index}", normalized_serial=f"w-{index:04d}")
			for index in range(50)
		]
		many = export.build([], [], records, rows)

		plan = export.plan(many, world())

		self.assertEqual(plan.conflicts, ())
		self.assertEqual(plan.records_in_file, 50)


class HistoricalRestoreTest(UnitTestCase):
	"""The one policy that places a cancelled invoice's own registrations, and its edges.

	This is the path the app opened deliberately for a file exported before the app was removed: a
	cancelled invoice's registrations are a faithful account of what a warehouse scanned, and losing
	them across a reinstall loses scan history that cannot be reconstructed. Two things keep it from
	being a door into the ordinary restore, and both are asserted here: it places **only** rows of
	cancelled invoices, and the ordinary restore is untouched by its existence.
	"""

	def cancelled_world(self, **overrides):
		return world(
			cancelled_invoices=frozenset({"SINV-1"}),
			invoices={"SINV-1": "COMPANY-A"},
			**overrides,
		)

	def test_it_places_the_row_the_ordinary_restore_refuses(self):
		plan = export.plan(document(), self.cancelled_world(), mode=export.MODE_HISTORY)

		self.assertEqual(len(plan.assignments), 1)
		self.assertEqual(plan.assignments[0]["sales_invoice"], "SINV-1")
		self.assertEqual(plan.conflicts, ())

	def test_it_places_only_cancelled_invoices(self):
		"""A live invoice's row is not its business: the ordinary restore owns that one."""
		two = document(
			serials=[
				live_serial(),
				live_serial(name="LS-2026-00002", serial_no="W-0002", normalized_serial="w-0002"),
			],
			assignments=[
				live_assignment(),
				live_assignment(
					name="LSA-2026-00002",
					sales_invoice="SINV-2",
					sales_invoice_item="row-2",
					logistics_serial="LS-2026-00002",
					normalized_serial="w-0002",
				),
			],
		)
		plan = export.plan(
			two,
			world(
				cancelled_invoices=frozenset({"SINV-1"}),
				invoices={"SINV-1": "COMPANY-A", "SINV-2": "COMPANY-A"},
				invoice_rows=frozenset({("SINV-1", "row-1"), ("SINV-2", "row-2")}),
				row_capacity={("SINV-1", "row-1"): 1, ("SINV-2", "row-2"): 1},
			),
			mode=export.MODE_HISTORY,
		)

		self.assertEqual([row["sales_invoice"] for row in plan.assignments], ["SINV-1"])
		self.assertEqual([conflict.reason for conflict in plan.conflicts], [export.REASON_NOT_HISTORY])
		self.assertIn("معمولی", plan.conflicts[0].describe())

	def test_the_ordinary_restore_still_refuses_the_same_row(self):
		"""The door is on one endpoint: the policy is passed, never assumed."""
		plan = export.plan(document(), self.cancelled_world())

		self.assertEqual(plan.assignments, ())
		self.assertEqual([conflict.reason for conflict in plan.conflicts], [export.REASON_INVOICE_CANCELLED])

	def test_the_mode_defaults_to_the_one_that_permits_the_least(self):
		plan = export.plan(document(), self.cancelled_world(), mode=export.MODE_LIVE)

		self.assertEqual(plan.assignments, ())

	def test_an_unknown_mode_is_refused_rather_than_treated_as_the_default(self):
		"""The default here is the one that permits, which is why an unknown value cannot take it."""
		with self.assertRaises(export.ExportError) as caught:
			export.plan(document(), self.cancelled_world(), mode=True)

		self.assertEqual(caught.exception.code, export.CODE_BAD_SHAPE)

	def test_an_archivist_restore_is_still_bounded_by_the_rows_quantity(self):
		"""The rules that do not change for history: a row may never hold more than it invoices."""
		plan = export.plan(
			document(),
			self.cancelled_world(
				row_capacity={("SINV-1", "row-1"): 1}, row_registered={("SINV-1", "row-1"): 1}
			),
			mode=export.MODE_HISTORY,
		)

		self.assertEqual(plan.assignments, ())
		self.assertEqual([conflict.reason for conflict in plan.conflicts], [export.REASON_EXCESS_QUANTITY])

	def test_a_row_whose_invoice_is_absent_is_still_absent(self):
		"""Opening the cancelled door does not open the missing-invoice one."""
		plan = export.plan(
			document(),
			world(cancelled_invoices=frozenset(), invoices={}),
			mode=export.MODE_HISTORY,
		)

		self.assertEqual(plan.assignments, ())
		self.assertEqual([conflict.reason for conflict in plan.conflicts], [export.REASON_INVOICE_MISSING])


class MalformedArchiveTest(UnitTestCase):
	"""Every way an archive of transfer records can lie about itself, refused before it is planned.

	A digest proves a file was not edited by accident. It proves nothing about a file that was edited
	and then digested again — the digest is computed from the file — so the checks that matter are
	the ones recomputing the digest cannot satisfy: the record and its rows have to agree with each
	other. Each case below is one of those, and each is refused with a code and a sentence naming the
	row rather than with a traceback.
	"""

	def archive(self, records=None, rows=None):
		return export.build([], [], records or [live_transfer()], rows or [live_transfer_item()])

	def refused(self, records, rows):
		"""The error a read of this archive raises."""
		built = self.archive(records, rows)

		with self.assertRaises(export.ExportError) as caught:
			export.read(built)

		return caught.exception

	def test_a_row_naming_a_record_that_is_not_here_is_refused(self):
		"""The missing-parent case: the row would otherwise be attached to nothing."""
		raised = self.refused(
			[live_transfer(source_total=1, moved_count=1, refused_count=0, unselected_count=0)],
			[live_transfer_item(parent="LST-2026-99999")],
		)

		self.assertEqual(raised.code, export.CODE_ROW_INVALID)
		self.assertIn("LST-2026-99999", raised.message)

	def test_a_record_claiming_more_moves_than_it_holds_is_refused(self):
		raised = self.refused([live_transfer(moved_count=10)], [live_transfer_item()])

		self.assertEqual(raised.code, export.CODE_ROW_INVALID)
		self.assertIn("منتقل", raised.message)

	def test_a_record_claiming_more_refusals_than_it_holds_is_refused(self):
		"""Reaching the *refusal* rule and no other, so the message is the one being asserted.

		The counts are chosen so the total still adds up: with one row placed and the claim of eight
		refusals, a source of nine is consistent — so the only thing wrong with this file is that it
		counts refusals it does not hold. The first version of this test used the stock fixture and
		passed through the *total* rule instead, whose sentence also contains «رد», which is a test
		passing for a reason it did not mean.
		"""
		raised = self.refused(
			[live_transfer(source_total=9, moved_count=1, refused_count=8, unselected_count=0)],
			[live_transfer_item()],
		)

		self.assertEqual(raised.code, export.CODE_ROW_INVALID)
		self.assertIn("رد شده", raised.message)
		self.assertNotIn("جمع نمی‌شود", raised.message)

	def test_a_total_that_its_own_outcomes_do_not_add_up_to_is_refused(self):
		"""Every serial the source held came out placed, refused or unselected — or the file is made up.

		The row counts are consistent here, so the check that fires is the total: the record claims a
		source of one serial and accounts for six.
		"""
		raised = self.refused(
			[live_transfer(source_total=1, moved_count=1, refused_count=0, unselected_count=5)],
			[live_transfer_item()],
		)

		self.assertEqual(raised.code, export.CODE_ROW_INVALID)
		self.assertIn("جمع", raised.message)

	def test_a_record_claiming_more_acknowledgments_than_its_rows_is_refused(self):
		raised = self.refused(
			[
				live_transfer(
					acknowledged_count=1, source_total=1, moved_count=1, refused_count=0, unselected_count=0
				)
			],
			[live_transfer_item(acknowledged=0)],
		)

		self.assertEqual(raised.code, export.CODE_ROW_INVALID)
		self.assertIn("تأییدشده", raised.message)

	def test_an_outcome_the_format_does_not_know_is_refused(self):
		raised = self.refused(
			[live_transfer(source_total=1, moved_count=1, refused_count=0, unselected_count=0)],
			[live_transfer_item(result="Deleted")],
		)

		self.assertEqual(raised.code, export.CODE_ROW_INVALID)
		self.assertIn("Deleted", raised.message)

	def test_a_refused_row_with_no_reason_is_refused(self):
		"""The reason is what the operator is shown; a row without one says nothing."""
		raised = self.refused(
			[live_transfer(refused_count=1, moved_count=0, source_total=18)],
			[live_refused_item(reason="")],
		)

		self.assertEqual(raised.code, export.CODE_ROW_INVALID)
		self.assertIn("دلیلی ندارد", raised.message)

	def test_a_moved_row_carrying_a_reason_is_refused(self):
		"""The other direction, which is the one a hand-edit produces by copying a row."""
		raised = self.refused(
			[live_transfer(source_total=18, moved_count=1, refused_count=0)],
			[live_transfer_item(reason="NO_ROOM")],
		)

		self.assertEqual(raised.code, export.CODE_ROW_INVALID)
		self.assertIn("دلیل رد دارد", raised.message)

	def test_an_archive_that_agrees_with_itself_is_accepted(self):
		"""The check has to be able to pass, or it is a refusal with a test around it."""
		records, rows = transfer_records()
		built = export.build([], [], records, rows)

		self.assertTrue(export.read(built))
		self.assertTrue(export.check_transfer_records(built))

	def test_a_version_1_file_is_not_asked_about_records_it_does_not_have(self):
		"""The validator is for the version that declares the sections."""
		old = document()
		old["format_version"] = 1
		old["counts"] = {"serials": 1, "assignments": 1}
		old.pop("transfers")
		old.pop("transfer_items")
		old["digest"] = export.document_digest(old)

		self.assertTrue(export.read(old))

	def test_the_two_vocabularies_agree(self):
		"""`core.transfer` decides an outcome and this module stores it; one vocabulary, two names."""
		self.assertEqual(tuple(export.TRANSFER_RESULTS), (transfer.RESULT_MOVED, transfer.RESULT_REFUSED))
