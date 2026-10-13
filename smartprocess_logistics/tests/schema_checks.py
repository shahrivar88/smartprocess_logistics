"""Static validators for this app's DocType JSON files.

Pure functions over files on disk: no Frappe import, no database, no site. They are run from
`tests/test_doctype_schemas.py` under Frappe, and they can also be run directly, which is how
they are checked before the app is installed anywhere:

	python -c "from smartprocess_logistics.tests.schema_checks import report; print(report())"

The checks mirror what Frappe itself enforces when a DocType is saved, so a problem found
here is a problem that would otherwise appear as a failed install.
"""

import ast
import json
import pathlib

from smartprocess_logistics.core.normalization import NORMALIZATION_VERSION

APP_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_ROOT = APP_ROOT / "smartprocess_logistics" / "smartprocess_logistics"

LAYOUT_FIELDTYPES = ("Tab Break", "Section Break", "Column Break")
INDEXABLE_FIELDTYPES = ("Data", "Date", "Datetime", "Dynamic Link", "Int", "Link", "Select")
LINK_FIELDTYPES = ("Link", "Dynamic Link")
OPTION_FIELDTYPES = ("Link", "Dynamic Link", "Table", "Select")

DOCPERM_FLAGS = (
	"amend",
	"cancel",
	"create",
	"delete",
	"email",
	"export",
	"if_owner",
	"import",
	"permlevel",
	"print",
	"read",
	"report",
	"role",
	"select",
	"share",
	"submit",
	"write",
)

APP_ROLES = ("Logistics Serial Viewer", "Logistics Serial Editor", "Logistics Serial Manager")
OTHER_ROLES = ("System Manager",)

REQUIRED_DOCTYPES = (
	"Logistics Settings",
	"Logistics Serial",
	"Logistics Serial Assignment",
	"Logistics Import Delimiter",
	"Logistics Item Marker",
	"Logistics Import Session",
)

# autoname forms that are their own naming rule and must not be labelled an expression.
NON_EXPRESSION_AUTONAMES = ("hash", "prompt")


def module_name() -> str:
	"""The module label declared in modules.txt."""
	return (APP_ROOT / "smartprocess_logistics" / "modules.txt").read_text(encoding="utf-8").strip()


def doctype_paths() -> list[pathlib.Path]:
	"""Every DocType JSON in this app, in a stable order."""
	return sorted(MODULE_ROOT.glob("doctype/*/*.json"))


def _folder_slug(name: str) -> str:
	return name.lower().replace(" ", "_")


def _check_identity(path: pathlib.Path, problems: list[str]) -> dict | None:
	try:
		doc = json.loads(path.read_text(encoding="utf-8"))
	except json.JSONDecodeError as exc:
		problems.append(f"{path.name}: invalid JSON: {exc}")
		return None

	if doc.get("doctype") != "DocType":
		problems.append(f"{path.name}: 'doctype' must be 'DocType'")

	name = doc.get("name")
	if not name:
		problems.append(f"{path.name}: missing 'name'")
	elif _folder_slug(name) != path.parent.name or path.stem != path.parent.name:
		problems.append(f"{path.name}: file and folder must be {_folder_slug(name)!r} for {name!r}")

	for key in ("engine", "fields", "field_order", "module", "permissions", "row_format"):
		if key not in doc:
			problems.append(f"{path.name}: missing top-level key {key!r}")

	return doc


def _check_fields(path: pathlib.Path, doc: dict, child_doctypes: set[str], problems: list[str]) -> None:
	fields = doc.get("fields") or []
	fieldnames = [field.get("fieldname") for field in fields]

	if None in fieldnames:
		problems.append(f"{path.name}: a field has no fieldname")
	if len(fieldnames) != len(set(fieldnames)):
		problems.append(f"{path.name}: duplicate fieldnames")

	order = doc.get("field_order") or []
	for fieldname in fieldnames:
		if fieldname not in order:
			problems.append(f"{path.name}: field {fieldname!r} is missing from field_order")
	for fieldname in order:
		if fieldname not in fieldnames:
			problems.append(f"{path.name}: field_order lists {fieldname!r}, which is not a field")

	by_name = {field.get("fieldname"): field for field in fields}

	for field in fields:
		fieldname, fieldtype = field.get("fieldname"), field.get("fieldtype")

		if fieldtype in LAYOUT_FIELDTYPES:
			for unset in ("fetch_from", "options", "reqd", "unique"):
				if field.get(unset):
					problems.append(f"{path.name}: layout field {fieldname!r} must not set {unset!r}")
			continue

		if fieldtype in OPTION_FIELDTYPES and not field.get("options"):
			problems.append(f"{path.name}: {fieldtype} field {fieldname!r} needs 'options'")

		if fieldtype == "Table" and field.get("options") not in child_doctypes:
			problems.append(
				f"{path.name}: Table {fieldname!r} points at {field.get('options')!r},"
				" which is not a child DocType of this app"
			)

		if (field.get("search_index") or field.get("unique")) and fieldtype not in INDEXABLE_FIELDTYPES:
			problems.append(f"{path.name}: {fieldname!r} is indexed, but {fieldtype!r} cannot be indexed")

		fetch_from = field.get("fetch_from")
		if fetch_from:
			source, _, target = fetch_from.partition(".")
			link_field = by_name.get(source)
			if not link_field or link_field.get("fieldtype") not in LINK_FIELDTYPES:
				problems.append(
					f"{path.name}: fetch_from on {fieldname!r} reads {source!r},"
					" which is not a link field here"
				)
			elif not target:
				problems.append(f"{path.name}: fetch_from on {fieldname!r} names no target field")

	for fieldname in ("title_field",):
		target = doc.get(fieldname)
		if target and target not in by_name:
			problems.append(f"{path.name}: {fieldname} {target!r} is not a field here")

	for entry in (doc.get("search_fields") or "").split(","):
		entry = entry.strip()
		if entry and entry not in by_name:
			problems.append(f"{path.name}: search_fields lists {entry!r}, which is not a field here")


