"""Authorization decisions, made without a database."""

from smartprocess_logistics.core.authz import (
	CODE_COMPANY_NOT_ALLOWED,
	CODE_INVOICE_CANCELLED,
	CODE_INVOICE_NOT_FOUND,
	CODE_NOT_AUTHENTICATED,
	CODE_NOT_PERMITTED,
	CODE_SESSION_NOT_OWNED,
	CODE_STRICT_COMPANY_UNSET,
	CODE_UNKNOWN_ENDPOINT,
	EDITOR,
	ENDPOINT_LEVELS,
	ENDPOINT_ROLES,
	INVOICE_ENDPOINTS,
	INVOICE_REFUSAL,
	MANAGER,
	REFUSAL_INVOICE_CANCELLED,
	ROLE_ARCHIVIST,
	ROLE_EDITOR,
	ROLE_MANAGER,
	ROLE_SYSTEM_MANAGER,
	ROLE_VIEWER,
	TRANSFER_ENDPOINTS,
	VIEWER,
	WRITING_ENDPOINTS,
	AuthorizationError,
	InvoiceRef,
	Principal,
	authorize,
	may_reach_any,
	require_company,
	require_endpoint,
	require_invoice_access,
	require_session_owner,
	require_transfer_invoices,
	visible_companies,
)
from smartprocess_logistics.tests.base import UnitTestCase

ALPHA = InvoiceRef(name="IN-1", company="Alpha", docstatus=1)
CANCELLED = InvoiceRef(name="IN-3", company="Alpha", docstatus=2)
DRAFT = InvoiceRef(name="IN-4", company="Alpha", docstatus=0)
BETA = InvoiceRef(name="IN-2", company="Beta", docstatus=1)

VIEWER_ACCOUNT = Principal(user="viewer@example.com", roles=frozenset({ROLE_VIEWER}))
EDITOR_ACCOUNT = Principal(user="editor@example.com", roles=frozenset({ROLE_EDITOR}))
MANAGER_ACCOUNT = Principal(user="manager@example.com", roles=frozenset({ROLE_MANAGER}))
SYSTEM_ACCOUNT = Principal(user="admin@example.com", roles=frozenset({ROLE_SYSTEM_MANAGER}))
NOBODY = Principal(user="nobody@example.com", roles=frozenset({"Sales User"}))
GUEST = Principal(user="", roles=frozenset())

ALPHA_EDITOR = Principal(
	user="editor@example.com",
	roles=frozenset({ROLE_EDITOR}),
	companies=frozenset({"Alpha"}),
)
STRICT_NO_COMPANY = Principal(
	user="strict@example.com",
	roles=frozenset({ROLE_EDITOR}),
	companies=None,
	strict_companies=True,
)


class TestLevels(UnitTestCase):
	def test_the_roles_map_to_their_levels(self):
		self.assertEqual(VIEWER_ACCOUNT.level(), VIEWER)
		self.assertEqual(EDITOR_ACCOUNT.level(), EDITOR)
		self.assertEqual(MANAGER_ACCOUNT.level(), MANAGER)
		self.assertEqual(SYSTEM_ACCOUNT.level(), MANAGER)

	def test_a_user_with_no_app_role_has_no_level(self):
		self.assertIsNone(NOBODY.level())

	def test_the_strongest_role_wins(self):
		both = Principal(user="both@example.com", roles=frozenset({ROLE_VIEWER, ROLE_EDITOR}))

		self.assertEqual(both.level(), EDITOR)

	def test_levels_are_ordered(self):
		self.assertTrue(MANAGER_ACCOUNT.has_level(VIEWER))
		self.assertTrue(MANAGER_ACCOUNT.has_level(MANAGER))
		self.assertFalse(VIEWER_ACCOUNT.has_level(EDITOR))
		self.assertFalse(EDITOR_ACCOUNT.has_level(MANAGER))


