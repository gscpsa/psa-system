from flask import Flask, request, redirect

app = Flask(__name__)

# =========================
# EXISTING FUNCTIONS / LOGIC
# (unchanged)
# =========================

def build_table(rows):
    keys = []
    clean_rows = []
    force_keys = ["Arrived / Completed"]

    for r in rows:
        data = r[0] or {}
        row = {}

        for k in ordered_display_keys(data):
            v = data.get(k)
            key_text = str(k).strip()

            if "unnamed" in key_text.lower():
                continue

            if should_hide_column(key_text):
                continue

            display_key = display_column_label(key_text)
            row[display_key] = v

            if display_key not in keys:
                keys.append(display_key)

        row["Status"] = r[1] or row.get("Status") or "Submitted"

        if "Status" not in keys:
            keys.append("Status")

        clean_rows.append(row)

    for forced_key in force_keys:
        if forced_key not in keys:
            keys.append(forced_key)

    if not clean_rows:
        return "<div class='card'>No records found.</div>"

    html = "<table><tr>"
    for k in keys:
        html += f"<th>{display_column_label(k)}</th>"
    html += "</tr>"

    for row in clean_rows:
        html += "<tr>"
        for k in keys:
            val = row.get(k, "")
            col_class = "notes-col" if "note" in str(k).lower() else ""

            # ✅ FIXED BLOCK (THIS WAS CRASHING)
            if "submission" in str(k).lower():
                sub_id = str(val or "").strip()
                html += f"<td class='{col_class}'><a href='/admin/submission/{sub_id}'>{val}</a></td>"
            elif k == "Status":
                html += f"<td class='status {col_class}'>{val}</td>"
            else:
                html += f"<td class='{col_class}'>{val}</td>"

        html += "</tr>"

    html += "</table>"
    return html


# =========================
# NEW ROUTE (UNCHANGED FROM BEFORE)
# =========================

@app.route("/admin/submission/<sub_id>")
@admin_required
def admin_submission(sub_id):

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT raw_data, status
        FROM submissions
        WHERE submission_number = %s
    """, (sub_id,))

    row = cur.fetchone()

    cur.close()
    conn.close()

    if not row:
        return page("<div class='card'>Submission not found.</div>")

    data = row[0] or {}
    status = row[1] or "Submitted"

    html = f"""
    <div class="portal-landing">
        <div class="portal-panel">

            <h2>Submission #{sub_id}</h2>
            <div class="portal-divider"></div>

            <p><b>Status:</b> {status}</p>
            <br>

            <table>
    """

    for k in ordered_display_keys(data):
        v = data.get(k, "")
        html += f"<tr><td><b>{k}</b></td><td>{v}</td></tr>"

    html += """
            </table>

            <br>
            <a class="btn" href="/admin">Back to Dashboard</a>

        </div>
    </div>
    """

    return page(html)


# =========================
# EXISTING APP RUN
# =========================

if __name__ == "__main__":
    app.run(debug=True)
