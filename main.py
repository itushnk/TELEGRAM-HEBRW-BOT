from flask import Flask, render_template, request, redirect
import pandas as pd
import os
import time
import telegram
import schedule
import threading

app = Flask(__name__)

CSV_PATH = "products_queue_managed.csv"
BOT_TOKEN = os.environ.get("Here is the token for bot בוט כולל תרגום גרסא 22 @hebrew22_bot:

8301372230:AAFWDmtNw9cbl5qu-7LffO5dSD2HNXcE52E")
CHANNEL_ID = os.environ.get("@NISAYON121")
POST_INTERVAL_MINUTES = 20

bot = telegram.Bot(token=BOT_TOKEN)

def load_posts():
    if not os.path.exists(CSV_PATH):
        return pd.DataFrame()
    df = pd.read_csv(CSV_PATH, encoding="utf-8-sig")
    return df

def save_posts(df):
    df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")

def load_pending_posts():
    df = load_posts()
    pending = df[df['Status'] == 'pending'].head(10)
    return pending.to_dict(orient='records')

def update_status(item_index, status):
    df = load_posts()
    df.at[item_index, 'Status'] = status
    save_posts(df)

def get_next_approved_post():
    df = load_posts()
    approved = df[df['Status'] == 'approved']
    if approved.empty:
        return None, df
    next_index = approved.index[0]
    return df.loc[next_index], df

def post_to_channel():
    post, df = get_next_approved_post()
    if post is None:
        print("No approved posts to send.")
        return
    try:
        text = post['PostText']
        image_url = post['ImageURL']
        bot.send_photo(chat_id=CHANNEL_ID, photo=image_url, caption=text, parse_mode=telegram.constants.ParseMode.HTML)
        df.at[post.name, 'Status'] = 'posted'
        save_posts(df)
        print(f"✅ Posted: {post['ProductId']}")
    except Exception as e:
        print(f"❌ Error posting: {e}")

def run_schedule():
    schedule.every(POST_INTERVAL_MINUTES).minutes.do(post_to_channel)
    while True:
        schedule.run_pending()
        time.sleep(1)

@app.route('/')
def index():
    posts = load_pending_posts()
    return render_template('index.html', posts=posts)

@app.route('/update_status', methods=['POST'])
def update():
    index = int(request.form['index'])
    action = request.form['action']
    if action == 'approve':
        update_status(index, 'approved')
    elif action == 'reject':
        update_status(index, 'rejected')
    return redirect('/')

if __name__ == '__main__':
    threading.Thread(target=run_schedule).start()
    app.run(debug=True, host='0.0.0.0', port=8080)
