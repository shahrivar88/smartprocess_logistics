"""Authorization decisions, made without a database."""

from smartprocess_logistics.core.authz import (
	CODE_COMPANY_NOT_ALLOWED,
	CODE_INVOICE_NOT_FOUND,
	CODE_NOT_AUTHENTICATED,
	CODE_NOT_PERMITTED,
	CODE_SESSION_NOT_OWNED,
	CODE_STRICT_COMPANY_UNSET,
	CODE_UNKNOWN_ENDPOINT,
	EDITOR,
	ENDPOINT_LEVELS,
	MANAGER,
	ROLE_EDITOR,
	ROLE_MANAGER,
	ROLE_SYSTEM_MANAGER,
	ROLE_VIEWER,
	VIEWER,
	AuthorizationError,
	InvoiceRef,
	Principal,
	authorize,
	require_company,
	require_endpoint,
	require_invoice_access,
	require_session_owner,
	visible_companies,
)
from smartprocess_logistics.tests.base import UnitTestCase

ALPHA = InvoiceRef(name="IN-1", company="Alpha")
BETA = InvoiceRef(name="IN-2", company="Beta")

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


class TestInvoiceAccess(UnitTestCase):
	def test_a_readable_invoice_in_the_right_company_is_allowed(self):
		self.assertIs(require_invoice_access(EDITOR_ACCOUNT, "set_row_serials", ALPHA), ALPHA)

	def test_a_missing_invoice_is_refused(self):
		with self.assertRaises(AuthorizationError) as caught:
			require_invoice_access(EDITOR_ACCOUNT, "set_row_serials", None)

		self.assertEqual(caught.exception.code, CODE_INVOICE_NOT_FOUND)

	def test_an_invoice_in_another_company_is_refused(self):
		with self.assertRaises(AuthorizationError) as caught:
			require_invoice_access(ALPHA_EDITOR, "get_invoice_serial_status", BETA)

		self.assertEqual(caught.exception.code, CODE_COMPANY_NOT_ALLOWED)

	def test_a_missing_invoice_and_a_forbidden_one_read_the_same(self):
		# Telling a caller which of the two it was would let them probe for invoice names.
		with self.assertRaises(AuthorizationError) as missing:
			require_invoice_access(ALPHA_EDITOR, "get_invoice_serial_status", None)

		with self.assertRaises(AuthorizationError) as forbidden:
			require_invoice_access(ALPHA_EDITOR, "get_invoice_serial_status", BETA)

		self.assertEqual(missing.exception.code, CODE_INVOICE_NOT_FOUND)
		self.assertEqual(forbidden.exception.code, CODE_COMPANY_NOT_ALLOWED)

	def test_the_level_is_checked_before_the_invoice(self):
		with self.assertRaises(AuthorizationError) as caught:
			require_invoice_access(VIEWER_ACCOUNT, "set_row_serials", ALPHA)

		self.assertEqual(caught.exception.code, CODE_NOT_PERMITTED)


class TestSessionOwnership(UnitTestCase):
	def test_the_preparer_may_confirm(self):
		self.assertTrue(require_session_owner(EDITOR_ACCOUNT, "editor@example.com"))

	def test_another_user_may_not_confirm(self):
		with self.assertRaises(AuthorizationError) as caught:
			require_session_owner(MANAGER_ACCOUNT, "editor@example.com")

		self.assertEqual(caught.exception.code, CODE_SESSION_NOT_OWNED)


class TestAuthorize(UnitTestCase):
	def test_a_read_endpoint_needs_its_invoice(self):
		self.assertTrue(
			authorize(EDITOR_ACCOUNT, "get_invoice_serial_status", invoice=ALPHA)
		)

	def test_confirm_checks_the_session_owner(self):
		self.assertTrue(
			authorize(
				EDITOR_ACCOUNT,
				"confirm_import",
				invoice=ALPHA,
				session_owner="editor@example.com",
			)
		)

	def test_confirm_refuses_another_users_session(self):
		with self.assertRaises(AuthorizationError):
			authorize(
				MANAGER_ACCOUNT,
				"confirm_import",
				invoice=ALPHA,
				session_owner="editor@example.com",
			)

	def test_a_forbidden_company_stops_the_call(self):
		with self.assertRaises(AuthorizationError):
			authorize(ALPHA_EDITOR, "get_invoice_serial_status", invoice=BETA)
