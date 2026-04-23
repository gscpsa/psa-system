from flask import Flask, request
import pandas as pd
import psycopg2
import os

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def reset_table():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    DROP TABLE IF EXISTS submissions;

    CREATE TABLE submissions (
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

    conn.commit()
    cur.close()
    conn.close()


def safe_text(val):
    if pd.isna(val):
        return ""
    return str(val).strip()


def safe_int(val):
    if pd.isna(val):
        return 0
    try:
        return int(float(val))
    except Exception:
        return 0


@app.route("/")
def home():
    return """
    <h2>Upload Excel File</h2>
    <form action="/upload" method="post" enctype="multipart/form-data">
        <input type="file" name="file" accept=".xlsx,.xls">
        <button type="submit">Submit</button>
    </form>
    <br>
    <a href="/dashboard">View Dashboard</a>
    """


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files["file"]

    # Your file is Excel, not CSV
    df = pd.read_excel(file)

    # normalize headers
    df.columns = df.columns.astype(str).str.strip()

    # remove junk excel columns
    df = df.loc[:, ~df.columns.str.contains("^Unnamed", na=False)]

    # clean rebuild every upload while you're testing
    reset_table()

    conn = get_conn()
    cur = conn.cursor()

    inserted = 0

    for _, row in df.iterrows():
        try:
            submission_date = safe_text(row.get("s"))
            submission_number = safe_text(row.get("Submission #"))
            customer_name = safe_text(row.get("Customer Name"))
            contact_info = safe_text(row.get("Contact Info"))
            card_count = safe_int(row.get("# Of Cards"))
            service_type = safe_text(row.get("Service Type"))
            est_cost = safe_text(row.get("Est Cost"))
            prep_needed = safe_text(row.get("Prep Needed"))
            customer_paid = safe_text(row.get("Customer Paid"))
            current_status = safe_text(row.get("Current Status"))
            decalared_value = safe_text(row.get("Decalared Value"))
            notes = safe_text(row.get("Notes"))

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
            """, (
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
            ))

            inserted += 1

        except Exception as e:
            print("ROW ERROR:", e)
            continue

    conn.commit()
    cur.close()
    conn.close()

    return f"<h2>Uploaded {inserted} rows successfully</h2><a href='/dashboard'>Go to dashboard</a>"


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
        ORDER BY id DESC
    """)

    rows = cur.fetchall()

    html = "<h2>Dashboard</h2><table border=1 cellpadding=5 cellspacing=0>"
    html += """
    <tr>
        <th>Date</th>
        <th>Submission #</th>
        <th>Customer Name</th>
        <th>Contact Info</th>
        <th># Of Cards</th>
        <th>Service Type</th>
        <th>Est Cost</th>
        <th>Prep Needed</th>
        <th>Customer Paid</th>
        <th>Current Status</th>
        <th>Decalared Value</th>
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
