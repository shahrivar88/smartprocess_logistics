# آزمایشگاه ایزولهٔ چرخهٔ «خروجی → حذف → نصب مجدد → بازیابی»

این پوشه یک استک **دورریز و کاملاً ایزوله** می‌سازد که در آن چرخهٔ واقعی تمرین می‌شود: گرفتن
خروجی از سوابق سریال‌ها، حذف app، نصب مجدد، و بازیابی همان سوابق. هیچ سرویس، volume، شبکه یا
دیتابیسی از استک `erpnext` (پورت ۸۰۸۰ و ۸۰۸۱) در آن شریک نیست.

| فایل | کار |
| ---- | --- |
| `compose.isolation-lab.yaml` | استک اختصاصی: `db`, `redis-cache`, `redis-queue`, `configurator`, `backend`, `websocket`, `scheduler`, `queue-*`, `frontend` |
| `.env.lab` (gitignore) | مسیر ریپو، رمز دورریز MariaDB، و رمز admin سایت آزمایشگاه |
| `cleanup_incomplete_site.sh` | حذف **فقط** پوشهٔ سایت نیم‌ساخته، با گاردهای پیش از حذف |
| `verify_isolation_cycle.py` | تأیید خودکار هر فاز؛ فقط داخل کانتینر `backend` اجرا می‌شود |
| `.env.example` | سه متغیر لازم با جای‌نگهدار؛ کپی‌اش کنید به `.env.lab` (که در git نیست) |
| `verify_lab_read_only.py` | همهٔ مسیرهای **خواندنی** روی سایت آزمایشگاه، با شمارش چهار جدول پیش و پس — برای وقتی که نوشتن ممنوع است |
| `verify_lab_transfer.py` | فازهای ۳ و ۴ برنامهٔ تست: سند حسابرسی ثبت‌شده و انتقال سریال روی اصلاحیه |
| `verify_lab_concurrency.py` | فاز ۵: دو فرآیند هم‌زمان روی یک فاکتور مقصد |
| `verify_lab_authorization.py` | فاز ۲: نقش‌ها، دروازهٔ نقش Archivist و محدودیت شرکت |
| `verify_lab_restore_history.py` | فاز ۶: بازآوری تاریخی در برابر بازآوری معمولی |
| `verify_lab_restore_records.py` | فاز ۸: جای‌گذاری سابقه‌های حسابرسی و ردهای آن |

این پنج اسکریپت با `run` داخل کانتینر `backend` اجرا می‌شوند و هر کدام با `PASS` تمام می‌شوند؛
`run` = `arm` + `check` + `disarm`، و `disarm` هیچ‌چیز را حذف نمی‌کند (سند ثبت‌شده و سابقهٔ
حسابرسی حذف‌شدنی نیستند). هر `check` تازه به یک `arm` تازه نیاز دارد.

پورت وب: `127.0.0.1:8095` · دیتابیس منتشر نشده · نام سایت: `sp_logistics_lab` · volumeها:
`sp_logistics_lab_*` (همه دورریز).

---

## گام‌های دستی — دستورهای PowerShell

سه دستور زیر را در ترمینال **خودتان** اجرا کنید (گارد محیط این‌ها را برای agent مسدود می‌کند).

علت گیرکردن تلاش قبلی: `docker compose run` بدون `-it` اجرا شده بود، پس پرسش‌های `new-site` روی
stdin بسته منتظر ماندند و کار نیمه‌تمام رها شد. دستورهای زیر **هیچ پرسشی نمی‌پرسند** چون رمزها را
از `deployment/lab/.env.lab` می‌خوانند.

### ۱) ساخت سایت

```powershell
cd <repo-root>; $root = ((Get-Content .\deployment\lab\.env.lab | Select-String '^LAB_DB_ROOT_PASSWORD=') -replace '^LAB_DB_ROOT_PASSWORD=', ''); $admin = ((Get-Content .\deployment\lab\.env.lab | Select-String '^LAB_SITE_ADMIN_PASSWORD=') -replace '^LAB_SITE_ADMIN_PASSWORD=', ''); docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab -f deployment/lab/compose.isolation-lab.yaml run --rm backend bench new-site sp_logistics_lab --mariadb-root-password $root --admin-password $admin
```

### ۲) نصب مجدد app (پس از حذف، در چرخه)

```powershell
cd <repo-root>; docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab -f deployment/lab/compose.isolation-lab.yaml exec backend bench --site sp_logistics_lab install-app smartprocess_logistics
```

### ۳) حذف app — فقط روی `sp_logistics_lab`

این گام همان چیزی است که سوابق را از بین می‌برد؛ به همین دلیل دستی است و به همین دلیل قبل از آن
`export` گرفته می‌شود.

```powershell
cd <repo-root>; docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab -f deployment/lab/compose.isolation-lab.yaml exec backend bench --site sp_logistics_lab uninstall-app smartprocess_logistics
```

پرسش تأیید را `y` بزنید. `--no-backup` را استفاده **نکنید** (می‌خواهیم رفتار پیش‌فرض سنجیده شود).

### اگر لازم شد پوشهٔ سایت نیم‌ساخته پاک شود

