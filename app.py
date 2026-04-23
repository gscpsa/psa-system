from flask import Flask, request
import pandas as pd
import psycopg2
import os

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


# ---------- SAFE HELPERS ----------
def safe_text(val):
    try:
        if pd.isna(val):
            return ""
    except:
        pass
    return str(val).strip()


def safe_int(val):
    try:
        if pd.isna(val):
            return 0
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

    # clean headers
    df.columns = df.columns.astype(str).str.strip()

    # remove junk excel columns
    df = df.loc[:, ~df.columns.str.contains("^Unnamed", na=False)]

    conn = get_conn()
    cur = conn.cursor()

    inserted = 0

    for _, row in df.iterrows():
        try:
            submission_number = safe_text(row.get("Submission #"))

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
                    current_status = EXCLUDED.current_status,
                    card_count = EXCLUDED.card_count,
                    est_cost = EXCLUDED.est_cost,
                    last_updated = NOW()
            """, (
                safe_text(row.get("s")),
                submission_number,
                safe_text(row.get("Customer Name")),
                safe_text(row.get("Contact Info")),
                safe_int(row.get("# Of Cards")),
                safe_text(row.get("Service Type")),
                safe_text(row.get("Est Cost")),
                safe_text(row.get("Prep Needed")),
                safe_text(row.get("Customer Paid")),
                safe_text(row.get("Current Status")),
                safe_text(row.get("Decalared Value")),
                safe_text(row.get("Notes")),
            ))

            inserted += 1

        except Exception as e:
            print("ROW ERROR:", e)
            continue

    conn.commit()
    cur.close()
    conn.close()

    return f"<h2>Uploaded / Updated {inserted} rows</h2><a href='/dashboard'>Go to dashboard</a>"


# ---------- DASHBOARD ----------
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
            decalared_value,
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
        <th>Submission #</th>
        <th>Name</th>
        <th>Contact</th>
        <th>Cards</th>
        <th>Service</th>
        <th>Est Cost</th>
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
