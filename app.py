from flask import Flask, request
import psycopg2
import pandas as pd
import os
import io

app = Flask(__name__)
DATABASE_URL = os.getenv("DATABASE_URL")

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
        notes TEXT
    );
    """)

    conn.commit()
    cur.close()
    conn.close()

setup_database()

# ---------- FILE READER ----------
def read_any(file):
    name = file.filename.lower()

    if name.endswith(("xlsx", "xls")):
        return pd.read_excel(file)

    raw = file.read()
    file.seek(0)

    for enc in ["utf-8", "latin1"]:
        try:
            text = raw.decode(enc)
            break
        except:
            continue

    for sep in [",", ";", "\t"]:
        try:
            return pd.read_csv(io.StringIO(text), sep=sep)
        except:
            continue

    raise Exception("Cannot parse file")

def clean(val):
    try:
        if pd.isna(val):
            return ""
    except:
        pass
    return str(val).strip()

# ---------- FIELD EXTRACTION ----------
def extract(row):
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
        k = str(key).lower()
        v = clean(row.get(key))

        if "submission" in k:
            data["submission_number"] = v
        elif k == "s" or "date" in k:
            data["submission_date"] = v
        elif "name" in k:
            data["customer_name"] = v
        elif "contact" in k or "phone" in k:
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

# ---------- ROUTES ----------
@app.route("/")
def home():
    return """
    <h2>PSA System</h2>
    <a href="/upload">Upload</a><br><br>
    <a href="/search">Staff Search</a>
    """

@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        file = request.files.get("file")

        try:
            df = read_any(file)
        except Exception as e:
            return str(e)

        conn = get_conn()
        cur = conn.cursor()

        inserted = updated = errors = 0

        for _, row in df.iterrows():
            try:
                d = extract(row)

                if not d["submission_number"]:
                    errors += 1
                    continue

                cur.execute("""
                INSERT INTO submissions (
                    submission_number, submission_date, customer_name,
                    contact_info, card_count, service_type,
                    est_cost, prep_needed, customer_paid,
                    status, declared_value, notes
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (submission_number)
                DO UPDATE SET
                    submission_date=EXCLUDED.submission_date,
                    customer_name=EXCLUDED.customer_name,
                    contact_info=EXCLUDED.contact_info,
                    card_count=EXCLUDED.card_count,
                    service_type=EXCLUDED.service_type,
                    est_cost=EXCLUDED.est_cost,
                    prep_needed=EXCLUDED.prep_needed,
                    customer_paid=EXCLUDED.customer_paid,
                    status=EXCLUDED.status,
                    declared_value=EXCLUDED.declared_value,
                    notes=EXCLUDED.notes
                """, tuple(d.values()))

                inserted += 1

            except Exception as e:
                print("ROW ERROR:", e)
                errors += 1

        conn.commit()
        cur.close()
        conn.close()

        return f"Inserted/Updated: {inserted} | Errors: {errors}"

    return """
    <h2>Upload</h2>
    <form method="post" enctype="multipart/form-data">
        <input type="file" name="file">
        <button>Upload</button>
    </form>
    """

@app.route("/search", methods=["GET", "POST"])
def search():
    results = []

    if request.method == "POST":
        q = request.form["query"]

        conn = get_conn()
        cur = conn.cursor()

        cur.execute("""
        SELECT *
        FROM submissions
        WHERE
            submission_number ILIKE %s OR
            customer_name ILIKE %s OR
            contact_info ILIKE %s
        LIMIT 50
        """, (f"%{q}%", f"%{q}%", f"%{q}%"))

        results = cur.fetchall()

        cur.close()
        conn.close()

    html = """
    <h2>Search</h2>
    <form method="post">
        <input name="query">
        <button>Search</button>
    </form>
    <table border=1>
    """

    for r in results:
        html += "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"

    html += "</table>"

    return html

if __name__ == "__main__":
    app.run()
