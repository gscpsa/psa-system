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
    return f"<pre>{str(e)}\n\n{traceback.format_exc()}</pre>"

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
    val = str(val).strip()
    val = val.split(".")[0]
    val = re.sub(r"\D", "", val)
    return val

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

    # REMOVE any Excel status
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
        row = {k:v for k,v in data.items() if not str(k).lower().startswith("unnamed")}

        if r[1]:
            row["Status"] = r[1]

        clean_rows.append(row)
        keys.update(row.keys())

    ordered = sorted(keys)

    html = "<b>PSA System</b> | <a href='/upload'>Upload Excel</a> | <a href='/upload_psa'>Upload PSA PDF</a> | <a href='/search'>Search</a><br><br>"

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

    html = f"<form><input name='q' value='{q}'><button>Search</button></form><br><table border=1>"

    for r in rows:
        data = r[0] or {}
        html += "<tr>"
        for v in data.values():
            html += f"<td>{v}</td>"
        html += "</tr>"

    html += "</table><br><a href='/'>Back</a>"
    return html

# =========================
# EXCEL
# =========================
@app.route("/upload", methods=["GET","POST"])
def upload():
    if request.method == "POST":
        file = request.files.get("file")

        df = read_file(file)
        df.columns = [str(c).strip() for c in df.columns]

        for _, row in df.iterrows():
            raw = {c: clean(row[c]) for c in df.columns}

            submission = raw.get("Submission #") or raw.get("Submission Number")
            submission = normalize_submission(submission)

            if submission:
                save_row(submission, raw)

        return "Excel uploaded"

    return '<form method="post" enctype="multipart/form-data"><input type="file" name="file"><button>Upload</button></form>'

# =========================
# PDF (FINAL RELIABLE VERSION)
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
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text += t + "\n"

        os.unlink(temp.name)

        if not text.strip():
            return "PDF TEXT EMPTY"

        conn = get_conn()
        cur = conn.cursor()

        updated = 0

        # FIND ALL submission numbers
        matches = re.findall(r"\b\d{8}\b", text)

        for sub in matches:
            sub = normalize_submission(sub)

            idx = text.find(sub)
            chunk = text[max(0, idx-200):idx+200]

            if "Complete" in chunk:
                status = "Complete"
            elif "QA Checks" in chunk:
                status = "QA Checks"
            elif "Research & ID" in chunk:
                status = "Research & ID"
            elif "Grading" in chunk:
                status = "Grading"
            elif "Order Arrived" in chunk:
                status = "Order Arrived"
            else:
                continue

            cur.execute("""
            UPDATE submissions
            SET status=%s, last_updated=NOW()
            WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g') = %s
            """, (status, sub))

            print("UPDATE:", sub, status, "ROWS:", cur.rowcount)

            if cur.rowcount > 0:
                updated += 1

        conn.commit()
        cur.close()
        conn.close()

        return f"Updated: {updated}"

    return '<form method="post" enctype="multipart/form-data"><input type="file" name="file"><button>Upload PDF</button></form>'

# =========================
if __name__ == "__main__":
    app.run()