اسکریپت گارددار (خودش بررسی می‌کند که سایت داده‌ای ندارد و فقط همان یک مسیر را حذف می‌کند):

```powershell
cd <repo-root>; docker run --rm -e SP_LAB_CONFIRM=sp_logistics_lab -v sp_logistics_lab_sites:/sites -v "${PWD}/deployment/lab:/lab:ro" alpine sh /lab/cleanup_incomplete_site.sh sp_logistics_lab
```

---

## تأیید ایزوله‌بودن **پیش از** نصب appها

بعد از ساخت سایت، این سه بررسی ملاک شروع است:

```powershell
cd <repo-root>; docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab -f deployment/lab/compose.isolation-lab.yaml exec backend bench --site sp_logistics_lab list-apps
```

```powershell
cd <repo-root>; docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab -f deployment/lab/compose.isolation-lab.yaml exec db sh -c "mariadb -uroot -p`$MARIADB_ROOT_PASSWORD -N -e 'show databases; select user, host from mysql.user;'"
```

```powershell
cd <repo-root>; docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab -f deployment/lab/compose.isolation-lab.yaml exec backend sh -c "grep -o '\"db_name\": \"[^\"]*\"' sites/sp_logistics_lab/site_config.json"
```

معیارها: `list-apps` باید `frappe` (و بعد از نصب، `erpnext`) را نشان دهد بدون خطا؛ نام دیتابیس
سایت باید **غیر از** `_939af57aabbacf4d` باشد و در فهرست دیتابیس‌های سرور آزمایشگاه دیده شود؛ و
`_939af57aabbacf4d` نباید در آن سرور وجود داشته باشد.

---

## ترتیب کامل تمرین

| ترتیب | کار | اجراکننده |
| ----- | --- | --------- |
| 1 | نصب `erpnext` و `smartprocess_logistics` | agent |
| 2 | `seed` — company/item/customer، دو فاکتور واقعی و ۵ سریال از مسیر endpoint خودِ app | agent |
| 3 | `export` — خروجی کامل، ذخیره‌شده در `private/files` همان سایت | agent |
| 4 | **حذف app** (دستور بالا) | **شما** |
| 5 | `after-removal` — اثبات اینکه DocTypeها رفته‌اند و فایل خروجی سالم است | agent |
| 6 | **نصب مجدد** (دستور بالا) | **شما** |
| 7 | `after-reinstall` — پیش‌نمایش، سپس **دو بار** بازیابی: بار دوم باید صفر ردیف بسازد، و `creation`/`owner`/`modified`/`modified_by` باید همان مقادیر فایل باشند | agent |
| 8 | تکرار از گام ۴ یک بار دیگر (چرخهٔ دوم) | شما + agent |

اجرای هر فاز (داخل کانتینر، بدون گارد):

```bash
docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab -f deployment/lab/compose.isolation-lab.yaml exec -w /home/frappe/frappe-bench/sites backend /home/frappe/frappe-bench/env/bin/python apps/smartprocess_logistics/deployment/lab/verify_isolation_cycle.py export --site sp_logistics_lab
```

معیار قبولی: هر فاز `phase <name>: PASS` و کد خروج `0`. شواهد در
`sites/sp_logistics_lab/private/files/lab-cycle-<phase>-<timestamp>.json`.

## اگر فایل JS فرم را تغییر دادید (تل‌ه واقعی)

JS فرم در Frappe از مسیر `/assets/...` نمی‌آید: `doctype_js` به‌صورت `__js` **داخل meta فرم**
تزریق می‌شود و آن meta در redis زیر کلید `doctype_form_meta::<DocType>` **کش** می‌شود (مگر
`developer_mode` روشن باشد). پس بعد از هر ویرایش:

```bash
docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab -f deployment/lab/compose.isolation-lab.yaml exec backend bench --site sp_logistics_lab clear-cache
```

و اگر پایتون هم تغییر کرده (endpoint جدید یا امضای عوض‌شده)، کانتینر backend را ری‌استارت کنید
(دستور `serve` با `--noreload` اجرا می‌شود و کد را در زمان اجرا دوباره نمی‌خواند):

```bash
docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab -f deployment/lab/compose.isolation-lab.yaml restart backend
```

سپس در مرورگر صفحه را دوباره بارگذاری کنید. مسیر استاندارد انتشار (`bench migrate`) خودش کش را
پاک می‌کند؛ این تله فقط در ویرایش داغ بدون migrate رخ می‌دهد.

برای تأیید خودکار همین چیزها (و اینکه مرورگر واقعاً چه چیزی می‌گیرد):

```bash
docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab -f deployment/lab/compose.isolation-lab.yaml exec -T -e LAB_ADMIN_PASSWORD="$(grep '^LAB_SITE_ADMIN_PASSWORD=' deployment/lab/.env.lab | cut -d= -f2)" backend /home/frappe/frappe-bench/env/bin/python /home/frappe/frappe-bench/apps/smartprocess_logistics/deployment/lab/verify_http_round_trip.py
```

## بررسی‌های رابط کاربری در مرورگر

این‌ها را در Desk سایت آزمایشگاه (`http://127.0.0.1:8095`) انجام دهید. **یک بار** با کاربر
`Administrator` و رمز `LAB_SITE_ADMIN_PASSWORD` از `.env.lab` وارد شوید؛ بعد از آن می‌توان
دکمه‌ها و دیالوگ‌ها را گام‌به‌گام بررسی کرد.

