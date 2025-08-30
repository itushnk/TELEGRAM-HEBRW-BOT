
import os
import csv
import requests

QUEUE_FILE = "queue.csv"

# Load API credentials from Railway environment variables
APP_KEY = os.getenv("AE_API_APP_KEY")
APP_SECRET = os.getenv("AE_API_APP_SECRET")

ALIEXPRESS_API_URL = "https://api.aliexpress.com/v1/products/search"

def import_affiliate_products(category_id="100003109", max_results=5):
    if not APP_KEY:
        print("❌ APP_KEY is missing in environment variables.")
        return False

    headers = {"X-API-KEY": APP_KEY}
    params = {
        "category_id": category_id,
        "target_country": "IL",
        "page_size": max_results,
        "sort": "orders_desc",
    }

    try:
        response = requests.get(ALIEXPRESS_API_URL, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"❌ API Error: {e}")
        return False

    products = data.get("products", [])
    if not products:
        print("ℹ️ No products received from AliExpress API.")
        return False

    fieldnames = [
        "ProductId", "Image Url", "Video Url", "Product Desc", "Origin Price",
        "Discount Price", "Discount", "Promotion Url", "CouponCode",
        "Opening", "Title", "Strengths"
    ]

    file_exists = os.path.exists(QUEUE_FILE)
    with open(QUEUE_FILE, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()

        for p in products:
            writer.writerow({
                "ProductId": p.get("product_id", ""),
                "Image Url": p.get("product_main_image_url", ""),
                "Video Url": "",
                "Product Desc": p.get("product_title", ""),
                "Origin Price": p.get("original_price", ""),
                "Discount Price": p.get("app_sale_price", ""),
                "Discount": p.get("discount", ""),
                "Promotion Url": p.get("product_detail_url", ""),
                "CouponCode": "",
                "Opening": "",
                "Title": p.get("product_title", ""),
                "Strengths": "✨ איכות גבוהה\n🚚 נשלח לישראל\n🔥 מוצר פופולרי"
            })

    print(f"✅ Added {len(products)} products to queue.csv")
    return True
