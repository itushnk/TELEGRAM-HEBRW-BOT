
# -*- coding: utf-8 -*-
import os, sys, csv, requests, time, telebot, threading, re
from telebot import types
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo
import openai

# הגדרות כלליות
BASE_DIR = os.environ.get("BOT_DATA_DIR", "./data")
try:
    os.makedirs(BASE_DIR)
except FileExistsError:
    pass

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
CHANNEL_ID = os.environ.get("PUBLIC_CHANNEL", "@your_channel")
ADMIN_USER_IDS = set()

DATA_CSV = os.path.join(BASE_DIR, "workfile.csv")
PENDING_CSV = os.path.join(BASE_DIR, "pending.csv")
USD_TO_ILS_RATE_DEFAULT = 3.55

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "TelegramPostBot/1.0"})
IL_TZ = ZoneInfo("Asia/Jerusalem")

# 🕒 פונקציה לבדיקת שעות שידור
def within_scheduled_hours():
    now = datetime.now(IL_TZ).time()
    return dtime(8, 0) <= now <= dtime(23, 0)

# 🧠 פונקציה לתרגום GPT
def translate_missing_fields(csv_path):
    if not OPENAI_API_KEY:
        print("[GPT ❌] אין מפתח API – לא ניתן לבצע תרגום")
        return

    openai.api_key = OPENAI_API_KEY
    updated_rows = []
    try:
        with open(csv_path, 'r', encoding='utf-8', newline='') as infile:
            reader = list(csv.DictReader(infile))
            if not reader:
                print("[GPT] ⚠️ הקובץ ריק או לא נמצא")
                return

            fieldnames = reader[0].keys()
            for row in reader:
                desc = row.get("ProductDesc", "").strip()
                if not desc:
                    print("[GPT] ⏭ דילוג – אין ProductDesc בשורה")
                    updated_rows.append(row)
                    continue

                needs_translation = any(not row.get(col, "").strip() for col in ["Opening", "Title", "Strengths"])
                if not needs_translation:
                    print("[GPT] ⏭ דילוג – שורה כבר מתורגמת")
                    updated_rows.append(row)
                    continue

                prompt = f'''
הפריט הבא מופיע באתר קניות. נא לנסח פוסט שיווקי לטלגרם לפי ההוראות:
1. כתוב משפט פתיחה שיווקי, מצחיק או מגרה שמתאים למוצר (עד 15 מילים).
2. כתוב תיאור שיווקי קצר של המוצר (שורה אחת עד שתיים).
3. הוסף 3 שורות עם יתרונות או תכונות של המוצר, כולל אימוג'ים.

הנה תיאור המוצר:
"{desc}"
'''
                print(f"[GPT] 🧠 מתרגם שורה: {desc[:40]}...")
                try:
                    response = openai.ChatCompletion.create(
                        model="gpt-4",
                        messages=[
                            {"role": "system", "content": "אתה עוזר שיווקי מומחה בכתיבה בעברית"},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.8
                    )
                    reply = response['choices'][0]['message']['content'].strip()
                    print("[GPT ✅] הצלחה בתרגום!")
                    lines = [line.strip() for line in reply.splitlines() if line.strip()]
                    row["Opening"] = lines[0] if len(lines) > 0 else ""
                    row["Title"] = lines[1] if len(lines) > 1 else ""
                    row["Strengths"] = "\n".join(lines[2:5]) if len(lines) >= 5 else ""
                except Exception as e:
                    print(f"[GPT ❌] שגיאה בתרגום: {str(e)}")
                updated_rows.append(row)

        with open(csv_path, 'w', encoding='utf-8', newline='') as outfile:
            writer = csv.DictWriter(outfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(updated_rows)
        print("[GPT] ✔️ תרגום הסתיים בהצלחה")
    except Exception as e:
        print(f"[GPT ❌] שגיאה בטעינת קובץ: {str(e)}")

if __name__ == "__main__":
    print("🚀 התחלת הרצה...")
    translate_missing_fields(PENDING_CSV)
