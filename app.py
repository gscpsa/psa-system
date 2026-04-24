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
            if k.lower().strip() == n.lower().strip():
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
    except:
        return pd.read_csv(io.StringIO(raw.decode("latin1")), on_bad_lines="skip")

def save_row(sub, raw):
    conn = get_conn()
    cur = conn.cursor()

    # Excel must not overwrite PSA status
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
        color: #111827;
    }}
    .topbar {{
        background: #1f2937;
        color: white;
        padding: 15px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }}
    .links a {{
        color: white;
        margin-left: 14px;
        text-decoration: none;
        font-weight: bold;
    }}
    .container {{
        padding: 20px;
    }}
    table {{
        width: 100%;
        border-collapse: collapse;
        background: white;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    }}
    th {{
        background: #111827;
        color: white;
        padding: 10px;
        text-align: left;
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
        padding: 7px 11px;
        border-radius: 20px;
        background: #e5e7eb;
        font-size: 13px;
    }}
    .done {{
        background: #bfdbfe;
        color: #1e40af;
        font-weight: bold;
    }}
    .current {{
        background: #2563eb;
        color: white;
        font-weight: bold;
    }}
    input, button {{
        padding: 10px;
        margin: 5px;
    }}
    .buttonlink {{
        display:inline-block;
        margin: 5px 10px 15px 0;
        padding: 8px 12px;
        background:#1f2937;
        color:white;
        text-decoration:none;
        border-radius:6px;
    }}
    </style>
    </head>
    <body>
    <div class="topbar">
        <div><b>PSA Tracking System</b></div>
        <div class="links">
            <a href="/admin">Admin</a>
            <a href="/admin/search">Search</a>
            <a href="/admin/upload">Upload Excel</a>
            <a href="/admin/upload_psa">Upload PDF</a>
            <a href="/portal">Customer Portal</a>
            <a href="/admin/logout">Logout</a>
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
            if "unnamed" in str(k).lower():
                continue
            if k == "S":
                k = "Submission Date"
            row[k] = v

        if r[1]:
            row["PSA Status"] = r[1]

        clean_rows.append(row)
        keys.update(row.keys())

    ordered = sorted(keys)

    if not ordered:
        return "<p>No records found.</p>"

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

def status_bar(status):
    steps = ["Order Arrived", "Research & ID", "Grading", "QA Checks", "Complete", "Picked Up"]
    idx = steps.index(status) if status in steps else -1

    html = "<div class='bar'>"
    for i, s in enumerate(steps):
        cls = "step"
        if i < idx:
            cls += " done"
        if i == idx:
            cls += " current"
        html += f"<div class='{cls}'>{s}</div>"
    html += "</div>"
    return html

# =========================
# ADMIN LOGIN / LOGOUT
# =========================
@app.route("/")
def root():
    return redirect("/admin")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/admin")
        return page("<h2>Wrong password</h2><a href='/admin/login'>Try again</a>")

    return page("""
    <div class="card">
    <h2>Admin Login</h2>
    <form method="post">
        <input type="password" name="password" placeholder="Admin password">
        <button>Login</button>
    </form>
    </div>
    """)

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")

# =========================
# ADMIN DASHBOARD
# =========================
@app.route("/admin")
@admin_required
def admin_dashboard():
    sort = request.args.get("sort", "new")
    order = "ASC" if sort == "old" else "DESC"

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"""
    SELECT raw_data, status
    FROM submissions
    ORDER BY last_updated {order}
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    html = """
    <h2>Admin Dashboard</h2>
    <a class="buttonlink" href="/admin?sort=new">Newest First</a>
    <a class="buttonlink" href="/admin?sort=old">Oldest First</a>
    <a class="buttonlink" href="/admin/search">Search</a>
    <a class="buttonlink" href="/admin/upload">Upload Excel</a>
    <a class="buttonlink" href="/admin/upload_psa">Upload PSA PDF</a>
    <a class="buttonlink" href="/portal">Customer Portal</a>
    <br><br>
    """
    html += build_table(rows)
    return page(html)

# =========================
# ADMIN SEARCH
# =========================
@app.route("/admin/search")
@admin_required
def admin_search():
    q = request.args.get("q", "")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    SELECT raw_data, status
    FROM submissions
    WHERE raw_data::text ILIKE %s
       OR submission_number ILIKE %s
       OR status ILIKE %s
    ORDER BY last_updated DESC
    """, (f"%{q}%", f"%{q}%", f"%{q}%"))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    html = f"""
    <h2>Admin Search</h2>
    <form>
        <input name="q" value="{q}" placeholder="Search name, phone, submission, status">
        <button>Search</button>
    </form>
    <br>
    """
    html += build_table(rows)
    return page(html)

