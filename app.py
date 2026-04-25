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
    except:
        pass

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
    return re.sub(r"\D", "", str(v))

def read_file(file):
    if file.filename.endswith(("xlsx","xls")):
        return pd.read_excel(file)

    raw = file.read()
    file.seek(0)

    try:
        return pd.read_csv(io.StringIO(raw.decode("utf-8")))
    except:
        return pd.read_csv(io.StringIO(raw.decode("latin1")))

# =========================
# STATUS
# =========================
def normalize_psa_status(s):
    s = str(s).lower()

    if "order arrived" in s: return "Order Arrived"
    if "research" in s: return "Research & ID"
    if "grading" in s: return "Grading"
    if "qa" in s: return "QA Checks"
    if "complete" in s: return "Complete"

    return None

def status_rank(s):
    order = [
        "Submitted","Order Arrived","Research & ID",
        "Grading","QA Checks","Complete",
        "Delivered to Us","Picked Up"
    ]
    return order.index(s) if s in order else 0

# =========================
# SAVE
# =========================
def save_row(sub, raw):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO submissions (submission_number, raw_data)
    VALUES (%s,%s)
    ON CONFLICT (submission_number)
    DO UPDATE SET raw_data=EXCLUDED.raw_data
    """,(sub,json.dumps(raw)))

    conn.commit()
    cur.close()
    conn.close()

# =========================
# ROUTES
# =========================
@app.route("/")
def root():
    return redirect("/admin")

@app.route("/admin/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        if request.form.get("password")==ADMIN_PASSWORD:
            session["admin"]=True
            return redirect("/admin")
    return "<form method=post><input name=password><button>Login</button></form>"

@app.route("/admin")
@admin_required
def dashboard():
    conn=get_conn()
    cur=conn.cursor()
    cur.execute("SELECT submission_number,status FROM submissions LIMIT 50")
    rows=cur.fetchall()
    cur.close(); conn.close()

    return "<br>".join([f"{r[0]} - {r[1]}" for r in rows])

# =========================
# EXCEL UPLOAD
# =========================
@app.route("/admin/upload", methods=["POST"])
@admin_required
def upload():
    file=request.files["file"]
    df=read_file(file)

    for _,row in df.iterrows():
        raw={c:clean(row[c]) for c in df.columns}
        sub=normalize_submission(raw.get("Submission #"))
        if sub:
            save_row(sub,raw)

    return "uploaded"

# =========================
# PSA PDF (FIXED HERE ONLY)
# =========================
@app.route("/admin/upload_psa", methods=["POST"])
@admin_required
def upload_psa():
    import pdfplumber, tempfile

    file=request.files["file"]
    temp=tempfile.NamedTemporaryFile(delete=False,suffix=".pdf")
    file.save(temp.name)

    full_text=""
    with pdfplumber.open(temp.name) as pdf:
        for p in pdf.pages:
            full_text += "\n" + (p.extract_text() or "")

    os.unlink(temp.name)

    # ===== FIX (ONLY CHANGE IN ENTIRE FILE) =====
    matches = re.findall(
        r"Sub\s*#\s*(\d+)\s+(Order Arrived|Research\s*&\s*ID|Grading|QA Checks|Assembly|Complete)",
        full_text,
        re.IGNORECASE
    )
    # ===========================================

    best={}
    for sub,raw_status in matches:
        status=normalize_psa_status(raw_status)
        if not status: continue

        if sub not in best or status_rank(status)>status_rank(best[sub]):
            best[sub]=status

    conn=get_conn()
    cur=conn.cursor()

    for sub,status in best.items():
        cur.execute("""
        UPDATE submissions
        SET status=%s
        WHERE REGEXP_REPLACE(submission_number,'\\D','','g')=%s
        """,(status,sub))

    conn.commit()
    cur.close(); conn.close()

    return f"updated {len(best)}"

# =========================
if __name__=="__main__":
    app.run()
