from flask import Flask, request, render_template_string, redirect
import psycopg2
import pandas as pd
import os

app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL)

@app.route("/")
def home():
    return """
    <h2>PSA System</h2>
    <a href="/upload">Upload CSV</a><br><br>
    <a href="/search">Staff Search</a>
    """

@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        file = request.files["file"]

        try:
            df = pd.read_csv(file, encoding="latin1")
        except:
            return "CSV READ ERROR"

        inserted = 0
        updated = 0
        errors = 0

        conn = get_conn()
        cur = conn.cursor()

        for _, row in df.iterrows():
            try:
                submission = str(row.get("Submission Number", "")).strip()
                name = str(row.get("Customer Name", "")).strip()
                contact = str(row.get("Contact Info", "")).strip()
                count = row.get("Card Count")
                service = str(row.get("Service Type", "")).strip()
                status = str(row.get("Current Status", "")).strip()

                cur.execute("""
                    INSERT INTO submissions
                    (submission_number, customer_name, contact_info, card_count, service_type, status)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (submission_number)
                    DO UPDATE SET
                        customer_name = EXCLUDED.customer_name,
                        contact_info = EXCLUDED.contact_info,
                        card_count = EXCLUDED.card_count,
                        service_type = EXCLUDED.service_type,
                        status = EXCLUDED.status
                """, (submission, name, contact, count, service, status))

                if cur.rowcount == 1:
                    inserted += 1
                else:
                    updated += 1

            except:
                errors += 1

        conn.commit()
        cur.close()
        conn.close()

        return f"Inserted: {inserted}<br>Updated: {updated}<br>Errors: {errors}"

    return """
    <h2>Upload CSV</h2>
    <form method="post" enctype="multipart/form-data">
        <input type="file" name="file">
        <button type="submit">Upload</button>
    </form>
    <br><a href="/">Back</a>
    """

@app.route("/search", methods=["GET", "POST"])
def search():
    results = []

    if request.method == "POST":
        query = request.form["query"]

        conn = get_conn()
        cur = conn.cursor()

        cur.execute("""
            SELECT submission_number, customer_name, contact_info, status
            FROM submissions
            WHERE
                submission_number ILIKE %s OR
                customer_name ILIKE %s OR
                contact_info ILIKE %s
            LIMIT 50
        """, (f"%{query}%", f"%{query}%", f"%{query}%"))

        results = cur.fetchall()

        cur.close()
        conn.close()

    html = """
    <h2>Staff Search</h2>
    <form method="post">
        <input name="query" placeholder="Search anything">
        <button type="submit">Search</button>
    </form>
    <br>
    <table border=1>
        <tr>
            <th>Submission</th>
            <th>Name</th>
            <th>Contact</th>
            <th>Status</th>
        </tr>
    """

    for r in results:
        html += f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td></tr>"

    html += "</table><br><a href='/'>Back</a>"

    return html


if __name__ == "__main__":
    app.run()
