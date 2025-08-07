
    import os
    import csv
    import openai

    # קביעת מפתח API
    openai.api_key = os.getenv("OPENAI_API_KEY")

    INPUT_FILE = "posts_ready_hebrew_openings.csv"
    OUTPUT_FILE = "posts_ready_hebrew_openings.csv"
    REQUIRED_COLUMNS = ["Opening", "Title", "Strengths"]
    SOURCE_COLUMN = "Product Desc"

    def translate_with_openai(desc):
        prompt = f"""תרגם את תיאור המוצר הבא לעברית בסגנון שיווקי קצר, והוסף שלוש נקודות חוזק:
- תכתוב שורת פתיחה שיווקית מותאמת אישית עם אימוג׳י.
- תכתוב שורת תיאור עד 80 תווים עם אימוג׳ים.
- תכתוב 3 נקודות חוזק – כל אחת בשורה חדשה עם אימוג׳ים.

הטקסט: {desc}
"""

        try:
            response = openai.ChatCompletion.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )
            return response['choices'][0]['message']['content']
        except Exception as e:
            print(f"⚠️ שגיאה בתרגום: {e}")
            return ""

    def ensure_columns_exist(headers):
        for col in REQUIRED_COLUMNS:
            if col not in headers:
                headers.append(col)
        return headers

    def process_csv(input_path, output_path):
        with open(input_path, mode='r', encoding='utf-8', newline='') as infile:
            reader = list(csv.DictReader(infile))
            headers = ensure_columns_exist(reader[0].keys() if reader else REQUIRED_COLUMNS)

        with open(output_path, mode='w', encoding='utf-8', newline='') as outfile:
            writer = csv.DictWriter(outfile, fieldnames=headers)
            writer.writeheader()

            for row in reader:
                source_text = row.get(SOURCE_COLUMN, "").strip()
                if source_text and (not row.get("Opening") or not row.get("Title") or not row.get("Strengths")):
                    print(f"🔄 מתרגם: {source_text[:50]}...")
                    translated = translate_with_openai(source_text)
                    if translated:
                        lines = translated.strip().split("\n")
                        row["Opening"] = lines[0] if len(lines) > 0 else ""
                        row["Title"] = lines[1] if len(lines) > 1 else ""
                        row["Strengths"] = "\n".join(lines[2:]) if len(lines) > 2 else ""
                writer.writerow(row)

    if __name__ == "__main__":
        print("🚀 מתחיל עיבוד תרגומים...")
        process_csv(INPUT_FILE, OUTPUT_FILE)
        print("✅ הסתיים.")
