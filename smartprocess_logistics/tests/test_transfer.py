"""Moving serials from a cancelled invoice to its amendment: the rules, without a database.

The live case this feature exists for, in numbers: an invoice carried eighteen serials of one
item, was cancelled, and was amended by an invoice that invoices ten of that item and no longer
carries a second item at all. What fits must move, what does not must be named, and the cancelled
invoice must come out of it holding exactly what it held before.
"""

import dataclasses
from dataclasses import replace

from smartprocess_logistics.core import transfer as rules
from smartprocess_logistics.core.transfer import (
	CODE_COMPANY_MISMATCH,
	CODE_SAME_INVOICE,
	CODE_SOURCE_NOT_CANCELLED,
	CODE_TARGET_CANCELLED,
	CODE_TARGET_NOT_AMENDMENT,
	REASON_ACKNOWLEDGEMENT_MISSING,
	REASON_ALREADY_ON_TARGET,
	REASON_ITEM_NOT_ON_TARGET,
	REASON_NO_ROOM,
	REASON_ROW_MISMATCH,
	REASON_TARGET_ROW_NOT_ELIGIBLE,
	REASON_UNKNOWN_SELECTION,
	Placement,
	SourceSerial,
	TargetRow,
	TransferError,
	TransferRequest,
	assignment_values,
	plan_transfer,
	preview_of,
	preview_token,
	record_of,
)
from smartprocess_logistics.tests.base import UnitTestCase

MARKED = True
UNMARKED = False


def source_serials(count=18, item="PR89889", row="row-3"):
	return tuple(
		SourceSerial(
			assignment=f"LSA-{index:04d}",
			serial=f"W-{index:04d}",
			normalized=f"w-{index:04d}",
			item_code=item,
			row_key=row,
			row_index=3,
			serial_ref=f"LS-{index:04d}",
			version=2,
		)
		for index in range(1, count + 1)
	)


def target_rows():
	"""The amendment: PR89887 five and seven, PR89889 ten — the item of the eighteen."""
	return (
		TargetRow(row_key="t-1", item_code="PR89887", capacity=5, serial_required=MARKED, index=1),
		TargetRow(row_key="t-2", item_code="PR89889", capacity=10, serial_required=MARKED, index=2),
		TargetRow(row_key="t-3", item_code="PR89887", capacity=7, serial_required=MARKED, index=3),
	)


def request(**overrides):
	values = {
		"source_invoice": "IN-260025",
		"target_invoice": "IN-260025-1",
		"source_company": "پردازش هوشمند",
		"target_company": "پردازش هوشمند",
		"source_cancelled": True,
		"target_cancelled": False,
		"target_amends_source": True,
		"serials": source_serials(),
		"rows": target_rows(),
	}
	values.update(overrides)

	return TransferRequest(**values)


class InvoiceGatesTest(UnitTestCase):
	"""A transfer is between a cancelled invoice and a live one, in one company."""

	def test_a_live_source_is_refused(self):
		with self.assertRaises(TransferError) as caught:
			plan_transfer(request(source_cancelled=False))

		self.assertEqual(caught.exception.code, CODE_SOURCE_NOT_CANCELLED)

	def test_a_cancelled_target_is_refused(self):
		with self.assertRaises(TransferError) as caught:
			plan_transfer(request(target_cancelled=True))

		self.assertEqual(caught.exception.code, CODE_TARGET_CANCELLED)

	def test_another_company_is_refused(self):
		with self.assertRaises(TransferError) as caught:
			plan_transfer(request(target_company="شرکت دیگر"))

		self.assertEqual(caught.exception.code, CODE_COMPANY_MISMATCH)

	def test_the_same_invoice_twice_is_refused(self):
		with self.assertRaises(TransferError) as caught:
			plan_transfer(request(target_invoice="IN-260025"))

		self.assertEqual(caught.exception.code, CODE_SAME_INVOICE)

	def test_a_target_that_does_not_amend_the_source_is_refused(self):
		"""Two unrelated invoices carrying the same item are not a transfer.

		The serial is on the cancelled invoice because of the invoice that replaced it, and
		`amended_from` is the site's own record of which one that is.
		"""
		with self.assertRaises(TransferError) as caught:
			plan_transfer(request(target_amends_source=False))

		self.assertEqual(caught.exception.code, CODE_TARGET_NOT_AMENDMENT)

	def test_the_document_facts_have_no_defaults(self):
		"""A default would be the wrong answer for whichever caller forgot to read it.

		`False` is the permitting value for a cancelled target and the refusing value for a
		cancelled source, so neither direction has a safe default and neither is offered one.
		"""
		required = [name for name in ("source_cancelled", "target_cancelled", "target_amends_source")]

		for name in required:
			with self.subTest(field=name):
				self.assertIs(TransferRequest.__dataclass_fields__[name].default, dataclasses.MISSING)

	def test_every_refusal_carries_a_persian_sentence(self):
		for code in rules.CODE_MESSAGES:
			with self.subTest(code=code):
				self.assertTrue(rules.CODE_MESSAGES[code].strip())


