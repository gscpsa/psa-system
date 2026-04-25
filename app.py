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
# DATABASE
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
    except Exception:
        pass

@app.errorhandler(Exception)
def error_handler(e):
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
    except Exception:
        pass
    return str(v).strip()

def normalize_submission(v):
    if not v:
        return None
    return re.sub(r"\D", "", str(v).split(".")[0])

def normalize_phone(v):
    return re.sub(r"\D", "", str(v or ""))

def get_field(data, names):
    for wanted in names:
        for k, v in data.items():
            if str(k).strip().lower() == wanted.strip().lower():
                return v
    return ""

def read_file(file):
    name = (file.filename or "").lower()

    if name.endswith(("xlsx", "xls")):
        return pd.read_excel(file)

    raw = file.read()
    file.seek(0)

    try:
        return pd.read_csv(io.StringIO(raw.decode("utf-8")), on_bad_lines="skip")
    except Exception:
        return pd.read_csv(io.StringIO(raw.decode("latin1")), on_bad_lines="skip")

# =========================
# INTERNAL STATUS DETECTION (FIXED)
# =========================
def detect_internal_status(raw):
    full_text = " ".join([f"{k} {v}" for k, v in raw.items()]).lower()

    if "picked up" in full_text:
        return "Picked Up"

    if (
        "delivered to us" in full_text or
        "received by us" in full_text or
        "arrived at store" in full_text
    ):
        return "Delivered to Us"

    return None

# =========================
# SAVE LOGIC (FIXED)
# =========================
def save_row(sub, raw):
    conn = get_conn()
    cur = conn.cursor()

    internal_status = detect_internal_status(raw)

    for k in list(raw.keys()):
        if "status" in str(k).lower():
            del raw[k]

    cur.execute("SELECT status FROM submissions WHERE submission_number=%s", (sub,))
    existing = cur.fetchone()
    existing_status = existing[0] if existing else None

    if existing_status == "Picked Up":
        cur.execute("""
        UPDATE submissions
        SET raw_data=%s, last_updated=NOW()
        WHERE submission_number=%s
        """, (json.dumps(raw), sub))

    elif internal_status:
        cur.execute("""
        INSERT INTO submissions (submission_number, status, raw_data)
        VALUES (%s, %s, %s)
        ON CONFLICT (submission_number)
        DO UPDATE SET
            status=%s,
            raw_data=EXCLUDED.raw_data,
            last_updated=NOW()
        """, (sub, internal_status, json.dumps(raw), internal_status))

    else:
        cur.execute("""
        INSERT INTO submissions (submission_number, status, raw_data)
        VALUES (%s, 'Submitted', %s)
        ON CONFLICT (submission_number)
        DO UPDATE SET
            raw_data=EXCLUDED.raw_data,
            status=COALESCE(submissions.status, 'Submitted'),
            last_updated=NOW()
        """, (sub, json.dumps(raw)))

    conn.commit()
    cur.close()
    conn.close()

# =========================
# UI (GREEN BRAND)
# =========================
def page(content, mode="admin"):
    if mode == "admin":
        nav = """
        <a href="/admin">Dashboard</a>
        <a href="/admin/search">Search</a>
        <a href="/admin/upload">Upload Excel</a>
        <a href="/admin/upload_psa">Upload PSA</a>
        <a href="/portal">Customer Portal</a>
        <a href="/admin/logout">Logout</a>
        """
    else:
        nav = """
        <a href="/portal">Home</a>
        <a href="/portal/logout">Logout</a>
        """

    return f"""
    <html>
    <head>
    <style>
    body {{ font-family: Arial; margin:0; background:#f4f6f8; }}
    .topbar {{ background:#0f5132; color:white; padding:15px 20px; display:flex; justify-content:space-between; }}
    .links a {{ color:white; margin-left:15px; text-decoration:none; font-weight:bold; }}
    .container {{ padding:20px; overflow-x:auto; }}

    table {{ width:100%; border-collapse:collapse; font-size:12px; background:white; }}
    th {{ background:#0f5132; color:white; padding:5px; }}
    td {{ padding:5px; border-bottom:1px solid #ddd; white-space:nowrap; }}
    tr:hover {{ background:#eef6f2; }}

    .status {{ color:#198754; font-weight:bold; }}

    .card {{ background:white; padding:15px; margin-bottom:15px; border-radius:8px; }}

    .btn {{ background:#198754; color:white; padding:8px 12px; border-radius:6px; text-decoration:none; }}

    .bar {{ display:flex; gap:5px; flex-wrap:wrap; margin-top:10px; }}
    .step {{ padding:5px 8px; border-radius:15px; background:#ddd; font-size:11px; }}
    .done {{ background:#d1e7dd; color:#0f5132; }}
    .current {{ background:#198754; color:white; }}
    </style>
    </head>

    <body>
        <div class="topbar">
            <div><b>Giant Sports Cards</b></div>
            <div class="links">{nav}</div>
        </div>
        <div class="container">{content}</div>
    </body>
    </html>
    """