def _check_naming(path: pathlib.Path, doc: dict, problems: list[str]) -> None:
	autoname = doc.get("autoname") or ""

	if doc.get("issingle") or doc.get("istable"):
		if autoname:
			problems.append(f"{path.name}: a Single or child DocType must not set 'autoname'")
		return

	if not autoname:
		problems.append(
			f"{path.name}: a standalone DocType needs 'autoname', otherwise DocType validation"
			" demands a controller module on disk"
		)
		return

	if autoname in NON_EXPRESSION_AUTONAMES or autoname.startswith(
		("eval:", "field:", "format:", "naming_series:")
	):
		return

	# Any other form is a legacy series expression such as LS-.YYYY.-.#####.
	if doc.get("naming_rule") != "Expression":
		problems.append(
			f"{path.name}: autoname {autoname!r} is a legacy expression, so naming_rule must be"
			f" 'Expression', not {doc.get('naming_rule')!r}. doctype.py rewrites"
			" 'Expression (old style)' to 'Expression' for this form."
		)


def _check_permissions(path: pathlib.Path, doc: dict, problems: list[str]) -> None:
	permissions = doc.get("permissions")
	if permissions is None:
		problems.append(f"{path.name}: 'permissions' must be a list, even when empty")
		return

	if doc.get("istable") and permissions:
		problems.append(f"{path.name}: a child DocType must not carry DocPerm rows")

	for row in permissions:
		role = row.get("role")
		if not role:
			problems.append(f"{path.name}: a DocPerm row has no role")
		elif role not in APP_ROLES + OTHER_ROLES:
			problems.append(f"{path.name}: DocPerm grants an unexpected role {role!r}")

		for flag in row:
			if flag not in DOCPERM_FLAGS:
				problems.append(f"{path.name}: DocPerm for {role!r} sets unknown flag {flag!r}")

		if row.get("if_owner"):
			problems.append(
				f"{path.name}: if_owner is not used by this app; access is by role level,"
				" so an Editor may correct a colleague's registration"
			)

		for flag, value in row.items():
			if flag not in ("permlevel", "role", "if_owner") and value not in (0, 1):
				problems.append(f"{path.name}: DocPerm flag {flag!r} for {role!r} must be 0 or 1")


def expected_controller_class(doctype_name: str) -> str:
	"""The class name Frappe resolves a controller by.

	`frappe/model/base_document.py::get_controller` does
	`classname = doctype.replace(" ", "").replace("-", "")` and then `getattr(module, classname)`.
	The two halves have to agree, and only one of them is ours.
	"""
	return doctype_name.replace(" ", "").replace("-", "")


def _check_controller(path: pathlib.Path, doc: dict, problems: list[str]) -> None:
	"""A DocType needs an importable controller module next to its JSON, with the right class.

	This is the check whose absence let a broken app reach an installation attempt: the JSON was
	valid, so every other check here passed, and `sync_for` then died on the first DocType it
	tried to import because `load_doctype_module` could not import the module. `doctype.py` calls
	`run_module_method("on_doctype_update")` on every DocType it syncs, and that loads the module
	whether or not the file defines anything.
	"""
	controller = path.with_suffix(".py")

	if not controller.exists():
		problems.append(
			f"{path.name}: no controller module {controller.name} beside it. Frappe imports the"
			" module by path while syncing, so the install fails on this DocType even though the"
			" JSON is valid"
		)
		return

	classname = expected_controller_class(doc.get("name") or "")
	tree = ast.parse(controller.read_text(encoding="utf-8"))
	declared = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}

	if classname not in declared:
		problems.append(
			f"{path.name}: {controller.name} declares no class {classname!r}, which is the name"
			f" Frappe resolves the controller by; it declares {sorted(declared) or 'nothing'}"
		)


