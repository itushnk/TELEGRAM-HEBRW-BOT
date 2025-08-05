# Telegram Post Moderator

מערכת לניהול פוסטים לטלגרם עם אישור ודחייה דרך דפדפן ודחייה אוטומטית כל 20 דקות.

## התקנה
1. העלה את הקבצים ל־GitHub
2. חבר את הריפו ל־[Railway](https://railway.app)
3. הגדר משתני סביבה:
   - `BOT_TOKEN` = טוקן של הבוט
   - `CHANNEL_ID` = מזהה ערוץ טלגרם (@yourchannel או מזהה מספרי)

## קובץ CSV
ודא שקיים קובץ בשם `products_queue_managed.csv` עם שדות: `ProductId`, `PostText`, `ImageURL`, `Status`

## הפעלה
ברגע שעלית ל־Railway – האפליקציה רצה אוטומטית.
