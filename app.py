from flask import Flask, request, redirect, session, render_template_string
import psycopg2
import os
import pandas as pd

app = Flask(__name__)
app.secret_key = "secret123"

# -------------------------
# DATABASE
# -------------------------
def get_db():
    url = os.environ.get("DATABASE_URL")
    return psycopg2.connect(url, sslmode="require")


# -------------------------
# DB INIT
# -------------------------
def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS submissions (
        submission_number TEXT PRIMARY KEY,
        customer_name TEXT,
        contact_info TEXT,
        service_type TEXT,
        card_count INT,
        status TEXT,
        est_cost TEXT,
        prep_needed TEXT,
        customer_paid TEXT,
        declared_value TEXT,
        submission_date TEXT,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()


# -------------------------
# HELPERS
# -------------------------
def safe_text(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def safe_int(value):
    txt = safe_text(value)
    if not txt:
        return 0
    try:
        return int(float(txt))
    except Exception:
        return 0


# -------------------------
# HTML
# -------------------------
ADMIN_HTML = """
<h2>Admin Login</h2>
<form method="POST">
    <input type="password" name="password" placeholder="Password">
    <button type="submit">Login</button>
</form>
"""

DASHBOARD_HTML = """
<h2>Admin Dashboard</h2>

<h3>Upload Spreadsheet</h3>
<form method="POST" action="/upload-csv" enctype="multipart/form-data">
    <input type="file" name="file" required>
    <button type="submit">Upload</button>
</form>

<p><a href="/debug">View Raw Table</a></p>

<h3>Submissions</h3>
<table border="1" cellpadding="5" cellspacing="0">
    <tr>
        <th>Submission #</th>
        <th>Customer Name</th>
        <th>Status</th>
        <th># Of Cards</th>
        <th>Service Type</th>
        <th>Submission Date</th>
    </tr>
    {% for o in orders %}
    <tr>
        <td>{{ o[0] }}</td>
        <td>{{ o[1] }}</td>
        <td>{{ o[5] }}</td>
        <td>{{ o[4] }}</td>
        <td>{{ o[3] }}</td>
        <td>{{ o[10] }}</td>
    </tr>
    {% endfor %}
</table>
"""

DEBUG_HTML = """
<h2>Raw Rows From submissions Table</h2>

<table border="1" cellpadding="5" cellspacing="0">
    <tr>
        <th>Submission #</th>
        <th>Customer Name</th>
        <th>Contact Info</th>
        <th>Service Type</th>
        <th># Of Cards</th>
        <th>Status</th>
        <th>Est Cost</th>
        <th>Prep Needed</th>
        <th>Customer Paid</th>
        <th>Declared Value</th>
        <th>Submission Date</th>
    </tr>
    {% for r in rows %}
    <tr>
        <td>{{ r[0] }}</td>
        <td>{{ r[1] }}</td>
        <td>{{ r[2] }}</td>
        <td>{{ r[3] }}</td>
        <td>{{ r[4] }}</td>
        <td>{{ r[5] }}</td>
        <td>{{ r[6] }}</td>
        <td>{{ r[7] }}</td>
        <td>{{ r[8] }}</td>
        <td>{{ r[9] }}</td>
        <td>{{ r[10] }}</td>
    </tr>
    {% endfor %}
</table>
"""

# -------------------------
# ROUTES
# -------------------------
@app.route("/")
def home():
    return redirect("/admin")


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        if request.form.get("password") == "shopadmin":
            session["admin"] = True
            return redirect("/dashboard")
    return render_template_string(ADMIN_HTML)


@app.route("/dashboard")
def dashboard():
    if not session.get("admin"):
        return redirect("/admin")

    init_db()

    conn = get_db()
    c = conn.cursor()

    c.execute("""
        SELECT
            submission_number,
            customer_name,
            contact_info,
            service_type,
            card_count,
            status,
            est_cost,
            prep_needed,
            customer_paid,
            declared_value,
            submission_date
        FROM submissions
        ORDER BY submission_date DESC NULLS LAST, last_updated DESC
    """)
    orders = c.fetchall()
    conn.close()

    return render_template_string(DASHBOARD_HTML, orders=orders)


@app.route("/upload-csv", methods=["POST"])
def upload_csv():
    if not session.get("admin"):
        return "Unauthorized", 403

    file = request.files.get("file")
    if not file:
        return "No file uploaded", 400

    init_db()

    # Read spreadsheet
    df = pd.read_excel(file)

    # Clean header names
    df.columns = [str(col).strip() for col in df.columns]

    # Drop unnamed junk columns from Excel
    df = df.loc[:, ~df.columns.str.contains("^Unnamed", na=False)]

    conn = get_db()
    c = conn.cursor()

    count = 0

    for _, row in df.iterrows():
        row_dict = {str(k).strip(): row[k] for k in df.columns}

        submission_number = safe_text(row_dict.get("Submission #"))
        if not submission_number:
            continue

        customer_name = safe_text(row_dict.get("Customer Name"))
        contact_info = safe_text(row_dict.get("Contact Info"))
        service_type = safe_text(row_dict.get("Service Type"))
        card_count = safe_int(row_dict.get("# Of Cards"))
        status = safe_text(row_dict.get("Current Status"))
        est_cost = safe_text(row_dict.get("Est Cost"))
        prep_needed = safe_text(row_dict.get("Prep Needed"))
        customer_paid = safe_text(row_dict.get("Customer Paid"))
        declared_value = safe_text(row_dict.get("Declared Value"))
        submission_date = safe_text(row_dict.get("s"))

        c.execute("""
            INSERT INTO submissions (
                submission_number,
                customer_name,
                contact_info,
                service_type,
                card_count,
                status,
                est_cost,
                prep_needed,
                customer_paid,
                declared_value,
                submission_date
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (submission_number)
            DO UPDATE SET
                customer_name = EXCLUDED.customer_name,
                contact_info = EXCLUDED.contact_info,
                service_type = EXCLUDED.service_type,
                card_count = EXCLUDED.card_count,
                status = EXCLUDED.status,
                est_cost = EXCLUDED.est_cost,
                prep_needed = EXCLUDED.prep_needed,
                customer_paid = EXCLUDED.customer_paid,
                declared_value = EXCLUDED.declared_value,
                submission_date = EXCLUDED.submission_date,
                last_updated = CURRENT_TIMESTAMP
        """, (
            submission_number,
            customer_name,
            contact_info,
            service_type,
            card_count,
            status,
            est_cost,
            prep_needed,
            customer_paid,
            declared_value,
            submission_date
        ))

        count += 1

    conn.commit()
    conn.close()

    return f"Uploaded {count} structured rows"


@app.route("/debug")
def debug():
    if not session.get("admin"):
        return redirect("/admin")

    init_db()

    conn = get_db()
    c = conn.cursor()

    c.execute("""
        SELECT
            submission_number,
            customer_name,
            contact_info,
            service_type,
            card_count,
            status,
            est_cost,
            prep_needed,
            customer_paid,
            declared_value,
            submission_date
        FROM submissions
        ORDER BY submission_date DESC NULLS LAST, last_updated DESC
        LIMIT 100
    """)
    rows = c.fetchall()
    conn.close()

    return render_template_string(DEBUG_HTML, rows=rows)


# -------------------------
# RUN
# -------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
