from flask import Flask, request
import pandas as pd
import psycopg2
import os, io, re
from datetime import datetime

app = Flask(__name__)
DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

# =========================
# INIT TABLE
# =========================
def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
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
        last_updated TIMESTAMP
    )
    """)

    conn.commit()
    cur.close()
    conn.close()

init_db()

# =========================
# UPSERT
# =========================
def save_row(row):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO submissions VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON CONFLICT (submission_number) DO UPDATE SET
        customer_name=EXCLUDED.customer_name,
        email=EXCLUDED.email,
        phone=EXCLUDED.phone,
        status=EXCLUDED.status,
        service_level=EXCLUDED.service_level,
        cards=EXCLUDED.cards,
        date_arrived=EXCLUDED.date_arrived,
        est_date=EXCLUDED.est_date,
        last_updated=EXCLUDED.last_updated
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
        datetime.now()
    ))

    conn.commit()
    cur.close()
    conn.close()

# =========================
# DASHBOARD
# =========================
@app.route("/")
def dashboard():
    conn = get_conn()
    df = pd.read_sql("SELECT * FROM submissions ORDER BY last_updated DESC LIMIT 100", conn)
    conn.close()

    return f"""
    <h2>PSA Dashboard</h2>

    <a href='/upload'>Upload Excel</a> |
    <a href='/upload_psa'>Upload PSA PDF</a> |
    <a href='/search'>Search</a>

    <br><br>

    {df.to_html(index=False)}
    """

# =========================
# SEARCH
# =========================
@app.route("/search", methods=["GET","POST"])
def search():
    if request.method == "POST":
        q = request.form.get("q")

        conn = get_conn()
        df = pd.read_sql("""
        SELECT * FROM submissions
        WHERE submission_number ILIKE %s
        OR customer_name ILIKE %s
        OR email ILIKE %s
        OR phone ILIKE %s
        """, conn, params=[f"%{q}%"]*4)
        conn.close()

        return df.to_html(index=False)

    return """
    <form method="post">
        <input name="q">
        <button>Search</button>
    </form>
    """

# =========================
# EXCEL UPLOAD
# =========================
@app.route("/upload", methods=["GET","POST"])
def upload():
    if request.method == "POST":
        file = request.files["file"]

        try:
            df = pd.read_excel(file)
        except:
            file.seek(0)
            df = pd.read_csv(file, encoding="utf-8", errors="ignore")

        for _, row in df.iterrows():
            save_row({
                "submission_number": str(row.get("Submission #")),
                "customer_name": row.get("Customer Name"),
                "email": row.get("Email"),
                "phone": row.get("Phone"),
                "status": row.get("Current Status"),
                "service_level": row.get("Service Type"),
                "cards": row.get("# Of Cards"),
                "date_arrived": row.get("S"),
                "est_date": None
            })

        return "Upload complete"

    return """
    <h2>Upload Excel</h2>
    <form method="post" enctype="multipart/form-data">
        <input type="file" name="file">
        <button>Upload</button>
    </form>
    """

# =========================
# PSA PDF PARSER
# =========================
@app.route("/upload_psa", methods=["GET","POST"])
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

        blocks = re.split(r"Sub\s*#", text)

        updated = 0

        for b in blocks:
            if not b.strip() or not b[0].isdigit():
                continue

            sub = re.match(r"(\d+)", b)
            if not sub:
                continue

            sub_number = sub.group(1)

            if "Order Arrived" in b:
                status = "Order Arrived"
            elif "Grading" in b:
                status = "Grading"
            elif "Complete" in b:
                status = "Complete"
            else:
                status = "Processing"

            save_row({
                "submission_number": sub_number,
                "status": status
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

# =========================
if __name__ == "__main__":
    app.run()
