from flask import Flask, request, session, redirect
import pandas as pd
import psycopg2
import os, io, json, re, traceback

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "temporary-dev-secret-change-later")
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
    return re.sub(r"\D", "", str(val).split(".")[0])

def normalize_phone(val):
    return re.sub(r"\D", "", str(val or ""))

def read_file(file):
    if file.filename.lower().endswith(("xlsx","xls")):
        return pd.read_excel(file)
    raw = file.read()
    file.seek(0)
    return pd.read_csv(io.StringIO(raw.decode("latin1")), on_bad_lines="skip")

def get_field(data, names):
    for n in names:
        for k, v in data.items():
            if k.lower().strip() == n.lower().strip():
                return v
    return ""

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
    body {{font-family:Arial;background:#f4f6f8;margin:0;color:#111827}}
    .topbar {{background:#1f2937;color:white;padding:15px}}
    .topbar a {{color:white;margin-left:15px;text-decoration:none}}
    .container {{padding:20px}}
    table {{width:100%;border-collapse:collapse;background:white}}
    th {{background:#111827;color:white;padding:10px;text-align:left}}
    td {{padding:8px;border-bottom:1px solid #ddd}}
    tr:hover {{background:#f1f5f9}}
    .status {{color:#2563eb;font-weight:bold}}
    input,button {{padding:10px;margin:4px}}
    .card {{background:white;padding:18px;margin-bottom:16px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,.08)}}
    .bar {{display:flex;gap:6px;margin-top:10px;flex-wrap:wrap}}
    .step {{padding:8px 10px;border-radius:20px;background:#e5e7eb;font-size:13px}}
    .done {{background:#bfdbfe;color:#1e40af;font-weight:bold}}
    .current {{background:#2563eb;color:white;font-weight:bold}}
    </style>
    </head>
    <body>
    <div class="topbar">
    PSA Tracking |
    <a href="/">Admin Dashboard</a>
    <a href="/upload">Upload Excel</a>
    <a href="/upload_psa">Upload PDF</a>
    <a href="/search">Search</a>
    <a href="/portal">Customer Portal</a>
    </div>
    <div class="container">{content}</div>
    </body>
    </html>
    """

def render_status_bar(status):
    steps = ["Order Arrived", "Research & ID", "Grading", "QA Checks", "Complete", "Picked Up"]
    status = status or "Submitted"

    if status == "Submitted":
        current_index = -1
    elif status in steps:
        current_index = steps.index(status)
    else:
        current_index = -1

    html = "<div class='bar'>"
    for i, step in enumerate(steps):
        cls = "step"
        if i < current_index:
            cls += " done"
        elif i == current_index:
            cls += " current"
        html += f"<div class='{cls}'>{step}</div>"
    html += "</div>"
    return html

def build_table(rows):
    keys = set()
    clean_rows = []

    for r in rows:
        data = r[0] or {}
        row = {}

        for k, v in data.items():
            if "unnamed" in k.lower():
                continue
            if k == "S":
                k = "Submission Date"
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

# =========================
# ADMIN DASHBOARD
# =========================
@app.route("/")
def dashboard():
    sort = request.args.get("sort", "new")
    order = "DESC" if sort == "new" else "ASC"

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT raw_data, status FROM submissions ORDER BY last_updated {order}")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    html = """
    <h2>Admin Dashboard</h2>
    <a href="/?sort=new">Newest</a> |
    <a href="/?sort=old">Oldest</a>
    <br><br>
    """
    html += build_table(rows)
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
    """, (f"%{q}%", f"%{q}%", f"%{q}%"))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    html = f"""
    <h2>Search</h2>
    <form>
    <input name="q" value="{q}" placeholder="Search name, phone, submission, status">
    <button>Search</button>
    </form><br>
    """
    html += build_table(rows)
    return page(html)

# =========================
# EXCEL UPLOAD
# =========================
@app.route("/upload", methods=["POST","GET"])
def upload():
    if request.method == "POST":
        df = read_file(request.files["file"])
        df.columns = [str(c).strip() for c in df.columns]

        count = 0
        for _, row in df.iterrows():
            raw = {c:clean(row[c]) for c in df.columns}
            sub = normalize_submission(raw.get("Submission #") or raw.get("Submission Number"))
            if sub:
                save_row(sub, raw)
                count += 1

        return page(f"Excel uploaded. Rows processed: {count}")

    return page("""
    <h2>Upload Excel / CSV</h2>
    <form method="post" enctype="multipart/form-data">
    <input type="file" name="file">
    <button>Upload</button>
    </form>
    """)

# =========================
# PSA PDF UPLOAD
# =========================
@app.route("/upload_psa", methods=["POST","GET"])
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
                if t:
                    text += t + "\n"

        os.unlink(temp.name)

        blocks = re.split(r"Sub\s*#", text)

        status_priority = {
            "Order Arrived":1,
            "Research & ID":2,
            "Grading":3,
            "QA Checks":4,
            "Complete":5
        }

        best = {}

        for b in blocks:
            if not b.strip() or not b[0].isdigit():
                continue

            m = re.match(r"\d+", b)
            if not m:
                continue

            sub = normalize_submission(m.group())

            for s in status_priority:
                if s in b:
                    if sub not in best or status_priority[s] < status_priority[best[sub]]:
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

        return page(f"PDF uploaded. Statuses updated: {updated}")

    return page("""
    <h2>Upload PSA PDF</h2>
    <form method="post" enctype="multipart/form-data">
    <input type="file" name="file">
    <button>Upload PDF</button>
    </form>
    """)

# =========================
# CUSTOMER PORTAL
# =========================
@app.route("/portal", methods=["GET","POST"])
def portal():
    if request.method == "POST":
        phone = normalize_phone(request.form.get("phone"))
        last = clean(request.form.get("last")).lower()

        if not phone or not last:
            return page("Enter phone and last name.")

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT raw_data, status FROM submissions")
        rows = cur.fetchall()
        cur.close()
        conn.close()

        matches = []

        for r in rows:
            data = r[0] or {}

            name = str(get_field(data, ["Customer Name", "Name"])).lower()
            contact = normalize_phone(get_field(data, ["Contact Info", "Phone", "Phone Number"]))

            if phone in contact and last in name:
                matches.append(r)

        if not matches:
            return page("""
            <h2>Customer Portal</h2>
            <p>No matching orders found. Check phone number and last name.</p>
            <a href="/portal">Try again</a>
            """)

        session["customer_phone"] = phone
        session["customer_last"] = last
        return redirect("/my_orders")

    return page("""
    <h2>Customer Portal</h2>
    <p>Enter the phone number and last name used on your submission.</p>
    <form method="post">
    <input name="phone" placeholder="Phone number">
    <input name="last" placeholder="Last name">
    <button>View My Orders</button>
    </form>
    """)

@app.route("/my_orders")
def my_orders():
    phone = session.get("customer_phone")
    last = session.get("customer_last")

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

        if phone in contact and last in name:
            sub = normalize_submission(get_field(data, ["Submission #", "Submission Number"]))
            if sub and sub not in grouped:
                grouped[sub] = (data, r[1])

    if not grouped:
        return page("No orders found.")

    html = "<h2>My Orders</h2>"

    for sub, item in grouped.items():
        data, status = item
        service = get_field(data, ["Service Type", "Service"])
        date = get_field(data, ["S", "Submission Date", "Date"])
        cards = get_field(data, ["# Of Cards", "# of Cards", "Cards"])
        display_status = status or "Submitted"

        html += f"""
        <div class="card">
            <h3>Submission #{sub}</h3>
            <p><b>Status:</b> <span class="status">{display_status}</span></p>
            <p><b>Service:</b> {service}</p>
            <p><b>Cards:</b> {cards}</p>
            <p><b>Submission Date:</b> {date}</p>
            {render_status_bar(display_status)}
        </div>
        """

    html += "<a href='/logout'>Log out</a>"
    return page(html)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/portal")

# =========================
if __name__ == "__main__":
    app.run()
