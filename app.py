from flask import Flask, request, redirect
import pandas as pd

app = Flask(__name__)

# =========================
# STATUS ORDER (FIXED)
# =========================
STATUS_ORDER = [
    "Received",
    "Processing",
    "Assembly",
    "Q & A",
    "Completed"
]

# =========================
# HELPERS (SAFE)
# =========================
def ordered_display_keys(data):
    return list(data.keys())

def display_column_label(k):
    return k

def should_hide_column(k):
    return False

# =========================
# TABLE BUILDER (FIXED)
# =========================
def build_table(rows):
    keys = []
    clean_rows = []

    for r in rows:
        data = r[0] or {}
        row = {}

        for k in ordered_display_keys(data):
            v = data.get(k)

            if should_hide_column(k):
                continue

            row[k] = v
            if k not in keys:
                keys.append(k)

        row["Status"] = r[1] or "Submitted"
        if "Status" not in keys:
            keys.append("Status")

        clean_rows.append(row)

    html = "<table><tr>"
    for k in keys:
        html += f"<th>{k}</th>"
    html += "</tr>"

    for row in clean_rows:
        html += "<tr>"
        for k in keys:
            val = row.get(k, "")
            col_class = ""

            # FIXED CLICKABLE BLOCK (SAFE)
            if "submission" in str(k).lower():
                sub_id = str(val or "").strip()
                html += f"<td><a href='/admin/submission/{sub_id}'>{val}</a></td>"
            else:
                html += f"<td>{val}</td>"

        html += "</tr>"

    html += "</table>"
    return html

# =========================
# PORTAL
# =========================
@app.route("/portal", methods=["GET", "POST"])
def portal():
    if request.method == "POST":
        phone = request.form.get("phone")
        last = request.form.get("last")
        return redirect(f"/portal/orders?phone={phone}&last={last}")

    return """
    <h2>TRACK YOUR ORDER</h2>
    <form method="post">
        <input name="phone" placeholder="Phone">
        <input name="last" placeholder="Last Name">
        <button>Submit</button>
    </form>
    """

# =========================
# PORTAL RESULTS
# =========================
@app.route("/portal/orders")
def orders():
    phone = request.args.get("phone")
    last = request.args.get("last")

    return f"""
    <h2>Results</h2>
    Phone: {phone}<br>
    Last: {last}<br>
    """

# =========================
# ADMIN DASHBOARD
# =========================
@app.route("/admin")
def admin():
    rows = [
        ({"Submission #": "12345", "Customer Name": "John"}, "Assembly"),
        ({"Submission #": "67890", "Customer Name": "Mike"}, "Q & A"),
    ]

    table = build_table(rows)
    return f"<h2>Dashboard</h2>{table}"

# =========================
# CLICKABLE SUBMISSION PAGE
# =========================
@app.route("/admin/submission/<sub_id>")
def admin_submission(sub_id):

    data = {
        "Submission #": sub_id,
        "Customer Name": "Sample User",
        "Status": "Assembly"
    }

    html = "<h2>Submission Detail</h2><table>"

    for k, v in data.items():
        html += f"<tr><td><b>{k}</b></td><td>{v}</td></tr>"

    html += "</table><br><a href='/admin'>Back</a>"

    return html

# =========================
# RUN
# =========================
if __name__ == "__main__":
    app.run(debug=True)
