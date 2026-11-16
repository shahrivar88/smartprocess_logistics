"""What this app needs from the site, and what it puts on it.

The two lists are separate because they are different claims, and conflating them is how a
registry starts lying:

* `DEPENDENCIES` says which apps this app needs. An app listed here has not been modified;
  `smartprocess_erpnext_accounting` is listed as an optional compatibility statement only.
* `APPLIED_CHANGES` says what installing this app puts on the site, and each entry names the
  mechanism that applies it, so a reader can check the claim in the source and remove it.

Nothing here decides, writes, or reads a site. The lists are static version-controlled
metadata; `rows()` only joins them with the facts that `smartprocess_logistics/interop.py`
reads, and `tests/test_interop.py` fails when a list stops matching the hooks, the fixtures,
the roles or the Custom Field files that actually ship.
"""

from dataclasses import dataclass, field

from smartprocess_logistics.core.authz import (
	ROLE_ARCHIVIST,
	ROLE_EDITOR,
	ROLE_MANAGER,
	ROLE_VIEWER,
)

KIND_DEPENDENCY = "dependency"
KIND_CHANGE = "change"

STATUS_ACTIVE = "active"
STATUS_ABSENT = "absent"
STATUS_PARTIAL = "partial"

SEVERITY_OK = "green"
SEVERITY_BLOCKER = "red"
SEVERITY_WARN = "orange"
SEVERITY_INFO = "grey"

CHECK_RECORD = "record"
CHECK_RECORDS = "records"
CHECK_FIELDS = "fields"

AUTOMATIC = "خودکار"
MANUAL = "دستی"

# The standard documents this app hooks. The lists are duplicated from `hooks_composition.py` on
# purpose: a test compares them, so a new hook cannot be added without appearing in the registry.
STANDARD_DOC_EVENT_DOCTYPES = (
	"Sales Invoice",
	"Delivery Note",
	"Item",
	"Purchase Order",
	"Purchase Invoice",
	"Purchase Receipt",
	"Stock Entry",
	"Stock Reservation Entry",
)
OWN_DOC_EVENT_DOCTYPES = ("Logistics Serial Assignment", "Logistics Settings")

STANDARD_SCRIPT_DOCTYPES = ("Sales Invoice", "Sales Order", "Quotation", "Purchase Receipt", "Stock Entry")
OWN_SCRIPT_DOCTYPES = ("Logistics Settings",)

ITEM_FIELDS = (
	"sp_serial_required",
	"sp_gross_weight",
	"sp_gross_weight_entered",
	"sp_length_cm",
	"sp_length_cm_entered",
	"sp_width_cm",
	"sp_width_cm_entered",
	"sp_height_cm",
	"sp_height_cm_entered",
)
PURCHASE_ROW_FIELDS = (
	"sp_gross_weight",
	"sp_length_cm",
	"sp_width_cm",
	"sp_height_cm",
	"sp_measurement_snapshot",
	"sp_measurement_item",
	"sp_measurement_presence",
)

WAREHOUSE_TYPE_RECORD = ("Warehouse Type", "Physical")
WAREHOUSE_TYPE_DEFAULT_SETTER = ("Property Setter", "Warehouse-warehouse_type-default")
WAREHOUSE_TYPE_TRANSLATION_SETTER = ("Property Setter", "Warehouse Type-translated_doctype")
ROLE_RECORDS = (ROLE_VIEWER, ROLE_EDITOR, ROLE_MANAGER, ROLE_ARCHIVIST)

CSS_ASSET = "/assets/smartprocess_logistics/css/logistics.css"


def _joined(names):
	return "، ".join(names)


@dataclass(frozen=True)
class Dependency:
	app: str
	component: str
	purpose: str
	constraint: str
	configuration: str
	deployment: str
	verification: str
	removal: str
	required: bool


@dataclass(frozen=True)
class AppliedChange:
	target: str
	purpose: str
	mechanism: str
	configuration: str
	deployment: str
	verification: str
	removal: str
	check: tuple = field(default_factory=tuple)


