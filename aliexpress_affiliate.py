import os
import time

class AliExpressAffiliateClient:
    """
    Minimal shim for AliExpress Affiliates.
    Reads credentials from ENV and exposes a couple of methods used by the bot.
    Replace with real implementation when ready.
    """
    def __init__(self, app_key=None, app_secret=None, tracking_id=None):
        self.app_key = app_key or os.getenv("AE_APP_KEY")
        self.app_secret = app_secret or os.getenv("AE_APP_SECRET")
        self.tracking_id = tracking_id or os.getenv("AE_TRACKING_ID")
        self.lang = os.getenv("AE_TARGET_LANGUAGE", "HE")
        self.currency = os.getenv("AE_TARGET_CURRENCY", "ILS")
        self.ship_to = os.getenv("AE_SHIP_TO_COUNTRY", "IL")

        # Don't fail import; actual API calls will validate.
        if not (self.app_key and self.app_secret):
            # Leave a hint that keys are missing; the bot can still run without AE.
            print("[WARN] AliExpress keys missing; set AE_APP_KEY / AE_APP_SECRET / AE_TRACKING_ID", flush=True)

    def _ensure_ready(self):
        if not (self.app_key and self.app_secret and self.tracking_id):
            raise RuntimeError("Missing AE_APP_KEY / AE_APP_SECRET / AE_TRACKING_ID")

    # --- Public helpers expected by the bot ---
    def search_products(self, keyword: str, page_size: int = 5):
        """Placeholder: integrate the real Open Platform API here.
        For now, just return an empty list if not configured."""
        self._ensure_ready()
        # TODO: implement real API call; keep placeholder for now.
        # Simulate network latency
        time.sleep(0.1)
        return []

    def generate_promotion_link(self, item_id: str):
        """Return a generic product URL; real impl should request promotion link."""
        self._ensure_ready()
        return {"promotion_url": f"https://www.aliexpress.com/item/{item_id}.html"}