class TestEndpointTable(UnitTestCase):
	def test_every_endpoint_has_a_level(self):
		for endpoint, level in ENDPOINT_LEVELS.items():
			with self.subTest(endpoint=endpoint):
				self.assertIn(level, (VIEWER, EDITOR, MANAGER))

	def test_the_read_endpoints_are_the_lowest_level(self):
		self.assertEqual(ENDPOINT_LEVELS["get_invoice_serial_status"], VIEWER)
		self.assertEqual(ENDPOINT_LEVELS["get_serial_assignments"], VIEWER)

	def test_the_administrative_endpoint_needs_a_manager(self):
		self.assertEqual(ENDPOINT_LEVELS["delete_assignments"], MANAGER)

	def test_adding_one_serial_is_the_same_act_as_typing_a_row(self):
		"""The scan station's endpoint: writing a label is writing a label, however it arrives."""
		self.assertEqual(ENDPOINT_LEVELS["add_row_serial"], ENDPOINT_LEVELS["set_row_serials"])

	def test_an_unknown_endpoint_is_refused(self):
		with self.assertRaises(AuthorizationError) as caught:
			require_endpoint(EDITOR_ACCOUNT, "not_an_endpoint")

		self.assertEqual(caught.exception.code, CODE_UNKNOWN_ENDPOINT)

	def test_an_unauthenticated_caller_is_refused(self):
		with self.assertRaises(AuthorizationError) as caught:
			require_endpoint(GUEST, "get_invoice_serial_status")

		self.assertEqual(caught.exception.code, CODE_NOT_AUTHENTICATED)


class TestLevelChecks(UnitTestCase):
	def test_a_viewer_may_read(self):
		self.assertEqual(require_endpoint(VIEWER_ACCOUNT, "get_invoice_serial_status"), VIEWER)

	def test_a_viewer_may_not_write(self):
		with self.assertRaises(AuthorizationError) as caught:
			require_endpoint(VIEWER_ACCOUNT, "set_row_serials")

		self.assertEqual(caught.exception.code, CODE_NOT_PERMITTED)

	def test_an_editor_may_write_and_may_not_administer(self):
		self.assertEqual(require_endpoint(EDITOR_ACCOUNT, "set_row_serials"), EDITOR)

		with self.assertRaises(AuthorizationError) as caught:
			require_endpoint(EDITOR_ACCOUNT, "delete_assignments")

		self.assertEqual(caught.exception.code, CODE_NOT_PERMITTED)

	def test_a_manager_may_administer(self):
		self.assertEqual(require_endpoint(MANAGER_ACCOUNT, "delete_assignments"), MANAGER)

	def test_a_sales_user_without_the_app_role_is_refused_everywhere(self):
		for endpoint in ENDPOINT_LEVELS:
			with self.subTest(endpoint=endpoint):
				with self.assertRaises(AuthorizationError):
					require_endpoint(NOBODY, endpoint)


class TestCompanyIsolation(UnitTestCase):
	def test_an_unrestricted_user_reaches_every_company(self):
		self.assertTrue(EDITOR_ACCOUNT.may_reach_company("Alpha"))
		self.assertTrue(EDITOR_ACCOUNT.may_reach_company("Beta"))

	def test_a_restricted_user_reaches_only_its_companies(self):
		self.assertTrue(ALPHA_EDITOR.may_reach_company("Alpha"))
		self.assertFalse(ALPHA_EDITOR.may_reach_company("Beta"))

	def test_strict_mode_without_a_company_denies_everything(self):
		self.assertFalse(STRICT_NO_COMPANY.may_reach_company("Alpha"))

		with self.assertRaises(AuthorizationError) as caught:
			require_company(STRICT_NO_COMPANY, "Alpha")

		self.assertEqual(caught.exception.code, CODE_STRICT_COMPANY_UNSET)

	def test_an_empty_company_is_refused(self):
		for company in ("", "   ", None, 5):
			with self.subTest(company=company):
				with self.assertRaises(AuthorizationError) as caught:
					require_company(EDITOR_ACCOUNT, company)

				self.assertEqual(caught.exception.code, CODE_COMPANY_NOT_ALLOWED)

	def test_a_forbidden_company_is_refused(self):
		with self.assertRaises(AuthorizationError) as caught:
			require_company(ALPHA_EDITOR, "Beta")

		self.assertEqual(caught.exception.code, CODE_COMPANY_NOT_ALLOWED)

	def test_visible_companies_reports_no_restriction_as_none(self):
		self.assertIsNone(visible_companies(EDITOR_ACCOUNT))

	def test_visible_companies_reports_the_restriction(self):
		self.assertEqual(visible_companies(ALPHA_EDITOR), frozenset({"Alpha"}))

	def test_visible_companies_reports_strict_and_unset_as_empty(self):
		self.assertEqual(visible_companies(STRICT_NO_COMPANY), frozenset())


