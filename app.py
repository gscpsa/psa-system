from flask import Flask, request, redirect, session, render_template_string
import psycopg2
import os
import pandas as pd
import json

app = Flask(__name__)
app.secret_key = "secret123"

# -------------------------
# DATABASE
# -------------------------
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
    CREATE TABLE IF NOT EXISTS raw_uploads (
        id SERIAL PRIMARY KEY,
        data JSONB,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

<p><a href="/debug">View Raw Data</a></p>
"""

DEBUG_HTML = """
<h2>Raw Uploaded Data</h2>
{% for row in rows %}
<div style="margin-bottom:20px; border-bottom:1px solid #ccc;">
<pre>{{ row }}</pre>
</div>
{% endfor %}
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
    return render_template_string(DASHBOARD_HTML)

# -------------------------
# UPLOAD (NO ASSUMPTIONS)
# -------------------------

@app.route("/upload-csv", methods=["POST"])
def upload_csv():
    if not session.get("admin"):
        return "Unauthorized", 403

    file = request.files.get("file")
    if not file:
        return "No file", 400

    # Read ANY Excel format
    df = pd.read_excel(file)

    conn = get_db()
    c = conn.cursor()

    count = 0

    for _, row in df.iterrows():
        row_dict = {}

        # Convert row safely to JSON-safe dict
        for col in df.columns:
            val = row[col]
            if pd.isna(val):
                row_dict[col] = None
            else:
                row_dict[col] = str(val)

        c.execute("""
        INSERT INTO raw_uploads (data)
        VALUES (%s)
        """, (json.dumps(row_dict),))

        count += 1

    conn.commit()
    conn.close()

    return f"Uploaded {count} rows successfully"

# -------------------------
# DEBUG VIEW (SEE REAL DATA)
# -------------------------

@app.route("/debug")
def debug():
    if not session.get("admin"):
        return redirect("/admin")

    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT data FROM raw_uploads ORDER BY id DESC LIMIT 50")
    rows = c.fetchall()

    conn.close()

    formatted = [json.dumps(r[0], indent=2) for r in rows]

    return render_template_string(DEBUG_HTML, rows=formatted)

# -------------------------
# RUN
# -------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
