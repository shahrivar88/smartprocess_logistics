"""The portable export and the import plan: what leaves the site, and what may come back.

The first test in this file is the one that matters. Everything else here checks a refusal or a
decision; that one checks that the format is **lossless** — by comparing its field list with the
DocType definitions in this repository, so a field added to `Logistics Serial` does not quietly
stop being exported. A promise in a docstring cannot do that.
"""

import json
import pathlib

from smartprocess_logistics.core import export
from smartprocess_logistics.tests.base import UnitTestCase

APP_ROOT = pathlib.Path(__file__).resolve().parents[2] / "smartprocess_logistics"
DOCTYPE_DIR = APP_ROOT / "smartprocess_logistics" / "doctype"

SKIPPED_FIELDTYPES = ("Section Break", "Column Break")

# Columns every Frappe table has and no DocType lists.
FRAMEWORK_COLUMNS = frozenset({"name", "creation", "modified", "owner", "modified_by"})


def doctype_fields(foldername):
	"""Every field name of one shipped DocType, read from its own definition."""
	path = next((DOCTYPE_DIR / foldername).glob("*.json"))
	document = json.loads(path.read_text(encoding="utf-8"))

	return tuple(
		field["fieldname"]
		for field in document["fields"]
		if field.get("fieldtype") not in SKIPPED_FIELDTYPES
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

	def assert_lossless(self, foldername, fields):
		"""Every field the DocType defines, and nothing beyond the framework's own columns.

		The five extras are real columns on every Frappe table that the DocType JSON does not
		list: the row's name, its timestamps and its authorship. They are what makes an export
		lossless rather than merely complete, so they belong in the format — and their presence is
		asserted, not overlooked, so a sixth one cannot appear unnoticed.
		"""
		defined = set(doctype_fields(foldername))

		self.assertTrue(defined <= set(fields), sorted(defined - set(fields)))
		self.assertEqual(set(fields) - defined, FRAMEWORK_COLUMNS)

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

	def test_an_empty_document_names_nothing(self):
		self.assertEqual(export.world_names(export.build([], [])), {"item_codes": (), "company_names": (), "invoice_names": ()})

	def test_the_endpoints_ask_about_the_documents_names(self):
		"""The bug this test exists for: the world was built from the site's own empty tables."""
		source = (APP_ROOT / "api" / "endpoints.py").read_text(encoding="utf-8")

		self.assertEqual(source.count("gateway.transfer_world(principal, **export.world_names(document))"), 2)


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
		self.assertIn('primary_action_label: __("ثبت کن")', source)

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
		conflicts = tuple(
			export.Conflict(f"ROW-{index}", export.REASON_ITEM_MISSING) for index in range(50)
		)

		self.assertEqual(len(export.sample(conflicts, limit=10)), 10)
		self.assertEqual(len(export.sample(conflicts)), export.PREVIEW_SAMPLE)

	def test_every_reason_has_a_message_in_persian(self):
		for reason in export.REASON_MESSAGES:
			with self.subTest(reason=reason):
				self.assertTrue(any(ord(character) > 127 for character in export.REASON_MESSAGES[reason]))


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
