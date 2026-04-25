from flask import Flask, request, session, redirect
import pandas as pd
import psycopg2
import os, io, json, re, traceback
from functools import wraps

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

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
def err(e):
    return f"<pre>{traceback.format_exc()}</pre>"

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect("/admin/login")
        return f(*args, **kwargs)
    return wrapper

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
    for n in names:
        for k, v in data.items():
            if str(k).lower().strip() == n.lower().strip():
                return v
    return ""

def row_is_picked_up(raw):
    for k, v in raw.items():
        key = str(k).lower()
        val = str(v).lower()
        if "pick" in key or "status" in key:
            if "picked up" in val or "customer picked up" in val:
                return True
    return False

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
        VALUES (%s, 'Picked Up', %s)
        ON CONFLICT (submission_number)
        DO UPDATE SET
            status='Picked Up',
            raw_data=EXCLUDED.raw_data,
            last_updated=NOW()
        """, (sub, json.dumps(raw)))
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

def page(content):
    return f"""
    <html>
    <head>
    <style>
        body {{ font-family: Arial; margin:0; background:#f4f6f8; color:#111827; }}
        .topbar {{ background:#1f2937; color:white; padding:15px; display:flex; justify-content:space-between; align-items:center; }}
        .links a {{ color:white; margin-left:12px; text-decoration:none; font-weight:bold; }}
        .container {{ padding:20px; }}
        table {{ width:100%; border-collapse:collapse; background:white; box-shadow:0 2px 8px rgba(0,0,0,.08); }}
        th {{ background:#111827; color:white; padding:10px; text-align:left; position:sticky; top:0; }}
        td {{ padding:8px; border-bottom:1px solid #ddd; }}
        tr:hover {{ background:#f1f5f9; }}
        .status {{ font-weight:bold; color:#2563eb; }}
        .card {{ background:white; padding:18px; margin-bottom:15px; border-radius:10px; box-shadow:0 2px 8px rgba(0,0,0,.08); }}
        .btn {{ display:inline-block; padding:8px 12px; background:#1f2937; color:white; text-decoration:none; border-radius:6px; margin:5px 8px 15px 0; }}
        input, button {{ padding:10px; margin:5px; }}
        .bar {{ display:flex; gap:6px; flex-wrap:wrap; margin-top:10px; }}
        .step {{ padding:7px 11px; border-radius:20px; background:#e5e7eb; font-size:13px; }}
        .done {{ background:#bfdbfe; color:#1e40af; font-weight:bold; }}
        .current {{ background:#2563eb; color:white; font-weight:bold; }}
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
            display_key = "Submission Date" if str(k).strip() == "S" else str(k)
            row[display_key] = v

        row["PSA Status"] = r[1] or "Submitted"
        clean_rows.append(row)
        keys.update(row.keys())

    if not clean_rows:
        return "<div class='card'>No records found.</div>"

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

def status_bar(status):
    steps = ["Submitted", "Order Arrived", "Research & ID", "Grading", "QA Checks", "Complete", "Picked Up"]
    status = status or "Submitted"
    idx = steps.index(status) if status in steps else 0

    html = "<div class='bar'>"
    for i, step in enumerate(steps):
        cls = "step"
        if i < idx:
            cls += " done"
        if i == idx:
            cls += " current"
        html += f"<div class='{cls}'>{step}</div>"
    html += "</div>"
    return html

@app.route("/")
def root():
    return redirect("/admin")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/admin")
        return page("<div class='card'>Wrong password. <a href='/admin/login'>Try again</a></div>")

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

@app.route("/admin")
@admin_required
def admin_dashboard():
    sort = request.args.get("sort", "new")
    order = "ASC" if sort == "old" else "DESC"

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT raw_data, status FROM submissions ORDER BY last_updated {order}")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    html = """
    <h2>Admin Dashboard</h2>
    <a class="btn" href="/admin?sort=new">Newest First</a>
    <a class="btn" href="/admin?sort=old">Oldest First</a>
    <a class="btn" href="/admin/search">Search</a>
    <a class="btn" href="/admin/upload">Upload Excel</a>
    <a class="btn" href="/admin/upload_psa">Upload PSA PDF</a>
    <a class="btn" href="/portal">Customer Portal</a>
    <br><br>
    """
    html += build_table(rows)
    return page(html)

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

@app.route("/admin/upload", methods=["GET", "POST"])
@admin_required
def admin_upload():
    if request.method == "POST":
        try:
            file = request.files.get("file")
            if not file:
                return page("<div class='card'>No file uploaded.</div>")

            df = read_file(file)
            df.columns = [str(c).strip() for c in df.columns]

            count = 0
            skipped = 0

            for _, row in df.iterrows():
                raw = {c: clean(row[c]) for c in df.columns}
                sub = normalize_submission(raw.get("Submission #") or raw.get("Submission Number"))

                if sub:
                    save_row(sub, raw)
                    count += 1
                else:
                    skipped += 1

            return page(f"""
            <div class="card">
                <h2>Excel uploaded</h2>
                <p>Rows processed: {count}</p>
                <p>Rows skipped: {skipped}</p>
                <a href="/admin">Back to Admin</a>
            </div>
            """)
        except Exception:
            return page(f"<pre>{traceback.format_exc()}</pre>")

    return page("""
    <div class="card">
        <h2>Upload Excel / CSV</h2>
        <form method="post" enctype="multipart/form-data">
            <input type="file" name="file">
            <button>Upload Excel</button>
        </form>
    </div>
    """)

@app.route("/admin/upload_psa", methods=["GET", "POST"])
@admin_required
def admin_upload_psa():
    if request.method == "POST":
        try:
            import pdfplumber, tempfile

            file = request.files.get("file")
            if not file:
                return page("<div class='card'>No PDF uploaded.</div>")

            temp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            file.save(temp.name)

            priority = {
                "Order Arrived": 1,
                "Research & ID": 2,
                "Grading": 3,
                "QA Checks": 4,
                "Complete": 5,
                "Assembly": 4
            }

            best = {}

            with pdfplumber.open(temp.name) as pdf:
                for pdf_page in pdf.pages:
                    tables = pdf_page.extract_tables() or []

                    for table in tables:
                        for row in table:
                            row_text = " ".join([str(c or "") for c in row])

                            sub_match = re.search(r"Sub\s*#\s*(\d+)", row_text)
                            if not sub_match:
                                continue

                            sub = normalize_submission(sub_match.group(1))

                            for status_name, rank in priority.items():
                                if status_name in row_text:
                                    mapped = "QA Checks" if status_name == "Assembly" else status_name
                                    mapped_rank = priority[mapped]
                                    if sub not in best or mapped_rank > priority[best[sub]]:
                                        best[sub] = mapped
                                    break

            os.unlink(temp.name)

            conn = get_conn()
            cur = conn.cursor()

            updated = 0
            skipped = 0

            for sub, status in best.items():
                cur.execute("""
                UPDATE submissions
                SET status=%s, last_updated=NOW()
                WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
                  AND COALESCE(status, '') != 'Picked Up'
                """, (status, sub))

                if cur.rowcount:
                    updated += 1
                else:
                    skipped += 1

            conn.commit()
            cur.close()
            conn.close()

            return page(f"""
            <div class="card">
                <h2>PDF processed</h2>
                <p>Statuses found in PDF: {len(best)}</p>
                <p>Status updates applied: {updated}</p>
                <p>Skipped picked-up or unmatched: {skipped}</p>
                <a href="/admin">Back to Admin</a>
            </div>
            """)
        except Exception:
            return page(f"<pre>{traceback.format_exc()}</pre>")

    return page("""
    <div class="card">
        <h2>Upload PSA PDF</h2>
        <form method="post" enctype="multipart/form-data">
            <input type="file" name="file">
            <button>Upload PDF</button>
        </form>
    </div>
    """)

@app.route("/portal", methods=["GET", "POST"])
def portal():
    if request.method == "POST":
        session["phone"] = normalize_phone(request.form.get("phone"))
        session["last"] = clean(request.form.get("last")).lower()
        return redirect("/portal/orders")

    return page("""
    <div class="card" style="max-width:420px">
        <h2>Customer Portal</h2>
        <p>Enter the phone number and last name used on your submission.</p>
        <form method="post">
            <input name="phone" placeholder="Phone number" style="width:95%"><br>
            <input name="last" placeholder="Last name" style="width:95%"><br>
            <button>View My Orders</button>
        </form>
    </div>
    """)

@app.route("/portal/orders")
def customer_orders():
    phone = normalize_phone(session.get("phone"))
    last = clean(session.get("last")).lower()

    if not phone or not last:
        return redirect("/portal")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT raw_data, status FROM submissions ORDER BY last_updated DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    grouped = {}

    for r in rows:
        data = r[0] or {}
        name = str(get_field(data, ["Customer Name", "Name"])).lower()
        contact = normalize_phone(get_field(data, ["Contact Info", "Phone", "Phone Number"]))
        sub = normalize_submission(get_field(data, ["Submission #", "Submission Number"]))

        phone_match = bool(contact) and (phone in contact or contact in phone)
        name_match = bool(last) and last in name

        if phone_match and name_match and sub:
            if sub not in grouped:
                grouped[sub] = (data, r[1] or "Submitted")

    html = "<h2>Your Orders</h2>"

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

if __name__ == "__main__":
    app.run()
