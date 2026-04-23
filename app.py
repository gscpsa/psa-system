from flask import Flask, request, render_template_string
import pandas as pd
import sqlite3
import re
from datetime import datetime

app = Flask(__name__)
DB = "data.db"

# -----------------------
# DATABASE INIT
# -----------------------
def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS submissions (
        submission_number TEXT PRIMARY KEY,
        customer_name TEXT,
        email TEXT,
        phone TEXT,
        status TEXT,
        service_level TEXT,
        cards INTEGER,
        date_arrived TEXT,
        est_date TEXT,
        last_updated TEXT
    )
    """)

    conn.commit()
    conn.close()

init_db()

# -----------------------
# SAVE / UPDATE (UPSERT)
# -----------------------
def save_row(row):
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("""
    INSERT INTO submissions (
        submission_number, customer_name, email, phone,
        status, service_level, cards, date_arrived,
        est_date, last_updated
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(submission_number) DO UPDATE SET
        customer_name=excluded.customer_name,
        email=excluded.email,
        phone=excluded.phone,
        status=excluded.status,
        service_level=excluded.service_level,
        cards=excluded.cards,
        date_arrived=excluded.date_arrived,
        est_date=excluded.est_date,
        last_updated=excluded.last_updated
    """, (
        row.get("submission_number"),
        row.get("customer_name"),
        row.get("email"),
        row.get("phone"),
        row.get("status"),
        row.get("service_level"),
        row.get("cards"),
        row.get("date_arrived"),
        row.get("est_date"),
        datetime.now().isoformat()
    ))

    conn.commit()
    conn.close()

# -----------------------
# HOME / DASHBOARD
# -----------------------
@app.route("/")
def home():
    conn = sqlite3.connect(DB)
    df = pd.read_sql("SELECT * FROM submissions ORDER BY last_updated DESC LIMIT 100", conn)
    conn.close()

    return render_template_string("""
    <h1>PSA Dashboard</h1>

    <a href="/upload">Upload Excel/CSV</a><br>
    <a href="/upload_psa">Upload PSA PDF</a><br>
    <a href="/search">Search</a><br><br>

    {{ table|safe }}
    """, table=df.to_html(index=False))

# -----------------------
# SEARCH
# -----------------------
@app.route("/search", methods=["GET", "POST"])
def search():
    results = ""

    if request.method == "POST":
        q = request.form.get("q")

        conn = sqlite3.connect(DB)
        df = pd.read_sql("""
            SELECT * FROM submissions
            WHERE submission_number LIKE ?
            OR customer_name LIKE ?
            OR email LIKE ?
            OR phone LIKE ?
        """, conn, params=[f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"])
        conn.close()

        results = df.to_html(index=False)

    return """
    <h2>Search</h2>
    <form method="post">
        <input name="q">
        <button>Search</button>
    </form>
    """ + results

# -----------------------
# CSV / EXCEL UPLOAD
# -----------------------
@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        file = request.files["file"]

        try:
            df = pd.read_excel(file)
        except:
            file.seek(0)
            df = pd.read_csv(file, encoding="utf-8", errors="ignore")

        df.columns = df.columns.str.strip()

        for _, row in df.iterrows():
            save_row({
                "submission_number": str(row.get("Submission #") or row.get("submission_number")),
                "customer_name": row.get("Customer Name"),
                "email": row.get("Email"),
                "phone": row.get("Phone"),
                "status": row.get("Status"),
                "service_level": row.get("Service Type"),
                "cards": row.get("# Of Cards"),
                "date_arrived": row.get("Date"),
                "est_date": None
            })

        return "Upload complete"

    return """
    <h2>Upload CSV/Excel</h2>
    <form method="post" enctype="multipart/form-data">
        <input type="file" name="file">
        <button>Upload</button>
    </form>
    """

# -----------------------
# PSA PDF PARSER (FIXED)
# -----------------------
@app.route("/upload_psa", methods=["GET", "POST"])
def upload_psa():
    if request.method == "POST":
        file = request.files["file"]

        import pdfplumber

        text = ""
        with pdfplumber.open(file) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text += t + "\n"

        # split by submissions
        blocks = re.split(r"Sub\s*#", text)

        updated = 0

        for block in blocks:
            block = block.strip()
            if not block or not block[0].isdigit():
                continue

            sub_match = re.match(r"(\d+)", block)
            if not sub_match:
                continue

            sub_number = sub_match.group(1)

            # STATUS TYPES
            if "Order Arrived" in block:
                status = "Order Arrived"
            elif "Grading" in block:
                status = "Grading"
            elif "Complete" in block:
                status = "Complete"
            elif "QA Checks" in block:
                status = "QA Checks"
            elif "Research & ID" in block:
                status = "Research & ID"
            else:
                status = "Processing"

            # cards
            cards_match = re.search(r"(\d+)\s+Cards?", block)
            cards = int(cards_match.group(1)) if cards_match else None

            # date arrived
            date_match = re.search(r"(Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}", block)
            date_arrived = date_match.group(0) if date_match else None

            # est date
            est_match = re.search(r"Est\. by\s+(.*?\d{4})", block)
            est_date = est_match.group(1) if est_match else None

            # service level
            if "Value" in block:
                service = "Value"
            elif "Express" in block:
                service = "Express"
            elif "Regular" in block:
                service = "Regular"
            else:
                service = None

            save_row({
                "submission_number": sub_number,
                "status": status,
                "cards": cards,
                "date_arrived": date_arrived,
                "est_date": est_date,
                "service_level": service
            })

            updated += 1

        return f"PSA updated: {updated}"

    return """
    <h2>Upload PSA PDF</h2>
    <form method="post" enctype="multipart/form-data">
        <input type="file" name="file">
        <button>Upload</button>
    </form>
    """

# -----------------------
# RUN
# -----------------------
if __name__ == "__main__":
    app.run(debug=True)
