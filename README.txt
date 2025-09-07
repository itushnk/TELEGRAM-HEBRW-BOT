# v7d – stronger discovery + webhook 502 fix
- Tries AliExpress mobile endpoints first, then desktop variants, then DuckDuckGo HTML.
- Immediate webhook ACK (background thread) avoids Telegram 502.
- Queue CSV (data/pending.csv) auto-created.
- Only affiliate links go into queue (REQUIRE_AFFILIATE=1). Fallback to s.click if AE_AFF_SHORT_KEY is set.
