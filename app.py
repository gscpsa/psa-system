from flask import Flask, request, session, redirect
import pandas as pd
import psycopg2
import os, io, json, re, traceback
from functools import wraps

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

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
        status TEXT DEFAULT 'Submitted',
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

@app.errorhandler(Exception)
def err(e):
    return f"<pre>{traceback.format_exc()}</pre>"

# =========================
# SECURITY
# =========================
def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect("/admin/login")
        return f(*args, **kwargs)
    return wrapper

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

def normalize_submission(v):
    if not v:
        return None
    return re.sub(r"\D", "", str(v).split(".")[0])

def normalize_phone(v):
    return re.sub(r"\D", "", str(v or ""))

def get_field(data, names):
    for n in names:
        for k, v in data.items():
            if str(k).lower().strip() == n.lower().strip():
                return v
    return ""

def row_is_picked_up(raw):
    for k, v in raw.items():
        key = str(k).lower()
        val = str(v).lower().strip()
        if "status" in key or "pick" in key:
            if "picked up" in val:
                return True
    return False

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

def save_row(sub, raw):
    conn = get_conn()
    cur = conn.cursor()

    picked_up = row_is_picked_up(raw)

    for k in list(raw.keys()):
        if "status" in str(k).lower():
            del raw[k]

    if picked_up:
        cur.execute("""
        INSERT INTO submissions (submission_number, status, raw_data)
        VALUES (%s, %s, %s)
        ON CONFLICT (submission_number)
        DO UPDATE SET
            status='Picked Up',
            raw_data=EXCLUDED.raw_data,
            last_updated=NOW()
        """, (sub, "Picked Up", json.dumps(raw)))
    else:
        cur.execute("""
        INSERT INTO submissions (submission_number, status, raw_data)
        VALUES (%s, %s, %s)
        ON CONFLICT (submission_number)
        DO UPDATE SET
            raw_data=EXCLUDED.raw_data,
            status=COALESCE(submissions.status, 'Submitted'),
            last_updated=NOW()
        """, (sub, "Submitted", json.dumps(raw)))

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
    body {{font-family:Arial;margin:0;background:#f4f6f8}}
    .topbar {{background:#1f2937;color:white;padding:15px;display:flex;justify-content:space-between}}
    .links a {{color:white;margin-left:12px;text-decoration:none;font-weight:bold}}
    .container {{padding:20px}}
    table {{width:100%;border-collapse:collapse;background:white}}
    th {{background:#111827;color:white;padding:10px}}
    td {{padding:8px;border-bottom:1px solid #ddd}}
    tr:hover {{background:#f1f5f9}}
    .status {{font-weight:bold;color:#2563eb}}
    input,button {{padding:10px;margin:5px}}
    </style>
    </head>
    <body>
    <div class="topbar">
        <div><b>PSA Tracking</b></div>
        <div class="links">
            <a href="/admin">Admin</a>
            <a href="/admin/search">Search</a>
            <a href="/admin/upload">Upload Excel</a>
            <a href="/admin/upload_psa">Upload PDF</a>
            <a href="/portal">Customer Portal</a>
            <a href="/admin/logout">Logout</a>
        </div>
    </div>
    <div class="container">{content}</div>
    </body>
    </html>
    """

def build_table(rows):
    keys = set()
    clean_rows = []

    for r in rows:
        data = r[0] or {}
        row = {}

        for k, v in data.items():
            if "unnamed" in str(k).lower():
                continue
            if k == "S":
                k = "Submission Date"
            row[k] = v

        row["PSA Status"] = r[1] or "Submitted"

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
            val = row.get(k, "")
            if k == "PSA Status":
                html += f"<td class='status'>{val}</td>"
            else:
                html += f"<td>{val}</td>"
        html += "</tr>"

    html += "</table>"
    return html

# =========================
# ADMIN
# =========================
@app.route("/")
def root():
    return redirect("/admin")

@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/admin")
        return page("Wrong password")
    return page("<form method='post'><input type='password' name='password'><button>Login</button></form>")

@app.route("/admin/logout")
def logout():
    session.clear()
    return redirect("/admin/login")

@app.route("/admin")
@admin_required
def admin():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT raw_data,status FROM submissions ORDER BY last_updated DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    return page("<h2>Admin Dashboard</h2>" + build_table(rows))

# =========================
# PDF PARSER (FIXED)
# =========================
@app.route("/admin/upload_psa", methods=["GET","POST"])
@admin_required
def upload_psa():
    if request.method == "POST":
        import pdfplumber, tempfile

        f = request.files["file"]
        temp = tempfile.NamedTemporaryFile(delete=False)
        f.save(temp.name)

        PRIORITY = {"Order Arrived":1,"Research & ID":2,"Grading":3,"QA Checks":4,"Complete":5}
        best = {}

        with pdfplumber.open(temp.name) as pdf:
            for pdf_page in pdf.pages:
                tables = pdf_page.extract_tables()

                for table in tables:
                    for row in table:
                        text = " ".join([str(c or "") for c in row])

                        sub_match = re.search(r"Sub\s*#(\d+)", text)
                        if not sub_match:
                            continue

                        sub = sub_match.group(1)

                        for s in PRIORITY:
                            if s in text:
                                if sub not in best or PRIORITY[s] > PRIORITY[best[sub]]:
                                    best[sub] = s

        os.unlink(temp.name)

        conn = get_conn()
        cur = conn.cursor()

        for sub, status in best.items():
            cur.execute("""
            UPDATE submissions
            SET status=%s
            WHERE submission_number=%s
            AND COALESCE(status,'')!='Picked Up'
            """,(status,sub))

        conn.commit()
        cur.close()
        conn.close()

        return page("PDF processed")

    return page("<form method='post' enctype='multipart/form-data'><input type='file' name='file'><button>Upload</button></form>")

# =========================
if __name__ == "__main__":
    app.run()
