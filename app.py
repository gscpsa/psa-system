from flask import Flask, request, render_template_string
import psycopg2
import pandas as pd
import os

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL)

# 🔥 HARD RESET TABLE EVERY START (fixes your schema mismatch)
def reset_table():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    DROP TABLE IF EXISTS submissions;

    CREATE TABLE submissions (
        id SERIAL PRIMARY KEY,
        submission_number TEXT,
        submission_date TIMESTAMP,
        customer_name TEXT,
        contact_info TEXT,
        card_count INTEGER,
        est_cost NUMERIC,
        prep_needed TEXT,
        service_type TEXT,
        customer_paid TEXT,
        current_status TEXT
    );
    """)

    conn.commit()
    cur.close()
    conn.close()

reset_table()

# ---------- HOME ----------
@app.route("/")
def home():
    return """
    <h2>Upload CSV</h2>
    <form action="/upload" method="post" enctype="multipart/form-data">
        <input type="file" name="file">
        <input type="submit">
    </form>
    <br>
    <a href="/dashboard">View Dashboard</a>
    """

# ---------- UPLOAD ----------
@app.route("/upload", methods=["POST"])
def upload():
    file = request.files["file"]

    df = pd.read_csv(file)

    conn = get_conn()
    cur = conn.cursor()

    inserted = 0

    for _, row in df.iterrows():
        try:
            cur.execute("""
                INSERT INTO submissions (
                    submission_number,
                    submission_date,
                    customer_name,
                    contact_info,
                    card_count,
                    est_cost,
                    prep_needed,
                    service_type,
                    customer_paid,
                    current_status
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                str(row.get("Submission #", "")),
                row.get("S"),
                str(row.get("Customer Name", "")),
                str(row.get("Contact Info", "")),
                int(row.get("# Of Cards", 0)) if str(row.get("# Of Cards", "")).isdigit() else 0,
                float(row.get("Est Cost", 0)) if str(row.get("Est Cost", "")).replace('.', '', 1).isdigit() else 0,
                str(row.get("Prep Needed", "")),
                str(row.get("Service Type", "")),
                str(row.get("Customer Paid", "")),
                str(row.get("Current Status", ""))
            ))
            inserted += 1
        except Exception as e:
            print("Row skipped:", e)

    conn.commit()
    cur.close()
    conn.close()

    return f"<h2>Uploaded {inserted} rows successfully</h2><a href='/dashboard'>Go to dashboard</a>"

# ---------- DASHBOARD ----------
@app.route("/dashboard")
def dashboard():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM submissions ORDER BY id DESC LIMIT 100")
    rows = cur.fetchall()

    cur.close()
    conn.close()

    html = "<h2>Submissions</h2><table border=1><tr>"
    headers = [
        "ID","Submission #","Date","Customer",
        "Contact","Cards","Cost","Prep",
        "Service","Paid","Status"
    ]

    for h in headers:
        html += f"<th>{h}</th>"
    html += "</tr>"

    for r in rows:
        html += "<tr>"
        for col in r:
            html += f"<td>{col}</td>"
        html += "</tr>"

    html += "</table><br><a href='/'>Back</a>"

    return html

# ---------- RUN ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
