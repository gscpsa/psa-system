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
# STATUS STAGES
# -------------------------
STAGES = ["Received", "Research & ID", "Grading", "Assembly", "QA Check", "Shipped"]

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

<h3>All Submissions</h3>
<table border="1" cellpadding="5">
<tr>
<th>Submission</th><th>Name</th><th>Status</th><th>Track</th>
</tr>

{% for o in orders %}
<tr>
<td>{{ o[0] }}</td>
<td>{{ o[1] }}</td>
<td>{{ o[5] }}</td>
<td><a href="/track/{{ o[0] }}">View</a></td>
</tr>
{% endfor %}
</table>
"""

TRACK_HTML = """
<h2>Submission Tracking</h2>

{% if order %}
    <h3>Submission #: {{ order[0] }}</h3>
    <p>Customer: {{ order[1] }}</p>

    <h3>Status Progress</h3>

    {% for s in stages %}
        {% if stages.index(s) < stages.index(order[5]) %}
            <div style="color:green;">✔ {{ s }}</div>
        {% elif s == order[5] %}
            <div style="color:orange;">➡ {{ s }}</div>
        {% else %}
            <div style="color:gray;">⬜ {{ s }}</div>
        {% endif %}
    {% endfor %}

{% else %}
    <p>Submission not found</p>
{% endif %}
"""

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
        card_count INT,
        service_type TEXT,
        status TEXT,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()

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

@app.route("/dashboard", methods=["GET"])
def dashboard():
    if not session.get("admin"):
        return redirect("/admin")

    init_db()

    conn = get_db()
    c = conn.cursor()

    c.execute("""
        SELECT submission_number, customer_name, contact_info, card_count, service_type, status
        FROM submissions
        ORDER BY last_updated DESC
    """)
    orders = c.fetchall()
    conn.close()

    return render_template_string(DASHBOARD_HTML, orders=orders)

# -------------------------
# CSV UPLOAD
# -------------------------

@app.route("/upload-csv", methods=["POST"])
def upload_csv():
    if not session.get("admin"):
        return "Unauthorized", 403

    file = request.files.get("file")
    if not file:
        return "No file", 400

    df = pd.read_excel(file)

    conn = get_db()
    c = conn.cursor()

    count = 0

    for _, row in df.iterrows():
        submission = str(row.get("Submission #", "")).strip()
        if not submission:
            continue

        c.execute("""
        INSERT INTO submissions
        (submission_number, customer_name, contact_info, card_count, service_type, status)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (submission_number)
        DO UPDATE SET
            customer_name = EXCLUDED.customer_name,
            contact_info = EXCLUDED.contact_info,
            card_count = EXCLUDED.card_count,
            service_type = EXCLUDED.service_type,
            status = EXCLUDED.status,
            last_updated = CURRENT_TIMESTAMP
        """, (
            submission,
            row.get("Customer Name", ""),
            row.get("Contact Info", ""),
            int(row.get("# Of Cards", 0)),
            row.get("Service Type", ""),
            row.get("Current Status", "")
        ))

        count += 1

    conn.commit()
    conn.close()

    return f"Uploaded {count} records"

# -------------------------
# TRACK PAGE
# -------------------------

@app.route("/track/<submission_number>")
def track(submission_number):
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        SELECT submission_number, customer_name, contact_info, card_count, service_type, status
        FROM submissions
        WHERE submission_number=%s
    """, (submission_number,))

    order = c.fetchone()
    conn.close()

    return render_template_string(TRACK_HTML, order=order, stages=STAGES)

# -------------------------
# RUN
# -------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
