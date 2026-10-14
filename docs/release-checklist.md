# چک‌لیست انتشار — نسخهٔ بعدی (انتقال سریال و سه بازآوری)

این سند چک‌لیست **آماده‌سازی** است، نه اجازهٔ انتشار: در فاز ۲۰ هیچ چیزی commit، tag یا
منتشر نشد. هر گام زیر یا انجام شده و اینجا ثبت است، یا **کار شماست** و با «⬜» علامت خورده.

## ۰. تصمیم اول: شمارهٔ نسخه

✅ **انجام شد:** `__version__` به `0.4.0` رسید و بخش Unreleased در CHANGELOG به
`## 0.4.0 — 2026-10-15` تغییر نام داد. `0.3.0` (ایستگاه اسکن، ۲۰۲۶-۱۰-۱۴) منتشرشده می‌ماند و
دست‌نخورده است؛ کار تازه (انتقال سریال، سه بازآوری، فرمت ۲) یک ویژگی تازه است و طبق semver
نسخهٔ بعدی `0.4.0` است. ⬜ تنها چیزی که مانده، تأیید همین شماره توسط شماست.

## ۱. آنچه باید commit شود

**همهٔ کار در working tree است و هیچ‌چیز commit نشده.** برای یک نصب تمیز، این‌ها باید در
انتشار باشند:

| گروه | فایل‌ها |
| ---- | ------- |
| هستهٔ خالص | `smartprocess_logistics/core/transfer.py`، `core/scan.py` (تازه) |
| لایهٔ دسترسی | `smartprocess_logistics/permissions.py` (تازه) |
| DocTypeهای تازه | `smartprocess_logistics/smartprocess_logistics/doctype/logistics_serial_transfer/` و `..._item/` (فایل‌های JSON و `.py`) |
| سمت مرورگر | `public/js/sales_invoice_serial_transfer.js` |
| تست‌ها | `tests/test_transfer.py`، `test_transfer_schema.py`، `test_transfer_reads.py`، `test_export_records.py`، `test_company_scope.py`، `test_scan.py`، `test_settings_layout.py`، و **`tests/fixtures/synthetic-workbook.xlsx`** |
| مستندات | `docs/integration-test-plan.md`، `docs/recovery-test-plan.md`، `docs/release-checklist.md` |
| ابزار آزمایشگاه | پنج `deployment/lab/verify_lab_*.py`، `verify_batch_intake.py`، `verify_company_isolation.py`، `verify_missing_report.py`، `verify_scan_station.py`، `verify_scan_logic.js`، `verify_transfer_logic.js`، `preview_scan_header.html`، `preview_settings_restore.html`، `.env.example` |
| تغییر نام دو فایل | `smartprocess_logistics/desktop_icon/سریال_لجستیک.json` و `workspace_sidebar/سریال_لجستیک.json` **باید اضافه شوند** و دو فایل لاتین قدیمی (`logistics.json`) **باید حذف‌شده ثبت شوند** — یک `git add` روی مسیر درست هر دو کار را می‌کند |

⚠️ `tests/fixtures/synthetic-workbook.xlsx` یک **پیش‌نیاز نصب تمیز** است. در فاز ۲۱ این هم
بسته شد: `TestTheSyntheticWorkbook` حالا اگر فایل نباشد آن را از دستور پختِ خودش می‌سازد
(`build_synthetic_workbook`)، و `test_the_recipe_builds_the_rows_the_checked_out_file_holds`
مقادیر ساخته‌شده را با فایل مخزن مقایسه می‌کند. پس یک clone بدون این دودویی هم کل سوئیت را
اجرا می‌کند؛ با این حال **فایل باید commit شود** تا مقدارش بایت‌به‌بایت بازتولیدپذیر بماند.

## ۲. فایل‌هایی که **نباید** در تاریخ باشند

`.gitignore` این‌ها را می‌گیرد و در فاز ۲۰ بازبینی شد: `.env.lab` (رمزهای دورریز),
`.venv/`, `__pycache__/`, `.claude/launch.json` (مسیر میزبان), `.claude/*.local.json`,
`deployment/lab/probe_*.py` (ابزار همین ماشین), `public/js/*.candidate`.
`.gitattributes` هم `CLAUDE.md`، `.claude/` و `docs/test-manifest.md` را از `git archive`
بیرون می‌گذارد (با `export-ignore`) — یعنی از tarball انتشار حذف می‌شوند.

