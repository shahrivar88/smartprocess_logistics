"""AST regression tests for the shared assignment validator in `serial_link.py`.

The fixes D-01, D-04 and S-01..S-04 all live in one place — `validate_assignment` and its helpers —
and none of them runs without a live site, which these tests never touch. So the tests parse the
module's AST and assert the shape a decision must keep: authorization inlined, the cancelled check
present, and every decision read taken under `for_update=True` **after** the invoice lock. A test
that only compiled the module would pass a validator that had silently dropped a lock, so the
assertions are about structure, not about success.
"""

import ast
import pathlib

from smartprocess_logistics.tests.base import UnitTestCase

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
SERIAL_LINK = APP_ROOT / "serial_link.py"
GATEWAY = APP_ROOT / "api" / "gateway.py"
ENDPOINTS = APP_ROOT / "api" / "endpoints.py"


def _source(path):
	return path.read_text(encoding="utf-8")


def _segment(source, name):
	"""The source text of the top-level function `name`, located through the AST."""
	tree = ast.parse(source)

	for node in tree.body:
		if isinstance(node, ast.FunctionDef) and node.name == name:
			return ast.get_source_segment(source, node)

	raise AssertionError(f"no function {name!r}")


class TestAuthorizationInline(UnitTestCase):
	def test_authorize_invoice_is_defined(self):
		self.assertIn("def _authorize_invoice", _source(SERIAL_LINK))

	def test_authorize_invoice_inlines_the_principal_and_company_and_read_checks(self):
		body = _segment(_source(SERIAL_LINK), "_authorize_invoice")

		self.assertIn("gateway.principal_for(frappe.session.user)", body)
		self.assertIn('authz.require_company(principal, invoice.get("company"))', body)
		self.assertIn(
			'frappe.has_permission(INVOICE_DOCTYPE, doc=invoice.get("name"), ptype="read")', body
		)

	def test_authorize_invoice_maps_every_failure_to_the_same_refusal(self):
		body = _segment(_source(SERIAL_LINK), "_authorize_invoice")

		self.assertIn("except authz.AuthorizationError:", body)
		self.assertGreaterEqual(body.count("authz.INVOICE_REFUSAL"), 3)

	def test_authorize_invoice_refuses_a_missing_invoice_like_an_unreachable_one(self):
		body = _segment(_source(SERIAL_LINK), "_authorize_invoice")

		self.assertIn("if not invoice:", body)
		self.assertIn("_refuse(authz.INVOICE_REFUSAL)", body)


class TestCancelledInvoice(UnitTestCase):
	def test_a_cancelled_invoice_is_refused_for_a_new_row(self):
		body = _segment(_source(SERIAL_LINK), "_refuse_cancelled")

		self.assertIn("authz.DOCSTATUS_CANCELLED", body)
		self.assertIn("authz.REFUSAL_INVOICE_CANCELLED", body)

	def test_the_cancelled_check_is_skipped_on_restore(self):
		body = _segment(_source(SERIAL_LINK), "_refuse_cancelled")

		self.assertLess(body.index("_is_restore()"), body.index("DOCSTATUS_CANCELLED"))

	def test_only_a_new_row_is_refused(self):
		body = _segment(_source(SERIAL_LINK), "validate_assignment")

		self.assertIn("if doc.is_new():", body)


class TestLockOrder(UnitTestCase):
	def test_the_invoice_is_read_before_the_serial_and_the_row(self):
		body = _segment(_source(SERIAL_LINK), "validate_assignment")

		self.assertLess(body.index("_invoice(doc)"), body.index("_serial(doc)"))
		self.assertLess(body.index("_serial(doc)"), body.index("_invoice_row(doc, invoice)"))

	def test_authorization_runs_after_the_invoice_lock_and_before_derivation(self):
		body = _segment(_source(SERIAL_LINK), "validate_assignment")

		self.assertLess(body.index("_invoice(doc)"), body.index("_authorize_invoice(invoice)"))
		self.assertLess(body.index("_authorize_invoice(invoice)"), body.index("_derive_delivery"))

	def test_authorization_is_skipped_on_restore(self):
		body = _segment(_source(SERIAL_LINK), "validate_assignment")

		self.assertIn("if not _is_restore():", body)


