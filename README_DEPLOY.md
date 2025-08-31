
# Telegram Bot via Webhook (Railway)

## 1) Variables (Railway → Variables)
- TELEGRAM_BOT_TOKEN: <הטוקן החדש/קיים>
- TELEGRAM_WEBHOOK_SECRET: <מחרוזת סודית כלשהי> (מומלץ)
- אחד מאלה כדי לבנות URL:
  - TELEGRAM_WEBHOOK_URL: https://YOUR-DOMAIN/webhook/<id>
  - TELEGRAM_WEBHOOK_BASE: https://YOUR-DOMAIN   (הקוד יוסיף /webhook/<id>)
  - או השאר ריק ותן ל-RAILWAY_STATIC_URL / RAILWAY_PUBLIC_DOMAIN לבנות כתובת אוטומטית.
- אופציונלי:
  - BOT_START_LOCKED=1  (אם תרצה לעלות כבוי)
  - BOT_DATA_DIR=./data (ברירת מחדל)

## 2) Start Command
```
python main.py
```

## 3) בדיקות
- אחרי Deploy, בדוק בלוגים שיש:
  - `[WH] set_webhook -> True url=https://.../webhook/<id>`
  - `[BOOT] Starting Flask on 0.0.0.0:...`
- בצ'אט:
  - `/ping` → pong ✅
  - `/start` → תפריט
  - כפתורים → נכון לעבוד, ותראה בלוגים עם `[CB] data=...`

## הערות
- Webhook מחליף polling, ואין יותר שגיאות 409.
- אם אתה עדיין רואה 409 במקום אחר, סביר שמופע polling ישן עדיין רץ בסביבה אחרת. החלפת טוקן אצל BotFather (/revoke) תפתור זאת מיידית.
