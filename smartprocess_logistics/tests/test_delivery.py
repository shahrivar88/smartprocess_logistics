"""Which delivery a serial belongs to, and what a stored link still means.

These are the pure decisions the missing-serial report and the registration gateway both rely
on: `select_delivery` never guesses between two live shipments, `delivered_units` counts what
actually left net of returns, and `link_state` reads a stored link without rewriting it.
"""

from smartprocess_logistics.core.delivery import (
	CODE_DELIVERY_CHOICE_REQUIRED,
	CODE_DELIVERY_CONFLICT,
	CODE_DELIVERY_NOT_A_CANDIDATE,
	DOCSTATUS_CANCELLED,
	DOCSTATUS_DRAFT,
	DOCSTATUS_SUBMITTED,
	LINK_CANCELLED,
	LINK_DELIVERED,
	LINK_DRAFT,
	LINK_RETURNED,
	LINK_UNKNOWN,
	LINK_UNLINKED,
	STATUS_CHOICE_REQUIRED,
	STATUS_LINKED,
	STATUS_NOT_A_CANDIDATE,
	STATUS_PROVISIONAL,
	DeliveryCandidate,
	conflicting_delivery,
	delivered_units,
	find_conflicts,
	link_state,
	missing_units,
	select_delivery,
)
from smartprocess_logistics.tests.base import UnitTestCase

INVOICE_ITEM = "sii-row-1"


def candidate(**overrides):
	arguments = {
		"delivery_note": "DN-0001",
		"delivery_note_item": "dn-row-1",
		"invoice_item": INVOICE_ITEM,
		"delivery_date": "2026-05-10",
		"qty": 5.0,
		"docstatus": DOCSTATUS_SUBMITTED,
		"is_return": False,
		"per_returned": 0.0,
		"warehouse": "Stores - T",
		"against_detail": None,
	}
	arguments.update(overrides)
	return DeliveryCandidate(**arguments)


def forward(note="DN-0001", qty=5.0, **overrides):
	return candidate(delivery_note=note, delivery_note_item=f"{note}-row", qty=qty, **overrides)


def returned(note="DN-RET-1", qty=-5.0, **overrides):
	return candidate(
		delivery_note=note,
		delivery_note_item=f"{note}-row",
		qty=qty,
		is_return=True,
		against_detail="DN-0001-row",
		**overrides,
	)


class TestCandidate(UnitTestCase):
	def test_units_ignore_the_sign_of_a_return(self):
		self.assertEqual(returned(qty=-5.0).units, 5)

	def test_units_round_a_fraction_up(self):
		self.assertEqual(candidate(qty=2.5).units, 3)

	def test_units_use_the_same_tolerance_as_the_invoice_row(self):
		self.assertEqual(candidate(qty=2.0000000001).units, 2)

	def test_a_zero_quantity_moves_no_unit(self):
		self.assertEqual(candidate(qty=0.0).units, 0)

	def test_a_submitted_note_is_the_only_submitted_state(self):
		self.assertTrue(candidate(docstatus=DOCSTATUS_SUBMITTED).submitted)
		self.assertFalse(candidate(docstatus=DOCSTATUS_DRAFT).submitted)
		self.assertFalse(candidate(docstatus=DOCSTATUS_CANCELLED).submitted)

	def test_a_cancelled_note_is_the_only_cancelled_state(self):
		self.assertTrue(candidate(docstatus=DOCSTATUS_CANCELLED).cancelled)
		self.assertFalse(candidate(docstatus=DOCSTATUS_SUBMITTED).cancelled)

	def test_a_return_row_is_not_forward(self):
		self.assertFalse(returned().is_forward)
		self.assertTrue(forward().is_forward)

	def test_a_forward_note_can_take_a_link_until_it_is_cancelled(self):
		self.assertTrue(forward(docstatus=DOCSTATUS_DRAFT).is_open)
		self.assertTrue(forward(docstatus=DOCSTATUS_SUBMITTED).is_open)
		self.assertFalse(forward(docstatus=DOCSTATUS_CANCELLED).is_open)

	def test_a_return_row_never_takes_a_link(self):
		self.assertFalse(returned(docstatus=DOCSTATUS_SUBMITTED).is_open)


