from flask import Flask, request
import pandas as pd
import psycopg2
import os

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL)

# ---------- HOME ----------
@app.route("/")
def home():
    return """
    <h2>Upload CSV</h2>
    <form action="/upload" method="post" enctype="multipart/form-data">
        <input type="file" name="file">
        <button type="submit">Submit</button>
    </form>
    <br>
    <a href="/dashboard">View Dashboard</a>
    """

# ---------- UPLOAD ----------
@app.route("/upload", methods=["POST"])
def upload():
    file = request.files["file"]

    # Robust CSV read
    df = pd.read_csv(
        file,
        encoding="latin1",
        engine="python",
        on_bad_lines="skip"
    )

    # 🔥 Normalize headers
    df.columns = df.columns.str.strip()

    # 🔥 DEBUG: print real headers (you will see in Railway logs)
    print("CSV HEADERS:", df.columns.tolist())

    # 🔥 Auto-map columns (no exact match needed)
    def find_col(keyword):
        for col in df.columns:
            if keyword.lower() in col.lower():
                return col
        return None

    col_submission = find_col("submission")
    col_name = find_col("name")
    col_contact = find_col("contact")
    col_cards = find_col("card")
    col_service = find_col("service")
    col_status = find_col("status")

    conn = get_conn()
    cur = conn.cursor()

    inserted = 0

    for _, row in df.iterrows():
        try:
            submission = str(row.get(col_submission, "")).strip()
            name = str(row.get(col_name, "")).strip()
            contact = str(row.get(col_contact, "")).strip()
            cards = str(row.get(col_cards, "")).strip()
            service = str(row.get(col_service, "")).strip()
            status = str(row.get(col_status, "")).strip()

            if not submission and not name:
                continue

            cur.execute("""
            INSERT INTO submissions (
                submission_number,
                customer_name,
                contact_info,
                card_count,
                service_type,
                status
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """, (submission, name, contact, cards, service, status))

            inserted += 1

        except Exception as e:
            print("ROW ERROR:", e)
            continue

    conn.commit()
    cur.close()
    conn.close()

    return f"<h2>Uploaded {inserted} rows successfully</h2><a href='/dashboard'>Go to dashboard</a>"

# ---------- DASHBOARD ----------
@app.route("/dashboard")
def dashboard():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    SELECT
        submission_number,
        customer_name,
        contact_info,
        card_count,
        service_type,
        status,
        last_updated
    FROM submissions
    ORDER BY last_updated DESC
    """)

    rows = cur.fetchall()

    html = "<h2>Dashboard</h2><table border=1>"
    html += "<tr><th>Submission</th><th>Name</th><th>Contact</th><th>Cards</th><th>Service</th><th>Status</th><th>Updated</th></tr>"

    for r in rows:
        html += "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"

    html += "</table><br><a href='/'>Upload More</a>"

    cur.close()
    conn.close()

    return html

# ---------- RUN ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
