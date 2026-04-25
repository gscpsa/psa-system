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
# STATUS LOGIC
# =========================
def normalize_psa_status(status):
    s = re.sub(r"\s+", " ", str(status or "")).strip().lower()

    if s == "order arrived": return "Order Arrived"
    if s == "research & id": return "Research & ID"
    if s == "grading": return "Grading"
    if s == "qa checks": return "QA Checks"
    if s == "assembly": return "QA Checks"
    if s == "complete": return "Complete"

    return None

def status_rank(status):
    ranks = {
        "Submitted": 0,
        "Order Arrived": 1,
        "Research & ID": 2,
        "Grading": 3,
        "QA Checks": 4,
        "Complete": 5,
        "Delivered to Us": 6,
        "Picked Up": 7,
    }
    return ranks.get(status or "Submitted", 0)

def detect_internal_status(raw):
    full_text = " ".join([f"{k} {v}" for k, v in raw.items()]).lower()

    if "picked up" in full_text:
        return "Picked Up"

    if "delivered to us" in full_text or "received by us" in full_text:
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
        pass

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
            last_updated=NOW()
        """, (sub, json.dumps(raw)))

    conn.commit()
    cur.close()
    conn.close()

# =========================
# UI + TABLE
# =========================
def should_hide_column(name):
    return str(name).strip().lower() in [
        "status", "current status", "psa status"
    ]

def build_table(rows):
    keys = []
    clean_rows = []

    for r in rows:
        data = r[0] or {}
        row = {}

        for k, v in data.items():
            if "unnamed" in str(k).lower():
                continue
            if should_hide_column(k):
                continue

            display_key = "Submission Date" if str(k).strip() == "S" else str(k)

            row[display_key] = v
            if display_key not in keys:
                keys.append(display_key)

        row["PSA Status"] = r[1] or "Submitted"
        if "PSA Status" not in keys:
            keys.append("PSA Status")

        clean_rows.append(row)

    html = "<table><tr>" + "".join([f"<th>{k}</th>" for k in keys]) + "</tr>"

    for row in clean_rows:
        html += "<tr>"
        for k in keys:
            val = row.get(k, "")
            if k == "PSA Status":
                html += f"<td class='status'>{val}</td>"
            else:
                html += f"<td>{val}</td>"
        html += "</tr>"

    html += "</table>"
    return html

def page(content, mode="admin"):
    return f"""
    <html>
    <body style="font-family:Arial;background:#f4f6f8;">
    <div style="background:#0f5132;color:white;padding:15px;">Giant Sports Cards</div>
    <div style="padding:20px;">{content}</div>
    </body>
    </html>
    """

# =========================
# SORT FIX (ONLY CHANGE)
# =========================
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

    def get_date(row):
        data = row[0] or {}
        val = get_field(data, ["Submission Date", "S", "Date"])
        try:
            return pd.to_datetime(val)
        except:
            return pd.Timestamp.min

    rows = sorted(rows, key=get_date, reverse=(sort=="new"))

    html = """
    <a href="/admin?sort=new">Newest</a> |
    <a href="/admin?sort=old">Oldest</a><br><br>
    """

    html += build_table(rows)
    return page(html)

# =========================
# PSA PDF PARSER (STABLE)
# =========================
@app.route("/admin/upload_psa", methods=["POST"])
@admin_required
def upload_psa():
    import pdfplumber, tempfile

    file = request.files["file"]
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    file.save(temp.name)

    full_text = ""
    with pdfplumber.open(temp.name) as pdf:
        for p in pdf.pages:
            full_text += "\n" + (p.extract_text() or "")

    os.unlink(temp.name)

    matches = re.findall(
        r"Sub\s*#\s*(\d+)[^\n\r]{0,80}?(Order Arrived|Research\s*&\s*ID|Grading|QA Checks|Assembly|Complete)",
        full_text,
        re.IGNORECASE
    )

    best = {}
    for sub, raw_status in matches:
        status = normalize_psa_status(raw_status)
        if not status:
            continue

        if sub not in best or status_rank(status) > status_rank(best[sub]):
            best[sub] = status

    conn = get_conn()
    cur = conn.cursor()

    updated = 0
    skipped = 0

    for sub, status in best.items():
        cur.execute("""
        UPDATE submissions
        SET status=%s
        WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
          AND COALESCE(status, '') NOT IN ('Picked Up','Delivered to Us')
        """, (status, sub))

        if cur.rowcount:
            updated += 1
        else:
            skipped += 1

    conn.commit()
    cur.close()
    conn.close()

    return f"Statuses found: {len(best)} Updated: {updated} Skipped: {skipped}"

# =========================
# CUSTOMER PORTAL (UNCHANGED)
# =========================
@app.route("/portal")
def portal():
    return page("<h2>Customer Portal</h2>")

if __name__ == "__main__":
    app.run()
