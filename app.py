from flask import Flask, request, redirect
import psycopg2
import pandas as pd
import os
import io

app = Flask(__name__)
DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

# ---------- SAFE TABLE ----------
def ensure_table():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS submissions (
        id SERIAL PRIMARY KEY,
        submission_number TEXT UNIQUE,
        submission_date TEXT,
        customer_name TEXT,
        contact_info TEXT,
        card_count TEXT,
        service_type TEXT,
        est_cost TEXT,
        prep_needed TEXT,
        customer_paid TEXT,
        status TEXT,
        declared_value TEXT,
        notes TEXT,
        last_updated TIMESTAMP DEFAULT NOW()
    );
    """)

    conn.commit()
    cur.close()
    conn.close()

ensure_table()

# ---------- HELPERS ----------
def clean(val):
    try:
        if pd.isna(val):
            return ""
    except:
        pass
    return str(val).strip()

def read_any(file):
    name = file.filename.lower()

    if name.endswith(("xlsx","xls")):
        return pd.read_excel(file)

    raw = file.read()
    file.seek(0)

    text = raw.decode("latin1")

    return pd.read_csv(io.StringIO(text))

# ---------- STRONG COLUMN DETECTION ----------
def extract_row(row):

    data = {
        "submission_number": "",
        "customer_name": "",
        "contact_info": "",
        "status": "",
        "service_type": "",
        "card_count": ""
    }

    for key in row.keys():
        k = str(key).strip().lower()
        v = clean(row.get(key))

        # --- submission (highest priority) ---
        if any(x in k for x in ["submission", "#", "id"]) and not data["submission_number"]:
            data["submission_number"] = v

        # --- name ---
        elif "customer name" in k or (k == "name"):
            data["customer_name"] = v

        # --- contact ---
        elif any(x in k for x in ["email","phone","contact"]):
            data["contact_info"] = v

        # --- status ---
        elif "status" in k:
            data["status"] = v

        # --- service ---
        elif "service" in k:
            data["service_type"] = v

        # --- cards ---
        elif "card" in k:
            data["card_count"] = v

    return data

# ---------- HOME ----------
@app.route("/")
def home():
    return """
    <h2>PSA System</h2>
    <a href="/upload">Upload</a><br>
    <a href="/dashboard">Dashboard</a><br>
    <a href="/staff">Staff Search</a>
    """

# ---------- UPLOAD ----------
@app.route("/upload", methods=["GET","POST"])
def upload():
    if request.method == "POST":
        file = request.files.get("file")

        df = read_any(file)
        df.columns = [str(c).strip() for c in df.columns]

        conn = get_conn()
        cur = conn.cursor()

        inserted = 0
        errors = 0

        for _, row in df.iterrows():
            try:
                d = extract_row(row)

                if not d["submission_number"]:
                    errors += 1
                    continue

                cur.execute("""
                INSERT INTO submissions
                (submission_number, customer_name, contact_info, card_count, service_type, status)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (submission_number)
                DO UPDATE SET
                    customer_name=EXCLUDED.customer_name,
                    contact_info=EXCLUDED.contact_info,
                    card_count=EXCLUDED.card_count,
                    service_type=EXCLUDED.service_type,
                    status=EXCLUDED.status
                """, (
                    d["submission_number"],
                    d["customer_name"],
                    d["contact_info"],
                    d["card_count"],
                    d["service_type"],
                    d["status"]
                ))

                inserted += 1

            except Exception as e:
                print("ROW ERROR:", e)
                errors += 1

        conn.commit()
        cur.close()
        conn.close()

        return f"Inserted: {inserted} | Errors: {errors}"

    return """
    <h3>Upload</h3>
    <form method="post" enctype="multipart/form-data">
    <input type="file" name="file">
    <button>Upload</button>
    </form>
    """

# ---------- DASHBOARD ----------
@app.route("/dashboard")
def dashboard():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    SELECT submission_number, customer_name, contact_info, status
    FROM submissions
    ORDER BY id DESC
    LIMIT 2000
    """)

    rows = cur.fetchall()

    html = "<h2>Dashboard</h2><table border=1>"
    html += "<tr><th>Submission</th><th>Name</th><th>Contact</th><th>Status</th></tr>"

    for r in rows:
        html += "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"

    html += "</table>"

    return html

# ---------- SEARCH ----------
@app.route("/staff", methods=["GET","POST"])
def staff():
    results = []

    if request.method == "POST":
        q = request.form["q"]

        conn = get_conn()
        cur = conn.cursor()

        cur.execute("""
        SELECT submission_number, customer_name, contact_info, status
        FROM submissions
        WHERE
            LOWER(customer_name) LIKE LOWER(%s)
            OR LOWER(contact_info) LIKE LOWER(%s)
            OR LOWER(submission_number) LIKE LOWER(%s)
        LIMIT 200
        """, (f"%{q}%", f"%{q}%", f"%{q}%"))

        results = cur.fetchall()

    html = """
    <h3>Search</h3>
    <form method="post">
    <input name="q">
    <button>Search</button>
    </form>
    <table border=1>
    """

    for r in results:
        html += "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"

    html += "</table>"
    return html

if __name__ == "__main__":
    app.run()
