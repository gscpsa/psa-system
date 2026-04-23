from flask import Flask, request, redirect
import psycopg2
import pandas as pd
import os
import io

app = Flask(__name__)
DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

# ---------- SAFE TABLE ----------
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
        card_count TEXT,
        service_type TEXT,
        est_cost TEXT,
        prep_needed TEXT,
        customer_paid TEXT,
        status TEXT,
        declared_value TEXT,
        notes TEXT,
        last_updated TIMESTAMP DEFAULT NOW()
    );
    """)

    conn.commit()
    cur.close()
    conn.close()

ensure_table()

# ---------- HELPERS ----------
def clean(val):
    try:
        if pd.isna(val):
            return ""
    except:
        pass
    return str(val).strip()

def read_any(file_storage):
    filename = (file_storage.filename or "").lower()

    if filename.endswith((".xlsx", ".xls")):
        return pd.read_excel(file_storage)

    raw = file_storage.read()
    file_storage.seek(0)

    text = None
    for enc in ["utf-8", "latin1"]:
        try:
            text = raw.decode(enc)
            break
        except:
            continue

    for sep in [",", ";", "\t"]:
        try:
            return pd.read_csv(io.StringIO(text), sep=sep)
        except:
            continue

    raise Exception("Cannot parse file")

def extract_row(row):
    data = {k: "" for k in [
        "submission_number","submission_date","customer_name","contact_info",
        "card_count","service_type","est_cost","prep_needed",
        "customer_paid","status","declared_value","notes"
    ]}

    for key in row.keys():
        k = str(key).lower()
        v = clean(row.get(key))

        if "submission" in k:
            data["submission_number"] = v
        elif "date" in k or k == "s":
            data["submission_date"] = v
        elif "name" in k:
            data["customer_name"] = v
        elif "contact" in k or "phone" in k:
            data["contact_info"] = v
        elif "card" in k:
            data["card_count"] = v
        elif "service" in k:
            data["service_type"] = v
        elif "cost" in k:
            data["est_cost"] = v
        elif "prep" in k:
            data["prep_needed"] = v
        elif "paid" in k:
            data["customer_paid"] = v
        elif "status" in k:
            data["status"] = v
        elif "declared" in k:
            data["declared_value"] = v
        elif "note" in k:
            data["notes"] = v

    return data

# ---------- HOME ----------
@app.route("/")
def home():
    return """
    <h2>PSA System</h2>
    <a href="/upload">Upload</a><br>
    <a href="/dashboard">Dashboard</a><br>
    <a href="/staff">Staff Search</a><br>
    <a href="/track">Customer Tracker</a>
    """

# ---------- UPLOAD ----------
@app.route("/upload", methods=["GET","POST"])
def upload():
    if request.method == "POST":
        file = request.files.get("file")

        df = read_any(file)

        conn = get_conn()
        cur = conn.cursor()

        inserted = 0
        errors = 0

        for _, row in df.iterrows():
            try:
                d = extract_row(row)

                if not d["submission_number"]:
                    errors += 1
                    continue

                cur.execute("""
                INSERT INTO submissions (
                    submission_number, submission_date, customer_name,
                    contact_info, card_count, service_type,
                    est_cost, prep_needed, customer_paid,
                    status, declared_value, notes
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (submission_number)
                DO UPDATE SET
                    submission_date=EXCLUDED.submission_date,
                    customer_name=EXCLUDED.customer_name,
                    contact_info=EXCLUDED.contact_info,
                    card_count=EXCLUDED.card_count,
                    service_type=EXCLUDED.service_type,
                    est_cost=EXCLUDED.est_cost,
                    prep_needed=EXCLUDED.prep_needed,
                    customer_paid=EXCLUDED.customer_paid,
                    status=EXCLUDED.status,
                    declared_value=EXCLUDED.declared_value,
                    notes=EXCLUDED.notes,
                    last_updated=NOW()
                """, tuple(d.values()))

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

# ---------- DASHBOARD + EDIT ----------
@app.route("/dashboard")
def dashboard():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id, submission_number, customer_name, status FROM submissions ORDER BY id DESC LIMIT 200")
    rows = cur.fetchall()

    html = "<h2>Dashboard</h2><table border=1>"
    html += "<tr><th>Submission</th><th>Name</th><th>Status</th><th>Edit</th></tr>"

    for r in rows:
        html += f"""
        <tr>
            <td>{r[1]}</td>
            <td>{r[2]}</td>
            <td>{r[3]}</td>
            <td><a href="/edit/{r[0]}">Edit</a></td>
        </tr>
        """

    html += "</table><br><a href='/'>Back</a>"

    cur.close()
    conn.close()
    return html

# ---------- EDIT ----------
@app.route("/edit/<id>", methods=["GET","POST"])
def edit(id):
    conn = get_conn()
    cur = conn.cursor()

    if request.method == "POST":
        status = request.form["status"]

        cur.execute("UPDATE submissions SET status=%s WHERE id=%s", (status, id))
        conn.commit()

        cur.close()
        conn.close()
        return redirect("/dashboard")

    cur.execute("SELECT submission_number, status FROM submissions WHERE id=%s", (id,))
    row = cur.fetchone()

    html = f"""
    <h3>Edit {row[0]}</h3>
    <form method="post">
        <input name="status" value="{row[1]}">
        <button>Save</button>
    </form>
    """

    cur.close()
    conn.close()
    return html

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
            LOWER(submission_number) LIKE LOWER(%s)
            OR LOWER(customer_name) LIKE LOWER(%s)
            OR LOWER(contact_info) LIKE LOWER(%s)
        LIMIT 100
        """, (f"%{q}%", f"%{q}%", f"%{q}%"))

        results = cur.fetchall()

        cur.close()
        conn.close()

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

        cur.execute("""
        SELECT submission_number, status
        FROM submissions
        WHERE submission_number=%s
        """, (sub,))

        result = cur.fetchone()

        cur.close()
        conn.close()

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