| # | کار | انتظار |
| - | --- | ------ |
| 1 | `Logistics Settings` را باز کنید | دو دکمهٔ «خروجی کامل اطلاعات سریال‌ها» و «بازیابی اطلاعات سریال‌ها» دیده شود. اگر کاربر نقش `Logistics Serial Archivist` هم داشته باشد، **چهار** دکمه دیده می‌شود: «بازآوری تاریخچهٔ فاکتورهای لغو‌شده» و «بازآوری سابقه‌های انتقال» |
| 2 | «خروجی کامل اطلاعات سریال‌ها» | دانلود یک فایل JSON با نام سایت، و پیام سبز با شمارش سریال/ردیف |
| 3 | در فایل دانلودشده | `format`, `format_version`, `digest`, `counts`, و ردیف‌ها با `sales_invoice`/`sales_invoice_item`/`company` |
| 4 | فایل را در ویرایشگر متن تغییر دهید و «بازیابی اطلاعات سریال‌ها» را با آن امتحان کنید | پیام خطای «اثر انگشت» و **هیچ** نوشتنی |
| 5 | فایل سالم را انتخاب کنید | دیالوگ پیش‌نمایش با «برای ثبت»، «قبلاً موجود»، «قابل ثبت نیست» و جدول دلایل |
| 6 | «انصراف» | هیچ تغییری در سایت |
| 7 | «ثبت کن» | پیام گزارش با شمارش ثبت‌شده/رد‌شده/قابل‌ثبت‌نبودن |
| 8 | همان فایل را دوباره وارد کنید | همه «قبلاً موجود»، ثبت‌شده = صفر |
| 9 | پس از چرخهٔ حذف/نصب/بازیابی: فاکتورهای `SP-LAB` را باز کنید و دکمهٔ «سریال‌ها» را بزنید | همان سریال‌ها روی همان ردیف‌ها، با همان متن اصلی |
| 10 | «بازآوری تاریخچهٔ فاکتورهای لغو‌شده» را با همان فایل بزنید | پیش‌نمایش با یک **هشدار** در بالا: این عمل روی فاکتورهای لغو‌شده می‌نویسد. دکمهٔ تأییدش «بازآوری کن» است، نه «ثبت کن» |
| 11 | در آن پیش‌نمایش، ردیف‌های فاکتور زنده | با دلیل «روی فاکتور لغو‌شده نیست؛ با بازآوری معمولی ثبت می‌شود» گزارش می‌شوند و ثبت **نمی‌شوند** |
| 12 | در فایل خروجی نسخهٔ ۲، بخش `transfers` | در پیش‌نمایش دو بازآوریِ *ثبت*، خطی می‌گوید چند سابقه در فایل هست و بازآوری **نمی‌شود**؛ در پیش‌نمایش «بازآوری سابقه‌های انتقال» همان خط می‌گوید چند تا **بازآوری می‌شود** |
| 13 | «بازآوری سابقه‌های انتقال» با فایل فاز ۳ | پیش‌نمایش با هشدار خودش و دکمهٔ «بازآوری کن»؛ پس از تأیید، سابقه‌ها با `restored_from` نام همین سایت مبدأ و `restored_by` کاربر جاری ساخته می‌شوند |

## پاک‌کردن محیط آزمایشگاه

```bash
docker compose -p sp-logistics-isolation-lab --env-file deployment/lab/.env.lab -f deployment/lab/compose.isolation-lab.yaml down -v
```

## ایمنی

- اسکریپت پیش از هر خواندن، **نام سایت** و **نام دیتابیس** را بررسی می‌کند و روی
  `SmartProcessERP` و روی دیتابیس `_939af57aabbacf4d` اجرا نمی‌شود.
- `cleanup_incomplete_site.sh` فقط `sp_logistics_lab` را می‌پذیرد، وجود پشتیبان/فایل/کلید رمز را
  رد می‌کند و در صورت وجود هر داده، متوقف می‌شود.
- هیچ دستوری از این پوشه نباید روی استک `erpnext` یا پورت ۸۰۸۰/۸۰۸۱ اجرا شود.

## خروجی، و آنچه پشتیبان پوشش نمی‌دهد

ترتیب پیش از حذف app در یک سایت واقعی: **خروجی کامل اطلاعات سریال‌ها** از `Logistics Settings`،
سپس `bench backup --with-files`، سپس حذف. سه محدودیت پشتیبان:

1. dumpی که **بعد از** حذف گرفته شود هیچ جدول سریالی ندارد؛ جدول‌ها با app رفته‌اند.
2. `--with-files` چیزی است که `private/files` را هم در dump می‌آورد.
3. بازگرداندن dump کل سایت را به آن لحظه برمی‌گرداند — از جمله تغییرات بی‌ربط پس از آن.
