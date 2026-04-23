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

    # Robust CSV read (fixes encoding + bad rows)
    df = pd.read_csv(
        file,
        encoding="latin1",
        engine="python",
        on_bad_lines="skip"
    )

    conn = get_conn()
    cur = conn.cursor()

    inserted = 0

    for _, row in df.iterrows():
        try:
            cur.execute("""
            INSERT INTO submissions (
                submission_number,
                customer_name,
                contact_info,
                card_count,
                service_type,
                status
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                str(row.get("Submission #")),
                str(row.get("Customer Name")),
                str(row.get("Contact Info")),
                str(row.get("# Of Cards")),
                str(row.get("Service Type")),
                str(row.get("Current Status"))
            ))
            inserted += 1
        except Exception:
            # skip any bad row silently
            continue

    conn.commit()
    cur.close()
    conn.close()

    return f"Uploaded {inserted} rows successfully"


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