DEPENDENCIES = (
	Dependency(
		app="frappe",
		component="frappe — چارچوب، Desk، Single، Sidebar، ترجمه",
		purpose="بدون آن برنامه بارگذاری نمی‌شود؛ فرم‌ها، گزارش‌ها و ترجمهٔ فارسی از آن می‌آیند.",
		constraint="نسخهٔ 16 — آزموده‌شده روی 16.55.2",
		configuration="هیچ پیکربندی‌ای لازم نیست.",
		deployment=AUTOMATIC,
		verification="نام و نسخهٔ نصب‌شدهٔ سایت در همین پنل خوانده می‌شود.",
		removal="حذف برنامه؛ پیش از آن داده‌های خودِ برنامه را export کنید.",
		required=True,
	),
	Dependency(
		app="erpnext",
		component="erpnext — اسناد فروش، خرید، انبار و نوع انبار",
		purpose="Sales Invoice و اسناد خرید، انبار، نوع انبار و گردش موجودی از آن می‌آیند.",
		constraint="نسخهٔ 16 — آزموده‌شده روی 16.54.0؛ نسخهٔ 17 آزموده نشده است",
		configuration="هیچ پیکربندی‌ای لازم نیست.",
		deployment=AUTOMATIC,
		verification="نام و نسخهٔ نصب‌شدهٔ سایت در همین پنل خوانده می‌شود.",
		removal="حذف برنامه؛ ERPNext پیش‌نیاز است و برداشتن آن این برنامه را از کار می‌اندازد.",
		required=True,
	),
	Dependency(
		app="smartprocess_erpnext_accounting",
		component="smartprocess_erpnext_accounting — سیاست حساب «کالای در راه»",
		purpose=(
			"فقط اعلام سازگاری: این نسخه هیچ فراخوانی‌ای به آن ندارد و هیچ منطق "
			"Goods in Transit پیاده نکرده است."
		),
		constraint="0.5.0 — اختیاری، بدون import و بدون تغییر از سوی این برنامه",
		configuration="هیچ پیکربندی‌ای از سوی این برنامه لازم نیست.",
		deployment=MANUAL,
		verification="نصب و نسخه در همین پنل؛ نبودِ import با آزمون ایستا بررسی می‌شود.",
		removal="هیچ اثری از این برنامه در آن نیست، پس کاری برای برگرداندن لازم نیست.",
		required=False,
	),
)


