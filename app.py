from flask import Flask, request
import psycopg2
import pandas as pd
import os
import io

app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

# ---------- ONE-TIME SETUP (resets table to match code) ----------
def setup_database():
    conn = get_conn()
    cur = conn.cursor()

    # ⚠️ This will DROP existing data (safe if your source is your file)
    cur.execute("DROP TABLE IF EXISTS submissions;")

    cur.execute("""
    CREATE TABLE submissions (
        id SERIAL PRIMARY KEY,
        submission_number TEXT UNIQUE,
        customer_name TEXT,
        contact_info TEXT,
        card_count TEXT,
        service_type TEXT,
        status TEXT
    );
    """)

    conn.commit()
    cur.close()
    conn.close()

# Run once at startup
setup_database()

# ---------- HELPERS ----------
def clean(val):
    try:
        if pd.isna(val):
            return ""
    except:
        pass
    return str(val).strip()

def read_any(file_storage):
    """
    Read CSV or Excel with encoding + delimiter fallback.
    """
    filename = (file_storage.filename or "").lower()

    # Excel
    if filename.endswith((".xlsx", ".xls")):
        return pd.read_excel(file_storage)

    # CSV
    raw = file_storage.read()
    file_storage.seek(0)

    # encoding fallback
    text = None
    for enc in ("utf-8", "latin1"):
        try:
            text = raw.decode(enc)
            break
        except:
            continue
    if text is None:
        raise Exception("Unable to decode file")

    # delimiter fallback
    for sep in (",", ";", "\t"):
        try:
            return pd.read_csv(io.StringIO(text), sep=sep)
        except:
            continue

    raise Exception("Unable to parse CSV (delimiter issue)")

def extract_fields(row):
    """
    Dynamically find fields from messy headers.
    """
    submission = ""
    name = ""
    contact = ""
    status = ""
    service = ""
    count = None

    for key in row.keys():
        k = str(key).lower()

        if not submission and "submission" in k:
            submission = clean(row.get(key))
        elif not name and ("name" in k):
            name = clean(row.get(key))
        elif not contact and ("contact" in k or "phone" in k):
            contact = clean(row.get(key))
        elif not status and ("status" in k):
            status = clean(row.get(key))
        elif not service and ("service" in k):
            service = clean(row.get(key))
        elif count is None and ("card" in k):
            count = row.get(key)

    return submission, name, contact, count, service, status

# ---------- ROUTES ----------
@app.route("/")
def home():
    return """
    <h2>PSA System</h2>
    <a href="/upload">Upload File</a><br><br>
    <a href="/search">Staff Search</a>
    """

@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        file = request.files.get("file")
        if not file:
            return "No file uploaded"

        try:
            df = read_any(file)
        except Exception as e:
            return f"FILE READ ERROR: {str(e)}"

        inserted = 0
        updated = 0
        errors = 0

        conn = get_conn()
        cur = conn.cursor()

        for _, row in df.iterrows():
            try:
                submission, name, contact, count, service, status = extract_fields(row)

                if not submission:
                    errors += 1
                    continue

                cur.execute("""
                    INSERT INTO submissions
                    (submission_number, customer_name, contact_info, card_count, service_type, status)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (submission_number)
                    DO UPDATE SET
                        customer_name = EXCLUDED.customer_name,
                        contact_info = EXCLUDED.contact_info,
                        card_count = EXCLUDED.card_count,
                        service_type = EXCLUDED.service_type,
                        status = EXCLUDED.status
                """, (submission, name, contact, count, service, status))

                # rowcount is 1 for insert or update in this pattern
                # we can approximate:
                if cur.rowcount == 1:
                    inserted += 1
                else:
                    updated += 1

            except Exception as e:
                print("ROW ERROR:", e)
                errors += 1

        conn.commit()
        cur.close()
        conn.close()

        return f"""
        <h3>Upload Results</h3>
        Inserted: {inserted}<br>
        Updated: {updated}<br>
        Errors: {errors}<br>
        <a href="/">Back</a>
        """

    return """
    <h2>Upload CSV or Excel</h2>
    <form method="post" enctype="multipart/form-data">
        <input type="file" name="file">
        <button type="submit">Upload</button>
    </form>
    <br><a href="/">Back</a>
    """

@app.route("/search", methods=["GET", "POST"])
def search():
    results = []

    if request.method == "POST":
        query = request.form.get("query", "")

        conn = get_conn()
        cur = conn.cursor()

        cur.execute("""
            SELECT submission_number, customer_name, contact_info, status
            FROM submissions
            WHERE
                submission_number ILIKE %s OR
                customer_name ILIKE %s OR
                contact_info ILIKE %s
            LIMIT 100
        """, (f"%{query}%", f"%{query}%", f"%{query}%"))

        results = cur.fetchall()

        cur.close()
        conn.close()

    html = """
    <h2>Staff Search</h2>
    <form method="post">
        <input name="query" placeholder="Search anything">
        <button type="submit">Search</button>
    </form>
    <br>
    <table border=1>
        <tr>
            <th>Submission</th>
            <th>Name</th>
            <th>Contact</th>
            <th>Status</th>
        </tr>
    """

    for r in results:
        html += f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td></tr>"

    html += "</table><br><a href='/'>Back</a>"

    return html


if __name__ == "__main__":
    app.run()
