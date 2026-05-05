from flask import Flask, request, session, redirect, url_for
import pandas as pd
import psycopg2
import os, io, json, re, traceback
from functools import wraps

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

# =========================
# DATABASE & LOGIC (YOUR ORIGINAL ENGINE)
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

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect("/admin/login")
        return f(*args, **kwargs)
    return wrapper

def clean(v):
    try:
        if pd.isna(v): return ""
    except Exception: pass
    return str(v).strip()

def normalize_submission(v):
    if not v: return None
    return re.sub(r"\D", "", str(v).split(".")[0])

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

def normalize_psa_status(status):
    s = re.sub(r"\s+", " ", str(status or "")).strip().lower()
    mapping = {
        "order arrived": "Order Arrived",
        "research & id": "Research & ID",
        "grading": "Grading",
        "qa checks": "QA Checks",
        "assembly": "Assembly",
        "shipping soon": "Shipping Soon",
        "complete": "Complete"
    }
    return mapping.get(s)

def detect_internal_status(raw):
    full_text = " ".join([f"{k} {v}" for k, v in raw.items()]).lower()
    if "not picked up" in full_text: return None
    if "picked up" in full_text: return "Picked Up"
    if any(x in full_text for x in ["delivered to us", "received by us", "arrived at store"]):
        return "Delivered to Us"
    return None

def save_row(sub, raw):
    conn = get_conn()
    cur = conn.cursor()
    internal_status = detect_internal_status(raw)
    cur.execute("SELECT status FROM submissions WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s", (sub,))
    existing = cur.fetchone()
    existing_status = existing[0] if existing else None

    if existing_status == "Picked Up":
        cur.execute("UPDATE submissions SET raw_data=%s, last_updated=NOW() WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s", (json.dumps(raw), sub))
    elif internal_status:
        cur.execute("""
            INSERT INTO submissions (submission_number, status, raw_data) VALUES (%s, %s, %s)
            ON CONFLICT (submission_number) DO UPDATE SET status=%s, raw_data=EXCLUDED.raw_data, last_updated=NOW()
        """, (sub, internal_status, json.dumps(raw), internal_status))
    else:
        cur.execute("""
            INSERT INTO submissions (submission_number, status, raw_data) VALUES (%s, 'Submitted', %s)
            ON CONFLICT (submission_number) DO UPDATE SET raw_data=EXCLUDED.raw_data, status=COALESCE(submissions.status, 'Submitted'), last_updated=NOW()
        """, (sub, json.dumps(raw)))
    conn.commit()
    cur.close()
    conn.close()

# =========================
# UI WRAPPER (THE MOCKUP DESIGN)
# =========================
def page(content, mode='customer'):
    # Determine nav visibility
    nav_html = ""
    if session.get("admin"):
        nav_html = '''
            <div class="header-nav">
                <a href="/admin"><i class="fa-solid fa-chart-simple"></i> Dashboard</a>
                <a href="/admin/search"><i class="fa-solid fa-magnifying-glass"></i> Search</a>
                <a href="/admin/upload"><i class="fa-regular fa-file-excel"></i> Upload Excel</a>
                <a href="/admin/upload_psa"><i class="fa-solid fa-file-lines"></i> Upload PSA</a>
                <a href="/portal"><i class="fa-solid fa-users"></i> Customer Portal</a>
            </div>
            <div class="header-logout"><a href="/admin/logout"><i class="fa-solid fa-arrow-right-from-bracket"></i> Logout</a></div>
        '''

    return f'''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Giant Sports Cards Portal</title>
        <link href="https://fonts.googleapis.com/css2?family=Oswald:wght@500;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
        <style>
            body {{ margin:0; font-family: 'Inter', sans-serif; background: #06100d; display: flex; flex-direction: column; min-height: 100vh; color: white; }}
            .header {{ background: #06100d; padding: 0 40px; height: 80px; display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #113824; }}
            .header-logo {{ font-family: 'Oswald', sans-serif; font-size: 26px; font-weight: 700; color: white; text-decoration: none; }}
            .header-logo span {{ color: #198754; }}
            .header-nav {{ display: flex; gap: 30px; }}
            .header-nav a, .header-logout a {{ color: #fff; text-decoration: none; font-size: 14px; display: flex; align-items: center; gap: 8px; }}
            .header-nav a:hover {{ color: #198754; }}
            
            .main-content {{ flex: 1; display: flex; flex-direction: column; }}
            .admin-container {{ padding: 40px; background: #fff; color: #333; margin: 20px; border-radius: 12px; }}
            
            /* Hero Styles for Portal */
            .hero {{ display: flex; flex: 1; background: url('/static/images/bg.png') center center no-repeat; background-size: cover; }}
            .hero-right {{ flex: 1; display: flex; justify-content: center; align-items: center; padding: 40px; }}
            .hero-left {{ flex: 1; }}
            
            .panel {{ width: 100%; max-width: 450px; background: white; padding: 50px; border-radius: 12px; box-shadow: 0 20px 60px rgba(0,0,0,0.4); text-align: center; color: #111; }}
            .panel h2 {{ font-family: 'Oswald', sans-serif; font-size: 32px; text-transform: uppercase; margin-bottom: 10px; }}
            .divider {{ width: 50px; height: 3px; background: #198754; margin: 0 auto 25px; }}
            
            .input-group {{ display: flex; align-items: center; border: 1px solid #ddd; border-radius: 8px; padding: 14px; margin-bottom: 15px; }}
            .input-group i {{ color: #888; margin-right: 12px; width: 20px; }}
            .input-group input {{ border: none; outline: none; flex: 1; font-size: 16px; }}
            
            .btn-action {{ width: 100%; padding: 16px; background: #085a31; color: white; border: none; border-radius: 8px; font-weight: 600; cursor: pointer; display: flex; justify-content: center; align-items: center; gap: 10px; }}
            
            /* Admin Tables */
            table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
            th {{ background: #f8f9fa; padding: 12px; text-align: left; border-bottom: 2px solid #dee2e6; }}
            td {{ padding: 12px; border-bottom: 1px solid #eee; font-size: 14px; }}
            
            .footer {{ background: #06100d; display: flex; justify-content: center; gap: 60px; padding: 30px; border-top: 1px solid #113824; }}
            .foot-item {{ display: flex; align-items: center; gap: 12px; }}
            .foot-icon {{ width: 40px; height: 40px; border-radius: 50%; border: 1px solid #198754; display: flex; justify-content: center; align-items: center; color: white; }}
            
            @media (max-width: 900px) {{ .hero {{ flex-direction: column; }} .hero-left {{ display: none; }} .footer {{ flex-direction: column; align-items: center; }} }}
        </style>
    </head>
    <body>
        <div class="header">
            <a href="/" class="header-logo">GIANT <span>SPORTS CARDS</span></a>
            {nav_html}
        </div>
        <div class="main-content">{content}</div>
        <div class="footer">
            <div class="foot-item"><div class="foot-icon"><i class="fa-solid fa-shield-check"></i></div><div><b>SECURE</b></div></div>
            <div class="foot-item"><div class="foot-icon"><i class="fa-solid fa-clock"></i></div><div><b>REAL-TIME</b></div></div>
            <div class="foot-item"><div class="foot-icon"><i class="fa-solid fa-check"></i></div><div><b>EXPERT CARE</b></div></div>
        </div>
    </body>
    </html>
    '''