class TestMayReachAny(UnitTestCase):
	def test_an_empty_set_is_reached_by_nobody(self):
		for principal in (EDITOR_ACCOUNT, ALPHA_EDITOR, STRICT_NO_COMPANY, GUEST):
			with self.subTest(user=principal.user):
				self.assertFalse(may_reach_any(principal, frozenset()))

	def test_an_unrestricted_principal_reaches_any_nonempty_set(self):
		self.assertTrue(may_reach_any(EDITOR_ACCOUNT, {"Alpha"}))
		self.assertTrue(may_reach_any(EDITOR_ACCOUNT, {"Alpha", "Beta"}))

	def test_a_restricted_principal_reaches_its_own_companies(self):
		self.assertTrue(may_reach_any(ALPHA_EDITOR, {"Alpha"}))
		self.assertTrue(may_reach_any(ALPHA_EDITOR, {"Alpha", "Beta"}))

	def test_a_restricted_principal_does_not_reach_another_company(self):
		self.assertFalse(may_reach_any(ALPHA_EDITOR, {"Beta"}))
		self.assertFalse(may_reach_any(ALPHA_EDITOR, {"Beta", "Gamma"}))

	def test_strict_without_a_company_reaches_no_set(self):
		self.assertFalse(may_reach_any(STRICT_NO_COMPANY, {"Alpha"}))


class TestInvoiceAccess(UnitTestCase):
	def test_a_readable_invoice_in_the_right_company_is_allowed(self):
		self.assertIs(require_invoice_access(EDITOR_ACCOUNT, "set_row_serials", ALPHA), ALPHA)

	def test_a_missing_invoice_is_refused(self):
		with self.assertRaises(AuthorizationError) as caught:
			require_invoice_access(EDITOR_ACCOUNT, "set_row_serials", None)

		self.assertEqual(caught.exception.code, CODE_INVOICE_NOT_FOUND)

	def test_an_invoice_in_another_company_is_refused_the_same_way(self):
		"""The code a caller can read, not only the sentence: `_fail` puts it in the message."""
		with self.assertRaises(AuthorizationError) as caught:
			require_invoice_access(ALPHA_EDITOR, "get_invoice_serial_status", BETA)

		self.assertEqual(caught.exception.code, CODE_INVOICE_NOT_FOUND)

	def test_a_missing_invoice_and_a_forbidden_one_read_the_same(self):
		# Telling a caller which of the two it was would let them probe for invoice names.
		with self.assertRaises(AuthorizationError) as missing:
			require_invoice_access(ALPHA_EDITOR, "get_invoice_serial_status", None)

		with self.assertRaises(AuthorizationError) as forbidden:
			require_invoice_access(ALPHA_EDITOR, "get_invoice_serial_status", BETA)

		self.assertEqual(missing.exception.code, CODE_INVOICE_NOT_FOUND)
		self.assertEqual(forbidden.exception.code, CODE_INVOICE_NOT_FOUND)
		self.assertEqual(forbidden.exception.message, missing.exception.message)

	def test_the_level_is_checked_before_the_invoice(self):
		with self.assertRaises(AuthorizationError) as caught:
			require_invoice_access(VIEWER_ACCOUNT, "set_row_serials", ALPHA)

		self.assertEqual(caught.exception.code, CODE_NOT_PERMITTED)