**بازبینی راز و اطلاعات شخصی (فاز ۲۰، انجام شد):** هیچ مقدار رمز/کلیدی در فایل‌های
قابل‌commit نیست (تنها موارد، placeholderهای `${...}` در compose و `<throwaway password>` در
`.env.example` است)؛ هیچ مسیر مطلقی مثل `C:\Users\...` در کد نیست؛ همهٔ ایمیل‌ها
`example.test`/`example.com`/`smartprocess.local` هستند.

## ۳. پیش‌نیازهای بسته‌بندی

- ⬜ `__version__` را به شمارهٔ تصمیم‌گرفته‌شده برسانید (بند ۰) و عنوان بخش Unreleased در
  `CHANGELOG.md` را به همان شماره و تاریخ تغییر دهید.
- ✅ `smartprocess_logistics/modules.txt`, `patches.txt`, `hooks.py` (ثبت‌شده‌ها:
  `after_install`, `after_migrate`, `after_uninstall`, یک job زمان‌بند، `doctype_js`،
  `app_include_css`, دو `permission_query_conditions`) — بازبینی شد.
- ✅ `pyproject.toml` (نام، نویسنده، `requires-python`، وابستگی `openpyxl`, `required_apps`).
- ✅ `.gitattributes` این فاز یک قاعدهٔ صریح `*.xlsx binary` گرفت و `.env.example` آزمایشگاه
  ساخته شد (هر دو تازه، و هر دو باید commit شوند).
- ✅ **linter (فاز ۲۱، اجرای خواندنی):** `ruff check smartprocess_logistics deployment` اکنون
  **All checks passed!** است. ۴۱ یافتهٔ اولیه بسته شد: ۱۵ ترتیب import، ۱۱ import بی‌استفاده و ۱۵
  مورد دیگر — یکیشان یک عیب واقعی در تست‌ها: دو متد با نام یکسان در `test_export.py` که فقط
  یکی‌شان اجرا می‌شد (F811) — به‌علاوهٔ `seeded` بی‌استفاده در `setup/seeds.py`، کلید تکراری در
  `fake_bench.py`، دو `assertRaises(Exception)` که باریک شدند، و سه `zip()` بی‌`strict=`.
- ✅ **قالب‌بندی (تصمیم تأییدشده: نگه‌داشتن `ruff-format`، با commit مستقل).** `ruff format` در
  فاز ۲۲-A روی کد اجرا شد؛ `ruff format --check` اکنون می‌گوید **همهٔ ۱۰۰ فایل قالب‌بندی‌شده‌اند**.
  درستی‌اش با AST سنجیده شد، نه با چشم: برای هر فایل tracked، محتوای HEAD در یک کپی قالب‌بندی و
  درخت AST پیش و پس مقایسه شد — **۶۹ فایل، ۰ تفاوت**، یعنی قالب‌بندی هیچ رفتاری را عوض نکرده.
  `ruff check` هم **All checks passed!** است.
- 📋 **طرح دو commit (هیچ‌کدام stage یا commit نشده):**
  1. **commit ۱ — فقط قالب‌بندی:** محتوای HEAD + `ruff format`. اندازه‌اش **۲۶ فایل، +۱۳۶/−۱۸۳
     خط**، فقط فاصله و شکستن خط (AST-یکسان). برای ساختنش: یک worktree یا کپی از HEAD بردارید،
     `ruff format smartprocess_logistics deployment` را در آن اجرا و همان را commit کنید.
  2. **commit ۲ — کارِ عملکردی:** همان درخت کاری که این گزارش تحویل می‌دهد (قالب‌بندی در آن
     هست)، چون قالب‌بندی قطعی و تکرارپذیر است، diff این commit نسبت به commit ۱ **فقط تغییرهای
     عملکردی** است.
  ترتیب «قالب‌بندی اول» با این روش قابل اجرا است؛ بدون آن، قالب‌بندی و کارِ عملکردی در یک diff
  قاطی می‌شوند و مرور آن ممکن نیست.

## ۴. آزمون‌هایی که پیش از انتشار باید سبز باشند

