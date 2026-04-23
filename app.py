from flask import Flask, request, redirect, session, render_template_string
import psycopg2
import os
import pandas as pd

app = Flask(__name__)
app.secret_key = "secret123"

def get_db():
    url = os.environ.get("DATABASE_URL")
    return psycopg2.connect(url, sslmode="require")

# -------------------------
# INIT DB
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
# HTML
# -------------------------

ADMIN_HTML = """
<h2>Admin Login</h2>
<form method="POST">
    <input type="password" name="password" placeholder="Password">
    <button>Login</button>
</form>
"""

DASHBOARD_HTML = """
<h2>Admin Dashboard</h2>

<h3>Upload Spreadsheet</h3>
<form method="POST" action="/upload-csv" enctype="multipart/form-data">
    <input type="file" name="file" required>
    <button>Upload</button>
</form>

<h3>Submissions</h3>
<table border="1" cellpadding="5">
<tr>
<th>Submission</th>
<th>Name</th>
<th>Status</th>
<th>Cards</th>
<th>Date</th>
</tr>

{% for o in orders %}
<tr>
<td>{{ o[0] }}</td>
<td>{{ o[1] }}</td>
<td>{{ o[5] }}</td>
<td>{{ o[4] }}</td>
<td>{{ o[10] }}</td>
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

    c.execute("SELECT * FROM submissions ORDER BY last_updated DESC")
    orders = c.fetchall()
    conn.close()

    return render_template_string(DASHBOARD_HTML, orders=orders)

# -------------------------
# UPLOAD (REAL MAPPING)
# -------------------------

@app.route("/upload-csv", methods=["POST"])
def upload_csv():
    if not session.get("admin"):
        return "Unauthorized", 403

    file = request.files.get("file")
    if not file:
        return "No file", 400

    df = pd.read_excel(file)

    # remove junk columns
    df = df.loc[:, ~df.columns.str.contains("^Unnamed", na=False)]

    conn = get_db()
    c = conn.cursor()

    count = 0

    for _, row in df.iterrows():
        try:
            submission = str(row.get("Submission #", "")).strip()
            if not submission:
                continue

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
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (submission_number)
            DO UPDATE SET
                status = EXCLUDED.status,
                card_count = EXCLUDED.card_count,
                last_updated = CURRENT_TIMESTAMP
            """, (
                submission,
                str(row.get("Customer Name", "")),
                str(row.get("Contact Info", "")),
                str(row.get("Service Type", "")),
                int(row.get("# Of Cards", 0)) if str(row.get("# Of Cards", "")).isdigit() else 0,
                str(row.get("Current Status", "")),
                str(row.get("Est Cost", "")),
                str(row.get("Prep Needed", "")),
                str(row.get("Customer Paid", "")),
                str(row.get("Declared Value", "")),
                str(row.get("s", "")),
            ))

            count += 1

        except:
            continue

    conn.commit()
    conn.close()

    return f"Uploaded {count} structured rows"

# -------------------------
# RUN
# -------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