class TestEveryInvoiceEndpointChecksItsInvoice(UnitTestCase):
	"""A missing invoice and a forbidden one are the same answer, on every endpoint that names one.

	They were not. An endpoint that only checked the invoice it had found answered
	`فاکتور یافت نشد` for a name that does not exist and `به این شرکت دسترسی ندارید` for one that
	does — two answers where the design promises one, and a caller who can tell them apart has a
	way to find out which invoice names are real. The write endpoints were the ones that leaked it,
	because they asked the gateway for the invoice and handed the answer to `core.authz` only when
	it was not `None`.
	"""

	def test_a_name_in_the_list_is_an_endpoint(self):
		"""A name here that named no endpoint would be a rule about nothing."""
		self.assertTrue(INVOICE_ENDPOINTS)

		for endpoint in INVOICE_ENDPOINTS:
			with self.subTest(endpoint=endpoint):
				self.assertIn(endpoint, ENDPOINT_LEVELS)

	def test_a_missing_invoice_is_the_same_answer_everywhere(self):
		for endpoint in INVOICE_ENDPOINTS:
			with self.subTest(endpoint=endpoint):
				with self.assertRaises(AuthorizationError) as caught:
					authorize(
						MANAGER_ACCOUNT,
						endpoint,
						invoice=None,
						session_owner="manager@example.com",
					)

				self.assertEqual(caught.exception.code, CODE_INVOICE_NOT_FOUND)

	def test_an_invoice_in_another_company_is_the_same_answer_everywhere(self):
		"""Not even the code differs, which it used to: `AUTH_COMPANY_NOT_ALLOWED` told a caller
		that the name it tried was real. The company rule still refuses — the invoice is not
		returned — but the refusal is the one a name that matched nothing gets."""
		for endpoint in INVOICE_ENDPOINTS:
			with self.subTest(endpoint=endpoint):
				with self.assertRaises(AuthorizationError) as forbidden:
					authorize(ALPHA_EDITOR, endpoint, invoice=BETA)

				self.assertEqual(forbidden.exception.code, CODE_INVOICE_NOT_FOUND)


class TestCancelledInvoice(UnitTestCase):
	"""A cancelled invoice may be read and emptied, and may not be added to.

	The window has this rule and always had it; the server did not, so the rule was a property of
	the client. Registration through an API call, or from a window that was left open across a
	cancellation, went through. These pin where it is decided now.
	"""

	def writing_endpoints(self):
		return tuple(endpoint for endpoint in ENDPOINT_LEVELS if endpoint in WRITING_ENDPOINTS)

	def test_every_writing_endpoint_is_a_known_endpoint(self):
		"""A name in the list that names no endpoint would be a rule about nothing."""
		self.assertTrue(self.writing_endpoints())

		for endpoint in WRITING_ENDPOINTS:
			with self.subTest(endpoint=endpoint):
				self.assertIn(endpoint, ENDPOINT_LEVELS)

	def test_no_writing_endpoint_accepts_a_cancelled_invoice(self):
		for endpoint in self.writing_endpoints():
			with self.subTest(endpoint=endpoint):
				with self.assertRaises(AuthorizationError) as caught:
					authorize(
						MANAGER_ACCOUNT,
						endpoint,
						invoice=CANCELLED,
						session_owner="manager@example.com",
					)

				self.assertEqual(caught.exception.code, CODE_INVOICE_CANCELLED)
				self.assertEqual(caught.exception.message, REFUSAL_INVOICE_CANCELLED)

	def test_a_draft_and_a_submitted_invoice_are_both_allowed(self):
		"""Draft and submitted are the states this app is for; only cancelled is not."""
		for invoice in (DRAFT, ALPHA):
			for endpoint in self.writing_endpoints():
				with self.subTest(invoice=invoice.name, endpoint=endpoint):
					self.assertTrue(
						authorize(
							MANAGER_ACCOUNT,
							endpoint,
							invoice=invoice,
							session_owner="manager@example.com",
						)
					)

	def test_a_cancelled_invoice_can_still_be_read(self):
		"""The registrations it holds were added when it was live, and somebody has to see them."""
		for endpoint in ("get_invoice_serial_status", "get_serial_assignments"):
			with self.subTest(endpoint=endpoint):
				self.assertTrue(authorize(MANAGER_ACCOUNT, endpoint, invoice=CANCELLED))

	def test_a_cancelled_invoice_is_refused_whatever_the_level(self):
		"""The rule is about the document, not about who is asking: a Manager does not override it."""
		with self.assertRaises(AuthorizationError) as caught:
			authorize(SYSTEM_ACCOUNT, "add_row_serials", invoice=CANCELLED)

		self.assertEqual(caught.exception.code, CODE_INVOICE_CANCELLED)