class MappingTest(UnitTestCase):
	"""Which rows the serials go to, and what happens when the invoice changed shape."""

	def test_what_fits_moves_and_the_rest_is_refused_with_a_reason(self):
		"""The live case: eighteen serials, ten places, and a row that is not there any more.

		Eight do not fit, and each of them is named with the reason it did not — which is the
		whole point of doing this as a preview rather than as an attempt.
		"""
		plan = plan_transfer(request())

		self.assertEqual(len(plan.placements), 10)
		self.assertEqual(len(plan.refusals), 8)
		self.assertEqual({refusal.reason for refusal in plan.refusals}, {REASON_NO_ROOM})
		self.assertEqual(plan.unselected, ())
		self.assertEqual(plan.counts()["source"], 18)

	def test_the_three_lists_account_for_every_serial_exactly_once(self):
		"""However the plan comes out, nothing is lost and nothing is counted twice."""
		for overrides in (
			{},
			{"selection": frozenset({"w-0001", "w-0002"})},
			{"on_target": frozenset({"w-0001"})},
			{"live_elsewhere": frozenset({"w-0002"})},
			{"rows": ()},
		):
			with self.subTest(overrides=sorted(overrides)):
				plan = plan_transfer(request(**overrides))

				accounted = (
					[placement.normalized for placement in plan.placements]
					+ [refusal.normalized for refusal in plan.refusals]
					+ [row.normalized for row in plan.unselected]
				)

				self.assertEqual(len(accounted), 18)
				self.assertEqual(sorted(accounted), sorted({row.normalized for row in source_serials()}))
				self.assertEqual(
					sum(plan.counts()[key] for key in ("moved", "refused", "unselected", "unresolved")),
					18,
				)

	def test_every_placement_lands_on_a_row_of_its_own_item(self):
		plan = plan_transfer(request())

		self.assertTrue(all(placement.target_row == "t-2" for placement in plan.placements))

	def test_every_placement_names_the_assignment_it_came_from(self):
		"""The source is referenced, never consumed: the plan carries its name and changes nothing."""
		plan = plan_transfer(request())

		self.assertEqual(
			[placement.source_assignment for placement in plan.placements],
			[f"LSA-{index:04d}" for index in range(1, 11)],
		)

	def test_the_order_is_the_order_the_serials_were_registered_in(self):
		plan = plan_transfer(request())

		self.assertEqual(
			[placement.normalized for placement in plan.placements],
			[f"w-{index:04d}" for index in range(1, 11)],
		)

	def test_an_item_the_amendment_no_longer_carries_is_refused_by_name(self):
		plan = plan_transfer(
			request(
				serials=source_serials(count=3, item="PR89888"),
				rows=target_rows(),
			)
		)

		self.assertEqual(plan.placements, ())
		self.assertEqual({refusal.reason for refusal in plan.refusals}, {REASON_ITEM_NOT_ON_TARGET})
		self.assertEqual({refusal.detail for refusal in plan.refusals}, {"PR89888"})

	def test_two_rows_of_the_same_item_are_filled_in_invoice_order(self):
		serials = source_serials(count=12, item="PR89887")

		plan = plan_transfer(request(serials=serials, rows=target_rows()))

		first = [placement for placement in plan.placements if placement.target_row == "t-1"]
		second = [placement for placement in plan.placements if placement.target_row == "t-3"]

		self.assertEqual(len(first), 5)
		self.assertEqual(len(second), 7)
		self.assertEqual(plan.counts()["moved"], 12)

	def test_a_row_that_already_holds_serials_has_only_its_rest_to_give(self):
		rows = (
			TargetRow(
				row_key="t-2",
				item_code="PR89889",
				capacity=10,
				serial_required=MARKED,
				held=6,
				index=2,
			),
		)

		plan = plan_transfer(request(rows=rows))

		self.assertEqual(len(plan.placements), 4)
		self.assertEqual(len(plan.refusals), 14)
		self.assertTrue(all(refusal.reason == REASON_NO_ROOM for refusal in plan.refusals))

	def test_a_target_too_small_for_anything_says_so(self):
		rows = (
			TargetRow(
				row_key="t-2",
				item_code="PR89889",
				capacity=10,
				serial_required=MARKED,
				held=10,
				index=2,
			),
		)

		plan = plan_transfer(request(rows=rows))

		self.assertEqual(plan.placements, ())
		self.assertEqual({refusal.reason for refusal in plan.refusals}, {REASON_NO_ROOM})
		self.assertEqual(len(plan.refusals), 18)