class TestCurrentReads(UnitTestCase):
	def test_the_invoice_serial_and_row_are_locked(self):
		for name in ("_invoice", "_serial", "_invoice_row"):
			with self.subTest(helper=name):
				self.assertIn("for_update=True", _segment(_source(SERIAL_LINK), name))

	def test_the_stored_assignment_is_read_under_the_lock(self):
		body = _segment(_source(SERIAL_LINK), "validate_assignment")

		self.assertIn("for_update=True", body)

	def test_the_excess_count_is_a_locking_read_not_a_plain_count(self):
		body = _segment(_source(SERIAL_LINK), "_refuse_excess")

		self.assertIn("frappe.db.get_values", body)
		self.assertIn("for_update=True", body)
		self.assertNotIn("frappe.db.count", body)

	def test_the_delivery_conflict_read_is_a_locking_read(self):
		body = _segment(_source(SERIAL_LINK), "_derive_delivery")

		self.assertIn("gateway.other_delivery_links((identity,), for_update=True)", body)


class TestOtherDeliveryLinksLock(UnitTestCase):
	def test_gateway_accepts_a_for_update_flag(self):
		body = _segment(_source(GATEWAY), "other_delivery_links")

		self.assertIn("def other_delivery_links(identities, for_update=False):", _source(GATEWAY))
		self.assertIn("frappe.db.get_values", body)
		self.assertIn("for_update=True", body)

	def test_the_plain_read_is_still_the_default(self):
		body = _segment(_source(GATEWAY), "other_delivery_links")

		self.assertIn("frappe.get_all", body)
		self.assertIn("limit_page_length=0", body)


class TestSerialValidator(UnitTestCase):
	def test_validate_serial_is_defined(self):
		self.assertIn("def validate_serial", _source(SERIAL_LINK))

	def test_the_identity_is_derived_from_the_serial_text(self):
		body = _segment(_source(SERIAL_LINK), "validate_serial")

		self.assertIn('derive_serial_identity(doc.get("serial_no"))', body)

	def test_the_derivation_is_skipped_on_restore(self):
		body = _segment(_source(SERIAL_LINK), "validate_serial")

		self.assertIn("_is_restore()", body)
		self.assertLess(body.index("_is_restore()"), body.index("derive_serial_identity"))

	def test_a_change_to_the_identity_fields_is_refused(self):
		body = _segment(_source(SERIAL_LINK), "validate_serial")

		self.assertIn("changed_identity_fields(stored, doc)", body)
		self.assertIn("_refuse(", body)

	def test_the_stored_identity_is_reasserted_on_update(self):
		body = _segment(_source(SERIAL_LINK), "validate_serial")

		self.assertIn('doc.normalized_serial = stored.get("normalized_serial")', body)
		self.assertIn('doc.normalization_version = stored.get("normalization_version")', body)

	def test_a_bad_serial_text_is_turned_into_a_refusal(self):
		body = _segment(_source(SERIAL_LINK), "validate_serial")

		self.assertIn("except normalization.SerialTextError as error:", body)
		self.assertIn("_refuse(error.message)", body)


class TestSerialHookRegistration(UnitTestCase):
	HOOKS = APP_ROOT / "hooks.py"
	COMPOSITION = APP_ROOT / "hooks_composition.py"

	def test_the_has_permission_hook_names_the_serial(self):
		self.assertIn(
			'"Logistics Serial": "smartprocess_logistics.permissions.serial_has_permission"',
			_source(self.HOOKS),
		)

	def test_the_serial_doc_event_is_registered(self):
		composition = _source(self.COMPOSITION)

		self.assertIn('"Logistics Serial": {', composition)
		self.assertIn('"validate": "smartprocess_logistics.serial_link.validate_serial"', composition)


class TestValidatorSafety(UnitTestCase):
	def test_the_validator_claims_no_privilege_and_commits_nothing(self):
		source = _source(SERIAL_LINK)

		self.assertNotIn("ignore_permissions=", source)
		self.assertNotIn("ignore_permissions =", source)
		self.assertNotIn("frappe.db.commit", source)
		self.assertNotIn("frappe.db.sql", source)

	def test_both_modules_still_parse(self):
		ast.parse(_source(SERIAL_LINK))
		ast.parse(_source(GATEWAY))
		ast.parse(_source(ENDPOINTS))