class TestSessionOwnership(UnitTestCase):
	def test_the_preparer_may_confirm(self):
		self.assertTrue(require_session_owner(EDITOR_ACCOUNT, "editor@example.com"))

	def test_another_user_may_not_confirm(self):
		with self.assertRaises(AuthorizationError) as caught:
			require_session_owner(MANAGER_ACCOUNT, "editor@example.com")

		self.assertEqual(caught.exception.code, CODE_SESSION_NOT_OWNED)


class TestAuthorize(UnitTestCase):
	def test_a_read_endpoint_needs_its_invoice(self):
		self.assertTrue(authorize(EDITOR_ACCOUNT, "get_invoice_serial_status", invoice=ALPHA))

	# Two tests used to sit here proving that `authorize` consulted the session owner for
	# `confirm_import`. That endpoint is gone — nothing prepares a session any more — so the
	# check they were about is not reachable through `authorize` at all. The primitive they
	# were really testing is still here and still tested above: `require_session_owner`.

	def test_a_forbidden_company_stops_the_call(self):
		with self.assertRaises(AuthorizationError):
			authorize(ALPHA_EDITOR, "get_invoice_serial_status", invoice=BETA)

	def test_the_company_rule_itself_still_refuses(self):
		"""The refusal is disguised, not dropped: the caller still cannot reach the invoice."""
		with self.assertRaises(AuthorizationError):
			authorize(ALPHA_EDITOR, "get_invoice_serial_status", invoice=BETA)

		with self.assertRaises(AuthorizationError) as caught:
			require_company(ALPHA_EDITOR, "Beta")

		self.assertEqual(caught.exception.code, CODE_COMPANY_NOT_ALLOWED)


