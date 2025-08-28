# Telegram Affiliate Bot – Minimal Data Skeleton

This repo includes an empty `data/queue.csv` so the bot can start without errors.

## Files
- `data/queue.csv` — headers only:
  `ProductId,Image Url,Product Desc,Opening,Title,Strengths,Promotion Url`
- `data/keywords.txt` — optional (leave empty or put search terms per line)
- `data/auto_mode.flag` — "on" enables auto mode; write "off" to disable

> Make sure your environment variables are set (BOT_TOKEN, CHANNEL_ID, AE_APP_KEY, AE_APP_SECRET, AE_TRACKING_ID).