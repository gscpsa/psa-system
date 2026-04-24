from flask import Flask, request
import pandas as pd
import psycopg2
import os, io, json, re, traceback

app = Flask(__name__)
DATABASE_URL = os.getenv("DATABASE_URL")

# =========================
# DB
# =========================
def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

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

@app.before_request
def setup():
    try:
        init_db()
    except:
        pass

# =========================
# ERROR HANDLER
# =========================
@app.errorhandler(Exception)
def err(e):
    return f"<pre>{traceback.format_exc()}</pre>"

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

def normalize_submission(val):
    if not val:
        return None
    val = str(val).strip().split(".")[0]
    return re.sub(r"\D", "", val)

def read_file(file):
    name = (file.filename or "").lower()

    if name.endswith(("xlsx","xls")):
        return pd.read_excel(file)

    raw = file.read()
    file.seek(0)

    try:
        return pd.read_csv(io.StringIO(raw.decode("utf-8")), on_bad_lines="skip")
    except:
        return pd.read_csv(io.StringIO(raw.decode("latin1")), on_bad_lines="skip")

# =========================
# SAVE (EXCEL SAFE)
# =========================
def save_row(submission, raw):
    conn = get_conn()
    cur = conn.cursor()

    for k in list(raw.keys()):
        if "status" in k.lower():
            del raw[k]

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
# UI
# =========================
def page(content):
    return f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial; margin:0; background:#f4f6f8; }}
            .topbar {{ background:#1f2937; color:white; padding:15px; display:flex; justify-content:space-between; }}
            .links a {{ color:white; margin-left:15px; text-decoration:none; }}
            .container {{ padding:20px; }}
            table {{ width:100%; border-collapse:collapse; background:white; }}
            th {{ background:#111827; color:white; padding:10px; }}
            td {{ padding:8px; border-bottom:1px solid #ddd; }}
            tr:hover {{ background:#f1f5f9; }}
            .status {{ font-weight:bold; color:#2563eb; }}
        </style>
    </head>
    <body>
    <div class="topbar">
        <div>PSA Tracking</div>
        <div class="links">
            <a href="/">Dashboard</a>
            <a href="/upload">Upload Excel</a>
            <a href="/upload_psa">Upload PDF</a>
            <a href="/search">Search</a>
        </div>
    </div>
    <div class="container">
    {content}
    </div>
    </body>
    </html>
    """

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
        row = {k:v for k,v in data.items() if "unnamed" not in k.lower()}

        if r[1]:
            row["PSA Status"] = r[1]

        clean_rows.append(row)
        keys.update(row.keys())

    ordered = sorted(keys)

    html = "<table><tr>"
    for k in ordered:
        html += f"<th>{k}</th>"
    html += "</tr>"

    for row in clean_rows:
        html += "<tr>"
        for k in ordered:
            val = row.get(k,"")
            if k == "PSA Status":
                html += f"<td class='status'>{val}</td>"
            else:
                html += f"<td>{val}</td>"
        html += "</tr>"

    html += "</table>"
    return page(html)

# =========================
# SEARCH
# =========================
@app.route("/search")
def search():
    q = request.args.get("q","")

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    SELECT raw_data, status FROM submissions
    WHERE raw_data::text ILIKE %s
       OR submission_number ILIKE %s
       OR status ILIKE %s
    LIMIT 100
    """, (f"%{q}%", f"%{q}%", f"%{q}%"))

    rows = cur.fetchall()

    cur.close()
    conn.close()

    html = f"""
    <form>
        <input name="q" value="{q}">
        <button>Search</button>
    </form><br>
    """

    for r in rows:
        html += str(r[0]) + "<br><br>"

    return page(html)

# =========================
# EXCEL
# =========================
@app.route("/upload", methods=["GET","POST"])
def upload():
    if request.method == "POST":
        file = request.files.get("file")
        df = read_file(file)

        for _, row in df.iterrows():
            raw = {c: clean(row[c]) for c in df.columns}
            sub = normalize_submission(raw.get("Submission #"))

            if sub:
                save_row(sub, raw)

        return page("Excel uploaded")

    return page("""
    <form method="post" enctype="multipart/form-data">
    <input type="file" name="file">
    <button>Upload</button>
    </form>
    """)

# =========================
# PDF (REAL FIX)
# =========================
@app.route("/upload_psa", methods=["GET","POST"])
def upload_psa():
    if request.method == "POST":
        file = request.files.get("file")

        import pdfplumber, tempfile

        temp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        file.save(temp.name)

        text = ""
        with pdfplumber.open(temp.name) as pdf:
            for p in pdf.pages:
                t = p.extract_text()
                if t:
                    text += t + "\n"

        os.unlink(temp.name)

        blocks = re.split(r"Sub\s*#", text)

        status_priority = {
            "Order Arrived": 1,
            "Research & ID": 2,
            "Grading": 3,
            "QA Checks": 4,
            "Complete": 5
        }

        best_status = {}

        for b in blocks:
            if not b.strip() or not b[0].isdigit():
                continue

            match = re.match(r"(\d+)", b)
            if not match:
                continue

            sub = normalize_submission(match.group(1))

            status = None
            for s in status_priority:
                if s in b:
                    status = s
                    break

            if not status:
                continue

            if sub not in best_status or status_priority[status] < status_priority[best_status[sub]]:
                best_status[sub] = status

        conn = get_conn()
        cur = conn.cursor()

        updated = 0

        for sub, status in best_status.items():
            cur.execute("""
            UPDATE submissions
            SET status=%s, last_updated=NOW()
            WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g') = %s
            """, (status, sub))

            updated += cur.rowcount

        conn.commit()
        cur.close()
        conn.close()

        return page(f"Updated: {updated}")

    return page("""
    <form method="post" enctype="multipart/form-data">
    <input type="file" name="file">
    <button>Upload PDF</button>
    </form>
    """)

# =========================
if __name__ == "__main__":
    app.run()