class TestTransferInvoices(UnitTestCase):
	"""Both ends of a transfer, each checked for what it is.

	A transfer is about two invoices and the two are not symmetric: the source is cancelled and is
	only read, the target is live and is written to. The rule that says so is
	`core.transfer.plan_transfer`'s; what this module owns is the question it cannot ask — whether
	this caller may see either document at all.
	"""

	SOURCE = InvoiceRef(name="IN-1", company="Alpha", docstatus=2)
	TARGET = InvoiceRef(name="IN-1-1", company="Alpha", docstatus=1)

	def test_an_editor_reaches_both_ends_of_its_own_transfer(self):
		self.assertTrue(
			require_transfer_invoices(EDITOR_ACCOUNT, "preview_serial_transfer", self.SOURCE, self.TARGET)
		)

	def test_the_cancelled_source_is_not_refused_for_being_cancelled(self):
		"""The one endpoint pair where a cancelled invoice is the point rather than the problem."""
		self.assertTrue(
			require_transfer_invoices(EDITOR_ACCOUNT, "apply_serial_transfer", self.SOURCE, self.TARGET)
		)

	def test_a_viewer_may_not_prepare_a_transfer(self):
		with self.assertRaises(AuthorizationError) as caught:
			require_transfer_invoices(VIEWER_ACCOUNT, "preview_serial_transfer", self.SOURCE, self.TARGET)

		self.assertEqual(caught.exception.code, CODE_NOT_PERMITTED)

	def test_an_unrestricted_caller_is_allowed(self):
		self.assertTrue(
			require_transfer_invoices(MANAGER_ACCOUNT, "apply_serial_transfer", self.SOURCE, self.TARGET)
		)

	def test_a_source_in_another_company_is_refused(self):
		with self.assertRaises(AuthorizationError) as caught:
			require_transfer_invoices(ALPHA_EDITOR, "apply_serial_transfer", BETA, self.TARGET)

		self.assertEqual(caught.exception.code, CODE_INVOICE_NOT_FOUND)
		self.assertEqual(caught.exception.message, INVOICE_REFUSAL)

	def test_a_target_in_another_company_is_refused(self):
		with self.assertRaises(AuthorizationError) as caught:
			require_transfer_invoices(ALPHA_EDITOR, "apply_serial_transfer", self.SOURCE, BETA)

		self.assertEqual(caught.exception.code, CODE_INVOICE_NOT_FOUND)

	def test_each_end_gives_the_same_answer_as_the_other(self):
		"""A caller cannot learn which of the two names is real by comparing the two refusals."""
		refusals = []

		for source, target in ((BETA, self.TARGET), (self.SOURCE, BETA), (None, self.TARGET)):
			with self.assertRaises(AuthorizationError) as caught:
				require_transfer_invoices(ALPHA_EDITOR, "apply_serial_transfer", source, target)

			refusals.append((caught.exception.code, caught.exception.message))

		self.assertEqual(len(set(refusals)), 1)
		self.assertEqual(refusals[0], (CODE_INVOICE_NOT_FOUND, INVOICE_REFUSAL))

	def test_a_missing_source_is_refused_the_same_way(self):
		with self.assertRaises(AuthorizationError) as caught:
			require_transfer_invoices(EDITOR_ACCOUNT, "preview_serial_transfer", None, self.TARGET)

		self.assertEqual(caught.exception.code, CODE_INVOICE_NOT_FOUND)

	def test_a_strict_site_with_no_company_is_refused_as_an_unreachable_invoice(self):
		"""Deliberately the same answer `require_invoice_access` gives, and worth stating why.

		On a strict site with no company assigned, `require_company` raises
		`AUTH_STRICT_COMPANY_UNSET` — a more actionable sentence than "not found" — and the
		invoice-level check swallows it, because every invoice-level refusal is the same refusal.
		That is what `require_invoice_access` does, with a test asserting the two answers read
		alike, and a transfer is not a second behaviour for the same question.

		The actionable sentence is reachable where it belongs: `require_any_company`, which the
		site-wide endpoints (export, restore) call because their question is about the site rather
		than about one document. The cost is real and small — an operator on such a site is told
		they cannot reach the invoice rather than why — and it is not this feature's to change.
		"""
		with self.assertRaises(AuthorizationError) as caught:
			require_transfer_invoices(STRICT_NO_COMPANY, "apply_serial_transfer", self.SOURCE, self.TARGET)

		self.assertEqual(caught.exception.code, CODE_INVOICE_NOT_FOUND)

		with self.assertRaises(AuthorizationError) as direct:
			require_company(STRICT_NO_COMPANY, "Alpha")

		self.assertEqual(direct.exception.code, CODE_STRICT_COMPANY_UNSET)


class TestTransferEndpointsAreListed(UnitTestCase):
	"""The two names, and the two things that are deliberately not true of them."""

	def test_both_transfer_endpoints_have_a_level(self):
		for endpoint in TRANSFER_ENDPOINTS:
			with self.subTest(endpoint=endpoint):
				self.assertIn(endpoint, ENDPOINT_LEVELS)

	def test_a_transfer_is_not_a_writing_endpoint(self):
		"""`WRITING_ENDPOINTS` means "writes to the invoice it names", and it is what refuses a
		cancelled invoice. A transfer names a cancelled one on purpose, and writes to the other.

		The rule that keeps a transfer off a cancelled invoice is
		`core.transfer.plan_transfer`'s refusal of a cancelled target, which has its own test.
		"""
		for endpoint in TRANSFER_ENDPOINTS:
			with self.subTest(endpoint=endpoint):
				self.assertNotIn(endpoint, WRITING_ENDPOINTS)

	def test_a_transfer_is_not_a_one_invoice_endpoint(self):
		"""It names two, and `INVOICE_ENDPOINTS` is the list checked against exactly one."""
		for endpoint in TRANSFER_ENDPOINTS:
			with self.subTest(endpoint=endpoint):
				self.assertNotIn(endpoint, INVOICE_ENDPOINTS)

	def test_the_source_may_be_cancelled_while_the_rule_that_refuses_one_still_stands(self):
		"""The cancelled-invoice refusal is about writing, and a transfer writes to the target."""
		transfer_endpoint = "apply_serial_transfer"

		self.assertNotIn(transfer_endpoint, WRITING_ENDPOINTS)

		with self.assertRaises(AuthorizationError) as caught:
			authorize(EDITOR_ACCOUNT, "set_row_serials", invoice=CANCELLED)

		self.assertEqual(caught.exception.code, CODE_INVOICE_CANCELLED)