class EligibilityTest(UnitTestCase):
	"""Only a row that may receive a registration can receive one — refusals and sources alike."""

	def test_an_unmarked_target_row_is_refused(self):
		rows = (
			TargetRow(row_key="t-2", item_code="PR89889", capacity=10, serial_required=UNMARKED, index=2),
		)

		plan = plan_transfer(request(rows=rows))

		self.assertEqual(plan.placements, ())
		self.assertEqual({refusal.reason for refusal in plan.refusals}, {REASON_TARGET_ROW_NOT_ELIGIBLE})

	def test_a_marked_row_beside_an_unmarked_one_is_still_used(self):
		"""The refusal is about the row, not about the item: any eligible row of it will do."""
		rows = (
			TargetRow(row_key="t-u", item_code="PR89889", capacity=10, serial_required=UNMARKED, index=1),
			TargetRow(row_key="t-m", item_code="PR89889", capacity=10, serial_required=MARKED, index=2),
		)

		plan = plan_transfer(request(rows=rows))

		self.assertEqual({placement.target_row for placement in plan.placements}, {"t-m"})

	def test_the_source_row_has_no_say_in_where_its_serials_go(self):
		"""The source row's own flag is not the question, so there is no field to answer it.

		The serial is registered: which row on the cancelled invoice it sits on was decided once,
		and it is history. Whether it may be *received* is the target row's question, asked above.
		"""
		self.assertNotIn("serial_required", SourceSerial.__dataclass_fields__)


class SelectiveTest(UnitTestCase):
	"""The operator chooses; the plan obeys and never re-aims a choice."""

	def test_a_selection_moves_only_what_it_names(self):
		"""Fifteen serials are left where they are, and nothing is said about them: not asked."""
		chosen = frozenset({"w-0001", "w-0002", "w-0003"})

		plan = plan_transfer(request(selection=chosen))

		self.assertEqual([placement.normalized for placement in plan.placements], sorted(chosen))
		self.assertEqual(plan.refusals, ())
		self.assertEqual(len(plan.unselected), 15)
		self.assertEqual(plan.counts()["source"] - plan.counts()["moved"], 15)

	def test_a_selection_naming_a_serial_that_is_not_there_is_refused(self):
		"""And kept apart from the refusals that describe the source: this one names no source row.

		It is a stale view, not a result, so it reaches no row of the record and the apply refuses
		on it — which is what keeps "what was confirmed" and "what moved" the same set.
		"""
		plan = plan_transfer(request(selection=frozenset({"w-0001", "ghost"})))

		self.assertEqual([refusal.reason for refusal in plan.refusals], [REASON_UNKNOWN_SELECTION])
		self.assertEqual([refusal.serial for refusal in plan.unknown_selection], ["ghost"])
		self.assertEqual(plan.unknown_selection[0].source_assignment, "")
		self.assertEqual(plan.counts()["refused"], 0)
		self.assertEqual(plan.counts()["unresolved"], 1)
		self.assertEqual(plan.counts()["moved"], 1)

	def test_an_explicit_row_is_obeyed(self):
		rows = (
			TargetRow(row_key="t-1", item_code="PR89889", capacity=2, serial_required=MARKED, index=1),
			TargetRow(row_key="t-2", item_code="PR89889", capacity=8, serial_required=MARKED, index=2),
		)

		plan = plan_transfer(request(rows=rows, mapping={"w-0001": "t-2"}))

		first = next(placement for placement in plan.placements if placement.normalized == "w-0001")

		self.assertEqual(first.target_row, "t-2")

	def test_an_explicit_row_of_another_item_is_refused_not_re_aimed(self):
		plan = plan_transfer(request(mapping={"w-0001": "t-1"}))

		refusal = next(refusal for refusal in plan.refusals if refusal.normalized == "w-0001")

		self.assertEqual(refusal.reason, REASON_ROW_MISMATCH)
		self.assertNotIn("w-0001", [placement.normalized for placement in plan.placements])

	def test_an_explicit_row_that_does_not_exist_is_refused(self):
		plan = plan_transfer(request(mapping={"w-0001": "t-9"}))

		refusal = next(refusal for refusal in plan.refusals if refusal.normalized == "w-0001")

		self.assertEqual(refusal.reason, REASON_ROW_MISMATCH)

	def test_an_explicit_row_with_no_room_is_refused_with_that_reason(self):
		rows = (
			TargetRow(
				row_key="t-2",
				item_code="PR89889",
				capacity=1,
				serial_required=MARKED,
				held=1,
				index=2,
			),
		)

		plan = plan_transfer(request(rows=rows, mapping={"w-0001": "t-2"}))

		refusal = next(refusal for refusal in plan.refusals if refusal.normalized == "w-0001")

		self.assertEqual(refusal.reason, REASON_NO_ROOM)


