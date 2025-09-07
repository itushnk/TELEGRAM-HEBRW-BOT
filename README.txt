# v7c – Telegram 502 fix + affiliate-enforced
- Webhook ACK immediately (background thread processes update) -> no 502 Bad Gateway from Telegram.
- Category click answers quickly, heavy work happens after.
- Adds more keywords per category.
- Only affiliate links enter queue (REQUIRE_AFFILIATE=1 by default). Fallback to s.click if AE_AFF_SHORT_KEY set.
