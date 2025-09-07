# Railway Hotfix Autopatch (v7)

- מוסיף global לכל פונקציה שמכילה POST_DELAY_SECONDS/CURRENT_TARGET (גם אם רק קוראת ואז כותבת).
- מנקה data/bot.lock בזמן עלייה כדי לצאת ממצב "הבוט כבוי".
- ACK מהיר לקליק על קטגוריה + fallback ל-AE + הוספה לתור.
- מוסיף פקודת /status להצגת סטטוס מהיר.

שימוש:
1) העלה את `autopatch.py` לצד `main.py`.
2) Start Command: `python autopatch.py && python main.py`