class DuplicateAndIdempotencyTest(UnitTestCase):
	"""What the target already holds is refused — which is what makes a second run a no-op."""

	def test_a_serial_the_target_already_holds_is_refused(self):
		plan = plan_transfer(request(on_target=frozenset({"w-0001"})))

		refusal = next(refusal for refusal in plan.refusals if refusal.normalized == "w-0001")

		self.assertEqual(refusal.reason, REASON_ALREADY_ON_TARGET)
		self.assertEqual(len(plan.placements), 10)

	def test_the_same_transfer_applied_twice_moves_nothing_the_second_time(self):
		"""Run the preview, put its result in the database, run it again: nothing moves again.

		The ten that moved come back as already-on-target; the eight that did not fit come back as
		having no room, which is still true. Neither is an error, and neither writes anything.
		"""
		first = plan_transfer(request())
		moved = frozenset(placement.normalized for placement in first.placements)
		rows = tuple(
			replace(row, held=len([p for p in first.placements if p.target_row == row.row_key]))
			for row in target_rows()
		)

		second = plan_transfer(request(rows=rows, on_target=moved))

		reasons = {refusal.normalized: refusal.reason for refusal in second.refusals}

		self.assertEqual(second.placements, ())
		self.assertEqual(second.counts()["moved"], 0)
		self.assertEqual(len(second.refusals), 18)
		self.assertTrue(all(reasons[serial] == REASON_ALREADY_ON_TARGET for serial in moved))
		self.assertTrue(
			all(reason == REASON_NO_ROOM for reason in reasons.values() if reason != REASON_ALREADY_ON_TARGET)
		)