APPLIED_CHANGES = (
	AppliedChange(
		target="Item — ۹ فیلد سفارشی",
		purpose="پرچم «نیاز به سریال» و وزن/ابعاد با نشانگرِ «وارد شده» روی کالا.",
		mechanism="Custom Field — smartprocess_logistics/smartprocess_logistics/custom/item.json",
		configuration="مقدار پیش‌فرض ندارد؛ sp_serial_required با تیکِ مدیر روشن می‌شود.",
		deployment=AUTOMATIC,
		verification="وجود هر ۹ فیلد در متادیتای Item، که همین پنل بررسی می‌کند.",
		removal="حذف فیلدها؛ uninstall آن‌ها را برمی‌دارد. پیش از آن مقادیر را export کنید.",
		check=(CHECK_FIELDS, "Item", ITEM_FIELDS),
	),
	AppliedChange(
		target="Purchase Order Item — ۷ فیلد سفارشی",
		purpose="اندازه‌گیری و snapshot آن روی ردیف‌های سفارش خرید.",
		mechanism=(
			"Custom Field — smartprocess_logistics/smartprocess_logistics/custom/purchase_order_item.json"
		),
		configuration="فیلدهای snapshot فقط‌خواندنی‌اند و در before_submit پر می‌شوند.",
		deployment=AUTOMATIC,
		verification="وجود هر ۷ فیلد در متادیتای ردیف، که همین پنل بررسی می‌کند.",
		removal="حذف فیلدها؛ uninstall آن‌ها را برمی‌دارد.",
		check=(CHECK_FIELDS, "Purchase Order Item", PURCHASE_ROW_FIELDS),
	),
	AppliedChange(
		target="Purchase Invoice Item — ۷ فیلد سفارشی",
		purpose="اندازه‌گیری و snapshot آن روی ردیف‌های فاکتور خرید.",
		mechanism=(
			"Custom Field — smartprocess_logistics/smartprocess_logistics/custom/purchase_invoice_item.json"
		),
		configuration="فیلدهای snapshot فقط‌خواندنی‌اند و در before_submit پر می‌شوند.",
		deployment=AUTOMATIC,
		verification="وجود هر ۷ فیلد در متادیتای ردیف، که همین پنل بررسی می‌کند.",
		removal="حذف فیلدها؛ uninstall آن‌ها را برمی‌دارد.",
		check=(CHECK_FIELDS, "Purchase Invoice Item", PURCHASE_ROW_FIELDS),
	),
	AppliedChange(
		target="Purchase Receipt Item — ۷ فیلد سفارشی",
		purpose="اندازه‌گیری و snapshot آن روی ردیف‌های رسید خرید.",
		mechanism=(
			"Custom Field — smartprocess_logistics/smartprocess_logistics/custom/purchase_receipt_item.json"
		),
		configuration="فیلدهای snapshot فقط‌خواندنی‌اند و در before_submit پر می‌شوند.",
		deployment=AUTOMATIC,
		verification="وجود هر ۷ فیلد در متادیتای ردیف، که همین پنل بررسی می‌کند.",
		removal="حذف فیلدها؛ uninstall آن‌ها را برمی‌دارد.",
		check=(CHECK_FIELDS, "Purchase Receipt Item", PURCHASE_ROW_FIELDS),
	),
	AppliedChange(
		target="Purchase Receipt — ۱ فیلد سفارشی",
		purpose="شناسهٔ بارِ اختیاری، قابل جست‌وجو و فیلتر در فهرست رسیدها.",
		mechanism=(
			"Custom Field — smartprocess_logistics/smartprocess_logistics/custom/purchase_receipt.json"
		),
		configuration="اختیاری، اجباری نیست و یکتا نیست.",
		deployment=AUTOMATIC,
		verification="وجود فیلد sp_cargo_ref در متادیتای Purchase Receipt.",
		removal="حذف فیلد؛ uninstall آن را برمی‌دارد.",
		check=(CHECK_FIELDS, "Purchase Receipt", ("sp_cargo_ref",)),
	),
	AppliedChange(
		target="Warehouse Type — رکورد Physical",
		purpose="نوع انباری که طبقه‌بندی انبار (فیزیکی/مجازی/در راه) به آن تکیه می‌کند.",
		mechanism="fixture — smartprocess_logistics/fixtures/warehouse_type.json",
		configuration="نام آن در sp_virtual_warehouse_types قابل تغییر است.",
		deployment=AUTOMATIC,
		verification="وجود رکورد Warehouse Type با نام Physical.",
		removal=(
			"حذف رکورد، تنها اگر هیچ انباری و هیچ نوع دیگری به آن ارجاع نمی‌دهد؛ "
			"پیش از آن انبارها را به نوع دیگری ببرید."
		),
		check=(CHECK_RECORD,) + WAREHOUSE_TYPE_RECORD,
	),
	AppliedChange(
		target="Warehouse — مقدار پیش‌فرض warehouse_type",
		purpose="انبار تازه به‌طور پیش‌فرض فیزیکی است، تا طبقه‌بندی بدون تنظیم دستی درست باشد.",
		mechanism="Property Setter — smartprocess_logistics/fixtures/property_setter.json",
		configuration="مدیر می‌تواند آن را از Customize Form تغییر دهد.",
		deployment=AUTOMATIC,
		verification="وجود Property Setter با نام Warehouse-warehouse_type-default.",
		removal="حذف Property Setter؛ رفتار به پیش‌فرض استاندارد برمی‌گردد و داده‌ای تغییر نمی‌کند.",
		check=(CHECK_RECORD,) + WAREHOUSE_TYPE_DEFAULT_SETTER,
	),
	AppliedChange(
		target="Warehouse Type — translated_doctype",
		purpose="برچسب فارسی نوع انبار از فایل ترجمه خوانده شود.",
		mechanism="Property Setter — smartprocess_logistics/fixtures/property_setter.json",
		configuration="—",
		deployment=AUTOMATIC,
		verification="وجود Property Setter با نام Warehouse Type-translated_doctype.",
		removal="حذف Property Setter؛ برچسب انگلیسی می‌شود و داده‌ای تغییر نمی‌کند.",
		check=(CHECK_RECORD,) + WAREHOUSE_TYPE_TRANSLATION_SETTER,
	),
	AppliedChange(
		target="اسناد استاندارد — " + _joined(STANDARD_DOC_EVENT_DOCTYPES),
		purpose=(
			"گاردهای اعتبارسنجی، snapshot اندازه‌گیری و جلوگیری از موجودی مجازی؛ "
			"هیچ خطی از کد ERPNext ویرایش نمی‌شود."
		),
		mechanism="doc_events در hooks.py — extension point استاندارد",
		configuration="—",
		deployment=AUTOMATIC,
		verification="فهرست hooks.py و آزمون ایستای همین برنامه؛ اجرای گاردها در فرم سند.",
		removal="برداشتن همین کلیدها از doc_events و restart؛ هیچ داده‌ای تغییر نمی‌کند.",
	),
	AppliedChange(
		target="اسناد خودِ برنامه — " + _joined(OWN_DOC_EVENT_DOCTYPES),
		purpose="اعتبارسنجی ارجاع سریال و پاک‌کردن حافظهٔ طبقه‌بندی پس از ذخیرهٔ تنظیمات.",
		mechanism="doc_events در hooks.py، روی DocTypeهای همین برنامه",
		configuration="—",
		deployment=AUTOMATIC,
		verification="فهرست hooks.py؛ آزمون کنترلرها.",
		removal="برداشتن کلیدها از doc_events و restart.",
	),
	AppliedChange(
		target="اسناد استاندارد — فرم‌های " + _joined(STANDARD_SCRIPT_DOCTYPES),
		purpose="دکمه‌ها و پنل‌های فقط‌خواندنی روی فرم همین اسناد؛ چیزی ساخته یا submit نمی‌شود.",
		mechanism="doctype_js در hooks.py — extension point استاندارد",
		configuration="—",
		deployment=AUTOMATIC,
		verification="فهرست hooks.py و بارگذاری اسکریپت در فرم؛ آزمون ایستا.",
		removal="برداشتن کلیدها از doctype_js و restart؛ فرم به حالت استاندارد برمی‌گردد.",
	),
	AppliedChange(
		target="Logistics Settings — فرم " + _joined(OWN_SCRIPT_DOCTYPES),
		purpose="انتقال/درج رکوردها، جدول طبقه‌بندی انبار و همین پنل وابستگی‌ها.",
		mechanism="doctype_js در hooks.py، روی DocType خودِ برنامه",
		configuration="—",
		deployment=AUTOMATIC,
		verification="فهرست hooks.py؛ بارگذاری اسکریپت در فرم تنظیمات.",
		removal="برداشتن کلیدها از doctype_js و restart.",
	),
	AppliedChange(
		target="همهٔ صفحات Desk — " + CSS_ASSET,
		purpose="ظاهر ماژول و راست‌به‌چپ‌سازی، از جمله فاصلهٔ بخش‌های Sidebar.",
		mechanism="app_include_css در hooks.py — فایل استاتیک، بدون build",
		configuration="—",
		deployment=AUTOMATIC,
		verification="بارگذاری همین مسیر در صفحات Desk.",
		removal="برداشتن app_include_css و restart؛ ظاهر استاندارد می‌شود.",
	),
	AppliedChange(
		target="Role — " + _joined(ROLE_RECORDS),
		purpose="نقش‌هایی که مجوزهای این برنامه به آن‌ها داده می‌شود؛ به هیچ کاربری داده نمی‌شوند.",
		mechanism="setup/roles.py در after_install و after_migrate",
		configuration="اعطای نقش به کاربر دستی و توسط مدیر است.",
		deployment=AUTOMATIC,
		verification="وجود هر چهار Role، که همین پنل بررسی می‌کند.",
		removal=(
			"uninstall نقشی را که هیچ کاربر یا مجوزی به آن ارجاع نمی‌دهد حذف می‌کند؛ "
			"نقش ارجاع‌دار باقی می‌ماند."
		),
		check=(CHECK_RECORDS, "Role", ROLE_RECORDS),
	),
)


