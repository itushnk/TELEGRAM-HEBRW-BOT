
import csv
import openai

# הגדר את מפתח ה־API שלך כאן (או דרך משתנה סביבה)
openai.api_key = os.getenv("OPENAI_API_KEY")

INPUT_FILE = "posts_ready_hebrew_openings.csv"
OUTPUT_FILE = "posts_ready_hebrew_openings.csv"

def translate_if_needed(row):
    desc = row.get("Product Desc", "").strip()
    opening = row.get("Opening", "").strip()
    title = row.get("Title", "").strip()
    strengths = row.get("Strengths", "").strip()

    if not desc or (opening and title and strengths):
        return row  # אין צורך בתרגום

    try:
        completion = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "תרגם תיאור שיווקי בעברית כולל פתיח עם אימוג׳י, תיאור מוצר ושורת יתרונות"},
                {"role": "user", "content": f"תאר לי את המוצר הבא בעברית בפורמט: פתיח שנון (Opening), תיאור קצר (Title), ושלוש נקודות חוזק (Strengths):\n\n{desc}"}
            ],
            temperature=0.7
        )
        response_text = completion.choices[0].message.content.strip()
        lines = response_text.split("\n")
        row["Opening"] = lines[0].strip()
        row["Title"] = lines[1].strip()
        row["Strengths"] = "\n".join(lines[2:]).strip()
        print(f"✅ תורגם: {row['Title'][:40]}...")
    except Exception as e:
        print(f"❌ שגיאת תרגום בשורה: {e}")
    return row

def main():
    try:
        with open(INPUT_FILE, newline='', encoding='utf-8') as infile:
            reader = csv.DictReader(infile)
            rows = list(reader)
            fieldnames = reader.fieldnames

        translated_rows = [translate_if_needed(row) for row in rows]

        with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8') as outfile:
            writer = csv.DictWriter(outfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(translated_rows)

        print("✅ הסתיים תרגום ושמירה של הקובץ.")
    except Exception as e:
        print(f"❌ שגיאה כללית: {e}")

if __name__ == "__main__":
    main()
