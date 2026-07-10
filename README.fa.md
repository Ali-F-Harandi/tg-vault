# tg-vault

> از تلگرام به عنوان فضای ابری شخصی استفاده کنید — **فقط با توکن ربات**، بدون شماره تلفن، بدون `api_id`/`api_hash`، بدون MTProto/Telethon/Pyrogram.

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

📖 **[English documentation](README.md)**

---

## مشکل

تلگرام برای Bot API یک محدودیت نامتقارن داره:

| عملیات | محدودیت |
|--------|---------|
| آپلود با `sendDocument` | **50 مگابایت** |
| دانلود با `getFile` | **20 مگابایت** ← گلوگاه واقعی |

یعنی: ربات می‌تونه فایل 50 مگابایتی آپلود کنه، ولی هیچ‌وقت نمی‌تونه فایل بزرگ‌تر از 20 مگابایت رو با Bot API رسمی دانلود کنه. ابزارهایی مثل [teldrive](https://github.com/tgdrive/teldrive) این مشکل رو با **MTProto** حل می‌کنن که به `api_id`/`api_hash` و session کاربر (با شماره تلفن) نیاز داره.

## راه‌حل

`tg-vault` فایل‌های بزرگ رو به پارت‌های ≤19 مگابایتی تقسیم می‌کنه، هر پارت رو به عنوان یک پیام document به کانالی که ربات درش ادمینه می‌فرسته، و در نهایت یک **پیام manifest** ذخیره می‌کنه که شامل متادیتای فایل (نام، حجم، SHA256 و لیست `message_id` همه پارت‌ها) هست.

برای دانلود، فقط به **لینک پیام manifest** نیاز دارید — `tg-vault` اون رو می‌خونه، هر پارت رو دانلود می‌کنه و SHA256 رو بررسی می‌کنه.

```
┌──────────────────────────────────────────────────────┐
│  کانال شما                                           │
│                                                      │
│  [Description]  ← نام + حجم + SHA256 + هشتگ‌ها       │
│       ↓ reply                                        │
│  [Part 1/4]    ← پارت (~19 مگابایت)                  │
│       ↓ reply                                        │
│  [Part 2/4]                                          │
│       ↓ reply                                        │
│  [Part 3/4]                                          │
│       ↓ reply                                        │
│  [Part 4/4]                                          │
│       ↓ reply                                        │
│  [Manifest]     ← JSON با همه message_id‌ها + SHA256 │
│                  (لینک همین پیام رو نگه می‌دارید)    │
└──────────────────────────────────────────────────────┘
```

## ویژگی‌ها

- ✅ **پشتیبانی از چند ربات** با چرخش round-robin (سرعت چند برابر)
- ✅ **دانلود موازی پارت‌ها** (همه ربات‌ها همزمان)
- ✅ **محدودیت نرخ per-bot** (در برابر FloodWait امن، حداقل 50ms فاصله)
- ✅ **Connection pooling** (`requests.Session` مجزا برای هر ربات)
- ✅ **پیام description** قبل از پارت‌ها (نام + حجم + SHA256 + متن + هشتگ)
- ✅ **پیام manifest** بعد از پارت‌ها (به عنوان نشانگر "پایان" + reply به آخرین پارت)
- ✅ **Resume** هم برای آپلود هم برای دانلود
- ✅ **اعتبارسنجی/پاک‌سازی طول نام فایل و caption**
- ✅ **پاک‌سازی خودکار با Ctrl+C** (حذف پیام‌های موقت)
- ✅ **فایل کانفیگ** (`~/.tg-vault.json`)
- ✅ **CLI + منوی تعاملی**
- ✅ **امن در برابر همزمانی** (هر سشن یک UUID منحصر به فرد داره)

## شروع سریع

### نصب

```bash
git clone https://github.com/kesafatkari/tg-vault.git
cd tg-vault
pip install -r requirements.txt
```

### پیکربندی

1. **ساخت ربات تلگرام** — به [@BotFather](https://t.me/BotFather) پیام بدید، `/newbot` رو بزنید، توکن رو کپی کنید.
2. **ساخت کانال تلگرام** (ترجیحاً خصوصی)، بعد ربات رو به عنوان **ادمین** اضافه کنید با دسترسی‌های:
   - ارسال پیام ✅
   - حذف پیام ✅
3. **گرفتن channel ID** — به بخش [گرفتن آیدی کانال](#گرفتن-آیدی-کانال) مراجعه کنید.

### راه‌اندازی اولیه

ساده‌ترین راه، جادوگر تعاملی setup هست:

```bash
python tg.py setup
```

این جادوگر توکن ربات رو تأیید می‌کنه، کانال‌ها رو تنظیم می‌کنه و در نهایت یک تست اتصال انجام می‌ده — همه تو یک مرحله.

یا می‌تونید از دستورات جداگانه استفاده کنید:

```bash
python tg.py init
python tg.py bots add 123456789:ABC-DEF...
python tg.py channels set main -1001234567890
python tg.py channels set temp -1009876543210   # اختیاری، پیش‌فرض همون main
python tg.py test
```

یا مستقیماً فایل کانفیگ رو ویرایش کنید — به [فایل کانفیگ](#فایل-کانفیگ) مراجعه کنید.

## گرفتن آیدی کانال

به آیدی کانال در یکی از این فرمت‌ها نیاز دارید:
- **کانال خصوصی**: `-1001234567890` (شروع با `-100`، بعد آیدی داخلی)
- **کانال عمومی**: `@mychannel_username`

### روش ۱: استفاده از @userinfobot (ساده‌ترین)

1. هر پیامی از کانالتون رو به [@userinfobot](https://t.me/userinfobot) فوروارد کنید.
2. با chat id (مثلاً `-1001234567890`) جواب می‌ده.

### روش ۲: از لینک `t.me/c/...`

اگه کانالتون خصوصیه، به لینک پیام‌هاش نگاه کنید:
- لینک: `https://t.me/c/1234567890/42`
- آیدی کانال: `-1001234567890` (اضافه کنید `-100` به عدد بعد `/c/`)

### روش ۳: استفاده از Telegram Web

1. کانالتون رو در https://web.telegram.org باز کنید
2. به URL نگاه کنید: `https://web.telegram.org/#-1001234567890`
3. عدد بعد از `#` همون آیدی کاناله.

### روش ۴: استفاده از Bot API

1. ربات رو به کانال به عنوان ادمین اضافه کنید.
2. هر پیامی در کانال بفرستید.
3. در مرورگر باز کنید: `https://api.telegram.org/bot<TOKEN>/getUpdates`
4. در JSON پاسخ، `chat.id` کانالتون رو پیدا کنید.

### افزودن ربات به عنوان ادمین

در کانالتون:
1. کانال → **مدیریت کانال** → **مدیران**
2. یوزرنیم ربات رو سرچ کنید
3. اضافه کنید با این دسترسی‌ها:
   - ✅ ارسال پیام
   - ✅ ویرایش پیام (اختیاری، پیشنهاد می‌شه)
   - ✅ حذف پیام

بدون این دسترسی‌ها، `tg-vault` نمی‌تونه پارت‌ها رو آپلود کنه یا پیام‌های موقت رو پاک کنه.

### آپلود

```bash
# آپلود ساده
python tg.py upload movie.mp4

# با توضیحات و هشتگ
python tg.py upload movie.mp4 --desc "نسخه پشتیبان Blade Runner 2049 - 4K" --tag movies,sci-fi,2026
```

خروجی:
```
🔗 ★ لینک دانلود:
   https://t.me/c/1234567890/42
```

### دانلود

```bash
# پیش‌فرض: با نام اصلی در مسیر فعلی ذخیره می‌شه
python tg.py download https://t.me/c/1234567890/42

# خروجی سفارشی
python tg.py download https://t.me/c/1234567890/42 --output my-movie.mp4 --output-dir ~/Downloads
```

### ادامه عملیات قطع شده

```bash
python tg.py upload movie.mp4 --resume
python tg.py download https://t.me/c/1234567890/42 --resume
```

## مرجع CLI

```
tg-vault v6 — Telegram Bot API cloud storage

دستورات:
  init                              ساخت فایل کانفیگ نمونه
  bots add <TOKEN>                  اضافه کردن ربات
  bots list                         لیست ربات‌های کانفیگ شده
  bots remove <INDEX>               حذف ربات با شماره
  channels set main <ID>            تنظیم کانال اصلی
  channels set temp <ID>            تنظیم کانال موقت (اختیاری)
  channels show                     نمایش کانال‌های کانفیگ شده
  test                              تست اتصال همه ربات‌ها و کانال‌ها
  upload <FILE> [گزینه‌ها]           آپلود فایل
    --desc, -d "متن"                  متن توضیحات
    --tag, -t "t1,t2,t3"              هشتگ‌ها (با کاما جدا کنید)
    --resume, -r                      ادامه آپلود قطع شده
  download <LINK> [گزینه‌ها]         دانلود با لینک manifest
    --resume, -r                      ادامه دانلود قطع شده
    --output, -o "name"               نام فایل خروجی
    --output-dir "path"               مسیر خروجی (پیش‌فرض: .)
  info <LINK>                       نمایش اطلاعات manifest بدون دانلود
  ls [--limit N]                    لیست فایل‌های اخیر در کانال اصلی
  delete <LINK> [--force]           حذف پیام‌های فایل از کانال
  cleanup [--max-count N]           پاک‌سازی کانال موقت

گزینه‌های کلی:
  --config <PATH>                   استفاده از فایل کانفیگ سفارشی (پیش‌فرض: ~/.tg-vault.json)
  --version                         نمایش نسخه
```

اگه `python tg.py` رو بدون آرگومان اجرا کنید، **منوی تعاملی** باز می‌شه.

## چرا چند ربات؟

تلگرام برای هر ربات محدودیت ~30 پیام در ثانیه به طور کلی و ~1 پیام در ثانیه به یک چت مشخص اعمال می‌کنه. با اضافه کردن چند ربات (همگی ادمین کانال)، `tg-vault` بینشون چرخش می‌کنه — سرعت شما چند برابر می‌شه.

هر ربات همچنین `requests.Session` اختصاصی برای connection pooling و rate limiter جداگانه داره.

```bash
# تا ~20 ربات می‌تونید اضافه کنید (محدودیت تلگرام برای هر اکانت)
python tg.py bots add <token1>
python tg.py bots add <token2>
python tg.py bots add <token3>
python tg.py test   # بررسی اینکه همه ادمین هستن
```

## دانلود چطور کار می‌کنه (نکته فنی)

ربات **نمی‌تونه به خودش پیام بده** در تلگرام. پس نمی‌شه پیام‌ها رو به چت خود ربات فوروارد کرد تا `file_id` رو گرفت.

به جای این، `tg-vault`:

1. یک `forwardMessage` از کانال مبدا به **کانال موقت** می‌فرسته (با `disable_notification=true` تا کسی نوتیفیکیشن نگیره).
2. `file_id` رو از پیام فوروارد شده می‌خونه.
3. `getFile` رو صدا می‌زنه تا URL دانلود رو بگیره.
4. پارت رو دانلود می‌کنه.
5. **بلافاصله پیام فوروارد شده** رو از کانال موقت حذف می‌کنه.

اگه کانال موقت کانفیگ نشده، از کانال اصلی به عنوان موقت استفاده می‌شه — ولی پیام‌های فوروارد شده خیلی کوتاه (و سپس حذف می‌شن) اونجا ظاهر می‌شن.

## امنیت در همزمانی

هر سشن آپلود/دانلود یک tag منحصر به فرد 8 کاراکتری UUID می‌گیره. این tag در caption هر پارت هست، پس:

- چند پروسه `tg-vault` می‌تونن موازی بدون تداخل اجرا بشن.
- پاک‌سازی کانال موقت فقط پیام‌های همون سشن رو حذف می‌کنه.
- وضعیت resume به ازای هر فایل در `<filename>.resume.json` ذخیره می‌شه.

برای 100 دانلود موازی، 100 instance اجرا کنید — `BotPool` به طور خودادار بین ربات‌ها چرخش می‌کنه. با 5 ربات، می‌تونید تا ~5 عملیات همزمان بدون FloodWait اجرا کنید.

## محدودیت‌ها

- **حداکثر حجم فایل**: 2 گیگابایت (محدودیت سخت تلگرام، حتی با Local Bot API Server).
- **اندازه پارت**: 19 مگابایت پیش‌فرض (زیر محدودیت 20 مگابایتی `getFile`). با `chunk_size_mb` در کانفیگ قابل تنظیم.
- **محدودیت نرخ**: ~30 پیام/ثانیه به ازای هر ربات. با N ربات، نرخ موثر ~N×30.
- **بدون streaming**: کل فایل باید قبل از بررسی SHA256 دانلود بشه.
- **ربات‌ها به ازای اکانت**: تلگرام ~20 ربات به ازای هر اکانت کاربر از طریق @BotFather اجازه می‌ده.

## فراتر: Local Bot API Server

اگه [Local Bot API Server](https://github.com/tdlib/telegram-bot-api) رو خودتون host کنید، محدودیت‌ها این‌طور می‌شن:

| | Cloud | Local Server |
|---|---|---|
| آپلود | 50 مگابایت | **2000 مگابایت** |
| دانلود | 20 مگابایت | **بدون محدودیت** |

این نیاز به `api_id`/`api_hash` (از https://my.telegram.org) داره **برای اجرای سرور**، ولی درخواست‌های کلاینت هنوز فقط با توکن ربات احراز هویت می‌شن. پس کاربران نهایی شما هنوز نیازی به شماره تلفن ندارن — فقط شما (اپراتور سرور) دارید.

## فایل کانفیگ

مسیر پیش‌فرض: `~/.tg-vault.json`

```json
{
  "bots": [
    {"token": "123:ABC...", "username": "my_first_bot"},
    {"token": "456:DEF...", "username": "my_second_bot"}
  ],
  "channels": {
    "main": -1001234567890,
    "temp": -1009876543210
  },
  "chunk_size_mb": 19,
  "upload_delay": 0.3,
  "download_delay": 0.2,
  "parallel_workers": 4,
  "version": 6
}
```

## اپ وب (GitHub Pages)

tg-vault همچنین یک **اپ وب کاملاً client-side** داره — بدون backend، بدون سرور، بدون نصب. توکن ربات هیچ‌وقت مرورگر شما رو ترک نمی‌کنه.

🌐 **دموی زنده**: https://kesafatkari.github.io/tg-vault/

ویژگی‌ها:
- 🔐 تنظیمات در `localStorage` ذخیره می‌شه (فقط مرورگر، هیچ جایی به جز تلگرام ارسال نمی‌شه)
- 📤 آپلود با drag-and-drop + progress زنده
- 📥 دانلود با لینک manifest + بررسی SHA256
- 📋 نمایش اطلاعات manifest بدون دانلود
- 🌙 تم تاریک، سازگار با موبایل
- 🚀 روی هر هاست استاتیک کار می‌کنه (GitHub Pages، Netlify، Cloudflare Pages، یا حتی فایل HTML رو локal باز کنید)

برای اجرای local:
```bash
# فایل رو در مرورگر باز کنید
open docs/index.html
# یا local serve کنید
python3 -m http.server 8000 -d docs
# بعد به http://localhost:8000 برید
```

## مثال‌ها

در پوشه [`examples/`](examples/) ببینید:
- [`parallel_uploads.py`](examples/parallel_uploads.py) — آپلود همزمان چند فایل
- [`backup_directory.py`](examples/backup_directory.py) — پشتیبان‌گیری بازگشتی از یک دایرکتوری
- [`download_all.py`](examples/download_all.py) — دانلود همه فایل‌های manifest از یک کانال

## مقایسه با پروژه‌های مشابه

| پروژه | روش | فقط ربات؟ | چند ربات؟ | رمزنگاری؟ |
|-------|-----|-----------|-----------|-----------|
| **tg-vault** (این پروژه) | پارت‌بندی 19 مگ + manifest | ✅ | ✅ | ❌ (در راه) |
| [Pentaract](https://github.com/Dominux/Pentaract) | پارت‌بندی 20 مگ | ✅ | ✅ (تا 20) | ❌ |
| [tas](https://github.com/ixchio/tas) | پارت‌بندی 49 مگ + AES-GCM | ✅ | ❌ | ✅ |
| [teldrive](https://github.com/tgdrive/teldrive) | MTProto | ❌ (نیاز به api_id/api_hash) | ❌ | ❌ |

## مشارکت

Pull request‌ها خوشامد! چند ایده:
- 🔐 رمزنگاری client-side با AES-256-GCM
- 🌐 پشتیبانی از Local Bot API Server
- 🎬 HTTP Range streaming برای فایل‌های ویدیویی
- 🐳 Docker image + REST API wrapper
- 🖥️ TUI (Textual / Rich) برای منوی تعاملی
- 📊 گزارش پیشرفت با WebSockets

## لایسنس

[MIT](LICENSE) © 2026 [kesafatkari](https://github.com/kesafatkari)

## تشکر از

- الهام گرفته از [Pentaract](https://github.com/Dominux/Pentaract) (Rust، پیاده‌سازی مرجع پارت‌بندی 20 مگ)
- الهام گرفته از [tas](https://github.com/ixchio/tas) (رمزنگاری AES-256-GCM + FUSE)
- ترفند portability بین ربات‌ها با `message_id` + `copyMessage` از [tg-bot-storage](https://github.com/DipandaAser/tg-bot-storage)
- ساخته شده با [Telegram Bot API](https://core.telegram.org/bots/api)
