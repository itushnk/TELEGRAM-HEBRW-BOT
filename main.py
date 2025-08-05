
from flask import Flask, render_template
import pandas as pd

app = Flask(__name__)

@app.route("/")
def index():
    try:
        df = pd.read_csv("products_queue_managed.csv")
        posts = df.to_dict(orient='records')
    except Exception as e:
        posts = []
    return render_template("index.html", posts=posts)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")
