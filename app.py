from flask import Flask, request, redirect
import psycopg2
import pandas as pd
import os, io, json

app = Flask(__name__)
DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

# ---------- STABLE TABLE (NO DROP) ----------
def ensure_table():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS submissions (
        id SERIAL PRIMARY KEY,
        submission_number TEXT UNIQUE,
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
    text = raw.decode("latin1")
    return pd.read_csv(io.StringIO(text))

# ---------- EXACT FIELD EXTRACTION ----------
def extract(row):
    r = {k: clean(row[k]) for k in row.keys()}

    return {
        "submission": r.get("Submission #") or r.get("Submission Number") or "",
        "name": r.get("Customer Name") or "",
        "contact": r.get("Contact Info") or r.get("Phone") or r.get("Email") or "",
        "status": r.get("Current Status") or r.get("Status") or "",
        "service": r.get("Service Type") or "",
        "raw": r
    }

# ---------- HOME ----------
@app.route("/")
def home():
    return """
    <h2>PSA SYSTEM</h2>
    <a href="/upload">Upload</a><br>
    <a href="/dashboard">Dashboard</a><br>
    <a href="/search">Search</a><br>
    """

# ---------- UPLOAD (DUPLICATE PROTECTED) ----------
@app.route("/upload", methods=["GET","POST"])
def upload():

    if request.method == "POST":
        file = request.files.get("file")

        df = read_any(file)
        df.columns = [str(c).strip() for c in df.columns]

        conn = get_conn()
        cur = conn.cursor()

        inserted = 0
        updated = 0
        errors = 0

        for _, row in df.iterrows():
            try:
                d = extract(row)

                if not d["submission"]:
                    errors += 1
                    continue

                cur.execute("""
                INSERT INTO submissions
                (submission_number, customer_name, contact_info, status, service_type, raw_data)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (submission_number)
                DO UPDATE SET
                    customer_name = EXCLUDED.customer_name,
                    contact_info = EXCLUDED.contact_info,
                    status = EXCLUDED.status,
                    service_type = EXCLUDED.service_type,
                    raw_data = EXCLUDED.raw_data,
                    last_updated = NOW()
                """, (
                    d["submission"],
                    d["name"],
                    d["contact"],
                    d["status"],
                    d["service"],
                    json.dumps(d["raw"])
                ))

                if cur.rowcount == 1:
                    inserted += 1
                else:
                    updated += 1

            except Exception as e:
                print("ROW ERROR:", e)
                errors += 1

        conn.commit()
        cur.close()
        conn.close()

        return f"Inserted: {inserted} | Updated: {updated} | Errors: {errors}"

    return """
    <h3>Upload File</h3>
    <form method="post" enctype="multipart/form-data">
        <input type="file" name="file">
        <button>Upload</button>
    </form>
    """

# ---------- DASHBOARD (SORT + FILTER) ----------
@app.route("/dashboard", methods=["GET"])
def dashboard():

    sort = request.args.get("sort", "last_updated")
    status_filter = request.args.get("status", "")

    conn = get_conn()
    cur = conn.cursor()

    query = "SELECT id, submission_number, customer_name, contact_info, status, service_type FROM submissions"

    if status_filter:
        query += " WHERE status = %s"
        cur.execute(query + f" ORDER BY {sort} DESC LIMIT 500", (status_filter,))
    else:
        cur.execute(query + f" ORDER BY {sort} DESC LIMIT 500")

    rows = cur.fetchall()

    html = """
    <h2>Dashboard</h2>

    <form method="get">
        Sort:
        <select name="sort">
            <option value="last_updated">Last Updated</option>
            <option value="submission_number">Submission</option>
            <option value="customer_name">Name</option>
        </select>

        Status:
        <input name="status" placeholder="Filter status">

        <button>Apply</button>
    </form>

    <table border=1>
    <tr>
        <th>Submission</th>
        <th>Name</th>
        <th>Contact</th>
        <th>Status</th>
        <th>Service</th>
        <th>Edit</th>
    </tr>
    """

    for r in rows:
        html += f"""
        <tr>
            <td>{r[1]}</td>
            <td>{r[2]}</td>
            <td>{r[3]}</td>
            <td>{r[4]}</td>
            <td>{r[5]}</td>
            <td><a href="/edit/{r[0]}">Edit</a></td>
        </tr>
        """

    html += "</table>"
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

        return redirect("/dashboard")

    cur.execute("SELECT submission_number, status FROM submissions WHERE id=%s", (id,))
    row = cur.fetchone()

    return f"""
    <h3>Edit {row[0]}</h3>
    <form method="post">
        Status: <input name="status" value="{row[1]}">
        <button>Save</button>
    </form>
    """

# ---------- SEARCH ----------
@app.route("/search", methods=["GET","POST"])
def search():
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
        """, (f"%{q}%", f"%{q}%", f"%{q}%"))

        results = cur.fetchall()

    html = """
    <h3>Search</h3>
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

if __name__ == "__main__":
    app.run()