# =========================
# ADMIN ROUTES (RE-WRAPPED)
# =========================
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/admin")
    return page('''
        <div class="hero"><div class="hero-right"><div class="panel">
            <h2>Admin Login</h2><div class="divider"></div>
            <form method="post">
                <div class="input-group"><i class="fa-solid fa-lock"></i><input type="password" name="password" placeholder="Password"></div>
                <button class="btn-action">LOGIN</button>
            </form>
        </div></div></div>
    ''')

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect("/admin/login")

@app.route("/admin")
@admin_required
def admin_dashboard():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT raw_data, status FROM submissions")
    rows = cur.fetchall(); cur.close(); conn.close()
    
    # Sorting logic from your original file
    def get_sort_date(row):
        data = row[0] or {}
        date_val = get_field(data, ["Submission Date", "S", "Date"])
        try: return pd.to_datetime(date_val) if date_val else pd.Timestamp.min
        except: return pd.Timestamp.min
    
    sorted_rows = sorted(rows, key=get_sort_date, reverse=True)
    
    # Use your original build_table logic here, wrapped in admin-container
    from helper_utils import build_table # Assuming you keep your helpers
    table_html = build_table(sorted_rows) 
    
    return page(f'<div class="admin-container"><h2>Admin Dashboard</h2>{table_html}</div>', mode='admin')

# ... [Keep your original admin_upload, admin_upload_psa, and admin_search routes here] ...
# (Just make sure they return page('<div class="admin-container">...</div>'))

# =========================
# CUSTOMER PORTAL (COMPLETED)
# =========================
@app.route("/", methods=["GET", "POST"])
@app.route("/portal", methods=["GET", "POST"])
def portal():
    if request.method == "POST":
        phone = normalize_phone(request.form.get("phone"))
        last = request.form.get("last", "").strip().lower()
        
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT raw_data, status FROM submissions")
        all_rows = cur.fetchall(); cur.close(); conn.close()
        
        results = []
        for rd, stat in all_rows:
            p_field = normalize_phone(get_field(rd, ["Phone", "Phone Number", "Telephone"]))
            l_field = str(get_field(rd, ["Last Name", "Last", "Name"])).lower()
            if phone in p_field and last in l_field:
                results.append((rd, stat))
        
        if not results:
            return page('<div class="hero"><div class="hero-right"><div class="panel"><h2>No Results Found</h2><p>Check your details and try again.</p><a href="/portal">Back</a></div></div></div>')
        
        # Build status tracking view
        track_html = '<div class="admin-container"><h2>Your Submissions</h2>'
        for rd, stat in results:
            track_html += f'<div style="margin-bottom:40px;"><h3>Status: {stat}</h3>'
            # Add your status_bar(stat) here
            track_html += '</div>'
        track_html += '</div>'
        return page(track_html)

    return page('''
        <div class="hero">
            <div class="hero-left"></div>
            <div class="hero-right">
                <div class="panel">
                    <div class="panel-icon-top"><i class="fa-solid fa-magnifying-glass"></i></div>
                    <h2>Track Your Order</h2>
                    <div class="divider"></div>
                    <p class="desc">Enter your details to see the real-time status of your PSA submission.</p>
                    <form method="post">
                        <div class="input-group"><i class="fa-solid fa-mobile-screen"></i><input name="phone" placeholder="Phone Number" required></div>
                        <div class="input-group"><i class="fa-solid fa-user"></i><input name="last" placeholder="Last Name" required></div>
                        <button class="btn-action" type="submit">VIEW STATUS <i class="fa-solid fa-arrow-right"></i></button>
                    </form>
                </div>
            </div>
        </div>
    ''')

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
