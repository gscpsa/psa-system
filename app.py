from flask import Flask, request
import pandas as pd
import psycopg2
import os

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

# ---------- SETUP ----------
def setup_database():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS submissions (
        id SERIAL PRIMARY KEY,
        submission_date TEXT,
        submission_number TEXT,
        customer_name TEXT,
        contact_info TEXT,
        card_count INTEGER,
        service_type TEXT,
        est_cost TEXT,
        prep_needed TEXT,
        customer_paid TEXT,
        current_status TEXT,
        decalared_value TEXT,
        notes TEXT,
        last_updated TIMESTAMP DEFAULT NOW()
    );
    """)

    try:
        cur.execute("""
        ALTER TABLE submissions
        ADD CONSTRAINT unique_submission UNIQUE (submission_number);
        """)
    except:
        pass

    conn.commit()
    cur.close()
    conn.close()

setup_database()

# ---------- HELPERS ----------
def clean(val):
    try:
        if pd.isna(val):
            return ""
    except:
        pass
    return str(val).strip()

def to_int(val):
    try:
        return int(float(val))
    except:
        return 0

# ---------- HOME ----------
@app.route("/")
def home():
    return """
    <h2>Upload PSA Excel File</h2>
    <form action="/upload" method="post" enctype="multipart/form-data">
        <input type="file" name="file" accept=".xlsx,.xls">
        <button type="submit">Upload</button>
    </form>
    <br>
    <a href="/dashboard">View Dashboard</a>
    """

# ---------- UPLOAD ----------
@app.route("/upload", methods=["POST"])
def upload():
    file = request.files["file"]

    df = pd.read_excel(file)

    df.columns = df.columns.astype(str).str.strip()
    df = df.loc[:, ~df.columns.str.contains("^Unnamed", na=False)]

    submission_col = [c for c in df.columns if "submission" in c.lower()][0]

    conn = get_conn()
    cur = conn.cursor()

    inserted = 0
    errors = 0

    for _, row in df.iterrows():
        try:
            submission_number = clean(row.get(submission_col))

            if not submission_number:
                continue

            cur.execute("""
                INSERT INTO submissions (
                    submission_date,
                    submission_number,
                    customer_name,
                    contact_info,
                    card_count,
                    service_type,
                    est_cost,
                    prep_needed,
                    customer_paid,
                    current_status,
                    decalared_value,
                    notes
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (submission_number)
                DO UPDATE SET
                    customer_name = EXCLUDED.customer_name,
                    contact_info = EXCLUDED.contact_info,
                    card_count = EXCLUDED.card_count,
                    service_type = EXCLUDED.service_type,
                    est_cost = EXCLUDED.est_cost,
                    prep_needed = EXCLUDED.prep_needed,
                    customer_paid = EXCLUDED.customer_paid,
                    current_status = EXCLUDED.current_status,
                    decalared_value = EXCLUDED.decalared_value,
                    notes = EXCLUDED.notes,
                    last_updated = NOW()
            """, (
                clean(row.get("s")),
                submission_number,
                clean(row.get("Customer Name")),
                clean(row.get("Contact Info")),
                to_int(row.get("# Of Cards")),
                clean(row.get("Service Type")),
                clean(row.get("Est Cost")),
                clean(row.get("Prep Needed")),
                clean(row.get("Customer Paid")),
                clean(row.get("Current Status")),
                clean(row.get("Decalared Value")),
                clean(row.get("Notes")),
            ))

            inserted += 1

        except Exception as e:
            print("ROW ERROR:", e)
            errors += 1
            continue

    conn.commit()
    cur.close()
    conn.close()

    return f"<h2>Uploaded/Updated {inserted} rows | Errors: {errors}</h2><a href='/dashboard'>Dashboard</a>"

# ---------- DASHBOARD ----------
@app.route("/dashboard")
def dashboard():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT submission_number, customer_name, current_status, last_updated
        FROM submissions
        ORDER BY last_updated DESC
    """)

    rows = cur.fetchall()

    html = "<h2>Dashboard</h2><table border=1>"
    html += "<tr><th>Submission</th><th>Name</th><th>Status</th><th>Updated</th></tr>"

    for r in rows:
        html += "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"

    html += "</table><br><a href='/'>Upload</a>"

    cur.close()
    conn.close()

    return html

# ---------- RUN ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
