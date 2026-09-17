# -*- coding: utf-8 -*-
"""
مانیتور خاموشی‌های برق - شهرستان سوادکوه شمالی
------------------------------------------------
این نسخه برای اجرا روی GitHub Actions طراحی شده است.
هر بار که اجرا می‌شود:
  1. لیست خاموشی‌های امروز را از سایت khamooshi.maztozi.ir می‌گیرد.
  2. وضعیت هر خاموشی را بر اساس مقایسه‌ی ساعت فعلی با ساعت شروع خاموشی محاسبه می‌کند.
  3. وضعیت قبلی را از یک فایل JSON (که در خود مخزن گیت‌هاب نگه‌داری می‌شود) می‌خواند.
  4. اگر خاموشی‌ای از "scheduled" به "in_progress" تغییر کرده باشد، پیام تلگرام می‌فرستد.
  5. فایل وضعیت جدید را ذخیره می‌کند تا GitHub Action آن را دوباره به مخزن کامیت کند.

توکن تلگرام و چت آیدی از متغیرهای محیطی TELEGRAM_BOT_TOKEN و TELEGRAM_CHAT_ID خوانده می‌شوند
(این‌ها در GitHub Actions به عنوان Secrets تنظیم می‌شوند، هرگز در کد نوشته نمی‌شوند).
"""

import json
import os
import sys
from datetime import datetime, time as dtime

import requests
import jdatetime  # pip install jdatetime
from zoneinfo import ZoneInfo  # پایتون 3.9+

# ============== تنظیمات ==============

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

CITY_CODE = 43          # کد شهرستان سوادکوه شمالی
PGDS_CODE = ""          # فقط یک امور برق دارد، خالی می‌ماند

OUTAGE_API_URL = "https://khamooshi.maztozi.ir/api/outages"

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outage_state.json")
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outage_monitor.log")

TEHRAN_TZ = ZoneInfo("Asia/Tehran")

# =============================================================================


def log(message: str) -> None:
    """ثبت لاگ ساده با زمان، فقط در خروجی استاندارد (برای دیدن در GitHub Actions logs)."""
    timestamp = datetime.now(TEHRAN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)


def today_jalali_str() -> str:
    """تاریخ امروز به فرمت جلالی مثل 1405/06/26 (مطابق فرمت مورد نیاز API)."""
    return jdatetime.date.today().strftime("%Y/%m/%d")


def fetch_outages(date_str: str) -> list:
    """فراخوانی API سایت و گرفتن لیست خاموشی‌های یک روز مشخص."""
    payload = {
        "fromDate": date_str,
        "toDate": date_str,
        "city": CITY_CODE,
        "pgds": PGDS_CODE,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Origin": "https://khamooshi.maztozi.ir",
        "Referer": "https://khamooshi.maztozi.ir/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    resp = requests.post(OUTAGE_API_URL, json=payload, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        log(f"هشدار: پاسخ API موفق نبود: {data}")
        return []
    return data.get("outageList", [])


def parse_jalali_date(date_str: str) -> jdatetime.date:
    """تبدیل رشته‌ی تاریخ جلالی مثل 1405/06/26 به آبجکت jdatetime.date"""
    y, m, d = (int(p) for p in date_str.split("/"))
    return jdatetime.date(y, m, d)


def parse_time(time_str: str) -> dtime:
    """تبدیل رشته‌ی ساعت مثل 08:30 به آبجکت time"""
    h, m = (int(p) for p in time_str.split(":"))
    return dtime(hour=h, minute=m)


def compute_status(outage: dict, now_jalali_date: jdatetime.date, now_time: dtime) -> str:
    """
    تعیین وضعیت یک خاموشی بر اساس مقایسه‌ی تاریخ/ساعت شروع آن با زمان فعلی.
    """
    outage_date = parse_jalali_date(outage["outage_date"])
    outage_time = parse_time(outage["outage_time"])

    if outage_date > now_jalali_date:
        return "scheduled"
    if outage_date < now_jalali_date:
        return "in_progress"

    if now_time < outage_time:
        return "scheduled"
    return "in_progress"


def send_telegram_message(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("خطا: TELEGRAM_BOT_TOKEN یا TELEGRAM_CHAT_ID تنظیم نشده است.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        log(f"خطا در ارسال پیام تلگرام: {e}")


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            log("هشدار: فایل وضعیت خراب بود، از نو شروع می‌شود.")
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def format_alert(outage: dict) -> str:
    return (
        "⚠️ <b>خاموشی وارد وضعیت «در حال انجام» شد</b>\n\n"
        f"📍 <b>منطقه:</b> {outage['address'].strip()}\n"
        f"📅 <b>تاریخ:</b> {outage['outage_date']}\n"
        f"⏰ <b>ساعت شروع:</b> {outage['outage_time']}\n"
        f"🔢 <b>شماره خاموشی:</b> {outage['outage_number']}"
    )


def main() -> None:
    now = datetime.now(TEHRAN_TZ)
    now_jalali_date = jdatetime.date.fromgregorian(date=now.date())
    now_time = now.time()

    date_str = today_jalali_str()
    log(f"شروع بررسی خاموشی‌های تاریخ {date_str} ...")

    try:
        outages = fetch_outages(date_str)
    except Exception as e:
        log(f"خطا در دریافت اطلاعات از سایت: {e}")
        sys.exit(1)

    log(f"{len(outages)} خاموشی دریافت شد.")

    state = load_state()
    changed = False

    for outage in outages:
        key = str(outage["outage_number"])
        new_status = compute_status(outage, now_jalali_date, now_time)
        old_status = state.get(key, {}).get("status")

        if old_status == "scheduled" and new_status == "in_progress":
            log(f"تغییر وضعیت شناسایی شد: {key} -> در حال انجام. ارسال پیام تلگرام...")
            send_telegram_message(format_alert(outage))

        state[key] = {
            "status": new_status,
            "outage_date": outage["outage_date"],
            "outage_time": outage["outage_time"],
            "address": outage["address"].strip(),
        }
        changed = True

    if changed:
        save_state(state)

    log("پایان بررسی.")


if __name__ == "__main__":
    main()
