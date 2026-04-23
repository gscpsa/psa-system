from flask import Flask, request
import psycopg2
import pandas as pd
import os
import io

app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def ensure_table():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS submissions (
        id SERIAL PRIMARY KEY,
        submission_number TEXT UNIQUE,
        submission_date TEXT,
        customer_name TEXT,
        contact_info TEXT,
        card_count TEXT,
        service_type TEXT,
        est_cost TEXT,
        prep_needed TEXT,
        customer_paid TEXT,
        status TEXT,
        declared_value TEXT,
        notes TEXT,
        last_updated TIMESTAMP DEFAULT NOW()
    );
    """)

    conn.commit()
    cur.close()
    conn.close()

ensure_table()

def clean(val):
    try:
        if pd.isna(val):
            return ""
    except:
        pass
    return str(val).strip()

def read_any(file_storage):
    filename = (file_storage.filename or "").lower()

    if filename.endswith((".xlsx", ".xls")):
        return pd.read_excel(file_storage)

    raw = file_storage.read()
    file_storage.seek(0)

    text = None
    for enc in ["utf-8", "latin1"]:
        try:
            text = raw.decode(enc)
            break
        except:
            continue

    if text is None:
        raise Exception("Unable to decode file")

    for sep in [",", ";", "\t"]:
        try:
            return pd.read_csv(io.StringIO(text), sep=sep)
        except:
            continue

    raise Exception("Unable to parse file")

def extract_row(row):
    data = {
        "submission_number": "",
        "submission_date": "",
        "customer_name": "",
        "contact_info": "",
        "card_count": "",
        "service_type": "",
        "est_cost": "",
        "prep_needed": "",
        "customer_paid": "",
        "status": "",
        "declared_value": "",
        "notes": ""
    }

    for key in row.keys():
        k = str(key).strip().lower()
        v = clean(row.get(key))

        if "submission" in k:
            data["submission_number"] = v
        elif k == "s" or ("date" in k and not data["submission_date"]):
            data["submission_date"] = v
        elif "customer name" in k or k == "name":
            data["customer_name"] = v
        elif "contact" in k or "phone" in k or "email" in k:
            data["contact_info"] = v
        elif "card" in k:
            data["card_count"] = v
        elif "service" in k:
            data["service_type"] = v
        elif "cost" in k:
            data["est_cost"] = v
        elif "prep" in k:
            data["prep_needed"] = v
        elif "paid" in k:
            data["customer_paid"] = v
        elif "status" in k:
            data["status"] = v
        elif "declared" in k:
            data["declared_value"] = v
        elif "note" in k:
            data["notes"] = v

    return data

@app.route("/")
def home():
    return """
    <h2>PSA Staff System</h2>
    <a href="/upload">Upload File</a><br><br>
    <a href="/dashboard">Dashboard</a><br><br>
    <a href="/staff-search">Staff Search</a>
    """

@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        file = request.files.get("file")
        if not file:
            return "No file uploaded"

        try:
            df = read_any(file)
        except Exception as e:
            return f"FILE READ ERROR: {str(e)}"

        df.columns = [str(c).strip() for c in df.columns]

        inserted = 0
        updated = 0
        errors = 0

        conn = get_conn()
        cur = conn.cursor()

        for _, row in df.iterrows():
            try:
                d = extract_row(row)

                if not d["submission_number"]:
                    errors += 1
                    continue

                cur.execute("""
                    INSERT INTO submissions (
                        submission_number,
                        submission_date,
                        customer_name,
                        contact_info,
                        card_count,
                        service_type,
                        est_cost,
                        prep_needed,
                        customer_paid,
                        status,
                        declared_value,
                        notes
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (submission_number)
                    DO UPDATE SET
                        submission_date = EXCLUDED.submission_date,
                        customer_name = EXCLUDED.customer_name,
                        contact_info = EXCLUDED.contact_info,
                        card_count = EXCLUDED.card_count,
                        service_type = EXCLUDED.service_type,
                        est_cost = EXCLUDED.est_cost,
                        prep_needed = EXCLUDED.prep_needed,
                        customer_paid = EXCLUDED.customer_paid,
                        status = EXCLUDED.status,
                        declared_value = EXCLUDED.declared_value,
                        notes = EXCLUDED.notes,
                        last_updated = NOW()
                """, (
                    d["submission_number"],
                    d["submission_date"],
                    d["customer_name"],
                    d["contact_info"],
                    d["card_count"],
                    d["service_type"],
                    d["est_cost"],
                    d["prep_needed"],
                    d["customer_paid"],
                    d["status"],
                    d["declared_value"],
                    d["notes"]
                ))

                # PostgreSQL rowcount is 1 for insert or update here, so track as updated if already existed
                inserted += 1

            except Exception as e:
                print("ROW ERROR:", e)
                errors += 1

        conn.commit()
        cur.close()
        conn.close()

        return f"""
        <h3>Upload Results</h3>
        Inserted/Updated: {inserted}<br>
        Errors: {errors}<br>
        <a href="/dashboard">Dashboard</a><br>
        <a href="/staff-search">Staff Search</a>
        """

    return """
    <h2>Upload CSV or Excel</h2>
    <form method="post" enctype="multipart/form-data">
        <input type="file" name="file">
        <button type="submit">Upload</button>
    </form>
    <br><a href="/">Back</a>
    """

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
            est_cost,
            prep_needed,
            customer_paid,
            declared_value,
            notes,
            submission_date,
            last_updated
        FROM submissions
        ORDER BY last_updated DESC
        LIMIT 500
    """)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    html = """
    <h2>Dashboard</h2>
    <table border="1" cellpadding="5" cellspacing="0">
        <tr>
            <th>Submission</th>
            <th>Name</th>
            <th>Contact</th>
            <th>Cards</th>
            <th>Service</th>
            <th>Status</th>
            <th>Cost</th>
            <th>Prep</th>
            <th>Paid</th>
            <th>Declared</th>
            <th>Notes</th>
            <th>Date</th>
            <th>Updated</th>
        </tr>
    """

    for r in rows:
        html += "<tr>" + "".join(f"<td>{'' if v is None else v}</td>" for v in r) + "</tr>"

    html += "</table><br><a href='/'>Back</a><br><a href='/staff-search'>Staff Search</a>"
    return html

