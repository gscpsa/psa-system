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
    return page(f"""
    <div class="card">
        <h2>Application Error</h2>
        <p>The app hit an internal error. Details below:</p>
        <pre>{traceback.format_exc()}</pre>
        <a href="/admin">Back to Admin</a>
    </div>
    """)

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
# STATUS LOGIC
# =========================
def normalize_psa_status(status):
    s = re.sub(r"\s+", " ", str(status or "")).strip().lower()

    if s == "order arrived":
        return "Order Arrived"
    if s == "research & id":
        return "Research & ID"
    if s == "grading":
        return "Grading"
    if s == "qa checks":
        return "QA Checks"
    if s == "assembly":
        return "Assembly"
    if s == "shipping soon":
        return "Shipping Soon"
    if s == "complete":
        return "Complete"

    return None

def status_rank(status):
    ranks = {
        "Submitted": 0,
        "Order Arrived": 1,
        "Research & ID": 2,
        "Grading": 3,
        "QA Checks": 4,
        "Assembly": 5,
        "Shipping Soon": 6,
        "Complete": 7,
        "Delivered to Us": 8,
        "Picked Up": 9,
    }
    return ranks.get(status or "Submitted", 0)

def detect_internal_status(raw):
    full_text = " ".join([f"{k} {v}" for k, v in raw.items()]).lower()

    if "not picked up" in full_text or "not picked-up" in full_text:
        return None

    if "picked up" in full_text or "customer picked up" in full_text:
        return "Picked Up"

    if (
        "delivered to us" in full_text
        or "received by us" in full_text
        or "arrived at store" in full_text
        or "delivered back" in full_text
    ):
        return "Delivered to Us"

    return None

