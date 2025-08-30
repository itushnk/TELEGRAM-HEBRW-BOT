
import os
import csv
import random
import requests

# קובץ התור הראשי
QUEUE_FILE = 'queue.csv'

# מפתחות API מסביבת Railway
API_KEY = os.getenv('AE_API_APP_KEY')
API_SECRET = os.getenv('AE_API_APP_SECRET')

# קריאות פתיחה לדוגמה
OPENINGS = [
    'מציאה שאסור לפספס! 🔥',
    'המוצר שיעשה לכם סדר 💡',
    'שדרוג חכם לבית 🏠',
    'נוחות, עיצוב וביצועים 👌',
    'חובה בכל בית 🛒',
    'הפתרון שחיפשתם 🎯',
]

def generate_opening():
    return random.choice(OPENINGS)

def translate_and_format(product):
    # תרגום שיווקי בסיסי לפי תיאור באנגלית
    title = product.get('product_title', '')[:90]
    price = product.get('app_sale_price', '')
    link = product.get('product_detail_url', '')
    image = product.get('product_main_image_url', '')

    return {
        "Opening": generate_opening(),
        "Title": title,
        "Strengths": "✨ מתאים לכל משתמש\n🛠 איכות חומרים גבוהה\n🚚 נשלח לישראל",
        "BuyLink": link,
        "ImageUrl": image,
        "PriceILS": price
    }

def append_to_queue(product_data):
    file_path = QUEUE_FILE
    fieldnames = ['Opening', 'Title', 'Strengths', 'BuyLink', 'ImageUrl', 'PriceILS']

    file_exists = os.path.exists(file_path)
    with open(file_path, mode='a', newline='', encoding='utf-8-sig') as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(product_data)

def fetch_products_by_category(category_id, max_results=5):
    url = "https://api.aliexpress.com/v1/products/search"
    headers = {"X-API-KEY": API_KEY}
    params = {
        "category_id": category_id,
        "target_country": "IL",
        "sort": "orders_desc",
        "page_size": max_results
    }

    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    data = response.json()

    products = data.get('products', [])[:max_results]
    added = 0

    for p in products:
        formatted = translate_and_format(p)
        append_to_queue(formatted)
        added += 1

    return added

if __name__ == "__main__":
    total = fetch_products_by_category(category_id="100003109")  # דוגמה: תאורה
    print(f"שאיבת {total} מוצרים הושלמה")
