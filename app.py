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
    """

# ---------- UPLOAD WITH DUPLICATE PROTECTION ----------
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

            submission_date = clean(row.get("s"))
            customer_name = clean(row.get("Customer Name"))
            contact_info = clean(row.get("Contact Info"))
            card_count = clean(row.get("# Of Cards"))
            service_type = clean(row.get("Service Type"))
            est_cost = clean(row.get("Est Cost"))
            prep_needed = clean(row.get("Prep Needed"))
            customer_paid = clean(row.get("Customer Paid"))
            current_status = clean(row.get("Current Status"))
            declared_value = clean(row.get("Decalared Value"))
            notes = clean(row.get("Notes"))

            # Check if submission already exists
            cur.execute(
                "SELECT id FROM submissions WHERE submission_number = %s LIMIT 1",
                (submission,)
            )
            existing = cur.fetchone()

            if existing:
                cur.execute("""
                    UPDATE submissions
                    SET
                        submission_date = %s,
                        customer_name = %s,
                        contact_info = %s,
                        card_count = %s,
                        service_type = %s,
                        est_cost = %s,
                        prep_needed = %s,
                        customer_paid = %s,
                        current_status = %s,
                        declared_value = %s,
                        notes = %s,
                        last_updated = NOW()
                    WHERE submission_number = %s
                """, (
                    submission_date,
                    customer_name,
                    contact_info,
                    card_count,
                    service_type,
                    est_cost,
                    prep_needed,
                    customer_paid,
                    current_status,
                    declared_value,
                    notes,
                    submission
                ))
                updated += 1
            else:
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
                        declared_value,
                        notes
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    submission_date,
                    submission,
                    customer_name,
                    contact_info,
                    card_count,
                    service_type,
                    est_cost,
                    prep_needed,
                    customer_paid,
                    current_status,
                    declared_value,
                    notes
                ))
                inserted += 1

        except Exception as e:
            print("ROW ERROR:", e)
            skipped += 1

    conn.commit()
    cur.close()
    conn.close()

    return f"""
    <h2>Upload Complete</h2>
    <p>Inserted: {inserted}</p>
    <p>Updated: {updated}</p>
    <p>Skipped: {skipped}</p>
    <a href='/dashboard'>Dashboard</a>
    """

# ---------- FULL DASHBOARD ----------
@app.route("/dashboard")
def dashboard():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
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
            declared_value,
            notes,
            last_updated
        FROM submissions
        ORDER BY last_updated DESC
    """)

    rows = cur.fetchall()

    html = "<h2>PSA Submissions</h2><table border=1 cellpadding=5 cellspacing=0>"
    html += """
    <tr>
        <th>Date</th>
        <th>Submission</th>
        <th>Name</th>
        <th>Contact</th>
        <th>Cards</th>
        <th>Service</th>
        <th>Cost</th>
        <th>Prep</th>
        <th>Paid</th>
        <th>Status</th>
        <th>Declared</th>
        <th>Notes</th>
        <th>Updated</th>
    </tr>
    """

    for r in rows:
        html += "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"

    html += "</table><br><a href='/'>Upload More</a>"

    cur.close()
    conn.close()

    return html

# ---------- RUN ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