class LiveElsewhereTest(UnitTestCase):
	"""A serial on another active invoice: told, acknowledged, and never silently ignored."""

	def test_it_is_refused_without_an_acknowledgment(self):
		plan = plan_transfer(request(live_elsewhere=frozenset({"w-0001"})))

		refusal = next(refusal for refusal in plan.refusals if refusal.normalized == "w-0001")

		self.assertEqual(refusal.reason, REASON_ACKNOWLEDGEMENT_MISSING)
		self.assertNotIn("w-0001", [placement.normalized for placement in plan.placements])

	def test_it_moves_when_the_operator_acknowledges_it(self):
		plan = plan_transfer(
			request(live_elsewhere=frozenset({"w-0001"}), acknowledged=frozenset({"w-0001"}))
		)

		self.assertIn("w-0001", [placement.normalized for placement in plan.placements])
		self.assertEqual(plan.acknowledged, ("w-0001",))
		self.assertNotIn(REASON_ACKNOWLEDGEMENT_MISSING, {refusal.reason for refusal in plan.refusals})

	def test_an_acknowledgment_for_something_else_changes_nothing(self):
		plan = plan_transfer(
			request(live_elsewhere=frozenset({"w-0001"}), acknowledged=frozenset({"w-0009"}))
		)

		refusal = next(refusal for refusal in plan.refusals if refusal.normalized == "w-0001")

		self.assertEqual(refusal.reason, REASON_ACKNOWLEDGEMENT_MISSING)
		self.assertEqual(plan.acknowledged, ())

	def test_an_unacknowledged_serial_is_a_warning_and_not_a_plain_refusal(self):
		"""One of the three lists the dialog shows, and the only one an answer changes.

		It costs a place, though: `w-0001` is not moved, so the row fills with `w-0002` through
		`w-0011` and the seven after them have no room — which is the ordinary arithmetic of a
		source that held more than the amendment invoices.
		"""
		plan = plan_transfer(request(live_elsewhere=frozenset({"w-0001"})))

		self.assertEqual([row.normalized for row in plan.warnings], ["w-0001"])
		self.assertEqual(len(plan.placements), 10)
		self.assertEqual(len(plan.blocked), 7)
		self.assertNotIn("w-0001", [row.normalized for row in plan.blocked])

	def test_a_serial_with_no_room_is_blocked_and_not_a_warning(self):
		plan = plan_transfer(request())

		self.assertEqual(plan.warnings, ())
		self.assertEqual(len(plan.blocked), 8)
		self.assertTrue(all(row.reason == REASON_NO_ROOM for row in plan.blocked))

	def test_the_three_lists_partition_the_refusals(self):
		"""Every refusal is in exactly one of them, so none is shown twice or dropped."""
		plan = plan_transfer(request(live_elsewhere=frozenset({"w-0001"}), selection=frozenset({"ghost"})))
		everything = (
			[row.normalized for row in plan.warnings]
			+ [row.normalized for row in plan.blocked]
			+ [row.normalized for row in plan.unknown_selection]
		)

		self.assertEqual(sorted(everything), sorted(row.normalized for row in plan.refusals))
		self.assertEqual(len(everything), len(set(everything)))


class TokenTest(UnitTestCase):
	"""The token proves which selection was confirmed — and nothing else."""

	def test_the_same_request_gives_the_same_token(self):
		self.assertEqual(preview_token(request()), preview_token(request()))

	def test_a_different_selection_gives_a_different_token(self):
		self.assertNotEqual(
			preview_token(request()),
			preview_token(request(selection=frozenset({"w-0001"}))),
		)

	def test_a_different_mapping_gives_a_different_token(self):
		self.assertNotEqual(
			preview_token(request()),
			preview_token(request(mapping={"w-0001": "t-2"})),
		)

	def test_a_different_acknowledgment_gives_a_different_token(self):
		self.assertNotEqual(
			preview_token(request()),
			preview_token(request(acknowledged=frozenset({"w-0001"}))),
		)

	def test_a_different_target_gives_a_different_token(self):
		self.assertNotEqual(
			preview_token(request()),
			preview_token(request(target_invoice="IN-260026-1")),
		)

	def test_the_default_selection_is_every_serial_the_source_holds(self):
		"""Two requests that mean the same thing must token the same way."""
		self.assertEqual(
			preview_token(request()),
			preview_token(request(selection=frozenset(row.normalized for row in source_serials()))),
		)

	def test_the_token_is_a_sha256_digest(self):
		self.assertEqual(len(preview_token(request())), 64)


