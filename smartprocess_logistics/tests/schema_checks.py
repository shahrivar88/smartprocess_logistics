"""Static validators for this app's DocType JSON files.

Pure functions over files on disk: no Frappe import, no database, no site. They are run from
`tests/test_doctype_schemas.py` under Frappe, and they can also be run directly, which is how
they are checked before the app is installed anywhere:

	python -c "from smartprocess_logistics.tests.schema_checks import report; print(report())"

The checks mirror what Frappe itself enforces when a DocType is saved, so a problem found
here is a problem that would otherwise appear as a failed install.
"""

import json
import pathlib

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
