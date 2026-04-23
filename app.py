from flask import Flask, request, redirect
import psycopg2, pandas as pd, os, io, json

app = Flask(__name__)
DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

# ---------- TABLE ----------
def ensure_table():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS submissions (
        id SERIAL PRIMARY KEY,
        submission_number TEXT UNIQUE,
        submission_date TEXT,
        customer_name TEXT,
        contact_info TEXT,
        status TEXT,
        service_type TEXT,
        raw_data JSONB,
        last_updated TIMESTAMP DEFAULT NOW()
    );
    """)
    conn.commit()
    cur.close()
    conn.close()

ensure_table()

# ---------- HELPERS ----------
def clean(v):
    try:
        if pd.isna(v):
            return ""
    except:
        pass
    return str(v).strip()

def read_any(file):
    name = file.filename.lower()
    if name.endswith(("xlsx","xls")):
        return pd.read_excel(file)
    raw = file.read()
    file.seek(0)
    return pd.read_csv(io.StringIO(raw.decode("latin1")))

def extract(raw):
    return {
        "submission": raw.get("Submission #") or raw.get("Submission Number") or "",
        "date": raw.get("Submission Date",""),
        "name": raw.get("Customer Name",""),
        "contact": raw.get("Contact Info") or raw.get("Email") or raw.get("Phone") or "",
        "status": raw.get("Current Status") or raw.get("Status") or "",
        "service": raw.get("Service Type","")
    }

# ---------- HOME ----------
@app.route("/")
def home():
    return """
    <h2>PSA SYSTEM</h2>
    <a href="/upload">Upload</a><br>
    <a href="/dashboard">Dashboard</a><br>
    <a href="/staff">Staff Search</a><br>
    <a href="/track">Customer Track</a>
    """

# ---------- UPLOAD ----------
@app.route("/upload", methods=["GET","POST"])
def upload():
    if request.method == "POST":
        file = request.files.get("file")
        df = read_any(file)
        df.columns = [str(c).strip() for c in df.columns]

        conn = get_conn()
        cur = conn.cursor()

        inserted = updated = errors = 0

        for _, row in df.iterrows():
            try:
                raw = {c: clean(row[c]) for c in df.columns}
                d = extract(raw)

                if not d["submission"]:
                    errors += 1
                    continue

                cur.execute("""
                INSERT INTO submissions
                (submission_number, submission_date, customer_name, contact_info, status, service_type, raw_data)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (submission_number)
                DO UPDATE SET
                    submission_date=EXCLUDED.submission_date,
                    customer_name=EXCLUDED.customer_name,
                    contact_info=EXCLUDED.contact_info,
                    status=EXCLUDED.status,
                    service_type=EXCLUDED.service_type,
                    raw_data=EXCLUDED.raw_data,
                    last_updated=NOW()
                """, (
                    d["submission"], d["date"], d["name"], d["contact"],
                    d["status"], d["service"], json.dumps(raw)
                ))

                inserted += 1
            except Exception as e:
                print(e)
                errors += 1

        conn.commit()
        cur.close()
        conn.close()

        return f"Inserted/Updated: {inserted} | Errors: {errors}"

    return """
    <h3>Upload</h3>
    <form method="post" enctype="multipart/form-data">
        <input type="file" name="file">
        <button>Upload</button>
    </form>
    """

# ---------- DASHBOARD ----------
@app.route("/dashboard")
def dashboard():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id, submission_number, customer_name, status, raw_data FROM submissions ORDER BY last_updated DESC LIMIT 500")
    rows = cur.fetchall()

    # collect ALL columns from raw_data
    keys = set()
    for r in rows:
        keys.update(r[4].keys())
    keys = list(keys)

    html = "<h2>Dashboard</h2><table border=1>"
    html += "<tr><th>Edit</th>" + "".join(f"<th>{k}</th>" for k in keys) + "</tr>"

    for r in rows:
        data = r[4]
        html += f"<tr><td><a href='/edit/{r[0]}'>Edit</a></td>" + "".join(f"<td>{data.get(k,'')}</td>" for k in keys) + "</tr>"

    html += "</table>"
    return html

# ---------- EDIT ----------
@app.route("/edit/<id>", methods=["GET","POST"])
def edit(id):
    conn = get_conn()
    cur = conn.cursor()

    if request.method == "POST":
        status = request.form["status"]
        cur.execute("UPDATE submissions SET status=%s WHERE id=%s", (status,id))
        conn.commit()
        return redirect("/dashboard")

    cur.execute("SELECT submission_number, status FROM submissions WHERE id=%s",(id,))
    row = cur.fetchone()

    return f"""
    <h3>Edit {row[0]}</h3>
    <form method="post">
        Status: <input name="status" value="{row[1]}">
        <button>Save</button>
    </form>
    """

# ---------- STAFF SEARCH ----------
@app.route("/staff", methods=["GET","POST"])
def staff():
    results = []

    if request.method == "POST":
        q = request.form["q"]

        conn = get_conn()
        cur = conn.cursor()

        cur.execute("""
        SELECT submission_number, customer_name, contact_info, status
        FROM submissions
        WHERE
            LOWER(customer_name) LIKE LOWER(%s)
            OR LOWER(contact_info) LIKE LOWER(%s)
            OR LOWER(submission_number) LIKE LOWER(%s)
        LIMIT 200
        """,(f"%{q}%",f"%{q}%",f"%{q}%"))

        results = cur.fetchall()

    html = """
    <h3>Staff Search</h3>
    <form method="post">
        <input name="q">
        <button>Search</button>
    </form>
    <table border=1>
    """

    for r in results:
        html += "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"

    html += "</table>"
    return html

# ---------- CUSTOMER TRACK ----------
@app.route("/track", methods=["GET","POST"])
def track():
    result = None

    if request.method == "POST":
        sub = request.form["sub"]

        conn = get_conn()
        cur = conn.cursor()

        cur.execute("SELECT submission_number, status FROM submissions WHERE submission_number=%s",(sub,))
        result = cur.fetchone()

    html = """
    <h3>Track Submission</h3>
    <form method="post">
        <input name="sub">
        <button>Check</button>
    </form>
    """

    if result:
        html += f"<p>Status: {result[1]}</p>"

    return html

if __name__ == "__main__":
    app.run()