class TestSelectDelivery(UnitTestCase):
	def test_one_live_delivery_is_linked(self):
		choice = select_delivery([forward()])

		self.assertEqual(choice.status, STATUS_LINKED)
		self.assertTrue(choice.is_linked)
		self.assertEqual(choice.delivery_note, "DN-0001")
		self.assertEqual(choice.delivery_note_item, "DN-0001-row")

	def test_nothing_delivered_means_provisional(self):
		choice = select_delivery([])

		self.assertEqual(choice.status, STATUS_PROVISIONAL)
		self.assertFalse(choice.is_linked)
		self.assertIsNone(choice.delivery_note)

	def test_two_live_deliveries_are_refused_not_guessed(self):
		choice = select_delivery([forward("DN-0001"), forward("DN-0002")])

		self.assertEqual(choice.status, STATUS_CHOICE_REQUIRED)
		self.assertEqual(choice.code, CODE_DELIVERY_CHOICE_REQUIRED)
		self.assertIsNone(choice.delivery_note)

	def test_a_cancelled_delivery_is_not_a_live_choice(self):
		choice = select_delivery([forward("DN-0001"), forward("DN-0002", docstatus=DOCSTATUS_CANCELLED)])

		self.assertEqual(choice.status, STATUS_LINKED)
		self.assertEqual(choice.delivery_note, "DN-0001")

	def test_a_return_row_does_not_create_a_second_choice(self):
		choice = select_delivery([forward("DN-0001"), returned("DN-RET-1")])

		self.assertEqual(choice.status, STATUS_LINKED)
		self.assertEqual(choice.delivery_note, "DN-0001")

	def test_two_rows_of_the_same_note_are_not_an_ambiguity(self):
		choice = select_delivery(
			[
				candidate(delivery_note="DN-0001", delivery_note_item="row-a"),
				candidate(delivery_note="DN-0001", delivery_note_item="row-b"),
			]
		)

		self.assertEqual(choice.status, STATUS_LINKED)
		self.assertEqual(choice.delivery_note_item, "row-a")

	def test_the_operator_can_name_the_delivery_among_several(self):
		choice = select_delivery(
			[forward("DN-0001"), forward("DN-0002")],
			requested_note="DN-0002",
		)

		self.assertEqual(choice.status, STATUS_LINKED)
		self.assertEqual(choice.delivery_note, "DN-0002")

	def test_the_operator_can_name_the_row_of_the_delivery(self):
		choice = select_delivery(
			[
				candidate(delivery_note="DN-0001", delivery_note_item="row-a"),
				candidate(delivery_note="DN-0001", delivery_note_item="row-b"),
			],
			requested_note="DN-0001",
			requested_item="row-b",
		)

		self.assertEqual(choice.status, STATUS_LINKED)
		self.assertEqual(choice.delivery_note_item, "row-b")

	def test_a_requested_note_that_is_not_live_is_refused(self):
		choice = select_delivery(
			[forward("DN-0001"), forward("DN-0002")],
			requested_note="DN-0003",
		)

		self.assertEqual(choice.status, STATUS_NOT_A_CANDIDATE)
		self.assertEqual(choice.code, CODE_DELIVERY_NOT_A_CANDIDATE)
		self.assertIsNone(choice.delivery_note)

	def test_a_cancelled_note_cannot_be_requested(self):
		choice = select_delivery(
			[forward("DN-0001", docstatus=DOCSTATUS_CANCELLED)],
			requested_note="DN-0001",
		)

		self.assertEqual(choice.status, STATUS_NOT_A_CANDIDATE)

	def test_a_blank_request_falls_back_to_the_automatic_rule(self):
		choice = select_delivery([forward()], requested_note="   ", requested_item="")

		self.assertEqual(choice.status, STATUS_LINKED)
		self.assertEqual(choice.delivery_note, "DN-0001")

	def test_a_requested_row_that_does_not_belong_is_refused(self):
		choice = select_delivery(
			[forward("DN-0001")],
			requested_note="DN-0001",
			requested_item="some-other-row",
		)

		self.assertEqual(choice.status, STATUS_NOT_A_CANDIDATE)


class TestDeliveredUnits(UnitTestCase):
	def test_a_submitted_delivery_counts(self):
		self.assertEqual(delivered_units([forward(qty=5.0)]), 5)

	def test_a_draft_delivery_has_not_left(self):
		self.assertEqual(delivered_units([forward(qty=5.0, docstatus=DOCSTATUS_DRAFT)]), 0)

	def test_a_cancelled_delivery_has_been_undone(self):
		self.assertEqual(delivered_units([forward(qty=5.0, docstatus=DOCSTATUS_CANCELLED)]), 0)

	def test_partial_deliveries_add_up(self):
		self.assertEqual(delivered_units([forward("DN-0001", qty=3.0), forward("DN-0002", qty=2.0)]), 5)

	def test_a_submitted_return_reduces_what_was_delivered(self):
		self.assertEqual(delivered_units([forward(qty=5.0), returned(qty=-2.0)]), 3)

	def test_a_draft_return_does_not_reduce_anything(self):
		self.assertEqual(
			delivered_units([forward(qty=5.0), returned(qty=-2.0, docstatus=DOCSTATUS_DRAFT)]),
			5,
		)

	def test_returns_beyond_what_was_shipped_floor_at_zero(self):
		self.assertEqual(delivered_units([forward(qty=2.0), returned(qty=-5.0)]), 0)

	def test_nothing_delivered_is_zero_not_negative(self):
		self.assertEqual(delivered_units([]), 0)


