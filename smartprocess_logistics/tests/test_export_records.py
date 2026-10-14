"""Placing the audit records themselves: whole records, marked, and never over an existing one.

The third restore, and the one the other two deliberately do not do. Three decisions shape it and
each is asserted here rather than left to the code's shape: a restored record says it was restored,
a name collision is refused rather than resolved, and a record is placed with all of its rows or not
at all. What a record cannot be is a document that claims an act was performed here — which is why
the marker is required rather than optional.

A module of its own rather than a class in `tests/test_export.py`: that file is about the format and
the assignment plans, and this is about one mode's rules and the reasons it can refuse with.
The fixtures are its own for the same reason — a transfer record with a row of each outcome, which
is the smallest archive that agrees with itself.
"""

import json
import pathlib

from smartprocess_logistics.core import export
from smartprocess_logistics.tests.base import UnitTestCase

APP = pathlib.Path(__file__).resolve().parent.parent
ENDPOINTS = APP / "api" / "endpoints.py"


def record(name="LST-2026-00001", **overrides):
	"""One audit record whose counts equal the two rows below and nothing else."""
	row = {
		"name": name,
		"source_invoice": "SINV-CANCELLED",
		"target_invoice": "SINV-1",
		"company": "COMPANY-A",
		"confirmed_token": "a" * 64,
		"source_total": 18,
		"moved_count": 1,
		"refused_count": 1,
		"unselected_count": 16,
		"acknowledged_count": 0,
		"docstatus": 1,
		"remarks": "",
		"restored_from": "",
		"restored_on": None,
		"restored_by": "",
		"creation": "2026-10-10 09:00:00",
		"modified": "2026-10-10 09:00:00",
		"owner": "operator@example.test",
		"modified_by": "operator@example.test",
	}

	row.update(overrides)

	return row


def moved_row(name="LSTI-0001", **overrides):
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
		"creation": "2026-10-10 09:00:00",
		"modified": "2026-10-10 09:00:00",
		"owner": "operator@example.test",
		"modified_by": "operator@example.test",
	}

	row.update(overrides)

	return row


