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
        card_count TEXT,
        service_type TEXT,
        est_cost TEXT,
        prep_needed TEXT,
        customer_paid TEXT,
        current_status TEXT,
        declared_value TEXT,
        notes TEXT,
        last_updated TIMESTAMP DEFAULT NOW()
    );
    """)

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

# ---------- HOME ----------
@app.route("/")
def home():
    return """
    <h2>Upload PSA Excel File</h2>
    <form action="/upload" method="post" enctype="multipart/form-data">
        <input type="file" name="file">
        <button type="submit">Upload</button>
    </form>
    <br>
    <a href="/dashboard">Dashboard</a>
    <br><br>
    <a href="/track">Track Submission</a>
    """

# ---------- UPLOAD ----------
@app.route("/upload", methods=["POST"])
def upload():
    file = request.files["file"]

    df = pd.read_excel(file)
    df.columns = df.columns.astype(str).str.strip()

    submission_col = None
    for col in df.columns:
        if "submission" in col.lower():
            submission_col = col
            break

    conn = get_conn()
    cur = conn.cursor()

    inserted = 0
    updated = 0
    skipped = 0

    for _, row in df.iterrows():
        try:
            submission = clean(row.get(submission_col))
            if not submission:
                skipped += 1
                continue

            data = (
                clean(row.get("s")),
                submission,
                clean(row.get("Customer Name")),
                clean(row.get("Contact Info")),
                clean(row.get("# Of Cards")),
                clean(row.get("Service Type")),
                clean(row.get("Est Cost")),
                clean(row.get("Prep Needed")),
                clean(row.get("Customer Paid")),
                clean(row.get("Current Status")),
                clean(row.get("Decalared Value")),
                clean(row.get("Notes")),
            )

            cur.execute("SELECT id FROM submissions WHERE submission_number=%s", (submission,))
            exists = cur.fetchone()

            if exists:
                cur.execute("""
                    UPDATE submissions SET
                        submission_date=%s,
                        customer_name=%s,
                        contact_info=%s,
                        card_count=%s,
                        service_type=%s,
                        est_cost=%s,
                        prep_needed=%s,
                        customer_paid=%s,
                        current_status=%s,
                        declared_value=%s,
                        notes=%s,
                        last_updated=NOW()
                    WHERE submission_number=%s
                """, data[0:1] + data[2:] + (submission,))
                updated += 1
            else:
                cur.execute("""
                    INSERT INTO submissions (
                        submission_date, submission_number, customer_name,
                        contact_info, card_count, service_type,
                        est_cost, prep_needed, customer_paid,
                        current_status, declared_value, notes
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, data)
                inserted += 1

        except Exception as e:
            print("ROW ERROR:", e)
            skipped += 1

    conn.commit()
    cur.close()
    conn.close()

    return f"""
    <h2>Upload Complete</h2>
    Inserted: {inserted}<br>
    Updated: {updated}<br>
    Skipped: {skipped}<br>
    <a href='/dashboard'>Dashboard</a>
    """

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

    html += "</table><br><a href='/'>Home</a>"

    cur.close()
    conn.close()

    return html

# ---------- TRACK SEARCH ----------
@app.route("/track")
def track_search():
    return """
    <h2>Track Your Submission</h2>
    <form action="/track_result">
        <input name="submission" placeholder="Enter Submission #">
        <button>Search</button>
    </form>
    """

# ---------- TRACK RESULT ----------
@app.route("/track_result")
def track_result():
    submission = request.args.get("submission")

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            submission_number,
            customer_name,
            card_count,
            service_type,
            current_status,
            last_updated
        FROM submissions
        WHERE submission_number=%s
    """, (submission,))

    row = cur.fetchone()

    cur.close()
    conn.close()

    if not row:
        return "<h3>Submission not found</h3><a href='/track'>Try Again</a>"

    return f"""
    <h2>Tracking Result</h2>
    <p><b>Submission:</b> {row[0]}</p>
    <p><b>Name:</b> {row[1]}</p>
    <p><b>Cards:</b> {row[2]}</p>
    <p><b>Service:</b> {row[3]}</p>
    <p><b>Status:</b> {row[4]}</p>
    <p><b>Last Updated:</b> {row[5]}</p>
    <br>
    <a href='/track'>Search Again</a>
    """

# ---------- RUN ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
