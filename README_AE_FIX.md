# AliExpress Pull Fix

This module provides `fetch_products_by_category(category_id_or_query, limit=12)`
that your `main.py` imports. It tries AliExpress API first (if keys exist),
then falls back to scraping search results (ShipCountry=IL), and appends
new products into `./data/queue.csv` with UTF-8-SIG and the header:

ItemId,Title,Price,Currency,Url,Image,Category,CreatedAt

## Install
- Upload `aliexpress.py` next to your `main.py` on Railway.
- Ensure the service can write to `./data` (or set `BOT_DATA_DIR` to another path).

## ENV (optional)
- AE_APP_KEY / AE_APP_SECRET (or AE_API_APP_KEY / AE_API_APP_SECRET)
- AE_GATEWAY_LIST="https://gw.api.taobao.com/router/rest,https://eco.taobao.com/router/rest"
- AE_SHIP_TO="IL"
- BOT_CURRENCY="ILS"
- AE_CONNECT_TIMEOUT=10
- AE_READ_TIMEOUT=20
- AE_HTTPS_PROXY=<proxy url> (if needed)
- AE_USE_API_FIRST=0  (# to force scraping first)

## Test
Use your existing buttons to trigger a category pull. Check logs:
[AE][API][WARN] ... or [AE][SCRAPE] ...
The queue count should increase, and /queue view should list the added titles.
