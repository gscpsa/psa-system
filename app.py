from flask import Flask, request
import pandas as pd
import psycopg2
import os

app = Flask(__name__)
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL)

# ---------- HOME ----------
@app.route("/")
def home():
    return """
    <h2>Upload CSV</h2>
    <form action="/upload" method="post" enctype="multipart/form-data">
        <input type="file" name="file">
        <button type="submit">Submit</button>
    </form>
    <br>
    <a href="/dashboard">View Dashboard</a>
    """

# ---------- UPLOAD ----------
@app.route("/upload", methods=["POST"])
def upload():
    file = request.files["file"]

    # Try CSV first; if it fails, try Excel
    try:
        df = pd.read_csv(file, encoding="latin1", engine="python", on_bad_lines="skip")
        source = "csv"
    except Exception:
        file.stream.seek(0)
        df = pd.read_excel(file)
        source = "excel"

    # Normalize headers aggressively
    df.columns = (
        df.columns
        .astype(str)
        .str.strip()
        .str.replace("\n", " ", regex=False)
        .str.replace("\r", " ", regex=False)
    )

    # DEBUG: show exactly what we got
    print("SOURCE:", source)
    print("CSV HEADERS:", df.columns.tolist())
    print("SHAPE:", df.shape)

    # Auto-detect columns by keywords (robust)
    def find_col(keywords):
        kws = [k.lower() for k in keywords]
        for col in df.columns:
            c = col.lower()
            if any(k in c for k in kws):
                return col
        return None

    col_submission = find_col(["submission"])
    col_name       = find_col(["customer name","name"])
    col_contact    = find_col(["contact","phone","email"])
    col_cards      = find_col(["# of cards","cards","card count"])
    col_service    = find_col(["service"])
    col_status     = find_col(["current status","status"])

    print("MAPPING:",
          col_submission, col_name, col_contact,
          col_cards, col_service, col_status)

    # If we can’t find a submission column, force insert anyway (to prove pipeline)
    conn = get_conn()
    cur = conn.cursor()

    inserted = 0

    for _, row in df.iterrows():
        try:
            submission = str(row.get(col_submission, "")).strip() if col_submission else ""
            name       = str(row.get(col_name, "")).strip() if col_name else ""
            contact    = str(row.get(col_contact, "")).strip() if col_contact else ""
            cards      = str(row.get(col_cards, "")).strip() if col_cards else ""
            service    = str(row.get(col_service, "")).strip() if col_service else ""
            status     = str(row.get(col_status, "")).strip() if col_status else ""

            # Do NOT skip unless truly empty row
            if not any([submission, name, contact, cards, service, status]):
                continue

            cur.execute("""
            INSERT INTO submissions (
                submission_number,
                customer_name,
                contact_info,
                card_count,
                service_type,
                status
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """, (submission, name, contact, cards, service, status))

            inserted += 1

        except Exception as e:
            print("ROW ERROR:", e)
            continue

    conn.commit()
    cur.close()
    conn.close()

    return f"<h2>Uploaded {inserted} rows successfully</h2><a href='/dashboard'>Go to dashboard</a>"

# ---------- DASHBOARD ----------
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
        last_updated
    FROM submissions
    ORDER BY last_updated DESC
    """)
    rows = cur.fetchall()

    html = "<h2>Dashboard</h2><table border=1>"
    html += "<tr><th>Submission</th><th>Name</th><th>Contact</th><th>Cards</th><th>Service</th><th>Status</th><th>Updated</th></tr>"

    for r in rows:
        html += "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"

    html += "</table><br><a href='/'>Upload More</a>"

    cur.close()
    conn.close()
    return html

# ---------- RUN ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
