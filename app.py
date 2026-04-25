<PASTE EVERYTHING BELOW EXACTLY>

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
        <pre>{traceback.format_exc()}</pre>
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

def normalize_psa_status(status):
    s = re.sub(r"\s+", " ", str(status or "")).strip().lower()
    if s == "order arrived": return "Order Arrived"
    if s == "research & id": return "Research & ID"
    if s == "grading": return "Grading"
    if s == "qa checks": return "QA Checks"
    if s == "assembly": return "QA Checks"
    if s == "complete": return "Complete"
    return None

# =========================
# ADMIN PSA UPLOAD (FIXED)
# =========================
@app.route("/admin/upload_psa", methods=["GET", "POST"])
@admin_required
def admin_upload_psa():
    if request.method == "POST":
        try:
            import pdfplumber, tempfile

            file = request.files.get("file")
            temp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            file.save(temp.name)

            best = {}

            with pdfplumber.open(temp.name) as pdf:
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    blocks = re.split(r"(?=Sub\s*#\s*\d+)", text, flags=re.I)

                    for block in blocks:
                        sub_match = re.search(r"Sub\s*#\s*(\d+)", block, re.I)
                        if not sub_match:
                            continue

                        sub = normalize_submission(sub_match.group(1))

                        # ===== FIXED PARSER =====
                        lines = block.splitlines()
                        status = None

                        for i, line in enumerate(lines):
                            if re.search(r"Sub\s*#\s*\d+", line, re.I):
                                if i + 1 < len(lines):
                                    next_line = lines[i + 1].strip()

                                    for s in [
                                        "Order Arrived",
                                        "Research & ID",
                                        "Grading",
                                        "QA Checks",
                                        "Assembly",
                                        "Complete"
                                    ]:
                                        if s.lower() in next_line.lower():
                                            status = normalize_psa_status(s)
                                            break
                                break

                        if status:
                            best[sub] = status

            conn = get_conn()
            cur = conn.cursor()

            for sub, status in best.items():
                cur.execute("""
                UPDATE submissions
                SET status=%s
                WHERE REGEXP_REPLACE(submission_number,'\\D','','g')=%s
                AND COALESCE(status,'') NOT IN ('Picked Up','Delivered to Us')
                """, (status, sub))

            conn.commit()
            cur.close()
            conn.close()

            return "done"

        except Exception:
            return traceback.format_exc()

    return "<form method=post enctype=multipart/form-data><input type=file name=file><button>upload</button></form>"

if __name__ == "__main__":
    app.run()

<END FILE>
