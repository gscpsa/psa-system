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

    cur.execute("DROP TABLE IF EXISTS submissions;")

    cur.execute("""
    CREATE TABLE submissions (
        id SERIAL PRIMARY KEY,
        submission_number TEXT,
        customer_name TEXT,
        contact_info TEXT,
        card_count TEXT,
        service_type TEXT,
        current_status TEXT
    );
    """)

    conn.commit()
    cur.close()
    conn.close()

setup_database()

# ---------- HOME ----------
@app.route("/")
def home():
    return """
    <h2>Upload Excel</h2>
    <form action="/upload" method="post" enctype="multipart/form-data">
        <input type="file" name="file">
        <button type="submit">Upload</button>
    </form>
    <br>
    <a href="/dashboard">Dashboard</a>
    """

# ---------- UPLOAD ----------
@app.route("/upload", methods=["POST"])
def upload():
    file = request.files["file"]

    df = pd.read_excel(file)
    df.columns = df.columns.astype(str).str.strip()

    # find submission column
    submission_col = None
    for col in df.columns:
        if "submission" in col.lower():
            submission_col = col
            break

    conn = get_conn()
    cur = conn.cursor()

    inserted = 0

    for _, row in df.iterrows():
        try:
            submission = str(row.get(submission_col, "")).strip()
            if not submission:
                continue

            cur.execute("""
                INSERT INTO submissions (
                    submission_number,
                    customer_name,
                    contact_info,
                    card_count,
                    service_type,
                    current_status
                ) VALUES (%s,%s,%s,%s,%s,%s)
            """, (
                submission,
                str(row.get("Customer Name", "")).strip(),
                str(row.get("Contact Info", "")).strip(),
                str(row.get("# Of Cards", "")).strip(),
                str(row.get("Service Type", "")).strip(),
                str(row.get("Current Status", "")).strip()
            ))

            inserted += 1

        except Exception as e:
            print("ROW ERROR:", e)

    conn.commit()
    cur.close()
    conn.close()

    return f"<h2>Inserted {inserted} rows</h2><a href='/dashboard'>Dashboard</a>"

# ---------- DASHBOARD ----------
@app.route("/dashboard")
def dashboard():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM submissions ORDER BY id DESC")
    rows = cur.fetchall()

    html = "<h2>Dashboard</h2><table border=1>"
    html += """
    <tr>
        <th>ID</th>
        <th>Submission</th>
        <th>Name</th>
        <th>Contact</th>
        <th>Cards</th>
        <th>Service</th>
        <th>Status</th>
    </tr>
    """

    for r in rows:
        html += "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"

    html += "</table><br><a href='/'>Upload</a>"

    cur.close()
    conn.close()

    return html

# ---------- RUN ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
