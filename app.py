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
        if pd.isna(v): return ""
    except: pass
    return str(v).strip()

def normalize_submission(v):
    if not v: return None
    return re.sub(r"\D", "", str(v).split(".")[0])

def normalize_phone(v):
    return re.sub(r"\D", "", str(v or ""))

def get_field(data, names):
    for n in names:
        for k, v in data.items():
            if k.lower().strip() == n.lower().strip():
                return v
    return ""

def read_file(file):
    if file.filename.lower().endswith(("xlsx","xls")):
        return pd.read_excel(file)
    raw = file.read()
    file.seek(0)
    return pd.read_csv(io.StringIO(raw.decode("latin1")), on_bad_lines="skip")

def save_row(sub, raw):
    conn = get_conn()
    cur = conn.cursor()

    for k in list(raw.keys()):
        if "status" in k.lower():
            del raw[k]

    cur.execute("""
    INSERT INTO submissions (submission_number, raw_data)
    VALUES (%s,%s)
    ON CONFLICT (submission_number)
    DO UPDATE SET raw_data = EXCLUDED.raw_data
    """, (sub, json.dumps(raw)))

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
    body {{
        font-family: Arial;
        margin: 0;
        background: #f4f6f8;
    }}

    .topbar {{
        background: #1f2937;
        color: white;
        padding: 15px;
        display: flex;
        justify-content: space-between;
    }}

    .links a {{
        color: white;
        margin-left: 15px;
        text-decoration: none;
    }}

    .container {{
        padding: 20px;
    }}

    table {{
        width: 100%;
        border-collapse: collapse;
        background: white;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }}

    th {{
        background: #111827;
        color: white;
        padding: 10px;
        position: sticky;
        top: 0;
    }}

    td {{
        padding: 8px;
        border-bottom: 1px solid #ddd;
    }}

    tr:hover {{
        background: #f1f5f9;
    }}

    .status {{
        font-weight: bold;
        color: #2563eb;
    }}

    .card {{
        background: white;
        padding: 18px;
        margin-bottom: 15px;
        border-radius: 10px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    }}

    .bar {{
        display: flex;
        gap: 6px;
        margin-top: 10px;
        flex-wrap: wrap;
    }}

    .step {{
        padding: 6px 10px;
        border-radius: 20px;
        background: #e5e7eb;
        font-size: 13px;
    }}

    .done {{
        background: #bfdbfe;
    }}

    .current {{
        background: #2563eb;
        color: white;
    }}

    input, button {{
        padding: 10px;
        margin: 5px;
    }}
    </style>
    </head>

    <body>

    <div class="topbar">
        <div>PSA Tracking System</div>
        <div class="links">
            <a href="/admin">Admin</a>
            <a href="/portal">Customer Portal</a>
        </div>
    </div>

    <div class="container">
    {content}
    </div>

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
            if "unnamed" in k.lower(): continue
            if k == "S": k = "Submission Date"
            row[k] = v

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
    return html

def status_bar(status):
    steps = ["Order Arrived","Research & ID","Grading","QA Checks","Complete","Picked Up"]
    idx = steps.index(status) if status in steps else -1

    html = "<div class='bar'>"
    for i, s in enumerate(steps):
        cls = "step"
        if i < idx: cls += " done"
        if i == idx: cls += " current"
        html += f"<div class='{cls}'>{s}</div>"
    html += "</div>"
    return html

# =========================
# ADMIN
# =========================
@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/admin")
        return page("Wrong password")

    return page("""
    <h2>Admin Login</h2>
    <form method="post">
    <input type="password" name="password">
    <button>Login</button>
    </form>
    """)

@app.route("/admin")
@admin_required
def admin_dashboard():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT raw_data, status FROM submissions ORDER BY last_updated DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    html = "<h2>Admin Dashboard</h2><br>"
    html += build_table(rows)

    html += """
    <br><br>
    <a href="/admin/upload">Upload Excel</a><br>
    <a href="/admin/upload_psa">Upload PDF</a><br>
    """

    return page(html)

# =========================
# CUSTOMER PORTAL
# =========================
@app.route("/portal", methods=["GET","POST"])
def portal():
    if request.method == "POST":
        session["phone"] = normalize_phone(request.form.get("phone"))
        session["last"] = request.form.get("last","").lower()
        return redirect("/portal/orders")

    return page("""
    <h2>Customer Portal</h2>
    <form method="post">
    <input name="phone" placeholder="Phone">
    <input name="last" placeholder="Last Name">
    <button>View Orders</button>
    </form>
    """)

@app.route("/portal/orders")
def orders():
    phone = session.get("phone")
    last = session.get("last")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT raw_data, status FROM submissions")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    grouped = {}

    for r in rows:
        data = r[0] or {}
        name = str(get_field(data, ["Customer Name","Name"])).lower()
        contact = normalize_phone(get_field(data, ["Phone","Contact Info"]))

        if phone in contact and last in name:
            sub = normalize_submission(get_field(data, ["Submission #"]))
            if sub and sub not in grouped:
                grouped[sub] = (data, r[1])

    html = "<h2>My Orders</h2>"

    for sub,(data,status) in grouped.items():
        html += f"<div class='card'>"
        html += f"<h3>Submission {sub}</h3>"
        html += f"<p><b>Status:</b> <span class='status'>{status}</span></p>"
        html += status_bar(status or "Submitted")
        html += "</div>"

    return page(html)

# =========================
if __name__ == "__main__":
    app.run()
