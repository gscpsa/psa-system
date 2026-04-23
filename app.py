from flask import Flask, request
import psycopg2
import pandas as pd
import os
import io
import json

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
        submission_number TEXT,
        customer_name TEXT,
        contact_info TEXT,
        status TEXT,
        raw_data JSONB
    );
    """)

    conn.commit()
    cur.close()
    conn.close()

ensure_table()

# ---------- READ FILE ----------
def read_any(file):
    name = file.filename.lower()

    if name.endswith(("xlsx","xls")):
        return pd.read_excel(file)

    raw = file.read()
    file.seek(0)
    text = raw.decode("latin1")
    return pd.read_csv(io.StringIO(text))

def clean(v):
    try:
        if pd.isna(v):
            return ""
    except:
        pass
    return str(v).strip()

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

        for _, row in df.iterrows():
            raw = {c: clean(row[c]) for c in df.columns}

            submission = raw.get("Submission #") or raw.get("Submission Number") or ""
            name = raw.get("Customer Name") or ""
            contact = raw.get("Contact Info") or raw.get("Phone") or raw.get("Email") or ""
            status = raw.get("Current Status") or raw.get("Status") or ""

            if not submission:
                continue

            cur.execute("""
            INSERT INTO submissions (submission_number, customer_name, contact_info, status, raw_data)
            VALUES (%s,%s,%s,%s,%s)
            """, (submission, name, contact, status, json.dumps(raw)))

            inserted += 1

        conn.commit()
        cur.close()
        conn.close()

        return f"Inserted: {inserted}"

    return """
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

    cur.execute("SELECT raw_data FROM submissions LIMIT 2000")
    rows = cur.fetchall()

    html = "<h2>Dashboard</h2><table border=1>"

    # get all keys
    keys = set()
    for r in rows:
        keys.update(r[0].keys())

    keys = list(keys)

    html += "<tr>" + "".join(f"<th>{k}</th>" for k in keys) + "</tr>"

    for r in rows:
        data = r[0]
        html += "<tr>" + "".join(f"<td>{data.get(k,'')}</td>" for k in keys) + "</tr>"

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
        SELECT raw_data FROM submissions
        WHERE
            LOWER(raw_data::text) LIKE LOWER(%s)
        LIMIT 200
        """, (f"%{q}%",))

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
        row = r[0]
        html += "<tr>" + "".join(f"<td>{v}</td>" for v in row.values()) + "</tr>"

    html += "</table>"
    return html

if __name__ == "__main__":
    app.run()