def required_checks():
	"""Every change check the reader must answer, so the registry cannot describe an unchecked claim."""
	return tuple(change.check for change in APPLIED_CHANGES if change.check)


def rows(facts):
	"""Join the static lists with the facts read from the site, in a stable order."""
	installed = facts.get("installed") or {}
	joined = [_dependency_row(dependency, installed) for dependency in DEPENDENCIES]
	joined.extend(_change_row(change, facts) for change in APPLIED_CHANGES)
	return joined


def _dependency_row(dependency, installed):
	present = dependency.app in installed
	status = STATUS_ACTIVE if present else STATUS_ABSENT
	if present:
		severity = SEVERITY_OK
	else:
		severity = SEVERITY_BLOCKER if dependency.required else SEVERITY_INFO

	return {
		"kind": KIND_DEPENDENCY,
		"target": dependency.component,
		"purpose": dependency.purpose,
		"constraint": dependency.constraint,
		"configuration": dependency.configuration,
		"deployment": dependency.deployment,
		"verification": dependency.verification,
		"removal": dependency.removal,
		"status": status,
		"status_label": "نصب‌شده" if present else ("نصب نشده — الزامی" if dependency.required else "نصب نشده"),
		"severity": severity,
		"version": installed.get(dependency.app) or "",
		"missing": [],
	}