class TestTransferValidator(UnitTestCase):
	"""The evidential transfer document's validator (D-05) keeps the shape the record rules need.

	`validate_transfer` is the one layer every write to `Logistics Serial Transfer` passes through, so
	a test that only compiled the module would pass a validator that had silently dropped the pure
	checks or the child-master comparison. These assertions are about structure, not success.
	"""

	def test_validate_transfer_is_defined(self):
		self.assertIn("def validate_transfer", _source(SERIAL_LINK))

	def test_the_pure_record_checks_run_first(self):
		body = _segment(_source(SERIAL_LINK), "validate_transfer")

		self.assertIn("transfer_rules.record_issues(_transfer_record(doc))", body)

	def test_the_child_master_checks_are_skipped_on_restore(self):
		body = _segment(_source(SERIAL_LINK), "validate_transfer")

		self.assertIn("if not _is_restore():", body)
		self.assertIn("_transfer_child_issues(doc)", body)
		self.assertLess(body.index("if not _is_restore():"), body.index("_transfer_child_issues(doc)"))

	def test_the_validator_refuses_on_the_first_issue(self):
		body = _segment(_source(SERIAL_LINK), "validate_transfer")

		self.assertIn("_refuse(issues[0].message)", body)

	def test_the_child_master_comparison_is_a_locking_read(self):
		body = _segment(_source(SERIAL_LINK), "_transfer_child_issues")

		self.assertIn("for_update=True", body)

	def test_the_record_carries_the_restore_marker(self):
		body = _segment(_source(SERIAL_LINK), "_transfer_record")

		self.assertIn('"restored_from": doc.get("restored_from")', body)
		self.assertIn('"restored_on": doc.get("restored_on")', body)
		self.assertIn('"restored_by": doc.get("restored_by")', body)


class TestTransferAuthorization(UnitTestCase):
	"""D-06: both invoices are authorized before their details are read, and the apply revalidates
	under the target's own lock.

	The `_transfer_request` helper is the single funnel every transfer read passes through, so its
	shape is the decision: the company rule runs first, then each document's own read permission, and
	only then are the site's rows and settings read. `apply_serial_transfer` calls that funnel twice —
	once to confirm the token, and again under `lock_invoice` — so a plan is never built against a
	site that has moved on.
	"""

	def test_the_transfer_request_authorizes_both_invoices_before_their_details(self):
		body = _segment(_source(ENDPOINTS), "_transfer_request")

		self.assertIn("gateway.assert_can_read_invoice(source.name)", body)
		self.assertIn("gateway.assert_can_read_invoice(target.name)", body)
		self.assertLess(body.index("authz.require_transfer_invoices"), body.index("assert_can_read_invoice(source.name)"))
		self.assertLess(body.index("assert_can_read_invoice(target.name)"), body.index("gateway.settings()"))

	def test_each_read_permission_maps_to_the_same_refusal(self):
		body = _segment(_source(ENDPOINTS), "_transfer_request")

		self.assertIn("except frappe.PermissionError:", body)
		self.assertIn("authz.CODE_INVOICE_NOT_FOUND", body)
		self.assertIn("authz.INVOICE_REFUSAL", body)

	def test_apply_runs_the_transfer_request_twice(self):
		body = _segment(_source(ENDPOINTS), "apply_serial_transfer")

		self.assertGreaterEqual(body.count("_transfer_request("), 2)

	def test_the_second_transfer_request_is_under_the_invoice_lock(self):
		body = _segment(_source(ENDPOINTS), "apply_serial_transfer")

		self.assertLess(body.index("gateway.open_transaction()"), body.index("gateway.lock_invoice("))
		self.assertLess(body.index("gateway.lock_invoice("), body.rindex("_transfer_request("))

	def test_the_token_is_confirmed_after_the_first_authorization(self):
		body = _segment(_source(ENDPOINTS), "apply_serial_transfer")

		self.assertLess(body.index("_transfer_request("), body.index("transfer.preview_token(request)"))
