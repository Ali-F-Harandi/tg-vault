# tg-vault

**ذخیره‌ساز ابری روی تلگرام با Bot API — تلگرام را به فضای ابری شخصی خود تبدیل کنید.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Version: v8.3.0](https://img.shields.io/badge/version-v8.3.0-green.svg)](CHANGELOG.md)

> 📖 فارسی | [English](README.md)

---

## tg-vault چیست؟

tg-vault تلگرام را به یک **درایو ابری شخصی نامحدود** تبدیل می‌کند — فقط با یک **Bot token**، بدون شماره تلفن، بدون `api_id`/`api_hash`، بدون MTProto/Telethon/Pyrogram.

Bot API تلگرام یک محدودیت نامتقارن دارد: `sendDocument` تا ۵۰ مگابایت آپلود قبول می‌کند ولی `getFile` فقط ۲۰ مگابایت دانلود می‌دهد. tg-vault فایل‌ها را به chunk‌های ۱۹ مگابایتی تقسیم می‌کند، هر کدام را به‌عنوان پیام `document` (با reply به پیام قبلی) در کانالی که بات ادمین است آپلود می‌کند، و در نهایت یک پیام **manifest** می‌فرستد که شامل متادیتای فایل (نام، اندازه، SHA256 و لیست `message_id` همه chunk‌ها) است. برای دانلود فقط به لینک manifest نیاز دارید.

## قابلیت‌ها (v8.3.0)

### هسته اصلی
- 🚀 **چند باتی با چرخش round-robin** — N بات = N برابر throughput
- ⚡ **دانلود موازی chunk‌ها** با `ThreadPoolExecutor`
- 🛡️ **محدودیت FloodWait-safe** per-bot (حداقل ۵۰ میلی‌ثانیه فاصله)
- 🔐 **رمزنگاری AES-256-GCM** (PBKDF2-HMAC-SHA512، ۶۰۰ هزار iteration) — zero-knowledge
- 📦 **فشرده‌سازی هوشمند gzip** — به‌صورت خودکار فرمت‌های فشرده (mp4, jpg, zip, pdf, …) را skip می‌کند
- 🏷️ **هدر خودتوصیف chunk** (magic `TGV1`) — شناسایی chunk بدون مراجعه به DB
- 🗄️ **دیتابیس SQLite** با جستجوی full-text، تگ و تاریخچه دانلود
- ⏯️ **Resume** هم برای آپلود و هم برای دانلود
- 🌐 **آپلود/دانلود bulk** — چندین فایل/لینک همزمان
- 📝 **manifest JSON فشرده** (`separators=(',',':')`)

### مدیریت دانلود (GUI)
- 🎯 **مدیریت دانلود به سبک IDM** در GUI tkinter
- ⏸️ **Pause / resume / cancel** برای چند دانلود همزمان
- 💾 **ماندگاری دانلودها بین راه‌اندازی‌های GUI** (`downloads.json`)
- 🔢 **کنترل همزمانی** با semaphore (محدود کردن API call‌ها به تعداد بات‌ها)
- 📂 **پوشه موقت** (`.temp/`) برای دانلودهای ناتمام
- 📊 **نمایش لحظه‌ای پیشرفت و سرعت**
- ✋ **انتخاب چندگانه** (Ctrl+click، Shift+click) برای عملیات گروهی

### پشتیبانی چند کاناله
- 📡 آپلود به **چند کانال ذخیره‌سازی**
- دستورات `channels add / remove / show`
- پرچم‌های `--channel` و `--all-channels`
- 🔍 اسکن orphan روی **همه** کانال‌های ذخیره‌سازی

### مدیریت دیتابیس
- 🔧 `db verify` — اصلاح عدم تطابق `share_link` / `manifest_msg_id`
- ❓ `db find-missing` — بررسی وجود فایل‌های DB در کانال
- 🧹 `db clear-temp` — حذف همه پیام‌های موقت کانال به‌جز بکاپ DB
- ✏️ `db edit` — ویرایش توضیحات/تگ (تکی + گروهی، با `--add-tag` / `--remove-tag`)
- 🔄 **auto-sync دیتابیس** به کانال تلگرام (تک‌بخشی و چندبخشی برای DBهای > ۱۹ مگابایت)
- 🗑️ پاکسازی بکاپ DB (بکاپ‌های قدیمی به‌درستی حذف می‌شوند)

### اسکنر Orphan (بهبودیافته)
- 🕵️ شناسایی **همه نوع پیام** (text، photo، video، sticker، audio، voice و ...)
- 💽 ذخیره orphan‌ها در DB محلی (بدون نیاز به اسکن مجدد)
- 📦 اسکن دسته‌ای (`--max-scan`، `--batch-size`، `--delay`)
- 🧨 حذف orphan‌ها به‌صورت تکی یا گروهی
- 🛡️ ایمنی: `message_id`‌های `share_link` به مجموعه شناخته‌شده اضافه می‌شوند

### انتخاب نوع Manifest
- ⚙️ فیلد کانفیگ `default_manifest_type` (`text` / `file` / `auto`، پیش‌فرض: `text`)
- 🚩 پرچم CLI `--manifest-type text|file|auto`
- ✏️ manifest متنی قابل ویرایش (`editMessageText`)
- 📄 manifest فایلی برای manifest‌های خیلی بزرگ (> ۴۰۹۰ کاراکتر)

### GUI
- 🖥️ **۵ تب**: Upload، Download Manager، Browse، Orphans، Configuration
- 📋 منوی راست‌کلیک **کپی/پیست** برای همه ویجت‌های Entry و Text
- 🏷️ تب Browse: ستون‌های Tags و Description، پنل ویرایش inline با پشتیبانی گروهی
- ⚙️ تب Configuration: ویرایشگر کامل کانفیگ (bots، channels، advanced، DB، proxy، storage channels)
- 📌 نوار وضعیت ثابت در پایین
- 🌐 **پشتیبانی از proxy** (سیستمی یا سفارشی)

## شروع سریع

```bash
# ۱. نصب
pip install -r requirements.txt        # یا: pip install .

# ۲. راه‌اندازی اولیه (ایجاد ~/.tg-vault.json)
python tg.py init

# ۳. ویزارد تعاملی راه‌اندازی (پیشنهادی)
python tg.py setup

# ۴. تست اتصال
python tg.py test

# ۵. آپلود یک فایل
python tg.py upload movie.mp4 --desc "Backup" --tag movies,2026

# ۶. دانلود با لینک
python tg.py download https://t.me/c/1234567890/42

# ۷. لیست / جستجو / حذف / اطلاعات
python tg.py ls --limit 10
python tg.py info https://t.me/c/1234567890/42
python tg.py delete https://t.me/c/1234567890/42 --force
```

همچنین می‌توانید به‌صورت ماژول اجرا کنید: `python -m tg_vault upload file.zip`.

## دستورات CLI

```
# راه‌اندازی
tg.py init / setup / test

# بات‌ها
tg.py bots add/list/remove

# کانال‌ها
tg.py channels set/show/add/remove

# آپلود / دانلود
tg.py upload <files> [--desc --tag --encrypt --password --no-compress
                      --channel --all-channels --manifest-type]
tg.py download <links> [--links-file --output --output-dir --password --resume]

# مدیریت فایل
tg.py info / ls / delete / cleanup

# دیتابیس (فعال‌سازی، query و مدیریت)
tg.py db enable/disable/info/list/search/stats/export
tg.py db query/count/download/sync/restore/vacuum/find
tg.py db find-orphans [--max-scan --batch-size --delay]
tg.py db orphans list/delete/clear/count
tg.py db edit <ID> / --ids 1,2,3 [--desc --tag --add-tag --remove-tag]
tg.py db verify [--force]
tg.py db find-missing
tg.py db clear-temp
```

## رمزنگاری

```bash
# رمزنگاری هنگام آپلود (پسورد را می‌پرسد)
python tg.py upload secret.txt --encrypt

# یا پسورد از طریق env var (پیشنهادی برای اسکریپت)
export TG_VAULT_PASSWORD="my-secret"
python tg.py upload secret.txt --encrypt

# رمزگشایی هنگام دانلود (می‌پرسد یا از TG_VAULT_PASSWORD استفاده می‌کند)
python tg.py download https://t.me/c/.../42
```

کلید رمزنگاری **هیچ‌جا ذخیره نمی‌شود**. در manifest فقط: salt، hash تأیید پسورد (برای fail-fast در صورت پسورد اشتباه) و IV هر chunk که از index chunk مشتق می‌شود، نگه داشته می‌شود.

## دیتابیس (اختیاری، پیشنهادی)

```bash
python tg.py db enable                                 # فعال‌سازی دیتابیس SQLite
python tg.py db list                                   # لیست فایل‌های اخیر
python tg.py db search "movie"                         # جستجو بر اساس نام/توضیحات/تگ
python tg.py db query --tag backup --min-size 1000000  # فیلتر پیشرفته
python tg.py db stats                                  # نمایش آمار
python tg.py db sync                                   # بکاپ DB به کانال تلگرام
python tg.py db restore                                # بازیابی DB از کانال
python tg.py db verify --force                         # اصلاح عدم تطابق link/id
python tg.py db find-missing                           # بررسی وجود فایل‌های DB در کانال
python tg.py db clear-temp                             # حذف پیام‌های موقت کانال
python tg.py db edit 12 --desc "New description" --add-tag backup
python tg.py db edit --ids 1,2,3 --remove-tag old
python tg.py db find-orphans                           # اسکن کانال برای orphan
python tg.py db orphans list                           # لیست orphan‌های یافته‌شده
python tg.py db orphans delete --all                   # حذف گروهی
```

## ساختار پروژه

```
tg-vault/
├── tg.py                    # shim سازگار با نسخه قدیمی → tg_vault.cli
├── gui.py                   # shim سازگار با نسخه قدیمی → gui.app
├── pyproject.toml           # متادیتای پکیج پایتون
├── requirements.txt
├── config.sample.json
│
├── tg_vault/                # پکیج اصلی
│   ├── __init__.py          # re-export API عمومی
│   ├── __main__.py          # نقطه ورود python -m tg_vault
│   ├── cli.py               # argparse CLI + main()
│   ├── commands.py          # همه توابع cmd_*
│   ├── interactive.py       # منوی تعاملی
│   ├── config.py            # کلاس Config (~/.tg-vault.json)
│   ├── bot_pool.py          # Bot + BotPool (round-robin، thread-safe)
│   ├── uploader.py          # کلاس Uploader
│   ├── downloader.py        # کلاس Downloader (chunk موازی)
│   ├── download_manager.py  # مدیریت دانلود (pause/resume/cancel)
│   ├── crypto.py            # رمزنگار AES-256-GCM (PBKDF2)
│   ├── compression.py       # gzip هوشمند با تشخیص فرمت
│   ├── chunk_header.py      # هدر ۴۰ بایتی خودتوصیف TGV1
│   ├── db.py                # دیتابیس SQLite (files, chunks, tags, downloads)
│   ├── db_sync.py           # بکاپ/بازیابی DB به کانال تلگرام
│   ├── orphan_scanner.py    # اسکنر orphan
│   ├── constants.py         # VERSION + محدودیت‌های API تلگرام
│   └── utils.py             # helperها (SHA256, format_size, sanitize, ProgressTracker)
│
├── gui/
│   ├── __init__.py
│   └── app.py               # GUI tkinter (۵ تب)
│
├── examples/
│   ├── backup_directory.py  # بکاپ بازگشتی دایرکتوری
│   ├── bulk_upload.py       # wrapper آپلود bulk
│   ├── bulk_download.py     # wrapper دانلود bulk
│   ├── encrypted_upload.py  # wrapper آپلود رمزنگاری‌شده
│   ├── parallel_uploads.py  # آپلود موازی با subprocess
│   ├── db_search.py         # جستجوی DB از اسکریپت
│   └── download_all.py      # دانلود همه manifest‌های کانال
│
├── docs/
│   ├── ARCHITECTURE.md      # تصمیمات طراحی + امنیت thread
│   ├── USAGE.md             # راهنمای استفاده دقیق
│   ├── CONFIGURATION.md     # مرجع فایل کانفیگ
│   ├── SECURITY.md          # رمزنگاری + مدل تهدید
│   └── TELEGRAM_LIMITS.md   # محدودیت‌های سخت/نرم Bot API
│
├── tests/
│   └── test_smoke.py        # تست‌های smoke
│
├── README.md                # نسخه انگلیسی
├── README.fa.md             # این فایل
├── CHANGELOG.md
├── CONTRIBUTING.md
└── LICENSE
```

## نحوه کار

```
آپلود:
  فایل → SHA256 → پیام توضیحات → [chunk1 → chunk2 → ...] → پیام manifest
          (raw → compress → encrypt → هدر TGV1) برای هر chunk
          (هر chunk به قبلی reply می‌کند، round-robin بین بات‌ها)

دانلود:
  لینک → fetch manifest → parse → دانلود موازی chunk‌ها
       → حذف هدر → decrypt → decompress → سرهم‌بندی
       → تأیید SHA256 → تغییر نام به filename نهایی
```

برای تصمیمات طراحی، امنیت thread و دلیل انتخاب اندازه chunk ۱۹ مگابایت و راهکار `forwardMessage` به [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) مراجعه کنید.

## پیش‌نیازها

- پایتون 3.8+
- `requests` (کلاینت HTTP)
- `cryptography` (برای `--encrypt`)
- `tkinter` (برای GUI؛ در ویندوز/macOS در خود پایتون وجود دارد، روی لینوکس ممکن است `python3-tk` لازم باشد)

## لایسنس

MIT — به [LICENSE](LICENSE) مراجعه کنید.

## تشکر

الهام‌گرفته از [TAS (Telegram as Storage)](https://github.com/ixchio/tas) — بهترین ایده‌های آن (هدر TGV1، pipeline رمزنگاری، نوار پیشرفت) پذیرفته شد، در حالی که اندازه chunk ۱۹ مگاباتی که واقعاً برای دانلود کار می‌کند حفظ شد.
