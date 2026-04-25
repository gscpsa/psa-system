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
    return re.sub(r"\D", "", str(v))

def normalize_phone(v):
    return re.sub(r"\D", "", str(v or ""))

def get_field(data, names):
    for n in names:
        for k, v in data.items():
            if str(k).lower().strip() == n.lower():
                return v
    return ""

# 🔥 FIXED DETECTION
def detect_internal_status(raw):
    text = " ".join([str(k) + " " + str(v) for k, v in raw.items()]).lower()

    if "picked up" in text:
        return "Picked Up"

    if (
        "delivered to us" in text or
        "received by us" in text or
        "arrived at store" in text
    ):
        return "Delivered to Us"

    return None

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

# 🔥 FIXED SAVE LOGIC
def save_row(sub, raw):
    conn = get_conn()
    cur = conn.cursor()

    new_status = detect_internal_status(raw)

    # remove any status columns from raw display
    for k in list(raw.keys()):
        if "status" in str(k).lower():
            del raw[k]

    # get existing status
    cur.execute("SELECT status FROM submissions WHERE submission_number=%s", (sub,))
    existing = cur.fetchone()
    existing_status = existing[0] if existing else None

    # PICKED UP = HARD LOCK
    if existing_status == "Picked Up":
        cur.execute("""
        UPDATE submissions
        SET raw_data=%s, last_updated=NOW()
        WHERE submission_number=%s
        """, (json.dumps(raw), sub))

    elif new_status:
        # Excel overrides ONLY for Delivered or Picked
        cur.execute("""
        INSERT INTO submissions (submission_number, status, raw_data)
        VALUES (%s,%s,%s)
        ON CONFLICT (submission_number)
        DO UPDATE SET
            status=%s,
            raw_data=EXCLUDED.raw_data,
            last_updated=NOW()
        """, (sub, new_status, json.dumps(raw), new_status))

    else:
        # No Excel status → preserve PSA
        cur.execute("""
        INSERT INTO submissions (submission_number, raw_data)
        VALUES (%s,%s)
        ON CONFLICT (submission_number)
        DO UPDATE SET
            raw_data=EXCLUDED.raw_data,
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
        <a href="/admin">Admin</a>
        <a href="/admin/search">Search</a>
        <a href="/admin/upload">Upload Excel</a>
        <a href="/admin/upload_psa">Upload PDF</a>
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
    <body style="font-family:Arial;background:#f4f6f8;margin:0">
    <div style="background:#1f2937;color:white;padding:12px">
        PSA Tracking
        <span style="float:right">{nav}</span>
    </div>
    <div style="padding:15px">{content}</div>
    </body>
    </html>
    """

# =========================
# STATUS BAR
# =========================
def status_bar(status):
    steps = [
        "Submitted","Order Arrived","Research & ID",
        "Grading","QA Checks","Complete",
        "Delivered to Us","Picked Up"
    ]

    idx = steps.index(status) if status in steps else 0

    html = "<div style='display:flex;gap:5px;flex-wrap:wrap'>"
    for i,s in enumerate(steps):
        color = "#ddd"
        if i < idx: color = "#a5b4fc"
        if i == idx: color = "#2563eb;color:white"
        html += f"<div style='padding:4px 8px;background:{color};border-radius:15px;font-size:12px'>{s}</div>"
    html += "</div>"
    return html

# =========================
# TABLE (TIGHT SPACING FIX)
# =========================
def build_table(rows):
    keys=set()
    clean=[]

    for r in rows:
        data=r[0] or {}
        row={}

        for k,v in data.items():
            if "unnamed" in str(k).lower():
                continue
            if k=="S":
                k="Submission Date"
            row[k]=v

        row["PSA Status"]=r[1]
        clean.append(row)
        keys.update(row.keys())

    ordered=sorted(keys)

    html="<table style='border-collapse:collapse;font-size:12px;width:100%'>"
    html+="<tr>"+"".join([f"<th style='padding:4px'>{k}</th>" for k in ordered])+"</tr>"

    for row in clean:
        html+="<tr>"
        for k in ordered:
            html+=f"<td style='padding:4px'>{row.get(k,'')}</td>"
        html+="</tr>"

    html+="</table>"
    return html

# =========================
# ADMIN + PORTAL (unchanged)
# =========================
@app.route("/")
def root(): return redirect("/admin")

@app.route("/admin/login",methods=["GET","POST"])
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

@app.route("/admin/upload",methods=["POST"])
@admin_required
def upload():
    df=read_file(request.files["file"])
    for _,row in df.iterrows():
        raw={c:clean(row[c]) for c in df.columns}
        sub=normalize_submission(raw.get("Submission #"))
        if sub:
            save_row(sub,raw)
    return "OK"

@app.route("/admin/upload_psa",methods=["POST"])
@admin_required
def upload_psa():
    import pdfplumber,tempfile

    f=request.files["file"]
    temp=tempfile.NamedTemporaryFile(delete=False)
    f.save(temp.name)

    PRIORITY={"Order Arrived":1,"Research & ID":2,"Grading":3,"QA Checks":4,"Complete":5}
    best={}

    with pdfplumber.open(temp.name) as pdf:
        for p in pdf.pages:
            for t in p.extract_tables():
                for r in t:
                    txt=" ".join([str(c or "") for c in r])
                    m=re.search(r"Sub\s*#(\d+)",txt)
                    if not m: continue
                    sub=m.group(1)
                    for s in PRIORITY:
                        if s in txt:
                            if sub not in best or PRIORITY[s]>PRIORITY[best[sub]]:
                                best[sub]=s

    conn=get_conn(); cur=conn.cursor()
    for sub,status in best.items():
        cur.execute("""
        UPDATE submissions
        SET status=%s
        WHERE submission_number=%s
        AND status!='Picked Up'
        """,(status,sub))
    conn.commit()
    cur.close(); conn.close()

    return "OK"

@app.route("/portal",methods=["GET","POST"])
def portal():
    if request.method=="POST":
        session["phone"]=normalize_phone(request.form.get("phone"))
        session["last"]=request.form.get("last","").lower()
        return redirect("/portal/orders")
    return page("<form method='post'><input name='phone'><input name='last'><button>Go</button></form>",mode="portal")

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
        name=str(get_field(data,["Name"])).lower()
        contact=normalize_phone(get_field(data,["Phone"]))
        if phone in contact and last in name:
            html+=f"<div><b>{name}</b><br>Status:{status}{status_bar(status)}</div>"

    return page(html,mode="portal")

if __name__=="__main__":
    app.run()
