from flask import Flask, render_template_string, request, redirect, session
import psycopg2
import os

app = Flask(__name__)
app.secret_key = "secret123"


def get_db():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise Exception("DATABASE_URL is missing")
    return psycopg2.connect(url, sslmode="require")


# 🔥 SIMPLE BUILT-IN PAGES (NO TEMPLATE FILES NEEDED)

ADMIN_HTML = """
<h2>Admin Login</h2>
<form method="POST">
    <input type="password" name="password" placeholder="Password">
    <button type="submit">Login</button>
</form>
"""

DASHBOARD_HTML = """
<h2>Dashboard</h2>

<form method="POST">
    <input name="order_code" placeholder="Order Code">
    <input name="status" placeholder="Status">
    <button type="submit">Update</button>
</form>

<h3>Orders</h3>
<ul>
{% for o in orders %}
    <li>{{ o }}</li>
{% endfor %}
</ul>
"""


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        if request.form.get("password") == "shopadmin":
            session["admin"] = True
            return redirect("/dashboard")
    return render_template_string(ADMIN_HTML)


@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if not session.get("admin"):
        return redirect("/admin")

    conn = get_db()
    c = conn.cursor()

    # create tables if missing
    c.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id SERIAL PRIMARY KEY,
        order_code TEXT,
        email TEXT,
        status TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS cards (
        id SERIAL PRIMARY KEY,
        order_code TEXT,
        name TEXT,
        status TEXT
    )
    """)

    conn.commit()

    # update
    if request.method == "POST":
        code = request.form.get("order_code")
        status = request.form.get("status")

        if code and status:
            c.execute("UPDATE orders SET status=%s WHERE order_code=%s", (status, code))
            c.execute("UPDATE cards SET status=%s WHERE order_code=%s", (status, code))
            conn.commit()

    # fetch
    c.execute("SELECT * FROM orders")
    orders = c.fetchall()

    conn.close()

    return render_template_string(DASHBOARD_HTML, orders=orders)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