class TheRecordTest(UnitTestCase):
	"""What the audit record says, decided here rather than in the gateway.

	A record that disagreed with the plan would be worse than no record: it would be a document
	somebody trusts. So the counts are the plan's own, the rows are one per decision, and the
	serial the source did not move is in no row at all.
	"""

	def test_the_header_carries_the_plans_own_numbers(self):
		plan = plan_transfer(request())
		record = record_of(plan, request())

		self.assertEqual(record["source_total"], 18)
		self.assertEqual(record["moved_count"], 10)
		self.assertEqual(record["refused_count"], 8)
		self.assertEqual(record["unselected_count"], 0)
		self.assertEqual(record["acknowledged_count"], 0)

	def test_the_header_names_both_invoices_and_the_company(self):
		record = record_of(plan_transfer(request()), request())

		self.assertEqual(record["source_invoice"], "IN-260025")
		self.assertEqual(record["target_invoice"], "IN-260025-1")
		self.assertEqual(record["company"], "پردازش هوشمند")

	def test_the_token_in_the_record_is_the_one_the_operator_confirmed(self):
		chosen = frozenset({"w-0001", "w-0002"})
		for_request = request(selection=chosen)

		record = record_of(plan_transfer(for_request), for_request)

		self.assertEqual(record["confirmed_token"], preview_token(for_request))

	def test_there_is_one_row_per_serial_that_was_decided_about(self):
		plan = plan_transfer(request())
		record = record_of(plan, request())

		self.assertEqual(len(record["items"]), 18)
		self.assertEqual(
			len([item for item in record["items"] if item["result"] == "Moved"]),
			record["moved_count"],
		)
		self.assertEqual(
			len([item for item in record["items"] if item["result"] == "Refused"]),
			record["refused_count"],
		)

	def test_the_rows_come_out_in_the_order_the_source_holds_them(self):
		plan = plan_transfer(request(selection=frozenset({"w-0003", "w-0001", "w-0002"})))
		record = record_of(plan, request(selection=frozenset({"w-0003", "w-0001", "w-0002"})))

		self.assertEqual(
			[item["normalized_serial"] for item in record["items"]],
			["w-0001", "w-0002", "w-0003"],
		)

	def test_an_unselected_serial_is_in_no_row(self):
		"""And the header is what says it existed: without `source_total` it would vanish."""
		for_request = request(selection=frozenset({"w-0001"}))
		record = record_of(plan_transfer(for_request), for_request)

		self.assertEqual(len(record["items"]), 1)
		self.assertEqual(record["unselected_count"], 17)
		self.assertEqual(record["source_total"], 18)

	def test_a_moved_row_says_where_it_went_and_names_what_it_came_from(self):
		record = record_of(plan_transfer(request()), request())
		moved = next(item for item in record["items"] if item["result"] == "Moved")

		self.assertEqual(moved["target_row"], "t-2")
		self.assertEqual(moved["target_index"], 2)
		self.assertEqual(moved["source_assignment"], "LSA-0001")
		self.assertEqual(moved["source_row"], "row-3")
		self.assertEqual(moved["source_index"], 3)
		self.assertEqual(moved["logistics_serial"], "LS-0001")
		self.assertEqual(moved["normalized_serial"], "w-0001")
		self.assertEqual(moved["serial_no"], "W-0001")
		self.assertEqual(moved["item_code"], "PR89889")
		self.assertEqual(moved["reason"], "")
		self.assertEqual(moved["target_row"], "t-2")

	def test_a_refused_row_says_why_and_names_no_target(self):
		record = record_of(plan_transfer(request()), request())
		refused = next(item for item in record["items"] if item["result"] == "Refused")

		self.assertEqual(refused["reason"], REASON_NO_ROOM)
		self.assertEqual(refused["reason_message"], rules.REASON_MESSAGES[REASON_NO_ROOM])
		self.assertEqual(refused["target_row"], "")
		self.assertIsNone(refused["target_index"])
		self.assertEqual(refused["source_assignment"], "LSA-0011")

	def test_a_stale_selection_reaches_no_row(self):
		"""It names no source assignment, so there is no source row to describe."""
		for_request = request(selection=frozenset({"ghost"}))
		record = record_of(plan_transfer(for_request), for_request)

		self.assertEqual(record["items"], [])
		self.assertEqual(record["moved_count"], 0)
		self.assertEqual(record["refused_count"], 0)

	def test_an_acknowledged_serial_is_marked_in_its_row(self):
		for_request = request(live_elsewhere=frozenset({"w-0001"}), acknowledged=frozenset({"w-0001"}))
		record = record_of(plan_transfer(for_request), for_request)

		self.assertEqual(record["acknowledged_count"], 1)
		self.assertEqual(
			[item["acknowledged"] for item in record["items"] if item["normalized_serial"] == "w-0001"],
			[1],
		)

	def test_every_row_carries_exactly_the_fields_the_child_table_has(self):
		"""So a field added to one side and not the other is a failing test."""
		record = record_of(plan_transfer(request()), request())

		for item in record["items"]:
			self.assertEqual(
				sorted(item),
				[
					"acknowledged",
					"item_code",
					"logistics_serial",
					"normalized_serial",
					"reason",
					"reason_message",
					"result",
					"serial_no",
					"source_assignment",
					"source_index",
					"source_row",
					"target_index",
					"target_row",
				],
			)


