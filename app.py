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
def error_handler(e):
    return page(f"<pre>{traceback.format_exc()}</pre>")

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect("/admin/login")
        return f(*args, **kwargs)
    return wrapper

def normalize_submission(v):
    return re.sub(r"\D", "", str(v or ""))

def normalize_psa_status(status):
    s = str(status or "").lower()
    if "order arrived" in s: return "Order Arrived"
    if "research" in s: return "Research & ID"
    if "grading" in s: return "Grading"
    if "qa" in s: return "QA Checks"
    if "assembly" in s: return "QA Checks"
    if "complete" in s: return "Complete"
    return None

def status_rank(status):
    return {
        "Submitted":0,"Order Arrived":1,"Research & ID":2,
        "Grading":3,"QA Checks":4,"Complete":5,
        "Delivered to Us":6,"Picked Up":7
    }.get(status,0)

def page(c):
    return f"<html><body>{c}</body></html>"

@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    if request.method=="POST":
        if request.form.get("password")==ADMIN_PASSWORD:
            session["admin"]=True
            return redirect("/admin")
    return page("<form method=post><input name=password><button>Login</button></form>")

@app.route("/admin")
@admin_required
def admin():
    conn=get_conn();cur=conn.cursor()
    cur.execute("SELECT submission_number,status FROM submissions")
    rows=cur.fetchall()
    cur.close();conn.close()
    return page(str(rows))

@app.route("/admin/upload_psa", methods=["GET","POST"])
@admin_required
def admin_upload_psa():
    if request.method=="POST":
        import pdfplumber, tempfile

        f=request.files.get("file")
        if not f: return page("no file")

        tmp=tempfile.NamedTemporaryFile(delete=False,suffix=".pdf")
        f.save(tmp.name)

        best={}
        try:
            with pdfplumber.open(tmp.name) as pdf:
                for p in pdf.pages:
                    try:
                        text=p.extract_text() or ""
                    except:
                        continue

                    # ===== FIXED PARSER =====
                    blocks=re.split(r"(?=Sub\s*#\s*\d+)", text, flags=re.I)

                    for block in blocks:
                        m=re.search(r"Sub\s*#\s*(\d+)", block, re.I)
                        if not m: continue

                        sub=normalize_submission(m.group(1))

                        status=None
                        for s in ["Order Arrived","Research & ID","Grading","QA Checks","Assembly","Complete"]:
                            if re.search(s,block,re.I):
                                status=normalize_psa_status(s)
                                break

                        if status:
                            best[sub]=status

        finally:
            os.unlink(tmp.name)

        conn=get_conn();cur=conn.cursor()

        for sub,status in best.items():
            cur.execute("""
            UPDATE submissions
            SET status=%s
            WHERE REGEXP_REPLACE(submission_number,'\\D','','g')=%s
            AND COALESCE(status,'') NOT IN ('Picked Up','Delivered to Us')
            """,(status,sub))

        conn.commit();cur.close();conn.close()

        return page(f"updated {len(best)}")

    return page("<form method=post enctype=multipart/form-data><input type=file name=file><button>upload</button></form>")

if __name__=="__main__":
    app.run()
