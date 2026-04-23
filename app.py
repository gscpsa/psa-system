from flask import Flask, request
import pandas as pd
import psycopg2
import os, io, json, re

app = Flask(__name__)
DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

# =========================
# INIT TABLE (SAFE)
# =========================
def ensure_table():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id SERIAL PRIMARY KEY,
            submission_number TEXT UNIQUE,
            status TEXT,
            raw_data JSONB,
            last_updated TIMESTAMP DEFAULT NOW()
        )
    """)

    conn.commit()
    cur.close()
    conn.close()

ensure_table()

# =========================
# HELPERS
# =========================
def clean(v):
    try:
        if pd.isna(v):
            return ""
    except:
        pass
    return str(v).strip()

def read_file(file):
    name = file.filename.lower()

    if name.endswith(("xlsx","xls")):
        return pd.read_excel(file)

    raw = file.read()
    file.seek(0)

    try:
        return pd.read_csv(io.StringIO(raw.decode("utf-8")), on_bad_lines="skip")
    except:
        return pd.read_csv(io.StringIO(raw.decode("latin1")), on_bad_lines="skip")

# =========================
# UPLOAD EXCEL
# =========================
@app.route("/upload", methods=["GET","POST"])
def upload():
    if request.method == "POST":
        file = request.files.get("file")

        if not file:
            return "No file uploaded"

        try:
            df = read_file(file)
        except Exception as e:
            return f"READ ERROR: {e}"

        df.columns = [str(c).strip() for c in df.columns]

        conn = get_conn()
        cur = conn.cursor()

        inserted = 0
        errors = 0

        for _, row in df.iterrows():
            try:
                raw = {c: clean(row[c]) for c in df.columns}

                submission = raw.get("Submission #") or raw.get("Submission Number") or ""

                if not submission:
                    errors += 1
                    continue

                cur.execute("""
                    INSERT INTO submissions (submission_number, raw_data)
                    VALUES (%s,%s)
                    ON CONFLICT (submission_number)
                    DO UPDATE SET
                        raw_data = EXCLUDED.raw_data,
                        last_updated = NOW()
                """, (submission, json.dumps(raw)))

                inserted += 1

            except Exception as e:
                print("ROW ERROR:", e)
                errors += 1

        conn.commit()
        cur.close()
        conn.close()

        return f"Inserted/Updated: {inserted} | Errors: {errors}"

    return """
    <h3>Upload Excel/CSV</h3>
    <form method="post" enctype="multipart/form-data">
        <input type="file" name="file">
        <button>Upload</button>
    </form>
    <br><a href="/">Dashboard</a>
    """

# =========================
# PSA PDF UPLOAD (SAFE)
# =========================
@app.route("/upload_psa", methods=["GET","POST"])
def upload_psa():
    if request.method == "POST":
        file = request.files.get("file")

        if not file:
            return "No file uploaded"

        try:
            import pdfplumber
        except:
            return "ERROR: pdfplumber not installed"

        text = ""

        try:
            with pdfplumber.open(file) as pdf:
                for page in pdf.pages:
                    t = page.extract_text()
                    if t:
                        text += t + "\n"
        except Exception as e:
            return f"PDF ERROR: {e}"

        if not text:
            return "ERROR: No readable text in PDF"

        subs = re.findall(r"Sub\s*#(\d+)", text)

        if not subs:
            return "ERROR: No submission numbers found"

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

        for sub in subs:
            try:
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

            except Exception as e:
                print("PSA ROW ERROR:", e)
                continue

        conn.commit()
        cur.close()
        conn.close()

        return f"PSA Updated: {updated}"

    return """
    <h3>Upload PSA PDF</h3>
    <form method="post" enctype="multipart/form-data">
        <input type="file" name="file">
        <button>Upload</button>
    </form>
    <br><a href="/">Dashboard</a>
    """

# =========================
# DASHBOARD (CLEAN)
# =========================
@app.route("/")
def dashboard():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT raw_data, status FROM submissions ORDER BY id DESC LIMIT 200")
    rows = cur.fetchall()

    cur.close()
    conn.close()

    keys = set()
    data_rows = []

    for r in rows:
        data = r[0] or {}

        clean_row = {
            k: v for k, v in data.items()
            if not str(k).lower().startswith("unnamed")
        }

        if r[1]:
            clean_row["PSA Status"] = r[1]

        data_rows.append(clean_row)
        keys.update(clean_row.keys())

    preferred = [
        "Submission #",
        "S",
        "Submission Date",
        "Customer Name",
        "Contact Info",
        "# Of Cards",
        "Service Type",
        "Est Cost",
        "Prep Needed",
        "Customer Paid",
        "Current Status",
        "PSA Status",
        "Declared Value",
        "Notes"
    ]

    ordered = [k for k in preferred if k in keys]
    ordered += [k for k in keys if k not in ordered]

    html = """
    <div style="position:sticky;top:0;background:white;padding:10px;border-bottom:2px solid black;">
        <strong>PSA System</strong> |
        <a href="/upload">Upload Excel</a> |
        <a href="/upload_psa">Upload PSA PDF</a> |
        <a href="/search">Search</a>
    </div>
    <br>
    """

    html += "<table border=1><tr>"

    for k in ordered:
        html += f"<th>{k}</th>"
    html += "</tr>"

    for row in data_rows:
        html += "<tr>"
        for k in ordered:
            html += f"<td>{row.get(k,'')}</td>"
        html += "</tr>"

    html += "</table>"

    return html

# =========================
# SEARCH
# =========================
@app.route("/search")
def search():
    q = request.args.get("q","")

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT raw_data FROM submissions
        WHERE raw_data::text ILIKE %s
        LIMIT 50
    """, (f"%{q}%",))

    rows = cur.fetchall()

    cur.close()
    conn.close()

    html = f"""
    <form>
        <input name="q" value="{q}">
        <button>Search</button>
    </form>
    <table border=1>
    """

    for r in rows:
        data = r[0] or {}
        html += "<tr>"
        for v in data.values():
            html += f"<td>{v}</td>"
        html += "</tr>"

    html += "</table><br><a href='/'>Back</a>"

    return html

# =========================
if __name__ == "__main__":
    app.run()
