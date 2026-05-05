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
    ordered = []
    seen = set()
    for wanted in PREFERRED_DASHBOARD_COLUMNS:
        for key in keys:
            if key not in seen and key.strip().lower() == wanted.strip().lower():
                ordered.append(key)
                seen.add(key)
    for key in keys:
        if key not in seen:
            ordered.append(key)
            seen.add(key)
    return ordered

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
    return page(f"""
    <div class="card">
        <h2>Application Error</h2>
        <pre>{traceback.format_exc()}</pre>
    </div>
    """)

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect("/admin/login")
        return f(*args, **kwargs)
    return wrapper

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
    return re.sub(r"\D", "", str(v).split(".")[0])

def normalize_phone(v):
    return re.sub(r"\D", "", str(v or ""))

def get_field(data, names):
    for wanted in names:
        for k in ordered_display_keys(data):
            if str(k).strip().lower() == wanted.lower():
                return data.get(k)
    return ""

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

def normalize_psa_status(status):
    s = re.sub(r"\s+", " ", str(status or "")).strip().lower()
    if s == "order arrived": return "Order Arrived"
    if s == "research & id": return "Research & ID"
    if s == "grading": return "Grading"
    if s == "qa checks": return "QA Checks"
    if s == "assembly": return "Assembly"
    if s == "shipping soon": return "Shipping Soon"
    if s == "complete": return "Complete"
    return None

def status_rank(status):
    ranks = {
        "Submitted": 0,
        "Order Arrived": 1,
        "Research & ID": 2,
        "Grading": 3,
        "Assembly": 4,
        "QA Checks": 5,
        "Shipping Soon": 6,
        "Complete": 7,
        "Delivered to Us": 8,
        "Picked Up": 9,
    }
    return ranks.get(status or "Submitted", 0)

def status_bar(status):
    steps = [
        "Submitted",
        "Order Arrived",
        "Research & ID",
        "Grading",
        "Assembly",
        "QA Checks",
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

if __name__ == "__main__":
    app.run()
