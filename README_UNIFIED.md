# Unified Fix: main.py + aliexpress.py

מה בפנים
- `main.py` — מתוקן:
  - `global POST_DELAY_SECONDS, CURRENT_TARGET` בתחילת `on_inline_click`
  - ACK מיידי ללחיצת קטגוריה ("⏳ שואב פריטים…")
  - החלפת `answer_callback_query` מאוחרות ב־`send_message` בתוך בלוק הקטגוריות (כדי למנוע 400 "query is too old")
- `aliexpress.py` — שאיבה מעליאקספרס: API-קודם (אם יש מפתחות), נפילה ל-scrape, הוספה ל-`./data/queue.csv`

מה צריך בסביבה (אופציונלי):
- `AE_USE_API_FIRST=0` כדי להתחיל מ-scrape אם ה-API חסום
- `AE_SHIP_TO=IL`, `BOT_CURRENCY=ILS`
- `AE_CONNECT_TIMEOUT=10`, `AE_READ_TIMEOUT=20`
- `AE_GATEWAY_LIST=https://gw.api.taobao.com/router/rest,https://eco.taobao.com/router/rest`
- במידת הצורך: `AE_HTTPS_PROXY=https://user:pass@host:port`

פריסה
1) החלף את `main.py` בפרויקט שלך בזה שבחבילה.
2) הוסף את `aliexpress.py` לאותה תיקייה של `main.py`.
3) Restart לשירות ב-Railway.
4) `/start` → לחץ קטגוריה → תראה חיווי מיידי ואחרי כמה שניות הודעת סיום/שגיאה.
