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

def detect_excel_status(raw):
    delivered = False
    picked = False

    for k, v in raw.items():
        key = str(k).lower()
        val = str(v).lower()

        if "deliver" in key and "deliver" in val:
            delivered = True
        if "pick" in key and "pick" in val:
            picked = True

    if picked:
        return "Picked Up"
    if delivered:
        return "Delivered to Us"
    return None

# =========================
# SAVE LOGIC (FIXED)
# =========================
def save_row(sub, raw):
    conn = get_conn()
    cur = conn.cursor()

    excel_status = detect_excel_status(raw)

    for k in list(raw.keys()):
        if "status" in str(k).lower():
            del raw[k]

    if excel_status:
        cur.execute("""
        INSERT INTO submissions (submission_number, status, raw_data)
        VALUES (%s,%s,%s)
        ON CONFLICT (submission_number)
        DO UPDATE SET
            status=%s,
            raw_data=EXCLUDED.raw_data,
            last_updated=NOW()
        """,(sub,excel_status,json.dumps(raw),excel_status))
    else:
        cur.execute("""
        INSERT INTO submissions (submission_number, raw_data)
        VALUES (%s,%s)
        ON CONFLICT (submission_number)
        DO UPDATE SET
            raw_data=EXCLUDED.raw_data,
            last_updated=NOW()
        """,(sub,json.dumps(raw)))

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
    <div style="background:#1f2937;color:white;padding:15px">
        PSA Tracking
        <span style="float:right">{nav}</span>
    </div>
    <div style="padding:20px">{content}</div>
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

    status = status or "Submitted"
    idx = steps.index(status) if status in steps else 0

    html = "<div style='display:flex;gap:6px;flex-wrap:wrap'>"
    for i,s in enumerate(steps):
        color = "#e5e7eb"
        if i < idx:
            color = "#bfdbfe"
        if i == idx:
            color = "#2563eb;color:white"
        html += f"<div style='padding:6px 10px;background:{color};border-radius:20px'>{s}</div>"
    html += "</div>"
    return html

# =========================
# TABLE (FULL COLUMNS)
# =========================
def build_table(rows):
    keys=set()
    cleaned=[]

    for r in rows:
        data=r[0] or {}
        row={}

        for k,v in data.items():
            if "unnamed" in str(k).lower():
                continue
            if k=="S":
                k="Submission Date"
            row[k]=v

        row["PSA Status"]=r[1] or "Submitted"
        cleaned.append(row)
        keys.update(row.keys())

    ordered=sorted(keys)

    html="<table border=1 style='width:100%;background:white'>"
    html+="<tr>"+"".join([f"<th>{k}</th>" for k in ordered])+"</tr>"

    for row in cleaned:
        html+="<tr>"
        for k in ordered:
            val=row.get(k,"")
            if k=="PSA Status":
                html+=f"<td style='color:blue;font-weight:bold'>{val}</td>"
            else:
                html+=f"<td>{val}</td>"
        html+="</tr>"

    html+="</table>"
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
    sort=request.args.get("sort","new")
    order="DESC" if sort=="new" else "ASC"

    conn=get_conn()
    cur=conn.cursor()
    cur.execute(f"SELECT raw_data,status FROM submissions ORDER BY last_updated {order}")
    rows=cur.fetchall()
    cur.close(); conn.close()

    html="""
    <h2>Admin Dashboard</h2>
    <a href="/admin?sort=new">Newest</a>
    <a href="/admin?sort=old">Oldest</a><br><br>
    """
    html+=build_table(rows)

    return page(html)

# =========================
# EXCEL UPLOAD
# =========================
@app.route("/admin/upload",methods=["GET","POST"])
@admin_required
def upload():
    if request.method=="POST":
        df=read_file(request.files["file"])
        df.columns=[str(c).strip() for c in df.columns]

        for _,row in df.iterrows():
            raw={c:clean(row[c]) for c in df.columns}
            sub=normalize_submission(raw.get("Submission #") or raw.get("Submission Number"))
            if sub:
                save_row(sub,raw)

        return page("Excel uploaded")

    return page("<form method='post' enctype='multipart/form-data'><input type='file' name='file'><button>Upload</button></form>")

# =========================
# PDF PARSER
# =========================
@app.route("/admin/upload_psa",methods=["GET","POST"])
@admin_required
def upload_psa():
    if request.method=="POST":
        import pdfplumber, tempfile

        f=request.files["file"]
        temp=tempfile.NamedTemporaryFile(delete=False)
        f.save(temp.name)

        PRIORITY={"Order Arrived":1,"Research & ID":2,"Grading":3,"QA Checks":4,"Complete":5}
        best={}

        with pdfplumber.open(temp.name) as pdf:
            for pdf_page in pdf.pages:
                tables=pdf_page.extract_tables()

                for table in tables:
                    for row in table:
                        text=" ".join([str(c or "") for c in row])

                        m=re.search(r"Sub\s*#(\d+)",text)
                        if not m:
                            continue

                        sub=m.group(1)

                        for s in PRIORITY:
                            if s in text:
                                if sub not in best or PRIORITY[s]>PRIORITY[best[sub]]:
                                    best[sub]=s

        os.unlink(temp.name)

        conn=get_conn()
        cur=conn.cursor()

        for sub,status in best.items():
            cur.execute("""
            UPDATE submissions
            SET status=%s
            WHERE submission_number=%s
            AND COALESCE(status,'')!='Picked Up'
            """,(status,sub))

        conn.commit()
        cur.close()
        conn.close()

        return page("PDF processed")

    return page("<form method='post' enctype='multipart/form-data'><input type='file' name='file'><button>Upload PDF</button></form>")

# =========================
# CUSTOMER PORTAL
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
    """,mode="portal")

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

    for r in rows:
        data=r[0] or {}
        status=r[1]

        name=str(get_field(data,["Customer Name","Name"])).lower()
        contact=normalize_phone(get_field(data,["Phone","Contact Info"]))
        sub=get_field(data,["Submission #"])

        if (phone in contact or contact in phone) and last in name:
            html+=f"""
            <div style='background:white;padding:15px;margin-bottom:10px'>
                <b>{name}</b><br>
                Submission: {sub}<br>
                Cards: {get_field(data,["Cards","# of Cards"])}<br>
                Service: {get_field(data,["Service","Service Type"])}<br>
                Status: {status}
                {status_bar(status)}
            </div>
            """

    return page(html,mode="portal")

# =========================
if __name__=="__main__":
    app.run()
