# Railway Hotfix Autopatch (v7-fix3)

מטרה: לתקן שברי קוד שנוצרו כשנכנסו רצפי `\n` *מילוליים* במקום ירידות שורה אמיתיות (למשל ב-`toggle_bot_lock`), מה שגרם
ל-`SyntaxError: unexpected character after line continuation character`.

מה זה עושה?
- עובר על כל פונקציה בקובץ וממיר `\n` עם רווחים שאחריהם (`return`, `except`, וכו') לירידת שורה אמיתית.
- בנוסף, מתקן כמה תבניות ברמה העליונה אם קיימות.

שימוש:
1) העלה `autopatch.py` לצד `main.py`.
2) Start Command: `python autopatch.py && python main.py`
