# --- SAME IMPORTS / DB / HELPERS AS BEFORE ---
# (unchanged — do not modify those sections)

# =========================
# STATUS BAR (RESTORED)
# =========================
def status_bar(status):
    steps = ["Submitted","Order Arrived","Research & ID","Grading","QA Checks","Complete","Picked Up"]
    status = status or "Submitted"

    idx = steps.index(status) if status in steps else 0

    html = "<div style='display:flex;gap:6px;margin-top:10px;flex-wrap:wrap;'>"
    for i,s in enumerate(steps):
        style = "padding:6px 10px;border-radius:20px;background:#e5e7eb;font-size:12px;"
        if i < idx:
            style = "padding:6px 10px;border-radius:20px;background:#bfdbfe;font-weight:bold;"
        if i == idx:
            style = "padding:6px 10px;border-radius:20px;background:#2563eb;color:white;font-weight:bold;"
        html += f"<div style='{style}'>{s}</div>"
    html += "</div>"
    return html

# =========================
# CUSTOMER PORTAL (FIXED FORM)
# =========================
@app.route("/portal", methods=["GET","POST"])
def portal():
    if request.method=="POST":
        session["phone"]=normalize_phone(request.form.get("phone"))
        session["last"]=request.form.get("last","").lower().strip()
        return redirect("/portal/orders")

    return page("""
    <div style="background:white;padding:20px;border-radius:10px;max-width:400px">
        <h2>Customer Portal</h2>
        <p>Enter your phone number and last name</p>

        <form method="post">
            <input name="phone" placeholder="Phone Number" style="width:100%;margin-bottom:10px">
            <input name="last" placeholder="Last Name" style="width:100%;margin-bottom:10px">
            <button style="width:100%">View Orders</button>
        </form>
    </div>
    """)

# =========================
# CUSTOMER ORDERS (FULL DETAILS)
# =========================
@app.route("/portal/orders")
def orders():
    phone = normalize_phone(session.get("phone"))
    last = (session.get("last") or "").lower()

    if not phone or not last:
        return redirect("/portal")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT raw_data, status FROM submissions")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    html = "<h2>Your Orders</h2>"

    found = False

    for r in rows:
        data = r[0] or {}
        status = r[1] or "Submitted"

        name = str(get_field(data,["Customer Name","Name"])).lower()
        contact = normalize_phone(get_field(data,["Phone","Contact Info","Phone Number"]))
        sub = get_field(data,["Submission #","Submission Number"])

        if (phone in contact or contact in phone) and last in name:
            found = True

            service = get_field(data,["Service Type","Service"])
            cards = get_field(data,["# Of Cards","# of Cards","Cards"])
            date = get_field(data,["S","Submission Date","Date"])

            html += f"""
            <div style="background:white;padding:18px;margin-bottom:15px;border-radius:10px">
                <h3>Submission #{sub}</h3>

                <p><b>Status:</b> <span class="status">{status}</span></p>
                <p><b>Service:</b> {service}</p>
                <p><b>Cards:</b> {cards}</p>
                <p><b>Submission Date:</b> {date}</p>

                {status_bar(status)}
            </div>
            """

    if not found:
        html += "<div>No matching orders found</div>"

    return page(html)
