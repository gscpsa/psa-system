from flask import Flask, request
import pandas as pd
import psycopg2
import os, io, json, re, traceback

app = Flask(__name__)

# =========================
# DB CONNECTION (SAFE)
# =========================
DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    if not DATABASE_URL:
        raise Exception("DATABASE_URL is not set in environment")
    return psycopg2.connect(DATABASE_URL, sslmode="require")

# =========================
# INIT TABLE
# =========================
def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS submissions (
        submission_number TEXT PRIMARY KEY,
        status TEXT,
        raw_data JSONB,
        last_updated TIMESTAMP DEFAULT NOW()
    )
    """)

    conn.commit()
    cur.close()
    conn.close()

init_db()

# =========================
# GLOBAL ERROR HANDLER
# =========================
@app.errorhandler(Exception)
def handle_error(e):
    return f"""
    <h3>APP ERROR</h3>
    <pre>{str(e)}</pre>
    <pre>{traceback.format_exc()}</pre>
    """

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

def save_row(submission, raw):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO submissions (submission_number, raw_data)
    VALUES (%s,%s)
    ON CONFLICT (submission_number)
    DO UPDATE SET
        raw_data = EXCLUDED.raw_data,
        last_updated = NOW()
    """, (submission, json.dumps(raw)))

    conn.commit()
    cur.close()
    conn.close()

# =========================
# DASHBOARD
# =========================
@app.route("/")
def dashboard():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT raw_data, status FROM submissions ORDER BY last_updated DESC LIMIT 200")
    rows = cur.fetchall()

    cur.close()
    conn.close()

    keys = set()
    clean_rows = []

    for r in rows:
        data = r[0] or {}

        row = {
            k: v for k, v in data.items()
            if not str(k).lower().startswith("unnamed")
        }

        if r[1]:
            row["PSA Status"] = r[1]

        clean_rows.append(row)
        keys.update(row.keys())

    ordered = sorted(keys)

    html = """
    <div style="position:sticky;top:0;background:white;padding:10px;border-bottom:2px solid black;">
        <b>PSA System</b> |
        <a href="/upload">Upload Excel</a> |
        <a href="/upload_psa">Upload PSA PDF</a> |
        <a href="/search">Search</a>
    </div><br>
    """

    html += "<table border=1><tr>"
    for k in ordered:
        html += f"<th>{k}</th>"
    html += "</tr>"

    for row in clean_rows:
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
    LIMIT 100
    """, (f"%{q}%",))

    rows = cur.fetchall()

    cur.close()
    conn.close()

    html = f"""
    <form>
        <input name="q" value="{q}">
        <button>Search</button>
    </form><br>
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
# EXCEL UPLOAD
# =========================
@app.route("/upload", methods=["GET","POST"])
def upload():
    if request.method == "POST":
        file = request.files.get("file")

        if not file:
            return "No file"

        df = read_file(file)
        df.columns = [str(c).strip() for c in df.columns]

        inserted = errors = 0

        for _, row in df.iterrows():
            try:
                raw = {c: clean(row[c]) for c in df.columns}
                submission = raw.get("Submission #") or raw.get("Submission Number")

                if not submission:
                    errors += 1
                    continue

                save_row(submission, raw)
                inserted += 1

            except Exception as e:
                errors += 1
                print("ROW ERROR:", e)

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

        blocks = re.split(r"Sub\s*#", text)

        conn = get_conn()
        cur = conn.cursor()

        updated = 0

        for b in blocks:
            try:
                if not b.strip() or not b[0].isdigit():
                    continue

                sub_match = re.match(r"(\d+)", b)
                if not sub_match:
                    continue

                sub = sub_match.group(1)

                if "Order Arrived" in b:
                    status = "Order Arrived"
                elif "Grading" in b:
                    status = "Grading"
                elif "Complete" in b:
                    status = "Complete"
                elif "QA Checks" in b:
                    status = "QA Checks"
                else:
                    status = "Processing"

                cur.execute("""
                UPDATE submissions
                SET status=%s, last_updated=NOW()
                WHERE submission_number=%s
                """, (status, sub))

                if cur.rowcount > 0:
                    updated += 1

            except Exception as e:
                print("PDF ROW ERROR:", e)

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
if __name__ == "__main__":
    app.run()