def refused_row(name="LSTI-0002", **overrides):
	row = moved_row(
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


def archive(records=None, rows=None, site="the-old-site"):
	return export.build(
		[],
		[],
		records if records is not None else [record()],
		rows if rows is not None else [moved_row(), refused_row()],
		site=site,
		exported_on="2026-10-10 09:00:00",
	)


def world(**overrides):
	"""The site as the record path reads it: the two serials, the target invoice, the company."""
	defaults = {
		"serial_identities": frozenset({("ITEM-1", "w-0001"), ("ITEM-1", "w-0002")}),
		"items": frozenset({"ITEM-1"}),
		"companies": frozenset({"COMPANY-A"}),
		"invoices": {"SINV-1": "COMPANY-A"},
		"transfers": frozenset(),
		"transfer_items": frozenset(),
	}

	defaults.update(overrides)

	return export.World(**defaults)


def stamp(**overrides):
	values = {"by": "archivist@example.test", "on": "2026-10-14 09:00:00"}
	values.update(overrides)

	return export.RestoreStamp(**values)


def planned(document=None, **overrides):
	return export.plan(
		document or archive(),
		world(**overrides),
		mode=export.MODE_RECORDS,
		stamp=stamp(),
	)


class ItPlacesRecordsTest(UnitTestCase):
	"""What it places, in which mode, and nothing else."""

	def test_it_places_the_records_and_their_rows(self):
		plan = planned()

		self.assertEqual([row["name"] for row in plan.transfers], ["LST-2026-00001"])
		self.assertEqual([row["name"] for row in plan.transfer_items], ["LSTI-0001", "LSTI-0002"])
		self.assertEqual(plan.conflicts, ())
		self.assertEqual(plan.summary()["to_create_records"]["transfers"], 1)

	def test_it_places_nothing_but_records(self):
		"""No serial and no assignment: those belong to the other two modes."""
		plan = planned()

		self.assertEqual(plan.serials, ())
		self.assertEqual(plan.assignments, ())
		self.assertEqual(plan.skipped_serials, ())
		self.assertEqual(plan.skipped_assignments, ())

	def test_the_other_modes_place_no_record(self):
		for mode in (export.MODE_LIVE, export.MODE_HISTORY):
			with self.subTest(mode=mode):
				plan = export.plan(archive(), world(), mode=mode)

				self.assertEqual(plan.transfers, ())
				self.assertEqual(plan.transfer_items, ())
				self.assertEqual(plan.records_in_file, 1)

	def test_the_count_in_the_file_is_reported_in_every_mode(self):
		self.assertEqual(planned().records_in_file, 1)
		self.assertEqual(planned().summary()["records_in_file"], 1)

	def test_the_file_s_own_record_count_is_not_something_this_mode_writes(self):
		"""The count describes the file; what is placed is `transfers`, and the two are different."""
		two = archive(
			[
				record(
					"LST-2026-00001",
					source_total=1,
					moved_count=1,
					refused_count=0,
					unselected_count=0,
				),
				record(
					"LST-2026-00002",
					source_total=1,
					moved_count=1,
					refused_count=0,
					unselected_count=0,
				),
			],
			[
				moved_row("LSTI-0001", parent="LST-2026-00001"),
				moved_row("LSTI-0002", parent="LST-2026-00002", normalized_serial="w-0002"),
			],
		)
		plan = export.plan(two, world(), mode=export.MODE_RECORDS, stamp=stamp())

		self.assertEqual(plan.records_in_file, 2)
		self.assertEqual(len(plan.transfers), 2)
		self.assertEqual(len(plan.transfer_items), 2)
		self.assertEqual([row["parent"] for row in plan.transfer_items], ["LST-2026-00001", "LST-2026-00002"])


class TheMarkerTest(UnitTestCase):
	"""A restored record has to be distinguishable from one performed here.

	This is the whole of what the third decision bought: the record's *act* still belongs to whoever
	performed it — `creation` and `owner` travel from the file, as every restore does — and these
	three fields are what say the document itself arrived from somewhere else.
	"""

	def test_it_names_the_site_the_archive_came_from(self):
		"""The document's own header, and never the site it is being restored *into*.

		Telling those two apart is the whole job of this field, and it is why the pure layer reads it
		from the document rather than being handed it: a caller can pass the wrong site, and the
		wrong site here is the one confusion the field exists to prevent.
		"""
		placed = planned().transfers[0]

		self.assertEqual(placed["restored_from"], "the-old-site")
		self.assertNotEqual(placed["restored_from"], "a-site")

	def test_it_carries_this_requests_user_and_clock(self):
		placed = planned().transfers[0]

		self.assertEqual(placed["restored_by"], "archivist@example.test")
		self.assertEqual(placed["restored_on"], "2026-10-14 09:00:00")

	def test_it_keeps_the_original_actor_of_the_act_untouched(self):
		"""The marker is about the document; the act still belongs to whoever performed it."""
		placed = planned().transfers[0]

		self.assertEqual(placed["owner"], "operator@example.test")
		self.assertEqual(placed["creation"], "2026-10-10 09:00:00")

	def test_the_files_own_marker_is_replaced_rather_than_carried(self):
		"""The marker describes *this* document's arrival; a chain of restores is not in the format.

		Carrying the file's values forward would make the record claim it arrived from a site it did
		not, which is worse than saying nothing about the older arrival.
		"""
		already = archive(
			[
				record(
					restored_from="someone-else",
					restored_by="other@example.test",
					restored_on="2020-01-01 00:00:00",
				)
			]
		)
		placed = planned(already).transfers[0]

		self.assertEqual(placed["restored_from"], "the-old-site")
		self.assertEqual(placed["restored_by"], "archivist@example.test")
		self.assertEqual(placed["restored_on"], "2026-10-14 09:00:00")

	def test_a_record_that_cannot_say_it_was_restored_is_refused(self):
		"""No marker means the record would claim to have been performed here."""
		with self.assertRaises(export.ExportError) as caught:
			export.plan(archive(), world(), mode=export.MODE_RECORDS, stamp=None)

		self.assertEqual(caught.exception.code, export.CODE_BAD_SHAPE)
		self.assertIn("نشانهٔ بازآوری", caught.exception.message)


class TheSubmittedRuleTest(UnitTestCase):
	"""A record is submitted, whatever the file says, and a file claiming otherwise is refused.

	I24's promise is the promise of a **submitted** document: it is what makes the framework refuse
	to change or delete it. `docstatus` travels in the file, so a hand-made file — the digest is
	computed *from* the file and proves nothing about who wrote it — could ask for a draft, and a
	draft is editable and deletable. The rule is answered in the two places it can be: the archive
	validator refuses the file, and the plan writes the value rather than carrying it.
	"""

	def test_a_record_the_file_says_is_a_draft_is_refused_whole(self):
		with self.assertRaises(export.ExportError) as caught:
			export.check_transfer_records(archive([record(docstatus=0)]))

		self.assertEqual(caught.exception.code, export.CODE_ROW_INVALID)
		self.assertIn("ثبت‌شده نیست", caught.exception.message)
		self.assertIn("LST-2026-00001", caught.exception.message)

	def test_the_door_every_endpoint_uses_refuses_it_too(self):
		"""`read` is what the preview and the apply both call, so it is where it has to fail."""
		content = json.dumps(archive([record(docstatus=0)]), ensure_ascii=False)

		with self.assertRaises(export.ExportError) as caught:
			export.read(content)

		self.assertEqual(caught.exception.code, export.CODE_ROW_INVALID)

	def test_a_submitted_record_still_passes(self):
		self.assertTrue(export.check_transfer_records(archive()))

	def test_a_placed_record_is_submitted_whatever_the_file_carried(self):
		"""The plan decides this, not the file: nothing a file says can produce a draft here."""
		plan = export.plan(archive([record(docstatus=0)]), world(), mode=export.MODE_RECORDS, stamp=stamp())

		self.assertEqual(plan.transfers[0]["docstatus"], export.DOCSTATUS_SUBMITTED)


class TheRefusalsTest(UnitTestCase):
	"""The four ways a record cannot be placed, each with its own reason and no silent overwrite."""

	def test_a_name_this_site_already_uses_is_refused_and_not_overwritten(self):
		plan = planned(transfers=frozenset({"LST-2026-00001"}))

		self.assertEqual(plan.transfers, ())
		self.assertEqual(plan.transfer_items, ())
		self.assertEqual([conflict.reason for conflict in plan.conflicts], [export.REASON_RECORD_EXISTS])
		self.assertEqual(plan.conflicts[0].row, "LST-2026-00001")
		self.assertEqual(plan.conflicts[0].detail, "LST-2026-00001")

	def test_a_child_name_this_site_already_uses_refuses_the_record_whole(self):
		"""A record missing a row is a record that lies, so one blocked row blocks the record."""
		plan = planned(transfer_items=frozenset({"LSTI-0002"}))

		self.assertEqual(plan.transfers, ())
		self.assertEqual([conflict.reason for conflict in plan.conflicts], [export.REASON_RECORD_EXISTS])
		self.assertEqual(plan.conflicts[0].row, "LST-2026-00001", "the record carries the reason")
		self.assertEqual(plan.conflicts[0].detail, "LSTI-0002", "and names the row that blocked it")

	def test_a_target_invoice_that_is_not_here_is_refused(self):
		plan = planned(invoices={})

		self.assertEqual(plan.transfers, ())
		self.assertEqual([conflict.reason for conflict in plan.conflicts], [export.REASON_INVOICE_MISSING])

	def test_a_target_invoice_that_is_cancelled_now_is_refused(self):
		"""`target_invoice` is a Link, and the framework refuses a link to a cancelled document."""
		plan = planned(cancelled_invoices=frozenset({"SINV-1"}))

		self.assertEqual(plan.transfers, ())
		self.assertEqual(
			[conflict.reason for conflict in plan.conflicts], [export.REASON_RECORD_LINK_CANCELLED]
		)

	def test_a_row_whose_serial_is_not_on_this_site_refuses_the_record(self):
		"""This mode places no serials, so the ordinary restore is what puts them there first."""
		plan = planned(serial_identities=frozenset({("ITEM-1", "w-0001")}))

		self.assertEqual(plan.transfers, ())
		self.assertEqual([conflict.reason for conflict in plan.conflicts], [export.REASON_SERIAL_UNRESOLVED])
		self.assertIn("w-0002", plan.conflicts[0].detail)

	def test_a_row_is_not_reported_again_for_its_refused_record(self):
		"""The record carries the reason; a line per row would be the same fact told twice."""
		plan = planned(transfers=frozenset({"LST-2026-00001"}))

		self.assertEqual(len(plan.conflicts), 1)
		self.assertEqual(plan.conflicts[0].row, "LST-2026-00001")

	def test_two_records_are_refused_separately_and_the_third_placed(self):
		"""One collision does not take the file down: each record is answered for itself."""
		three = archive(
			[
				record(
					"LST-2026-00001",
					source_total=1,
					moved_count=1,
					refused_count=0,
					unselected_count=0,
				),
				record(
					"LST-2026-00002",
					source_total=1,
					moved_count=1,
					refused_count=0,
					unselected_count=0,
				),
				record(
					"LST-2026-00003",
					source_total=1,
					moved_count=1,
					refused_count=0,
					unselected_count=0,
				),
			],
			[
				moved_row("LSTI-0001", parent="LST-2026-00001"),
				moved_row("LSTI-0002", parent="LST-2026-00002", normalized_serial="w-0002"),
				moved_row("LSTI-0003", parent="LST-2026-00003", normalized_serial="w-0003"),
			],
		)
		site = world(serial_identities=frozenset({("ITEM-1", "w-0001"), ("ITEM-1", "w-0003")}))
		plan = export.plan(three, site, mode=export.MODE_RECORDS, stamp=stamp())

		self.assertEqual([row["name"] for row in plan.transfers], ["LST-2026-00001", "LST-2026-00003"])
		self.assertEqual([conflict.row for conflict in plan.conflicts], ["LST-2026-00002"])
		self.assertEqual([conflict.reason for conflict in plan.conflicts], [export.REASON_SERIAL_UNRESOLVED])


class ThePreviewCarriesTheMarkerTest(UnitTestCase):
	"""The records preview needs the stamp as much as the apply does, and did not have it.

	`_plan_records` refuses a document with no stamp, because a restored record that cannot say it
	was restored is a record claiming the act happened here. The preview called `plan` without
	one — measured on the lab, **every** records preview answered `EXPORT_BAD_SHAPE`, so the
	button could never reach the apply and the mode was unreachable through the API.
	"""

	def preview_body(self):
		source = ENDPOINTS.read_text(encoding="utf-8")
		start = source.index("def _preview_export(")

		return source[start : source.index(chr(10) + "@frappe.whitelist", start)]

	def test_the_preview_passes_a_stamp(self):
		self.assertIn("stamp=_stamp(principal, document)", self.preview_body())

	def test_it_is_the_same_stamp_the_apply_passes(self):
		"""One helper, so the preview and the apply cannot describe the act differently."""
		source = ENDPOINTS.read_text(encoding="utf-8")

		self.assertGreaterEqual(source.count("stamp=_stamp(principal, document)"), 2)
