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
# ERROR HANDLER (CRITICAL)
# =========================
@app.errorhandler(Exception)
def err(e):
    return f"<pre>ERROR:\n{str(e)}\n\nTRACE:\n{traceback.format_exc()}</pre>"

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
            row["PSA Status"] = r[1]

        clean_rows.append(row)
        keys.update(row.keys())

    ordered = sorted(keys)

    html = "<b>PSA System</b> | <a href='/upload'>Upload Excel</a> | <a href='/upload_psa'>Upload PDF</a><br><br>"

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
# EXCEL
# =========================
@app.route("/upload", methods=["GET","POST"])
def upload():
    if request.method == "POST":
        try:
            file = request.files.get("file")
            df = pd.read_excel(file)

            for _, row in df.iterrows():
                raw = {c: clean(row[c]) for c in df.columns}

                sub = normalize_submission(raw.get("Submission #"))
                if not sub:
                    continue

                conn = get_conn()
                cur = conn.cursor()

                cur.execute("""
                INSERT INTO submissions (submission_number, raw_data)
                VALUES (%s,%s)
                ON CONFLICT (submission_number)
                DO UPDATE SET raw_data = EXCLUDED.raw_data
                """, (sub, json.dumps(raw)))

                conn.commit()
                cur.close()
                conn.close()

            return "Excel uploaded"

        except Exception as e:
            return f"Excel Error:\n{traceback.format_exc()}"

    return '<form method="post" enctype="multipart/form-data"><input type="file" name="file"><button>Upload</button></form>'

# =========================
# PDF (SAFE VERSION)
# =========================
@app.route("/upload_psa", methods=["GET","POST"])
def upload_psa():
    if request.method == "POST":
        try:
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

            if not text:
                return "PDF EMPTY OR UNREADABLE"

            blocks = re.split(r"Sub\s*#", text)

            conn = get_conn()
            cur = conn.cursor()

            updated = 0

            for b in blocks:
                if not b.strip() or not b[0].isdigit():
                    continue

                match = re.match(r"(\d+)", b)
                if not match:
                    continue

                sub = normalize_submission(match.group(1))

                if "Complete" in b:
                    status = "Complete"
                elif "QA Checks" in b:
                    status = "QA Checks"
                elif "Research & ID" in b:
                    status = "Research & ID"
                elif "Grading" in b:
                    status = "Grading"
                elif "Order Arrived" in b:
                    status = "Order Arrived"
                else:
                    continue

                cur.execute("""
                UPDATE submissions
                SET status=%s
                WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g') = %s
                """, (status, sub))

                updated += cur.rowcount

            conn.commit()
            cur.close()
            conn.close()

            return f"Updated: {updated}"

        except Exception as e:
            return f"PDF ERROR:\n{traceback.format_exc()}"

    return '<form method="post" enctype="multipart/form-data"><input type="file" name="file"><button>Upload PDF</button></form>'

# =========================
if __name__ == "__main__":
    app.run()