def save_row(sub, raw):
    conn = get_conn()
    cur = conn.cursor()

    internal_status = detect_internal_status(raw)

    cur.execute("""
    SELECT status FROM submissions
    WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
    """, (sub,))
    existing = cur.fetchone()
    existing_status = existing[0] if existing else None

    if existing_status == "Picked Up":
        cur.execute("""
        UPDATE submissions
        SET raw_data=%s, last_updated=NOW()
        WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
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
# UI
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
    <meta name='viewport' content='width=device-width, initial-scale=1.0'>

    <style>
    * {{
        box-sizing: border-box;
    }}

    body {{
        margin:0;
        font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;
        background:
            radial-gradient(circle at top left, rgba(0,255,156,0.18), transparent 32%),
            radial-gradient(circle at top right, rgba(0,207,255,0.16), transparent 28%),
            linear-gradient(135deg, #07130f 0%, #0b0f0d 48%, #020403 100%);
        color:#f8fafc;
        min-height:100vh;
    }}

    .topbar {{
        padding:18px 26px;
        display:flex;
        justify-content:space-between;
        align-items:center;
        border-bottom:1px solid rgba(255,255,255,0.08);
        background:rgba(0,0,0,0.22);
        backdrop-filter:blur(14px);
        position:sticky;
        top:0;
        z-index:20;
    }}

    .brand {{
        font-size:22px;
        font-weight:800;
        letter-spacing:1px;
        color:#e5e7eb;
        text-transform:uppercase;
        
    }}

    .links {{
        display:flex;
        gap:10px;
        flex-wrap:wrap;
        justify-content:flex-end;
    }}

    .links a {{
        color:#d1d5db;
        text-decoration:none;
        font-size:13px;
        font-weight:700;
        padding:8px 10px;
        border-radius:999px;
        background:rgba(255,255,255,0.05);
        border:1px solid rgba(255,255,255,0.07);
        transition:0.18s ease;
    }}

    .links a:hover {{
        color:#ffffff;
        background:rgba(0,255,156,0.12);
        border-color:rgba(0,255,156,0.35);
        transform:translateY(-1px);
    }}

    .container {{
        width:100%;
        padding:28px;
        overflow-x:auto;
    }}

    body:has(.card form) .container {{
        display:flex;
        justify-content:center;
        align-items:center;
        min-height:calc(100vh - 85px);
    }}

    .card {{
        background:rgba(255,255,255,0.075);
        border:1px solid rgba(255,255,255,0.12);
        padding:28px;
        margin-bottom:18px;
        border-radius:20px;
        box-shadow:0 28px 80px rgba(0,0,0,0.48);
        backdrop-filter:blur(18px);
        color:#f8fafc;
    }}

    body:has(.card form) .card {{
        width:100%;
        max-width:430px;
    }}

    h2 {{
        margin:0 0 10px 0;
        color:#ffffff;
        letter-spacing:-0.02em;
    }}

    h3 {{
        color:#ffffff;
    }}

    p {{
        color:#cbd5e1;
        line-height:1.45;
    }}

    a {{
        color:#e5e7eb;
    }}

    input {{
        width:100%;
        padding:13px 14px;
        margin:7px 0 12px 0;
        border-radius:12px;
        border:1px solid rgba(255,255,255,0.14);
        background:rgba(255,255,255,0.065);
        color:#ffffff;
        font-size:15px;
        outline:none;
        transition:0.18s ease;
    }}

    input::placeholder {{
        color:#94a3b8;
    }}

    input:focus {{
        border-color:#e5e7eb;
        background:rgba(255,255,255,0.095);
        box-shadow:0 0 0 4px rgba(0,255,156,0.14);
    }}

    button {{
        width:100%;
        padding:13px 15px;
        border:none;
        border-radius:12px;
        background:linear-gradient(90deg, #e5e7eb, #198754);
        color:#03120c;
        font-weight:900;
        font-size:15px;
        cursor:pointer;
        transition:0.18s ease;
        box-shadow:0 14px 32px rgba(0,0,0,0.3);
    }}

    button:hover {{
        transform:translateY(-2px);
        box-shadow:0 18px 42px rgba(0,0,0,0.35);
    }}

    .btn {{
        display:inline-block;
        padding:10px 13px;
        background:rgba(255,255,255,0.08);
        color:#ffffff;
        text-decoration:none;
        border-radius:999px;
        margin:5px 8px 15px 0;
        font-weight:800;
        border:1px solid rgba(255,255,255,0.15);
        transition:0.18s ease;
    }}

    .btn:hover {{
        background:rgba(0,0,0,0.3);
        transform:translateY(-1px);
    }}

    table {{
        width:100%;
        border-collapse:separate;
        border-spacing:0;
        background:rgba(255,255,255,0.94);
        color:#111827;
        font-size:12px;
        table-layout:auto;
        border-radius:14px;
        overflow:hidden;
        box-shadow:0 20px 60px rgba(0,0,0,0.22);
    }}

    th {{
        background:#0f5132;
        color:white;
        padding:8px;
        text-align:left;
        position:sticky;
        top:0;
        white-space:nowrap;
        z-index:2;
    }}

    td {{
        padding:7px 8px;
        border-bottom:1px solid #e5e7eb;
        white-space:nowrap;
        max-width:180px;
        overflow:hidden;
        text-overflow:ellipsis;
        background:white;
    }}

    td.notes-col {{
        white-space:normal;
        max-width:220px;
        min-width:160px;
        overflow-wrap:break-word;
        word-break:break-word;
    }}

    tr:hover td {{
        background:#eef6f2;
    }}

    .status {{
        font-weight:900;
        color:#198754;
    }}

    .bar {{
        display:flex;
        gap:6px;
        flex-wrap:wrap;
        margin-top:12px;
    }}

    .step {{
        padding:7px 11px;
        border-radius:999px;
        background:rgba(255,255,255,0.13);
        border:1px solid rgba(255,255,255,0.12);
        color:#d1d5db;
        font-size:13px;
    }}

    .done {{
        background:rgba(0,255,156,0.16);
        color:#e5e7eb;
        font-weight:800;
        border-color:rgba(0,0,0,0.35);
    }}

    .current {{
        background:linear-gradient(90deg, #e5e7eb, #198754);
        color:#03120c;
        font-weight:900;
        border-color:transparent;
    }}

    pre {{
        background:#020617;
        color:#e5e7eb;
        padding:12px;
        overflow:auto;
        border-radius:12px;
        font-size:12px;
        border:1px solid rgba(255,255,255,0.10);
    }}

    @media (max-width: 760px) {{
        .topbar {{
            align-items:flex-start;
            gap:14px;
            flex-direction:column;
            padding:16px;
        }}

        .links {{
            justify-content:flex-start;
        }}

        .container {{
            padding:18px;
        }}

        .brand {{
            font-size:19px;
        }}
    }}
    </style>
    </head>

    <body>
        <div class="topbar">
            <div class="brand">Giant Sports Cards</div>
            <div class="links">{nav}</div>
        </div>

        <div class="container">{content}</div>
    </body>
    </html>
    """

def status_bar(status):
    steps = [
        "Submitted",
        "Order Arrived",
        "Research & ID",
        "Grading",
        "QA Checks",
        "Assembly",
        "Shipping Soon",
        "Complete",
        "Delivered to Us",
        "Picked Up"
    ]

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

def should_hide_column(column_name):
    key = str(column_name).strip().lower()

    return key in [
        "status",
        "current status",
        "psa status",
        "order status",
        "customer status"
    ]

def build_table(rows):
    keys = []
    clean_rows = []
    force_keys = ["Arrived / Completed"]

    for r in rows:
        data = r[0] or {}
        row = {}

        for k, v in data.items():
            key_text = str(k).strip()

            if "unnamed" in key_text.lower():
                continue

            if should_hide_column(key_text):
                continue

            display_key = "Submission Date" if key_text in ["S", "ƒand"] else key_text
            row[display_key] = v

            if display_key not in keys:
                keys.append(display_key)

        row["PSA Status"] = r[1] or "Submitted"

        if "PSA Status" not in keys:
            keys.append("PSA Status")

        clean_rows.append(row)

    for forced_key in force_keys:
        if forced_key not in keys:
            keys.append(forced_key)

    if not clean_rows:
        return "<div class='card'>No records found.</div>"

    html = "<table><tr>"
    for k in keys:
        html += f"<th>{k}</th>"
    html += "</tr>"

    for row in clean_rows:
        html += "<tr>"
        for k in keys:
            val = row.get(k, "")
            col_class = "notes-col" if "note" in k.lower() else ""

            if k == "PSA Status":
                html += f"<td class='status {col_class}'>{val}</td>"
            else:
                html += f"<td class='{col_class}'>{val}</td>"
        html += "</tr>"

    html += "</table>"
    return html

def get_sort_date(row):
    data = row[0] or {}
    date_value = get_field(data, ["Submission Date", "S", "ƒand", "Date"])

    try:
        if date_value:
            return pd.to_datetime(date_value)
    except Exception:
        pass

    return pd.Timestamp.min

# =========================
# ADMIN ROUTES
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
    session.pop("admin", None)
    return redirect("/admin/login")


@app.route("/admin/clear_submissions")
@admin_required
def clear_submissions():
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM submissions")
        conn.commit()
        cur.close()
        conn.close()

        return page("""
        <div class="card">
            <h2>All submission data cleared</h2>
            <p>Excel and PSA PDF submission records have been removed.</p>
            <p>You can now re-upload the Excel file and PSA PDF from a clean database.</p>
            <a class="btn" href="/admin">Back to Admin</a>
        </div>
        """)
    except Exception:
        return page(f"""
        <div class="card">
            <h2>Error Clearing Submission Data</h2>
            <pre>{traceback.format_exc()}</pre>
            <a class="btn" href="/admin">Back to Admin</a>
        </div>
        """)

@app.route("/admin")
@admin_required
def admin_dashboard():
    sort = request.args.get("sort", "new")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT raw_data, status FROM submissions")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    rows = sorted(rows, key=get_sort_date, reverse=(sort != "old"))

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
            return page(f"""
            <div class="card">
                <h2>Excel Upload Error</h2>
                <pre>{traceback.format_exc()}</pre>
                <a href="/admin/upload">Try again</a>
            </div>
            """)

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
            import tempfile

            file = request.files.get("file")

            if not file:
                return page("<div class='card'>No PDF uploaded.</div>")

            filename = (file.filename or "").lower()

            if not filename.endswith(".pdf"):
                return page(f"""
                <div class="card">
                    <h2>Wrong File Type</h2>
                    <p>You uploaded: <b>{filename}</b></p>
                    <p>This uploader only accepts PDF files from PSA.</p>
                    <p>If you uploaded a PSD/image by mistake, export or print the PSA Orders page as a PDF first.</p>
                    <a href="/admin/upload_psa">Back to PDF Upload</a>
                </div>
                """)

            temp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            file.save(temp.name)

            best = {}
            ac_map = {}
            pages_read = 0

            status_regex = re.compile(
                r"(Order\s+Arrived|Research\s*&\s*ID|Grading|QA\s+Checks|Assembly|Shipping\s+Soon|Complete)",
                re.IGNORECASE
            )

            def find_status(text_value):
                match = status_regex.search(text_value or "")
                if not match:
                    return None
                return normalize_psa_status(match.group(1))

            def parse_text(text):
                nonlocal best

                lines = [line.strip() for line in (text or "").splitlines()]
                i = 0

                while i < len(lines):
                    line = lines[i]
                    sub = None
                    search_parts = []

                    # Normal case:
                    # Sub #14577350
                    # Research & ID
                    sub_match = re.search(r"Sub\s*#\s*(\d+)", line, re.IGNORECASE)

                    if sub_match:
                        sub = normalize_submission(sub_match.group(1))

                        after_sub_text = line[sub_match.end():].strip()
                        if after_sub_text:
                            search_parts.append(after_sub_text)

                        j = i + 1
                        while j < len(lines):
                            next_line = lines[j].strip()

                            # Stop if the next submission starts before a status is found.
                            if re.search(r"Sub\s*#\s*\d+", next_line, re.IGNORECASE):
                                break

                            if next_line:
                                search_parts.append(next_line)

                            j += 1

                    # Split case:
                    # • Sub
                    # #14550482
                    # Research & ID
                    elif re.search(r"\bSub\b\s*$", line, re.IGNORECASE) and i + 1 < len(lines):
                        number_match = re.search(r"#\s*(\d+)", lines[i + 1], re.IGNORECASE)

                        if number_match:
                            sub = normalize_submission(number_match.group(1))

                            j = i + 2
                            while j < len(lines):
                                next_line = lines[j].strip()

                                if re.search(r"Sub\s*#\s*\d+", next_line, re.IGNORECASE):
                                    break

                                if next_line:
                                    search_parts.append(next_line)

                                j += 1

                    if sub:
                        status = None

                        # The actual PSA row status is the first valid status after the submission number.
                        for part in search_parts:
                            status = find_status(part)
                            if status:
                                break

                        if status:
                            best[sub] = status
                            # Extract Arrived / Completed
                            block_text = " ".join(search_parts)

                            # Fix broken PDF line breaks (Apr 22, \n 2026)
                            block_text = re.sub(r",\s+(\d{4})", r", \1", block_text)

                            matches = re.findall(
                                r"(Completed\s+[A-Za-z]{3}\s+\d{1,2},\s+\d{4}|Est\.\s+by\s+[A-Za-z]{3}\s+\d{1,2},\s+\d{4}|[A-Za-z]{3}\s+\d{1,2},\s+\d{4})",
                                block_text
                            )

                            if matches:
                                ac_map[sub] = " | ".join(matches)

                    i += 1

            try:
                # Fast path: PyMuPDF reads this PSA PDF much more reliably than pdfplumber.
                # Fallback keeps the route working if PyMuPDF is not installed on the host.
                try:
                    import fitz

                    doc = fitz.open(temp.name)
                    pages_read = len(doc)

                    for pdf_page in doc:
                        try:
                            text = pdf_page.get_text("text") or ""
                        except Exception:
                            continue

                        if text:
                            parse_text(text)

                    doc.close()

                except Exception:
                    import pdfplumber

                    with pdfplumber.open(temp.name) as pdf:
                        for pdf_page in pdf.pages:
                            pages_read += 1

                            try:
                                text = pdf_page.extract_text() or ""
                            except Exception:
                                continue

                            if text:
                                parse_text(text)

            finally:
                try:
                    os.unlink(temp.name)
                except Exception:
                    pass

            conn = get_conn()
            cur = conn.cursor()

            updated = 0
            skipped = 0

            for sub, status in best.items():
                cur.execute("""
                UPDATE submissions
                SET status=%s,
                    raw_data = jsonb_set(
                        COALESCE(raw_data, '{}'::jsonb),
                        '{Arrived / Completed}',
                        to_jsonb(%s::text),
                        true
                    ),
                    last_updated=NOW()
                WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
                  AND COALESCE(status, '') NOT IN ('Picked Up', 'Delivered to Us')
                """, (status, ac_map.get(sub, ""), sub))

                if cur.rowcount:
                    updated += 1
                else:
                    skipped += 1

            conn.commit()

            verification_rows = []
            mismatch_count = 0
            checked_count = 0

            # Show the first 150 parsed submissions so verification is clearly visible after upload.
            for sub, parsed_status in list(best.items())[:150]:
                cur.execute("""
                SELECT status FROM submissions
                WHERE REGEXP_REPLACE(submission_number, '\D', '', 'g')=%s
                """, (sub,))

                row = cur.fetchone()
                db_status = row[0] if row else "NOT FOUND"
                is_match = (db_status == parsed_status)
                match = "MATCH" if is_match else "MISMATCH"

                checked_count += 1
                if not is_match:
                    mismatch_count += 1

                result_style = "color:#198754;font-weight:bold;" if is_match else "color:#dc3545;font-weight:bold;"

                verification_rows.append(
                    f"<tr>"
                    f"<td>{sub}</td>"
                    f"<td>{parsed_status}</td>"
                    f"<td>{db_status}</td>"
                    f"<td style='{result_style}'>{match}</td>"
                    f"</tr>"
                )

            cur.close()
            conn.close()

            warning = ""
            if len(best) == 0:
                warning += """
                <p><b>Warning:</b> No PSA statuses were found. This usually means the PDF is not the PSA Orders page, or the PDF is image-only / unreadable text.</p>
                """

            if verification_rows:
                verification_html = "".join(verification_rows)
            else:
                verification_html = """
                <tr>
                    <td colspan="4">No verification rows were created because no matching parsed submissions were found.</td>
                </tr>
                """

            return page(f"""
            <div class="card" style="border:3px solid #198754;">
                <h2>PDF processed</h2>
                {warning}
                <p><b>Pages read:</b> {pages_read}</p>
                <p><b>Statuses found:</b> {len(best)}</p>
                <p><b>Updated:</b> {updated}</p>
                <p><b>Skipped:</b> {skipped}</p>

                <hr>

                <h2 style="color:#0f5132;">VERIFICATION RESULTS</h2>
                <p><b>Verification rows shown:</b> {checked_count}</p>
                <p><b>Mismatches in sample:</b> {mismatch_count}</p>
                <p>This compares what the PDF parser read against what is actually stored in the database after upload.</p>

                <table>
                    <tr>
                        <th>Submission #</th>
                        <th>PDF Parsed Status</th>
                        <th>Database Status After Upload</th>
                        <th>Result</th>
                    </tr>
                    {verification_html}
                </table>

                <br>
                <a class="btn" href="/admin">Back to Admin</a>
            </div>
            """)

        except Exception:
            return page(f"""
            <div class="card">
                <h2>PDF Upload Error</h2>
                <p>The file could not be processed.</p>
                <pre>{traceback.format_exc()}</pre>
                <a href="/admin/upload_psa">Try again</a>
            </div>
            """)

    return page("""
    <div class="card">
        <h2>Upload PSA PDF</h2>
        <form method="post" enctype="multipart/form-data">
            <input type="file" name="file" accept=".pdf,application/pdf">
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
        <p>Track your PSA submissions instantly.</p>
        <form method="post">
            <input name="phone" placeholder="Phone number">
            <input name="last" placeholder="Last name">
            <button>View My Orders</button>
        </form>
    </div>
    """, mode="portal")

@app.route("/portal/orders")
def portal_orders():
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

    html = "<h2>Your Orders</h2>"
    grouped = {}

    for r in rows:
        data = r[0] or {}
        name = str(get_field(data, ["Customer Name", "Name"])).lower()
        contact = normalize_phone(get_field(data, ["Contact Info", "Phone", "Phone Number"]))
        sub = normalize_submission(get_field(data, ["Submission #", "Submission Number"]))

        phone_match = bool(contact) and (phone in contact or contact in phone)
        name_match = bool(last) and last in name

        if phone_match and name_match and sub and sub not in grouped:
            grouped[sub] = (data, r[1] or "Submitted")

    if not grouped:
        html += "<div class='card'>No matching orders found. Check phone number and last name.</div>"
        return page(html, mode="portal")

    for sub, (data, status) in grouped.items():
        customer_name = get_field(data, ["Customer Name", "Name"])
        cards = get_field(data, ["# Of Cards", "# of Cards", "Cards"])
        service = get_field(data, ["Service Type", "Service"])
        date = get_field(data, ["S", "ƒand", "Submission Date", "Date"])
        arrived_completed = get_field(data, ["Arrived / Completed"])
        display_status = status or "Submitted"

        html += f"""
        <div class="card">
            <h3>{customer_name}</h3>
            <p><b>Submission #:</b> {sub}</p>
            <p><b>Status:</b> <span class="status">{display_status}</span></p>
            <p><b>Arrived / Completed:</b> {arrived_completed}</p>
            <p><b>Cards:</b> {cards}</p>
            <p><b>Service:</b> {service}</p>
            <p><b>Submission Date:</b> {date}</p>
            {status_bar(display_status)}
        </div>
        """

    html += "<a href='/portal/logout'>Log out</a>"
    return page(html, mode="portal")

@app.route("/portal/logout")
def portal_logout():
    session.pop("phone", None)
    session.pop("last", None)
    return redirect("/portal")

if __name__ == "__main__":
    app.run()
