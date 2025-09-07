# v8 upgrade — IL+1000 discovery & post template

**New features**
- Filters: orders ≥ 1000, ship_to_country=IL, sorted by highest orders
- Wide category coverage via `categories.json` (add your niches & sub-niches)
- Coupon extraction (best-effort) and insertion into the post
- Hebrew post template matched to the example you sent

**Env vars**
- TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL (e.g. @yourchannel or -100...)
- AE_KEY, AE_SECRET, AE_TRACKING_ID (AliExpress Open Platform Affiliate)
- WEBHOOK_BASE_URL, WEBHOOK_SECRET (default `/webhook/secret`)
- TARGET_CURRENCY=ILS (default), TARGET_LANGUAGE=HE (default)
- IL_MIN_ORDERS=1000

**Usage**
1. Deploy and set envs.
2. Call `/discover` in your bot (collects candidates).
3. Call `/post` to send the top product to your channel.
4. Edit `categories.json` to expand niches.