class ThePreviewOfWhatWillHappenTest(UnitTestCase):
	"""The list the operator reads, in the order they will read it.

	One row per serial the source holds and not four lists: the plan keeps placements and refusals
	apart because they are acted on differently, but a person checking a transfer reads the invoice.
	The window drew a table out of four grouped lists once and produced a table grouped by outcome,
	so the order is decided here and the window only draws.
	"""

	def preview(self, **overrides):
		for_request = request(**overrides)

		return preview_of(plan_transfer(for_request), for_request)

	def test_there_is_one_row_per_serial_the_source_holds(self):
		preview = self.preview()

		self.assertEqual(len(preview["serials"]), 18)
		self.assertEqual(
			[row["serial"] for row in preview["serials"]],
			[f"W-{index:04d}" for index in range(1, 19)],
		)

	def test_the_rows_come_out_in_the_source_order_and_not_grouped_by_outcome(self):
		"""The order is the one the invoice is printed in, and the two can be told apart.

		The serials alternate between an item the amendment carries and one it no longer does, so
		source order and outcome order are different lists. Grouped — which is what the window
		produced when it regrouped four lists of its own — this would read three moves and then
		three refusals; in source order it alternates, which is what an operator holding the
		invoice can check a row at a time.
		"""
		serials = tuple(
			SourceSerial(
				assignment=f"LSA-{index:04d}",
				serial=f"W-{index:04d}",
				normalized=f"w-{index:04d}",
				item_code="PR89889" if index % 2 else "PR89888",
				row_key="row-3",
				serial_ref=f"LS-{index:04d}",
				version=2,
			)
			for index in range(1, 7)
		)
		for_request = request(serials=serials)
		preview = preview_of(plan_transfer(for_request), for_request)
		outcomes = [row["outcome"] for row in preview["serials"]]

		self.assertEqual(
			outcomes,
			[
				"Moved",
				REASON_ITEM_NOT_ON_TARGET,
				"Moved",
				REASON_ITEM_NOT_ON_TARGET,
				"Moved",
				REASON_ITEM_NOT_ON_TARGET,
			],
		)
		self.assertNotEqual(outcomes, sorted(outcomes), "grouped by outcome the two would read alike")
		self.assertEqual([row["order"] for row in preview["serials"]], list(range(6)))

	def test_a_serial_that_moves_names_the_row_it_goes_to(self):
		moved = self.preview()["serials"][0]

		self.assertEqual(moved["outcome"], "Moved")
		self.assertEqual(moved["target_row"], "t-2")
		self.assertEqual(moved["target_index"], 2)
		self.assertEqual(moved["message"], "")
		self.assertFalse(moved["answerable"])

	def test_a_serial_with_no_room_carries_its_reason_and_its_sentence(self):
		last = self.preview()["serials"][-1]

		self.assertEqual(last["outcome"], REASON_NO_ROOM)
		self.assertEqual(last["message"], rules.REASON_MESSAGES[REASON_NO_ROOM])
		self.assertEqual(last["detail"], "PR89889")
		self.assertIsNone(last["target_row"])
		self.assertFalse(last["answerable"])

	def test_only_a_serial_live_elsewhere_is_answerable(self):
		"""The flag the window reads instead of knowing which reason code is a question."""
		preview = self.preview(live_elsewhere=frozenset({"w-0001"}))
		answerable = [row for row in preview["serials"] if row["answerable"]]

		self.assertEqual([row["serial"] for row in answerable], ["W-0001"])
		self.assertEqual(answerable[0]["outcome"], REASON_ACKNOWLEDGEMENT_MISSING)

	def test_an_acknowledged_serial_is_marked_and_moves(self):
		preview = self.preview(live_elsewhere=frozenset({"w-0001"}), acknowledged=frozenset({"w-0001"}))
		first = preview["serials"][0]

		self.assertTrue(first["acknowledged"])
		self.assertEqual(first["outcome"], "Moved")

	def test_a_serial_nobody_asked_for_is_a_row_of_its_own(self):
		"""Shown, and marked as not moving, so the operator sees the whole invoice they cancelled."""
		preview = self.preview(selection=frozenset({"w-0001", "w-0002"}))

		self.assertEqual([row["outcome"] for row in preview["serials"][:3]], ["Moved", "Moved", "Unselected"])
		self.assertEqual(len(preview["serials"]), 18)
		self.assertEqual(preview["counts"]["unselected"], 16)

	def test_a_stale_selection_is_reported_beside_the_list(self):
		"""It has no source row to sit on, so it is not in the list and is not lost either."""
		preview = self.preview(selection=frozenset({"ghost"}))

		self.assertEqual([row["serial"] for row in preview["stale"]], ["ghost"])
		self.assertEqual(len(preview["serials"]), 18)
		self.assertTrue(all(row["outcome"] == "Unselected" for row in preview["serials"]))

	def test_the_counts_are_the_plans_own(self):
		preview = self.preview()

		self.assertEqual(preview["counts"], plan_transfer(request()).counts())

	def test_the_list_accounts_for_every_serial_exactly_once(self):
		"""Whatever the plan is, one row per source serial — never more and never fewer."""
		for overrides in (
			{},
			{"selection": frozenset({"w-0001"})},
			{"live_elsewhere": frozenset({"w-0001"})},
			{"live_elsewhere": frozenset({"w-0001"}), "acknowledged": frozenset({"w-0001"})},
			{"rows": ()},
			{"on_target": frozenset({"w-0001"})},
		):
			with self.subTest(overrides=sorted(overrides)):
				preview = self.preview(**overrides)

				self.assertEqual(len(preview["serials"]), 18)
				self.assertEqual(
					[row["serial"] for row in preview["serials"]],
					[f"W-{index:04d}" for index in range(1, 19)],
				)


