# Hebrew AliExpress Bot (webhook, affiliate-enforced)

## ENV you mentioned
- AE_API_APP_KEY, AE_APP_SECRET, AE_TRACKING_ID, AE_GATEWAY_LIST (optional for library)
- AE_SHIP_TO_COUNTRY, AE_TARGET_CURRENCY, AE_TARGET_LANGUAGE
- BOT_TOKEN (or TELEGRAM_BOT_TOKEN)
- TELEGRAM_WEBHOOK_BASE (e.g. https://your-app.up.railway.app)
- TELEGRAM_WEBHOOK_SECRET (e.g. some-strong-secret)
- USE_WEBHOOK=1
- PUBLIC_CHANNEL (optional, numeric id like -100xxxxxxxxxx)
- POST_DELAY_SECONDS (optional)
- REQUIRE_AFFILIATE=1 (default, only add to queue if affiliate conversion succeeded)

## Verify affiliate
- Use /aff_test <url> — will return ✅ or ⚠️ NO-AFF and the final link.
- On posts, the button text shows “✅ אפילייט” when the link is affiliate-tagged.
