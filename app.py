from flask import Flask, request, redirect
import psycopg2
import pandas as pd
import os
import io
import json

app = Flask(__name__)
DATABASE_URL = os.getenv("DATABASE_URL")


def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


# ---------- DATABASE SETUP (SAFE, NO DROPS) ----------
def ensure_table():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id SERIAL PRIMARY KEY,
            submission_number TEXT,
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
            raw_data JSONB,
            last_updated TIMESTAMP DEFAULT NOW()
        );
    """)

    # Add any missing columns for older versions of the table
    needed_columns = {
        "submission_number": "TEXT",
        "submission_date": "TEXT",
        "customer_name": "TEXT",
        "contact_info": "TEXT",
        "card_count": "TEXT",
        "service_type": "TEXT",
        "est_cost": "TEXT",
        "prep_needed": "TEXT",
        "customer_paid": "TEXT",
        "status": "TEXT",
        "declared_value": "TEXT",
        "notes": "TEXT",
        "raw_data": "JSONB",
        "last_updated": "TIMESTAMP DEFAULT NOW()",
    }

    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'submissions'
    """)
    existing = {row[0] for row in cur.fetchall()}

    for col, col_type in needed_columns.items():
        if col not in existing:
            cur.execute(f"ALTER TABLE submissions ADD COLUMN {col} {col_type};")

    # Helpful indexes, safe to run repeatedly
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_submissions_submission_number
        ON submissions (submission_number);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_submissions_customer_name
        ON submissions (customer_name);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_submissions_contact_info
        ON submissions (contact_info);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_submissions_status
        ON submissions (status);
    """)

    conn.commit()
    cur.close()
    conn.close()


ensure_table()


# ---------- HELPERS ----------
def clean(value):
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def read_any(file_storage):
    filename = (file_storage.filename or "").lower()

    if filename.endswith((".xlsx", ".xls")):
        return pd.read_excel(file_storage)

    raw = file_storage.read()
    file_storage.seek(0)

    text = None
    for enc in ("utf-8", "latin1"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue

    if text is None:
        raise Exception("Unable to decode file")

    for sep in (",", ";", "\t"):
        try:
            return pd.read_csv(io.StringIO(text), sep=sep)
        except Exception:
            continue

    raise Exception("Unable to parse CSV")


def get_value_by_alias(raw_row, aliases):
    """
    Case-insensitive exact alias match against original file headers.
    """
    lowered = {str(k).strip().lower(): k for k in raw_row.keys()}
    for alias in aliases:
        key = lowered.get(alias.strip().lower())
        if key is not None:
            return clean(raw_row.get(key))
    return ""


def extract_row(raw_row):
    """
    Locked mapping for your file structure, with safe fallbacks.
    """
    return {
        "submission_number": get_value_by_alias(
            raw_row,
            ["Submission #", "Submission Number", "Submission No", "Submission"]
        ),
        "submission_date": get_value_by_alias(
            raw_row,
            ["S", "Submission Date", "Date"]
        ),
        "customer_name": get_value_by_alias(
            raw_row,
            ["Customer Name", "Name"]
        ),
        "contact_info": get_value_by_alias(
            raw_row,
            ["Contact Info", "Phone", "Email", "Contact"]
        ),
        "card_count": get_value_by_alias(
            raw_row,
            ["# Of Cards", "# of Cards", "Card Count", "Cards"]
        ),
        "service_type": get_value_by_alias(
            raw_row,
            ["Service Type", "Service"]
        ),
        "est_cost": get_value_by_alias(
            raw_row,
            ["Est Cost", "Estimated Cost", "Cost"]
        ),
        "prep_needed": get_value_by_alias(
            raw_row,
            ["Prep Needed", "Prep"]
        ),
        "customer_paid": get_value_by_alias(
            raw_row,
            ["Customer Paid", "Paid"]
        ),
        "status": get_value_by_alias(
            raw_row,
            ["Current Status", "Status"]
        ),
        "declared_value": get_value_by_alias(
            raw_row,
            ["Declared Value", "Decalared Value"]
        ),
        "notes": get_value_by_alias(
            raw_row,
            ["Notes", "Note"]
        ),
    }


def get_union_keys(rows):
    keys = []
    seen = set()
    preferred = [
        "S",
        "Submission Date",
        "Submission #",
        "Customer Name",
        "Contact Info",
        "# Of Cards",
        "Service Type",
        "Est Cost",
        "Prep Needed",
        "Customer Paid",
        "Current Status",
        "Declared Value",
        "Decalared Value",
        "Notes",
    ]

    # add preferred keys first if they exist in any row
    for pref in preferred:
        for row in rows:
            if isinstance(row, dict) and pref in row and pref not in seen:
                keys.append(pref)
                seen.add(pref)
                break

    # then add everything else
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in row.keys():
            if key not in seen:
                keys.append(key)
                seen.add(key)

    return keys


# ---------- HOME ----------
@app.route("/")
def home():
    return """
    <h2>PSA Staff System</h2>
    <a href="/upload">Upload File</a><br><br>
    <a href="/dashboard">Dashboard</a><br><br>
    <a href="/staff">Staff Search</a><br><br>
    <a href="/track">Customer Tracker</a>
    """


# ---------- UPLOAD ----------
@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        file = request.files.get("file")
        if not file:
            return "No file uploaded"

        try:
            df = read_any(file)
        except Exception as e:
            return f"FILE READ ERROR: {e}"

        df.columns = [str(c).strip() for c in df.columns]

        conn = get_conn()
        cur = conn.cursor()

        inserted = 0
        updated = 0
        errors = 0

        for _, row in df.iterrows():
            try:
                raw = {str(c).strip(): clean(row[c]) for c in df.columns}
                d = extract_row(raw)

                if not d["submission_number"]:
                    errors += 1
                    continue

                cur.execute(
                    "SELECT id FROM submissions WHERE submission_number = %s LIMIT 1",
                    (d["submission_number"],)
                )
                existing = cur.fetchone()

                if existing:
                    cur.execute("""
                        UPDATE submissions
                        SET
                            submission_date = %s,
                            customer_name = %s,
                            contact_info = %s,
                            card_count = %s,
                            service_type = %s,
                            est_cost = %s,
                            prep_needed = %s,
                            customer_paid = %s,
                            status = %s,
                            declared_value = %s,
                            notes = %s,
                            raw_data = %s,
                            last_updated = NOW()
                        WHERE submission_number = %s
                    """, (
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
                        d["notes"],
                        json.dumps(raw),
                        d["submission_number"],
                    ))
                    updated += 1
                else:
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
                            notes,
                            raw_data
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                        d["notes"],
                        json.dumps(raw),
                    ))
                    inserted += 1

            except Exception as e:
                print("ROW ERROR:", e)
                errors += 1

        conn.commit()
        cur.close()
        conn.close()

        return f"""
        <h3>Upload Results</h3>
        Inserted: {inserted}<br>
        Updated: {updated}<br>
        Errors: {errors}<br><br>
        <a href="/dashboard">Dashboard</a><br>
        <a href="/staff">Staff Search</a>
        """

    return """
    <h3>Upload CSV or Excel</h3>
    <form method="post" enctype="multipart/form-data">
        <input type="file" name="file">
        <button type="submit">Upload</button>
    </form>
    <br><a href="/">Back</a>
    """


# ---------- DASHBOARD ----------
@app.route("/dashboard")
def dashboard():
    sort = request.args.get("sort", "last_updated")
    status_filter = clean(request.args.get("status", ""))

    allowed_sort = {
        "last_updated": "last_updated",
        "submission_number": "submission_number",
        "customer_name": "customer_name",
        "status": "status",
        "service_type": "service_type",
        "submission_date": "submission_date",
    }
    sort_sql = allowed_sort.get(sort, "last_updated")

    conn = get_conn()
    cur = conn.cursor()

    if status_filter:
        cur.execute(f"""
            SELECT id, submission_number, customer_name, contact_info, status, service_type, submission_date, raw_data
            FROM submissions
            WHERE status = %s
            ORDER BY {sort_sql} DESC NULLS LAST
            LIMIT 500
        """, (status_filter,))
    else:
        cur.execute(f"""
            SELECT id, submission_number, customer_name, contact_info, status, service_type, submission_date, raw_data
            FROM submissions
            ORDER BY {sort_sql} DESC NULLS LAST
            LIMIT 500
        """)

    rows = cur.fetchall()
    raw_rows = [r[7] if isinstance(r[7], dict) else {} for r in rows]
    keys = get_union_keys(raw_rows)

    html = """
    <h2>Dashboard</h2>
    <form method="get">
        Sort:
        <select name="sort">
            <option value="last_updated">Last Updated</option>
            <option value="submission_number">Submission</option>
            <option value="customer_name">Name</option>
            <option value="status">Status</option>
            <option value="service_type">Service</option>
            <option value="submission_date">Submission Date</option>
        </select>
        Status:
        <input name="status" placeholder="Filter exact status">
        <button type="submit">Apply</button>
    </form>
    <br>
    <table border="1" cellpadding="5" cellspacing="0">
        <tr>
            <th>Edit</th>
    """
    html += "".join(f"<th>{k}</th>" for k in keys)
    html += "</tr>"

    for row in rows:
        row_id = row[0]
        raw = row[7] if isinstance(row[7], dict) else {}
        html += f"<tr><td><a href='/edit/{row_id}'>Edit</a></td>"
        html += "".join(f"<td>{raw.get(k, '')}</td>" for k in keys)
        html += "</tr>"

    html += "</table><br><a href='/'>Back</a>"
    cur.close()
    conn.close()
    return html


# ---------- EDIT ----------
@app.route("/edit/<int:row_id>", methods=["GET", "POST"])
def edit(row_id):
    conn = get_conn()
    cur = conn.cursor()

    if request.method == "POST":
        status = clean(request.form.get("status", ""))
        cur.execute("""
            UPDATE submissions
            SET status = %s, last_updated = NOW()
            WHERE id = %s
        """, (status, row_id))
        conn.commit()
        cur.close()
        conn.close()
        return redirect("/dashboard")

    cur.execute("""
        SELECT submission_number, status, customer_name
        FROM submissions
        WHERE id = %s
    """, (row_id,))
    row = cur.fetchone()

    cur.close()
    conn.close()

    if not row:
        return "Row not found"

    return f"""
    <h3>Edit Submission {row[0]}</h3>
    <p>Name: {row[2]}</p>
    <form method="post">
        Status: <input name="status" value="{row[1] or ''}">
        <button type="submit">Save</button>
    </form>
    <br><a href="/dashboard">Back</a>
    """


# ---------- STAFF SEARCH ----------
@app.route("/staff", methods=["GET", "POST"])
def staff():
    results = []

    if request.method == "POST":
        q = clean(request.form.get("q", ""))

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT submission_number, customer_name, contact_info, status, service_type, submission_date
            FROM submissions
            WHERE
                LOWER(COALESCE(submission_number, '')) LIKE LOWER(%s)
                OR LOWER(COALESCE(customer_name, '')) LIKE LOWER(%s)
                OR LOWER(COALESCE(contact_info, '')) LIKE LOWER(%s)
                OR LOWER(COALESCE(status, '')) LIKE LOWER(%s)
                OR LOWER(COALESCE(service_type, '')) LIKE LOWER(%s)
            ORDER BY last_updated DESC
            LIMIT 200
        """, (
            f"%{q}%",
            f"%{q}%",
            f"%{q}%",
            f"%{q}%",
            f"%{q}%",
        ))
        results = cur.fetchall()
        cur.close()
        conn.close()

    html = """
    <h3>Staff Search</h3>
    <form method="post">
        <input name="q" placeholder="Search by submission, name, phone, email, status, service">
        <button type="submit">Search</button>
    </form>
    <br>
    <table border="1" cellpadding="5" cellspacing="0">
        <tr>
            <th>Submission</th>
            <th>Date</th>
            <th>Name</th>
            <th>Contact</th>
            <th>Status</th>
            <th>Service</th>
        </tr>
    """

    for r in results:
        html += (
            f"<tr>"
            f"<td>{r[0] or ''}</td>"
            f"<td>{r[5] or ''}</td>"
            f"<td>{r[1] or ''}</td>"
            f"<td>{r[2] or ''}</td>"
            f"<td>{r[3] or ''}</td>"
            f"<td>{r[4] or ''}</td>"
            f"</tr>"
        )

    html += "</table><br><a href='/'>Back</a>"
    return html


# ---------- CUSTOMER TRACK ----------
@app.route("/track", methods=["GET", "POST"])
def track():
    result = None

    if request.method == "POST":
        sub = clean(request.form.get("sub", ""))

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT submission_number, status, customer_name, submission_date, service_type
            FROM submissions
            WHERE submission_number = %s
            LIMIT 1
        """, (sub,))
        result = cur.fetchone()
        cur.close()
        conn.close()

    html = """
    <h3>Track Submission</h3>
    <form method="post">
        <input name="sub" placeholder="Enter submission number">
        <button type="submit">Check</button>
    </form>
    """

    if result:
        html += f"""
        <br>
        <p><b>Submission:</b> {result[0] or ''}</p>
        <p><b>Name:</b> {result[2] or ''}</p>
        <p><b>Date:</b> {result[3] or ''}</p>
        <p><b>Service:</b> {result[4] or ''}</p>
        <p><b>Status:</b> {result[1] or ''}</p>
        """

    html += "<br><a href='/'>Back</a>"
    return html


if __name__ == "__main__":
    app.run()
