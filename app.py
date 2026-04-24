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
def normalize_submission(v):
    if not v:
        return None
    return re.sub(r"\D","",str(v))

def normalize_phone(v):
    return re.sub(r"\D","",str(v or ""))

def get_field(data, names):
    for n in names:
        for k,v in data.items():
            if str(k).lower().strip()==n.lower():
                return v
    return ""

# =========================
# UI
# =========================
def page(content):
    return f"""
    <html>
    <body style="font-family:Arial;background:#f4f6f8;margin:0">
    <div style="background:#1f2937;color:white;padding:15px">
        PSA Tracking
        <span style="float:right">
            <a href='/admin' style='color:white'>Admin</a> |
            <a href='/portal' style='color:white'>Portal</a>
        </span>
    </div>
    <div style="padding:20px">{content}</div>
    </body>
    </html>
    """

def status_bar(status):
    steps=["Submitted","Order Arrived","Research & ID","Grading","QA Checks","Complete","Picked Up"]
    i=steps.index(status) if status in steps else 0
    html="<div style='display:flex;gap:6px'>"
    for idx,s in enumerate(steps):
        color="#e5e7eb"
        if idx<i: color="#bfdbfe"
        if idx==i: color="#2563eb;color:white"
        html+=f"<div style='padding:6px 10px;background:{color};border-radius:20px'>{s}</div>"
    html+="</div>"
    return html

# =========================
# ADMIN
# =========================
@app.route("/")
def root():
    return redirect("/admin")

@app.route("/admin/login",methods=["GET","POST"])
def login():
    if request.method=="POST":
        if request.form.get("password")==ADMIN_PASSWORD:
            session["admin"]=True
            return redirect("/admin")
        return page("Wrong password")
    return page("<form method='post'><input type='password' name='password'><button>Login</button></form>")

@app.route("/admin")
@admin_required
def admin():
    conn=get_conn()
    cur=conn.cursor()
    cur.execute("SELECT submission_number,status FROM submissions ORDER BY last_updated DESC")
    rows=cur.fetchall()
    cur.close(); conn.close()

    html="<h2>Admin</h2>"
    for r in rows:
        html+=f"<div>{r[0]} - {r[1]}</div>"
    return page(html)

# =========================
# EXCEL UPLOAD
# =========================
@app.route("/admin/upload",methods=["GET","POST"])
@admin_required
def upload():
    if request.method=="POST":
        df=pd.read_excel(request.files["file"])
        for _,row in df.iterrows():
            sub=normalize_submission(row.get("Submission #"))
            raw={str(c):str(row[c]) for c in df.columns}

            conn=get_conn()
            cur=conn.cursor()
            cur.execute("""
            INSERT INTO submissions (submission_number,status,raw_data)
            VALUES (%s,'Submitted',%s)
            ON CONFLICT (submission_number)
            DO UPDATE SET raw_data=EXCLUDED.raw_data
            """,(sub,json.dumps(raw)))
            conn.commit()
            cur.close(); conn.close()

        return page("Excel uploaded")

    return page("<form method='post' enctype='multipart/form-data'><input type='file' name='file'><button>Upload</button></form>")

# =========================
# PORTAL (FIXED)
# =========================
@app.route("/portal",methods=["GET","POST"])
def portal():
    if request.method=="POST":
        session["phone"]=normalize_phone(request.form.get("phone"))
        session["last"]=request.form.get("last","").lower()
        return redirect("/portal/orders")

    return page("""
    <h2>Customer Portal</h2>
    <form method="post">
        <input name="phone" placeholder="Phone"><br><br>
        <input name="last" placeholder="Last Name"><br><br>
        <button>View Orders</button>
    </form>
    """)

@app.route("/portal/orders")
def orders():
    phone=normalize_phone(session.get("phone"))
    last=(session.get("last") or "").lower()

    conn=get_conn()
    cur=conn.cursor()
    cur.execute("SELECT raw_data,status FROM submissions")
    rows=cur.fetchall()
    cur.close(); conn.close()

    html="<h2>Your Orders</h2>"
    found=False

    for r in rows:
        data=r[0] or {}
        status=r[1]

        name=str(get_field(data,["Customer Name","Name"])).lower()
        contact=normalize_phone(get_field(data,["Phone","Contact Info"]))
        sub=get_field(data,["Submission #"])

        if (phone in contact or contact in phone) and last in name:
            found=True
            html+=f"""
            <div style='background:white;padding:15px;margin-bottom:10px'>
                <b>{sub}</b><br>
                Status: {status}
                {status_bar(status)}
            </div>
            """

    if not found:
        html+="No orders found"

    return page(html)

# =========================
if __name__=="__main__":
    app.run()
