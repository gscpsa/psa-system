from flask import Flask, request
import pandas as pd
import psycopg2
import os, io, json, re, traceback, time

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

def save_row(sub, raw):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO submissions (submission_number, raw_data)
    VALUES (%s,%s)
    ON CONFLICT (submission_number)
    DO UPDATE SET raw_data=EXCLUDED.raw_data, last_updated=NOW()
    """, (sub, json.dumps(raw)))

    conn.commit()
    cur.close()
    conn.close()

# =========================
# DASHBOARD
# =========================
@app.route("/")
def home():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT raw_data, status FROM submissions ORDER BY last_updated DESC LIMIT 200")
    rows = cur.fetchall()

    cur.close()
    conn.close()

    keys = set()
    data_rows = []

    for r in rows:
        d = r[0] or {}

        row = {k:v for k,v in d.items() if not str(k).lower().startswith("unnamed")}

        if r[1]:
            row["PSA Status"] = r[1]

        data_rows.append(row)
        keys.update(row.keys())

    cols = sorted(keys)

    html = """
    <div style="position:sticky;top:0;background:white;padding:10px;">
    <a href='/upload'>Upload Excel</a> |
    <a href='/upload_psa'>Upload PDF</a> |
    <a href='/search'>Search</a>
    </div><br>
    """

    html += "<table border=1><tr>"
    for c in cols:
        html += f"<th>{c}</th>"
    html += "</tr>"

    for row in data_rows:
        html += "<tr>"
        for c in cols:
            html += f"<td>{row.get(c,'')}</td>"
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
        for v in r[0].values():
            html += f"<td>{v}</td>"
        html += "</tr>"

    html += "</table>"
    return html

# =========================
# EXCEL UPLOAD
# =========================
@app.route("/upload", methods=["GET","POST"])
def upload():
    if request.method == "POST":
        file = request.files.get("file")

        df = read_file(file)
        df.columns = [str(c).strip() for c in df.columns]

        count = 0

        for _, row in df.iterrows():
            raw = {c: clean(row[c]) for c in df.columns}
            sub = raw.get("Submission #") or raw.get("Submission Number")

            if sub:
                save_row(sub, raw)
                count += 1

        return f"Uploaded: {count}"

    return """
    <form method="post" enctype="multipart/form-data">
    <input type="file" name="file">
    <button>Upload</button>
    </form>
    """

# =========================
# PDF UPLOAD (STREAMED FIX)
# =========================
@app.route("/upload_psa", methods=["GET","POST"])
def upload_psa():
    if request.method == "POST":
        file = request.files.get("file")

        import pdfplumber

        file_bytes = file.read()

        conn = get_conn()
        cur = conn.cursor()

        updated = 0

        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page_index, page in enumerate(pdf.pages):

                text = page.extract_text()
                if not text:
                    continue

                blocks = re.split(r"Sub\s*#", text)

                for b in blocks:
                    if not b.strip() or not b[0].isdigit():
                        continue

                    match = re.match(r"(\d+)", b)
                    if not match:
                        continue

                    sub = match.group(1)

                    if "Complete" in b:
                        status = "Complete"
                    elif "QA Checks" in b:
                        status = "QA Checks"
                    elif "Grading" in b:
                        status = "Grading"
                    elif "Order Arrived" in b:
                        status = "Order Arrived"
                    else:
                        continue

                    cur.execute("""
                    UPDATE submissions
                    SET status=%s, last_updated=NOW()
                    WHERE submission_number=%s
                    """, (status, sub))

                    if cur.rowcount > 0:
                        updated += 1

                # 🔥 KEY: small delay prevents timeout kill
                if page_index % 5 == 0:
                    conn.commit()
                    time.sleep(0.1)

        conn.commit()
        cur.close()
        conn.close()

        return f"Updated: {updated}"

    return """
    <form method="post" enctype="multipart/form-data">
    <input type="file" name="file">
    <button>Upload PDF</button>
    </form>
    """

# =========================
if __name__ == "__main__":
    app.run()