class TestLinkState(UnitTestCase):
	def test_a_provisional_link_is_unlinked(self):
		self.assertEqual(link_state([forward()], None), LINK_UNLINKED)
		self.assertEqual(link_state([forward()], "   "), LINK_UNLINKED)

	def test_a_note_that_no_longer_carries_the_row_is_unknown(self):
		self.assertEqual(link_state([forward("DN-0002")], "DN-0001"), LINK_UNKNOWN)

	def test_a_note_with_only_return_rows_is_cancelled(self):
		self.assertEqual(link_state([returned("DN-0001")], "DN-0001"), LINK_CANCELLED)

	def test_a_cancelled_note_is_cancelled(self):
		self.assertEqual(
			link_state([forward("DN-0001", docstatus=DOCSTATUS_CANCELLED)], "DN-0001"),
			LINK_CANCELLED,
		)

	def test_a_draft_note_is_draft(self):
		self.assertEqual(
			link_state([forward("DN-0001", docstatus=DOCSTATUS_DRAFT)], "DN-0001"),
			LINK_DRAFT,
		)

	def test_a_submitted_note_is_delivered(self):
		self.assertEqual(link_state([forward("DN-0001")], "DN-0001"), LINK_DELIVERED)

	def test_a_fully_returned_note_is_returned_by_its_own_percentage(self):
		# The return lives on its own note, so it never appears among this note's candidates.
		self.assertEqual(
			link_state([forward("DN-0001", per_returned=100)], "DN-0001"),
			LINK_RETURNED,
		)

	def test_a_partly_returned_note_is_still_delivered(self):
		self.assertEqual(
			link_state([forward("DN-0001", per_returned=40)], "DN-0001"),
			LINK_DELIVERED,
		)

	def test_a_note_whose_net_units_reach_zero_is_returned(self):
		self.assertEqual(
			link_state([forward("DN-0001", qty=5.0), returned("DN-0001", qty=-5.0)], "DN-0001"),
			LINK_RETURNED,
		)

	def test_another_note_in_the_list_does_not_decide_this_links_state(self):
		state = link_state(
			[forward("DN-0001"), forward("DN-0002", per_returned=100)],
			"DN-0001",
		)

		self.assertEqual(state, LINK_DELIVERED)


class TestConflicts(UnitTestCase):
	def test_a_provisional_registration_conflicts_with_nothing(self):
		self.assertIsNone(conflicting_delivery(None, [("DN-0009", DOCSTATUS_SUBMITTED, 0)]))

	def test_the_same_delivery_is_not_a_conflict(self):
		self.assertIsNone(conflicting_delivery("DN-0001", [("DN-0001", DOCSTATUS_SUBMITTED, 0)]))

	def test_another_submitted_delivery_is_a_conflict(self):
		self.assertEqual(
			conflicting_delivery("DN-0001", [("DN-0002", DOCSTATUS_SUBMITTED, 0)]),
			CODE_DELIVERY_CONFLICT,
		)

	def test_a_draft_other_delivery_is_not_a_conflict(self):
		self.assertIsNone(conflicting_delivery("DN-0001", [("DN-0002", DOCSTATUS_DRAFT, 0)]))

	def test_a_cancelled_other_delivery_is_not_a_conflict(self):
		self.assertIsNone(conflicting_delivery("DN-0001", [("DN-0002", DOCSTATUS_CANCELLED, 0)]))

	def test_a_fully_returned_other_delivery_released_the_unit(self):
		self.assertIsNone(conflicting_delivery("DN-0001", [("DN-0002", DOCSTATUS_SUBMITTED, 100)]))

	def test_an_empty_other_link_is_not_a_conflict(self):
		self.assertIsNone(conflicting_delivery("DN-0001", [("", DOCSTATUS_SUBMITTED, 0)]))

	def test_the_first_conflicting_delivery_decides(self):
		self.assertEqual(
			conflicting_delivery(
				"DN-0001",
				[("DN-0002", DOCSTATUS_DRAFT, 0), ("DN-0003", DOCSTATUS_SUBMITTED, 0)],
			),
			CODE_DELIVERY_CONFLICT,
		)

	def test_find_conflicts_names_only_the_held_identities_in_order(self):
		others = {
			"AAA": [("DN-0002", DOCSTATUS_SUBMITTED, 0)],
			"BBB": [("DN-0001", DOCSTATUS_SUBMITTED, 0)],
		}

		conflicts = find_conflicts(
			[("BBB", "DN-0001"), ("AAA", "DN-0001"), ("CCC", "DN-0001")],
			others,
		)

		self.assertEqual(conflicts, ("AAA",))

	def test_find_conflicts_of_nothing_is_empty(self):
		self.assertEqual(find_conflicts([], {}), ())


class TestMissingUnits(UnitTestCase):
	def test_the_shortfall_is_delivered_minus_registered(self):
		self.assertEqual(missing_units(5, 2), 3)

	def test_a_fully_labelled_delivery_is_not_short(self):
		self.assertEqual(missing_units(5, 5), 0)

	def test_a_row_registered_beyond_what_left_is_not_negative(self):
		self.assertEqual(missing_units(5, 7), 0)

	def test_nothing_delivered_is_not_short(self):
		self.assertEqual(missing_units(0, 0), 0)
