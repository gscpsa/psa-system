from flask import Flask, request, session, redirect
import pandas as pd
import psycopg2
import os, io, json, re, traceback
from functools import wraps

app = Flask(__name__)

PREFERRED_DASHBOARD_COLUMNS = [
    "Submission #","Submission Number","Submission","Customer Name","Name",
    "Customer Contact","Contact","Phone","Status","PSA Status","Arrived",
    "Completed","Arrived / Completed","Submission Date","S","ƒand","fand",
    "Date","Service","Declared Value","Total Cards","Notes",
]

def ordered_display_keys(data):
    keys = list(data.keys())
    ordered, seen = [], set()
    for wanted in PREFERRED_DASHBOARD_COLUMNS:
        for key in keys:
            if key not in seen and key.strip().lower() == wanted.strip().lower():
                ordered.append(key); seen.add(key)
    for key in keys:
        if key not in seen:
            ordered.append(key); seen.add(key)
    return ordered

app.secret_key = os.getenv("SECRET_KEY", "change-this-secret")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS submissions (
        submission_number TEXT PRIMARY KEY,
        status TEXT DEFAULT 'Submitted',
        raw_data JSONB,
        last_updated TIMESTAMP DEFAULT NOW()
    )""")
    conn.commit(); cur.close(); conn.close()

@app.before_request
def setup():
    try: init_db()
    except Exception: pass

@app.errorhandler(Exception)
def error_handler(e):
    return page(f"<div class='card'><h2>Error</h2><pre>{traceback.format_exc()}</pre></div>")

def admin_required(f):
    @wraps(f)
    def wrapper(*a, **k):
        if not session.get("admin"): return redirect("/admin/login")
        return f(*a, **k)
    return wrapper

def clean(v):
    try:
        if pd.isna(v): return ""
    except: pass
    return str(v).strip()

def normalize_submission(v):
    return re.sub(r"\D","",str(v).split(".")[0]) if v else None

def normalize_phone(v):
    return re.sub(r"\D","",str(v or ""))

def get_field(data, names):
    for wanted in names:
        for k in ordered_display_keys(data):
            if str(k).strip().lower()==wanted.lower():
                return data.get(k)
    return ""

def read_file(file):
    name=(file.filename or "").lower()
    if name.endswith(("xlsx","xls")):
        return pd.read_excel(file)
    raw=file.read(); file.seek(0)
    try:
        return pd.read_csv(io.StringIO(raw.decode("utf-8")),on_bad_lines="skip")
    except:
        return pd.read_csv(io.StringIO(raw.decode("latin1")),on_bad_lines="skip")

def normalize_psa_status(status):
    s=re.sub(r"\s+"," ",str(status or "")).strip().lower()
    if s=="order arrived": return "Order Arrived"
    if s=="research & id": return "Research & ID"
    if s=="grading": return "Grading"
    if s=="qa checks": return "QA Checks"
    if s=="assembly": return "Assembly"
    if s=="shipping soon": return "Shipping Soon"
    if s=="complete": return "Complete"
    return None

def status_rank(status):
    ranks = {
        "Submitted": 0,
        "Order Arrived": 1,
        "Research & ID": 2,
        "Grading": 3,
        "Assembly": 4,      # FIXED
        "QA Checks": 5,     # FIXED
        "Shipping Soon": 6,
        "Complete": 7,
        "Delivered to Us": 8,
        "Picked Up": 9,
    }
    return ranks.get(status or "Submitted", 0)

def detect_internal_status(raw):
    full=" ".join([f"{k} {v}" for k,v in raw.items()]).lower()
    if "picked up" in full: return "Picked Up"
    if "delivered to us" in full: return "Delivered to Us"
    return None

def save_row(sub, raw):
    conn=get_conn(); cur=conn.cursor()
    internal=detect_internal_status(raw)
    cur.execute("SELECT status FROM submissions WHERE submission_number=%s",(sub,))
    existing=cur.fetchone()
    existing_status=existing[0] if existing else None

    if internal:
        cur.execute("""
        INSERT INTO submissions (submission_number,status,raw_data)
        VALUES (%s,%s,%s)
        ON CONFLICT (submission_number)
        DO UPDATE SET status=%s, raw_data=EXCLUDED.raw_data
        """,(sub,internal,json.dumps(raw),internal))
    else:
        cur.execute("""
        INSERT INTO submissions (submission_number,status,raw_data)
        VALUES (%s,'Submitted',%s)
        ON CONFLICT (submission_number)
        DO UPDATE SET raw_data=EXCLUDED.raw_data
        """,(sub,json.dumps(raw)))

    conn.commit(); cur.close(); conn.close()

def page(content,mode="admin"):
    nav = "<a href='/admin'>Dashboard</a>" if mode=="admin" else "<a href='/portal'>Home</a>"
    return f"<html><body>{nav}<div>{content}</div></body></html>"

def status_bar(status):
    steps = [
        "Submitted","Order Arrived","Research & ID","Grading",
        "Assembly",      # FIXED
        "QA Checks",     # FIXED
        "Shipping Soon","Complete","Delivered to Us","Picked Up"
    ]
    status=status or "Submitted"
    idx=steps.index(status) if status in steps else 0
    html="<div>"
    for i,step in enumerate(steps):
        html+=f"[{step}] "
    html+="</div>"
    return html

def build_table(rows):
    html="<table border=1><tr><th>Submission</th><th>Status</th></tr>"
    for r in rows:
        html+=f"<tr><td>{r[0].get('Submission #')}</td><td>{r[1]}</td></tr>"
    html+="</table>"
    return html

@app.route("/admin")
@admin_required
def admin_dashboard():
    conn=get_conn(); cur=conn.cursor()
    cur.execute("SELECT raw_data,status FROM submissions")
    rows=cur.fetchall()
    cur.close(); conn.close()

    rows=sorted(rows,key=lambda x:status_rank(x[1]))
    return page(build_table(rows))

@app.route("/admin/login",methods=["GET","POST"])
def admin_login():
    if request.method=="POST":
        if request.form.get("password")==ADMIN_PASSWORD:
            session["admin"]=True
            return redirect("/admin")
    return page("<form method=post><input name=password><button>Login</button></form>")

@app.route("/portal",methods=["GET","POST"])
def portal():
    if request.method=="POST":
        session["phone"]=normalize_phone(request.form.get("phone"))
        session["last"]=clean(request.form.get("last")).lower()
        return redirect("/portal/orders")
    return page("<form method=post><input name=phone><input name=last><button>Go</button></form>",mode="portal")

@app.route("/portal/orders")
def portal_orders():
    return page("<h2>Orders</h2>",mode="portal")

if __name__=="__main__":
    app.run()