def _check_set_only_once(path: pathlib.Path, doc: dict, problems: list[str]) -> None:
	"""No field in this app may be `set_only_once`.

	Frappe honours the flag only when a document is **inserted**: `Document.validate_set_only_once`
	returns immediately unless `_doc_before_save` exists. `installer.init_singles` inserts the
	`Logistics Settings` row before `after_install` runs, so by the time this app writes
	`installed_on` the document already exists, the write counts as a change, and the framework
	refuses it with `CannotChangeConstantError`. That is what stopped the installation three times.

	No field here holds a value chosen at creation time, which is the only kind of value the flag
	is for, so using it is always a defect in this app rather than a parameter to tune.
	"""
	for field in doc.get("fields") or []:
		if field.get("set_only_once"):
			problems.append(
				f"{path.name}: field {field.get('fieldname')!r} sets set_only_once. Frappe honours"
				" that only at insert time, and this app writes its values after the document"
				" exists, so the write is refused"
			)


def _check_hidden_and_mandatory(path: pathlib.Path, doc: dict, problems: list[str]) -> None:
	"""A hidden field that is required and has no default is refused by Frappe.

	`doctype.py::check_hidden_and_mandatory` throws `HiddenAndMandatoryWithoutDefaultError` for
	that combination, because a field nobody can see is a field nobody can fill. The rule is not
	in the install path — it runs from the customization paths (`Custom Field`, `Customize Form`,
	`Property Setter` and `sync_customizations_for_doctype`) — so a DocType that breaks it installs
	cleanly and then fails the first time somebody saves it from the Desk, on a site whose
	`developer_mode` makes that possible. Checking it here keeps that failure out of the app.

	The three fields that used to break it are the two server-derived `normalized_serial` copies,
	which are now read-only and required so a serial cannot be stored without an identity, and
	`Logistics Import Session.token`, which stays hidden because it is a live capability and whose
	non-emptiness is guaranteed by the `secrets.token_urlsafe` call that generates it.
	"""
	for field in doc.get("fields") or []:
		if field.get("hidden") and field.get("reqd") and not field.get("default"):
			problems.append(
				f"{path.name}: field {field.get('fieldname')!r} is hidden and required with no"
				" default, which Frappe refuses; either show it as read-only or drop 'reqd' and"
				" enforce the value where it is produced"
			)


def _check_normalization_version(path: pathlib.Path, doc: dict, problems: list[str]) -> None:
	"""`Logistics Settings.normalization_version` must default to the rule set the code applies.

	The value is a statement about the **code**, so a definition that ships a different number is
	a definition that lies to whoever reads the settings screen: this app shipped `1` while
	`core.normalization.NORMALIZATION_VERSION` was `2`, and nothing said so. `setup.seeds`
	repairs a site whose stored value has fallen behind, but the shipped default is what a fresh
	install starts from, and the two have to agree for that repair to be a repair rather than the
	first thing that happens.

	This is the check that makes bumping the rule set a two-line change instead of a silent
	drift: raise the constant, and this test names the file that still says otherwise.
	"""
	if doc.get("name") != "Logistics Settings":
		return

	for field in doc.get("fields") or []:
		if field.get("fieldname") != "normalization_version":
			continue

		if str(field.get("default")) != str(NORMALIZATION_VERSION):
			problems.append(
				f"{path.name}: normalization_version defaults to {field.get('default')!r} while"
				f" core.normalization.NORMALIZATION_VERSION is {NORMALIZATION_VERSION}; the shipped"
				" default and the rules the app applies must be the same number"
			)


def check_all() -> list[str]:
	"""Return every problem found in this app's DocType definitions."""
	problems: list[str] = []
	expected_module = module_name()

	parsed: list[tuple[pathlib.Path, dict]] = []
	for path in doctype_paths():
		doc = _check_identity(path, problems)
		if doc:
			parsed.append((path, doc))

	names = {doc.get("name") for _, doc in parsed}
	for required in REQUIRED_DOCTYPES:
		if required not in names:
			problems.append(f"missing DocType: {required}")

	child_doctypes = {doc.get("name") for _, doc in parsed if doc.get("istable")}

	for path, doc in parsed:
		if doc.get("module") != expected_module:
			problems.append(
				f"{path.name}: module {doc.get('module')!r} does not match modules.txt"
				f" ({expected_module!r})"
			)
		_check_fields(path, doc, child_doctypes, problems)
		_check_naming(path, doc, problems)
		_check_permissions(path, doc, problems)
		_check_controller(path, doc, problems)
		_check_set_only_once(path, doc, problems)
		_check_hidden_and_mandatory(path, doc, problems)
		_check_normalization_version(path, doc, problems)

	return problems


def report() -> str:
	"""A single printable summary, for running these checks without a site."""
	problems = check_all()
	lines = [f"{len(doctype_paths())} DocType files checked"]
	if problems:
		lines += [f"  PROBLEM {problem}" for problem in problems]
	else:
		lines.append("  no problems found")
	return "\n".join(lines)


if __name__ == "__main__":
	print(report())