| چه چیزی | دستور | آخرین نتیجه |
| ------- | ----- | ----------- |
| تست‌های بدون پایگاه داده | `python -m unittest discover -s smartprocess_logistics/tests -t .` (برای تست‌های XLSX: `.venv/Scripts/python.exe …`) | **1119 تست، OK (۱ skip)** |
| linter | `ruff check smartprocess_logistics deployment` | **All checks passed!** |
| منطق مرورگر | `node deployment/lab/verify_scan_logic.js` و `verify_transfer_logic.js` | **PASS** (۲۲۲ و ۵۹ بررسی) |
| DOM واقعی — پنجرهٔ اسکن و انتقال | `preview_scan_header.html` با `?base=/smartprocess_logistics/public` | **PASS (۲۱۴ بررسی)** |
| DOM واقعی — سه بازآوری | `preview_settings_restore.html` با همان پارامتر | **PASS (۵۶ بررسی)** |
| آزمایشگاه — فازهای ۲ تا ۶ و ۸ | پنج `verify_lab_*.py` با آرگومان `run` | **PASS** (گزارش در `docs/integration-test-plan.md`) |
| آزمایشگاه — مسیر HTTP | `verify_http_round_trip.py` | **PASS** (۱۵ بررسی، شامل هر سه بازآوری) |

این مجموعه در فاز ۲۲-A روی همین درخت — پس از قالب‌بندی، چهار تست مرزی I28، اصلاح fixture و
 اصلاح `world_names` — دوباره اجرا شد و همه PASS شدند. آزمون‌های آزمایشگاه هم در همین فاز اجرا
 شدند: هفت اسکریپت `verify_lab_*.py` و `verify_http_round_trip.py`، همه PASS، و تازه‌ترینشان
 `verify_lab_recovery.py` که یک بازیابی کامل و یک **rollback** را می‌سنجد. ⬜ پس از commit، یک
 بار دیگر روی همان درخت.

## ۵. استقرار (نیازمند اجازهٔ صریح شما)

۰. ⛔ **گام‌های حذف/نصب مجدد چرخهٔ آزمایشگاه BLOCKED است** و به اجازهٔ جداگانه نیاز دارد: حذف
   app جدول‌های خودش را می‌برد و دادهٔ انباشتهٔ آزمایشگاه را از بین می‌برد، و ساختن سایت/volume
   تازه هم مجاز نیست. تفصیل و دو راه پیشنهادی در `docs/recovery-test-plan.md`، بخش ۳.۱.
۱. ⬜ `bench migrate` روی `SmartProcessERP` — دو DocType و ستون‌های تازه را می‌سازد. با اصلاحِ
   نام فایل‌های ناوبری، این migrate دیگر سایدبار و آیکون را به‌عنوان orphan حذف نمی‌کند؛
   بعدش حضور «سریال لجستیک» و چهار نقش را بررسی کنید.
۲. ⬜ دکمه‌های صفحهٔ `Logistics Settings` را با دو کاربر ببینید: Manager بدون نقش Archivist
   (سه دکمه) و با نقش (چهار دکمه).
۳. ⬜ گام‌های ۴ و ۶ چرخهٔ آزمایشگاه (`docs/recovery-test-plan.md`) — حذف و نصب مجدد، فقط توسط
   شما.
۴. ⬜ برچسب نسخه و انتشار، پس از سبز شدن همهٔ موارد بالا.

## ۶. کارهای باقی‌مانده و تصمیم‌های شما

- **هیچ‌چیز commit نشده** — بزرگ‌ترین ریسک این انتشار، درخت کاری است که فقط روی این ماشین
  اثبات شده.
- **حذف/نصب مجدد (فاز ۷)** روی هیچ سایتی اجرا نشده است.
- **دو تصمیم برای تأیید:** (الف) `World.serial_names` — تنها خواندن بین‌شرکتی مجاز، که در
  `docs/integrity-invariants.md` به‌عنوان I28 ثبت شد و دو تست نگهبان دارد؛ (ب) برخورد نامِ
  سابقه با نامی که در شرکت دیگری است، کل بازآوری را برمی‌گرداند (با جملهٔ درستِ «نام گرفته
  شده»، نه پیام هم‌زمانی) — پذیرش یا طراحی دوباره، تصمیم شماست.
- **محدودکنندهٔ نرخ** در اجرای پشت‌سرهم اسکریپت HTTP فعال می‌شود (مشاهده‌شده): اجرای مجدد
  آزمون‌ها باید با فاصله باشد.