def _change_row(change, facts):
	status, missing = _check_status(change.check, facts)
	severity = SEVERITY_OK if status == STATUS_ACTIVE else SEVERITY_WARN

	return {
		"kind": KIND_CHANGE,
		"target": change.target,
		"purpose": change.purpose,
		"constraint": change.mechanism,
		"configuration": change.configuration,
		"deployment": change.deployment,
		"verification": change.verification,
		"removal": change.removal,
		"status": status,
		"status_label": _STATUS_LABELS[status],
		"severity": severity,
		"version": "",
		"missing": missing,
	}


_STATUS_LABELS = {
	STATUS_ACTIVE: "اعمال شده",
	STATUS_ABSENT: "ثبت نشده",
	STATUS_PARTIAL: "ناقص",
}


def _check_status(check, facts):
	"""Answer one check from the facts alone, so the registry never claims what it did not see."""
	if not check:
		return STATUS_ACTIVE, []

	kind = check[0]

	if kind == CHECK_RECORD:
		_, doctype, name = check
		records = facts.get("records") or {}
		return (STATUS_ACTIVE, []) if records.get((doctype, name)) else (STATUS_ABSENT, [name])

	if kind == CHECK_RECORDS:
		_, doctype, names = check
		records = facts.get("records") or {}
		missing = [name for name in names if not records.get((doctype, name))]
		if not missing:
			return STATUS_ACTIVE, []
		if len(missing) == len(names):
			return STATUS_ABSENT, missing
		return STATUS_PARTIAL, missing

	if kind == CHECK_FIELDS:
		_, doctype, names = check
		present = (facts.get("fields") or {}).get(doctype) or frozenset()
		missing = [name for name in names if name not in present]
		if not missing:
			return STATUS_ACTIVE, []
		if len(missing) == len(names):
			return STATUS_ABSENT, missing
		return STATUS_PARTIAL, missing

	return STATUS_ABSENT, []
