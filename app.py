from flask import Flask, request, redirect, url_for, render_template_string
import pandas as pd
import psycopg2
import os
import io
import pdfplumber
import re

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL)

# =========================
# INIT DB (DYNAMIC COLUMNS)
# =========================
def ensure_base_table():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id SERIAL PRIMARY KEY,
            submission_number TEXT UNIQUE,
            status TEXT,
            last_updated TIMESTAMP DEFAULT NOW()
        )
    """)

    conn.commit()
    cur.close()
    conn.close()

ensure_base_table()

# =========================
# ADD MISSING COLUMNS
# =========================
def add_missing_columns(df):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name='submissions'
    """)
    existing = [r[0] for r in cur.fetchall()]

    for col in df.columns:
        col_clean = col.strip().lower().replace(" ", "_")
        if col_clean not in existing:
            cur.execute(f'ALTER TABLE submissions ADD COLUMN "{col_clean}" TEXT')

    conn.commit()
    cur.close()
    conn.close()

# =========================
# UPLOAD CSV / EXCEL
# =========================
@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        file = request.files.get("file")
        if not file:
            return "No file"

        try:
            if file.filename.endswith(".csv"):
                df = pd.read_csv(file, encoding="utf-8", on_bad_lines="skip")
            else:
                df = pd.read_excel(file)
        except:
            try:
                df = pd.read_csv(file, encoding="latin1", on_bad_lines="skip")
            except Exception as e:
                return f"READ ERROR: {e}"

        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

        if "submission_number" not in df.columns:
            return "Missing submission_number column"

        add_missing_columns(df)

        conn = get_conn()
        cur = conn.cursor()

        inserted = 0
        updated = 0
        errors = 0

        for _, row in df.iterrows():
            try:
                sub = str(row.get("submission_number")).strip()

                cur.execute("""
                    INSERT INTO submissions (submission_number)
                    VALUES (%s)
                    ON CONFLICT (submission_number) DO NOTHING
                """, (sub,))

                cols = []
                vals = []

                for col in df.columns:
                    if col == "submission_number":
                        continue
                    cols.append(f'"{col}"=%s')
                    vals.append(str(row[col]))

                if cols:
                    query = f"""
                        UPDATE submissions
                        SET {",".join(cols)}, last_updated=NOW()
                        WHERE submission_number=%s
                    """
                    cur.execute(query, vals + [sub])

                if cur.rowcount > 0:
                    updated += 1
                else:
                    inserted += 1

            except:
                errors += 1

        conn.commit()
        cur.close()
        conn.close()

        return f"Inserted: {inserted} | Updated: {updated} | Errors: {errors}"

    return '''
    <h2>Upload Excel/CSV</h2>
    <form method="post" enctype="multipart/form-data">
        <input type="file" name="file">
        <button>Upload</button>
    </form>
    <br><a href="/">Back</a>
    '''

# =========================
# PSA PDF UPLOAD
# =========================
@app.route("/upload_psa", methods=["GET", "POST"])
def upload_psa():
    if request.method == "POST":
        file = request.files.get("file")
        if not file:
            return "No file"

        text = ""

        try:
            with pdfplumber.open(file) as pdf:
                for page in pdf.pages:
                    text += page.extract_text() + "\n"
        except Exception as e:
            return f"PDF ERROR: {e}"

        matches = re.findall(r"Sub\s*#(\d+)", text)

        statuses = [
            "Order Arrived",
            "Grading",
            "QA Checks",
            "Research & ID",
            "Complete"
        ]

        conn = get_conn()
        cur = conn.cursor()

        updated = 0

        for sub in matches:
            block = re.search(rf"Sub\s*#{sub}(.{{0,200}})", text)
            if not block:
                continue

            found = None
            for s in statuses:
                if s in block.group(1):
                    found = s
                    break

            if not found:
                continue

            cur.execute("""
                UPDATE submissions
                SET status=%s, last_updated=NOW()
                WHERE submission_number=%s
            """, (found, sub))

            if cur.rowcount > 0:
                updated += 1

        conn.commit()
        cur.close()
        conn.close()

        return f"PSA Updated: {updated}"

    return '''
    <h2>Upload PSA PDF</h2>
    <form method="post" enctype="multipart/form-data">
        <input type="file" name="file">
        <button>Upload</button>
    </form>
    <br><a href="/">Back</a>
    '''

# =========================
# SEARCH (ALL FIELDS)
# =========================
@app.route("/search", methods=["GET"])
def search():
    q = request.args.get("q", "")

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT * FROM submissions
        WHERE submission_number ILIKE %s
        OR status ILIKE %s
        OR CAST(submission_number AS TEXT) ILIKE %s
        LIMIT 50
    """, (f"%{q}%", f"%{q}%", f"%{q}%"))

    rows = cur.fetchall()
    cols = [desc[0] for desc in cur.description]

    cur.close()
    conn.close()

    table = "<table border=1><tr>"
    for c in cols:
        table += f"<th>{c}</th>"
    table += "</tr>"

    for r in rows:
        table += "<tr>"
        for v in r:
            table += f"<td>{v}</td>"
        table += "</tr>"

    table += "</table>"

    return f'''
    <form>
        <input name="q" value="{q}">
        <button>Search</button>
    </form>
    {table}
    <br><a href="/">Back</a>
    '''

# =========================
# DASHBOARD
# =========================
@app.route("/")
def dashboard():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM submissions ORDER BY id DESC LIMIT 50")
    rows = cur.fetchall()
    cols = [desc[0] for desc in cur.description]

    cur.close()
    conn.close()

    table = "<table border=1><tr>"
    for c in cols:
        table += f"<th>{c}</th>"
    table += "</tr>"

    for r in rows:
        table += "<tr>"
        for v in r:
            table += f"<td>{v}</td>"
        table += "</tr>"

    table += "</table>"

    return f'''
    <h2>Dashboard</h2>
    <a href="/upload">Upload Excel</a> |
    <a href="/upload_psa">Upload PSA PDF</a> |
    <a href="/search">Search</a>
    <br><br>
    {table}
    '''