# =========================
# TABLE (FIXED)
# =========================
def build_table(rows):
    keys = set()
    clean_rows = []

    for r in rows:
        data = r[0] or {}
        row = {}

        for k, v in data.items():
            key_lower = str(k).lower()

            if "unnamed" in key_lower:
                continue

            # REMOVE Excel status columns
            if key_lower.strip() in ["status", "current status"]:
                continue

            display_key = "Submission Date" if str(k).strip() == "S" else str(k)
            row[display_key] = v

        row["PSA Status"] = r[1]
        clean_rows.append(row)
        keys.update(row.keys())

    ordered = sorted(keys)

    html = "<table><tr>" + "".join([f"<th>{k}</th>" for k in ordered]) + "</tr>"

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
# STATUS BAR
# =========================
def status_bar(status):
    steps = ["Submitted","Order Arrived","Research & ID","Grading","QA Checks","Complete","Delivered to Us","Picked Up"]
    idx = steps.index(status) if status in steps else 0

    html = "<div class='bar'>"
    for i, s in enumerate(steps):
        cls = "step"
        if i < idx: cls += " done"
        if i == idx: cls += " current"
        html += f"<div class='{cls}'>{s}</div>"
    html += "</div>"
    return html

# =========================
# ROUTES
# =========================
@app.route("/")
def root(): return redirect("/admin")

@app.route("/admin/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        if request.form.get("password")==ADMIN_PASSWORD:
            session["admin"]=True
            return redirect("/admin")
    return page("<form method='post'><input type='password' name='password'><button>Login</button></form>")

@app.route("/admin")
@admin_required
def admin():
    conn=get_conn(); cur=conn.cursor()
    cur.execute("SELECT raw_data,status FROM submissions ORDER BY last_updated DESC")
    rows=cur.fetchall()
    cur.close(); conn.close()
    return page(build_table(rows))

@app.route("/admin/upload", methods=["POST"])
@admin_required
def upload():
    df = read_file(request.files["file"])
    for _, row in df.iterrows():
        raw = {c: clean(row[c]) for c in df.columns}
        sub = normalize_submission(raw.get("Submission #"))
        if sub:
            save_row(sub, raw)
    return "OK"

@app.route("/portal", methods=["GET","POST"])
def portal():
    if request.method=="POST":
        session["phone"]=normalize_phone(request.form.get("phone"))
        session["last"]=request.form.get("last","").lower()
        return redirect("/portal/orders")
    return page("<form method='post'><input name='phone'><input name='last'><button>Go</button></form>", mode="portal")

@app.route("/portal/orders")
def orders():
    phone=normalize_phone(session.get("phone"))
    last=(session.get("last") or "").lower()

    conn=get_conn(); cur=conn.cursor()
    cur.execute("SELECT raw_data,status FROM submissions")
    rows=cur.fetchall()
    cur.close(); conn.close()

    html=""
    for r in rows:
        data=r[0]; status=r[1]
        name=str(get_field(data,["Customer Name","Name"])).lower()
        contact=normalize_phone(get_field(data,["Phone"]))
        if phone in contact and last in name:
            html+=f"<div class='card'><b>{name}</b><br>Status:{status}{status_bar(status)}</div>"

    return page(html, mode="portal")

if __name__=="__main__":
    app.run()