@app.route("/staff-search", methods=["GET", "POST"])
def staff_search():
    results = []

    if request.method == "POST":
        query = clean(request.form.get("query")).strip()

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
                est_cost,
                prep_needed,
                customer_paid,
                declared_value,
                notes,
                submission_date,
                last_updated
            FROM submissions
            WHERE
                LOWER(COALESCE(submission_number, '')) LIKE LOWER(%s)
                OR LOWER(COALESCE(customer_name, '')) LIKE LOWER(%s)
                OR LOWER(COALESCE(contact_info, '')) LIKE LOWER(%s)
                OR LOWER(COALESCE(status, '')) LIKE LOWER(%s)
                OR LOWER(COALESCE(service_type, '')) LIKE LOWER(%s)
            ORDER BY last_updated DESC
            LIMIT 100
        """, (
            f"%{query}%",
            f"%{query}%",
            f"%{query}%",
            f"%{query}%",
            f"%{query}%"
        ))

        results = cur.fetchall()
        cur.close()
        conn.close()

    html = """
    <h2>Staff Search</h2>
    <form method="post">
        <input name="query" placeholder="Search by submission, name, phone, status, service">
        <button type="submit">Search</button>
    </form>
    <br>
    <table border="1" cellpadding="5" cellspacing="0">
        <tr>
            <th>Submission</th>
            <th>Name</th>
            <th>Contact</th>
            <th>Cards</th>
            <th>Service</th>
            <th>Status</th>
            <th>Cost</th>
            <th>Prep</th>
            <th>Paid</th>
            <th>Declared</th>
            <th>Notes</th>
            <th>Date</th>
            <th>Updated</th>
        </tr>
    """

    for r in results:
        html += "<tr>" + "".join(f"<td>{'' if v is None else v}</td>" for v in r) + "</tr>"

    html += "</table><br><a href='/'>Back</a>"
    return html

if __name__ == "__main__":
    app.run()