class TheWriteIsDerivedLikeAnyOtherTest(UnitTestCase):
	"""A transfer writes registrations through the one function that derives them."""

	def test_every_placement_becomes_one_assignment_on_the_target(self):
		for_request = request()
		plan = plan_transfer(for_request)
		values = assignment_values(plan, for_request)

		self.assertEqual(len(values), 10)
		self.assertTrue(all(value.sales_invoice == "IN-260025-1" for value in values))
		self.assertTrue(all(value.company == "پردازش هوشمند" for value in values))

	def test_the_identity_on_the_new_row_is_the_serials_own(self):
		for_request = request()
		values = assignment_values(plan_transfer(for_request), for_request)

		self.assertEqual(
			[(value.item_code, value.normalized_serial) for value in values],
			[("PR89889", f"w-{index:04d}") for index in range(1, 11)],
		)

	def test_the_new_row_points_at_the_serials_document_and_its_row(self):
		for_request = request()
		values = assignment_values(plan_transfer(for_request), for_request)

		self.assertEqual(values[0].logistics_serial, "LS-0001")
		self.assertEqual(values[0].sales_invoice_item, "t-2")
		self.assertEqual(values[0].invoice_item_idx, 2)

	def test_nothing_is_written_when_nothing_moves(self):
		for_request = request(rows=())
		values = assignment_values(plan_transfer(for_request), for_request)

		self.assertEqual(values, ())

	def test_the_written_values_are_the_ones_the_planner_promised(self):
		"""Field for field against the placement, so the write cannot drift from the preview."""
		for_request = request()
		plan = plan_transfer(for_request)

		# `strict=True`: the two lists correspond one for one, and a zip that silently stopped at
		# the shorter one would let a placement go unchecked.
		for placement, value in zip(plan.placements, assignment_values(plan, for_request), strict=True):
			with self.subTest(serial=placement.normalized):
				self.assertEqual(value.logistics_serial, placement.serial_ref)
				self.assertEqual(value.sales_invoice_item, placement.target_row)
				self.assertEqual(value.item_code, placement.item_code)
				self.assertEqual(value.normalized_serial, placement.normalized)


class NothingIsWrittenToTheSourceTest(UnitTestCase):
	"""The plan is a description, and the source appears in it only as a name."""

	def test_a_placement_carries_nothing_but_what_the_target_write_needs(self):
		"""Field for field, so a write cannot be built from a plan without naming one of these."""
		self.assertEqual(
			sorted(Placement.__dataclass_fields__),
			[
				"item_code",
				"normalized",
				"serial",
				"serial_ref",
				"source_assignment",
				"target_index",
				"target_row",
				"version",
			],
		)

	def test_a_placement_points_at_the_serial_document_and_its_rule_set(self):
		"""So `derive_assignment_values` can build the new row without a second read."""
		placement = plan_transfer(request()).placements[0]

		self.assertEqual(placement.serial_ref, "LS-0001")
		self.assertEqual(placement.version, 2)

	def test_the_source_serials_themselves_are_returned_untouched(self):
		"""The caller gets the same source rows it passed in, for the report about what stayed."""
		serials = source_serials()

		plan = plan_transfer(request(serials=serials, selection=frozenset({"w-0001", "w-0002"})))

		self.assertTrue(all(row in serials for row in plan.unselected))
		self.assertEqual(
			{placement.source_assignment for placement in plan.placements}, {"LSA-0001", "LSA-0002"}
		)