# =========================
# ADMIN EXCEL UPLOAD
# =========================
@app.route("/admin/upload", methods=["GET", "POST"])
@admin_required
def admin_upload():
    if request.method == "POST":
        df = read_file(request.files["file"])
        df.columns = [str(c).strip() for c in df.columns]

        count = 0
        for _, row in df.iterrows():
            raw = {c: clean(row[c]) for c in df.columns}
            sub = normalize_submission(raw.get("Submission #") or raw.get("Submission Number"))
            if sub:
                save_row(sub, raw)
                count += 1

        return page(f"<h2>Excel uploaded</h2><p>Rows processed: {count}</p><a href='/admin'>Back to Admin</a>")

    return page("""
    <div class="card">
    <h2>Upload Excel / CSV</h2>
    <form method="post" enctype="multipart/form-data">
        <input type="file" name="file">
        <button>Upload</button>
    </form>
    </div>
    """)

# =========================
# ADMIN PDF UPLOAD
# =========================
@app.route("/admin/upload_psa", methods=["GET", "POST"])
@admin_required
def admin_upload_psa():
    if request.method == "POST":
        import pdfplumber, tempfile

        f = request.files["file"]
        temp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        f.save(temp.name)

        text = ""
        with pdfplumber.open(temp.name) as pdf:
            for p in pdf.pages:
                t = p.extract_text()
                if t:
                    text += t + "\n"

        os.unlink(temp.name)

        blocks = re.split(r"Sub\s*#", text)

        priority = {
            "Order Arrived": 1,
            "Research & ID": 2,
            "Grading": 3,
            "QA Checks": 4,
            "Complete": 5
        }

        best = {}

        for b in blocks:
            if not b.strip() or not b[0].isdigit():
                continue

            m = re.match(r"\d+", b)
            if not m:
                continue

            sub = normalize_submission(m.group())

            for s in priority:
                if s in b:
                    if sub not in best or priority[s] < priority[best[sub]]:
                        best[sub] = s
                    break

        conn = get_conn()
        cur = conn.cursor()

        updated = 0
        for sub, status in best.items():
            cur.execute("""
            UPDATE submissions
            SET status=%s, last_updated=NOW()
            WHERE REGEXP_REPLACE(submission_number, '\\D','','g')=%s
            """, (status, sub))
            updated += cur.rowcount

        conn.commit()
        cur.close()
        conn.close()

        return page(f"<h2>PDF uploaded</h2><p>Status updates applied: {updated}</p><a href='/admin'>Back to Admin</a>")

    return page("""
    <div class="card">
    <h2>Upload PSA PDF</h2>
    <form method="post" enctype="multipart/form-data">
        <input type="file" name="file">
        <button>Upload PDF</button>
    </form>
    </div>
    """)

# =========================
# CUSTOMER PORTAL
# =========================
@app.route("/portal", methods=["GET", "POST"])
def portal():
    if request.method == "POST":
        session["phone"] = normalize_phone(request.form.get("phone"))
        session["last"] = clean(request.form.get("last")).lower()
        return redirect("/portal/orders")

    return page("""
    <div class="card">
    <h2>Customer Portal</h2>
    <p>Enter the phone number and last name used on your submission.</p>
    <form method="post">
        <input name="phone" placeholder="Phone number">
        <input name="last" placeholder="Last name">
        <button>View My Orders</button>
    </form>
    </div>
    """)

@app.route("/portal/orders")
def customer_orders():
    phone = session.get("phone")
    last = session.get("last")

    if not phone or not last:
        return redirect("/portal")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT raw_data, status FROM submissions")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    grouped = {}

    for r in rows:
        data = r[0] or {}
        name = str(get_field(data, ["Customer Name", "Name"])).lower()
        contact = normalize_phone(get_field(data, ["Contact Info", "Phone", "Phone Number"]))
        sub = normalize_submission(get_field(data, ["Submission #", "Submission Number"]))

        if phone in contact and last in name and sub:
            if sub not in grouped:
                grouped[sub] = (data, r[1])

    html = "<h2>My Orders</h2>"

    if not grouped:
        html += "<div class='card'>No matching orders found. Check phone number and last name.</div>"
        return page(html)

    for sub, (data, status) in grouped.items():
        service = get_field(data, ["Service Type", "Service"])
        cards = get_field(data, ["# Of Cards", "# of Cards", "Cards"])
        date = get_field(data, ["S", "Submission Date", "Date"])
        display_status = status or "Submitted"

        html += f"""
        <div class="card">
            <h3>Submission #{sub}</h3>
            <p><b>Status:</b> <span class="status">{display_status}</span></p>
            <p><b>Service:</b> {service}</p>
            <p><b>Cards:</b> {cards}</p>
            <p><b>Submission Date:</b> {date}</p>
            {status_bar(display_status)}
        </div>
        """

    html += "<a href='/portal/logout'>Log out</a>"
    return page(html)

@app.route("/portal/logout")
def portal_logout():
    session.pop("phone", None)
    session.pop("last", None)
    return redirect("/portal")

# =========================
if __name__ == "__main__":
    app.run()
