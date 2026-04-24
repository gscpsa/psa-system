from flask import Flask, request, session, redirect
import pandas as pd
import psycopg2
import os, io, json, re, traceback
from functools import wraps

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")  # set in Railway

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
    <body style="font-family:Arial;background:#f4f6f8;margin:0">
    <div style="background:#1f2937;color:white;padding:15px">
    <b>PSA System</b> |
    <a href="/admin" style="color:white">Admin</a> |
    <a href="/portal" style="color:white">Customer Portal</a>
    </div>
    <div style="padding:20px">{content}</div>
    </body>
    </html>
    """

def status_bar(status):
    steps = ["Order Arrived","Research & ID","Grading","QA Checks","Complete","Picked Up"]
    idx = steps.index(status) if status in steps else -1
    html = "<div style='display:flex;gap:6px;margin-top:10px'>"
    for i,s in enumerate(steps):
        color = "#e5e7eb"
        if i < idx: color = "#bfdbfe"
        if i == idx: color = "#2563eb;color:white"
        html += f"<div style='padding:6px 10px;background:{color};border-radius:20px'>{s}</div>"
    html += "</div>"
    return html

# =========================
# ADMIN LOGIN
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
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT raw_data, status FROM submissions ORDER BY last_updated DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    html = "<h2>Admin Dashboard</h2>"
    for r in rows:
        html += f"<div>{r}</div><br>"

    html += """
    <br>
    <a href="/admin/upload">Upload Excel</a><br>
    <a href="/admin/upload_psa">Upload PDF</a><br>
    <a href="/admin/search">Search</a><br>
    <a href="/admin/logout">Logout</a>
    """

    return page(html)

# =========================
# ADMIN UPLOADS
# =========================
@app.route("/admin/upload", methods=["GET","POST"])
@admin_required
def upload():
    if request.method == "POST":
        df = read_file(request.files["file"])
        for _, row in df.iterrows():
            raw = {c:clean(row[c]) for c in df.columns}
            sub = normalize_submission(raw.get("Submission #"))
            if sub: save_row(sub, raw)
        return page("Uploaded")
    return page('<form method="post" enctype="multipart/form-data"><input type="file" name="file"><button>Upload</button></form>')

@app.route("/admin/upload_psa", methods=["GET","POST"])
@admin_required
def upload_psa():
    if request.method == "POST":
        import pdfplumber, tempfile
        f = request.files["file"]
        temp = tempfile.NamedTemporaryFile(delete=False)
        f.save(temp.name)

        text = ""
        with pdfplumber.open(temp.name) as pdf:
            for p in pdf.pages:
                t = p.extract_text()
                if t: text += t

        os.unlink(temp.name)

        blocks = re.split(r"Sub\s*#", text)

        priority = {"Order Arrived":1,"Research & ID":2,"Grading":3,"QA Checks":4,"Complete":5}
        best = {}

        for b in blocks:
            if not b or not b[0].isdigit(): continue
            sub = normalize_submission(re.match(r"\d+", b).group())
            for s in priority:
                if s in b:
                    if sub not in best or priority[s] < priority[best[sub]]:
                        best[sub] = s
                    break

        conn = get_conn()
        cur = conn.cursor()

        for sub,status in best.items():
            cur.execute("""
            UPDATE submissions SET status=%s
            WHERE REGEXP_REPLACE(submission_number,'\\D','','g')=%s
            """,(status,sub))

        conn.commit()
        cur.close()
        conn.close()

        return page(f"Updated {len(best)}")

    return page('<form method="post" enctype="multipart/form-data"><input type="file" name="file"><button>Upload PDF</button></form>')

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
        name = str(get_field(data, ["Customer Name","Name"])).lower()
        contact = normalize_phone(get_field(data, ["Phone","Contact Info"]))

        if phone in contact and last in name:
            sub = normalize_submission(get_field(data, ["Submission #"]))
            if sub and sub not in grouped:
                grouped[sub] = (data, r[1])

    html = "<h2>My Orders</h2>"

    for sub,(data,status) in grouped.items():
        html += f"<div style='background:white;padding:15px;margin-bottom:10px'>"
        html += f"<b>Submission {sub}</b><br>Status: {status or 'Submitted'}"
        html += status_bar(status or "Submitted")
        html += "</div>"

    return page(html)

# =========================
if __name__ == "__main__":
    app.run()
