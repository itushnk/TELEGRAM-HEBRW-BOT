
import csv
import os
import threading

FILE_LOCK = threading.Lock()

def load_posts_from_csv(file_path):
    posts = []
    with FILE_LOCK:
        with open(file_path, 'r', encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            # ודא שכל העמודות הדרושות קיימות
            required_fields = ["Opening", "Title", "Strengths"]
            for row in rows:
                for field in required_fields:
                    if field not in row:
                        row[field] = ""
            posts = rows

        # שמור מחדש את הקובץ עם העמודות החסרות אם נוספו
        fieldnames = reader.fieldnames or []
        for field in required_fields:
            if field not in fieldnames:
                fieldnames.append(field)

    # כתיבה חזרה עם כל העמודות
    with FILE_LOCK:
        with open(file_path, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(posts)

    return posts

# דוגמה לשימוש
if __name__ == "__main__":
    file_path = "posts_ready_hebrew_openings.csv"
    if os.path.exists(file_path):
        print("טוען קובץ...")
        posts = load_posts_from_csv(file_path)
        print(f"הועלו {len(posts)} פוסטים")
    else:
        print("קובץ לא נמצא")
