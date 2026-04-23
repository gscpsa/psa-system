from flask import Flask, request
import psycopg2
import pandas as pd
import os, io, json

app = Flask(__name__)
DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

# ---------- TABLE ----------
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
        raw_data JSONB,
        last_updated TIMESTAMP DEFAULT NOW()
    );
    """)

    conn.commit()
    cur.close()
    conn.close()

ensure_table()

# ---------- READ ----------
def read_any(file):
    name = file.filename.lower()

    if name.endswith(("xlsx","xls")):
        return pd.read_excel(file)

    raw = file.read()
    file.seek(0)
    return pd.read_csv(io.StringIO(raw.decode("latin1")))

def clean(v):
    try:
        if pd.isna(v):
            return ""
    except:
        pass
    return str(v).strip()

# ---------- HOME ----------
@app.route("/")
def home():
    return """
    <h2>PSA System</h2>
    <a href="/upload">Upload</a><br>
    <a href="/dashboard">Dashboard</a><br>
    <a href="/search">Search</a>
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
                raw = {c: clean(row[c]) for c in df.columns}

                submission = raw.get("Submission #", "")
                date = raw.get("Submission Date", "")
                name = raw.get("Customer Name", "")
                contact = raw.get("Contact Info", "")
                cards = raw.get("# of Cards", "")
                service = raw.get("Service Type", "")
                cost = raw.get("Est Cost", "")
                prep = raw.get("Prep Needed", "")
                paid = raw.get("Customer Paid", "")
                status = raw.get("Current Status", "")
                declared = raw.get("Declared Value", "")
                notes = raw.get("Notes", "")

                if not submission:
                    errors += 1
                    continue

                cur.execute("""
                INSERT INTO submissions (
                    submission_number, submission_date, customer_name,
                    contact_info, card_count, service_type,
                    est_cost, prep_needed, customer_paid,
                    status, declared_value, notes, raw_data
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (submission_number)
                DO UPDATE SET
                    submission_date=EXCLUDED.submission_date,
                    customer_name=EXCLUDED.customer_name,
                    contact_info=EXCLUDED.contact_info,
                    card_count=EXCLUDED.card_count,
                    service_type=EXCLUDED.service_type,
                    est_cost=EXCLUDED.est_cost,
                    prep_needed=EXCLUDED.prep_needed,
                    customer_paid=EXCLUDED.customer_paid,
                    status=EXCLUDED.status,
                    declared_value=EXCLUDED.declared_value,
                    notes=EXCLUDED.notes,
                    raw_data=EXCLUDED.raw_data,
                    last_updated=NOW()
                """, (
                    submission, date, name, contact, cards, service,
                    cost, prep, paid, status, declared, notes,
                    json.dumps(raw)
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
    SELECT submission_number, submission_date, customer_name, contact_info, status
    FROM submissions
    ORDER BY last_updated DESC
    LIMIT 2000
    """)

    rows = cur.fetchall()

    html = "<h2>Dashboard</h2><table border=1>"
    html += "<tr><th>Submission</th><th>Date</th><th>Name</th><th>Contact</th><th>Status</th></tr>"

    for r in rows:
        html += "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"

    html += "</table>"
    return html

# ---------- SEARCH ----------
@app.route("/search", methods=["GET","POST"])
def search():
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
