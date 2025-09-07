# Railway Hotfix Autopatch (v6)

מה חדש:
- מוסיף 'global POST_DELAY_SECONDS' (וגם CURRENT_TARGET) לכל פונקציה שמבצעת השמה למשתנים האלה — לא רק ב-on_inline_click.
- משאיר את תיקוני ה-ACK, ה-fallback של AliExpress, והסרת קובץ הנעילה ב-/on.

שימוש:
1) העלה את `autopatch.py` לצד `main.py`.
2) Start Command: `python autopatch.py && python main.py`
3) פרוס מחדש. בדוק בלוגים הודעות [AUTOPATCH].