class TestTheArchivistRole(UnitTestCase):
	"""One endpoint, one named role, and the reason a level is not enough for it.

	`restore_cancelled_serial_history` may add a registration to an invoice whose accounting has
	been reversed — the one act this app refuses everywhere else (I22). Being able to administer the
	app does not imply being trusted with it, and a site may have several Managers and mean one of
	them by this, so the endpoint asks for the role as well as the level.
	"""

	def archivist(self, **overrides):
		values = {
			"user": "archivist@example.test",
			"roles": frozenset({ROLE_MANAGER, ROLE_ARCHIVIST}),
		}
		values.update(overrides)

		return Principal(**values)

	def test_a_manager_with_the_role_is_allowed(self):
		self.assertTrue(require_endpoint(self.archivist(), "restore_cancelled_serial_history"))

	def test_a_manager_without_the_role_is_refused(self):
		with self.assertRaises(AuthorizationError) as caught:
			require_endpoint(MANAGER_ACCOUNT, "restore_cancelled_serial_history")

		self.assertEqual(caught.exception.code, CODE_NOT_PERMITTED)
		self.assertIn(ROLE_ARCHIVIST, caught.exception.message)

	def test_the_refusal_names_the_role_rather_than_only_the_level(self):
		"""`not permitted` alone tells an operator nothing they can act on."""
		with self.assertRaises(AuthorizationError) as caught:
			require_endpoint(MANAGER_ACCOUNT, "restore_cancelled_serial_history")

		self.assertNotIn("سطح دسترسی", caught.exception.message)

	def test_a_system_manager_satisfies_it_as_it_satisfies_everything(self):
		"""The app's convention rather than a decision for this endpoint: refusing the
		Administrator would leave a site that lost track of its roles with no way to recover."""
		self.assertTrue(require_endpoint(SYSTEM_ACCOUNT, "restore_cancelled_serial_history"))

	def test_holding_the_role_without_the_level_is_not_enough(self):
		with self.assertRaises(AuthorizationError) as caught:
			require_endpoint(
				Principal(user="editor@example.test", roles=frozenset({ROLE_EDITOR, ROLE_ARCHIVIST})),
				"restore_cancelled_serial_history",
			)

		self.assertEqual(caught.exception.code, CODE_NOT_PERMITTED)
		self.assertIn("سطح دسترسی", caught.exception.message)

	def test_the_shortest_possible_set_of_endpoints_asks_for_a_role(self):
		"""One act and its preview — the pair is the door, and the pair is what is named here.

		A third name appearing in this set is a new capability somebody granted, and it has to be a
		deliberate change rather than a line added to make an endpoint pass.
		"""
		self.assertEqual(
			set(ENDPOINT_ROLES),
			{
				"preview_cancelled_serial_history",
				"preview_restore_audit_records",
				"restore_audit_records",
				"restore_cancelled_serial_history",
			},
		)
		self.assertEqual(len(ENDPOINT_ROLES), 4, "two acts, and each one's preview")

	def test_the_history_preview_is_gated_with_the_apply(self):
		"""It reports which rows would be placed on a cancelled invoice — the act, seen first."""
		self.assertIn("preview_cancelled_serial_history", ENDPOINT_ROLES)

	def test_the_ordinary_restore_asks_for_no_role(self):
		"""A Manager who may restore the site's records is not thereby trusted with history, and a
		Manager who is trusted with history does not thereby gain the ordinary restore's power: the
		two are separate, which is the point of naming the role."""
		self.assertNotIn("apply_serial_restore", ENDPOINT_ROLES)
		self.assertNotIn("preview_serial_restore", ENDPOINT_ROLES)

	def test_the_endpoint_exists_and_needs_a_manager(self):
		self.assertEqual(ENDPOINT_LEVELS["restore_cancelled_serial_history"], MANAGER)
