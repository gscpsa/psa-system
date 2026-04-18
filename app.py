from flask import Flask, request, redirect, session, render_template_string
import psycopg2
import os

app = Flask(__name__)
app.secret_key = "secret123"


def get_db():
    url = os.environ.get("DATABASE_URL")
    return psycopg2.connect(url, sslmode="require")


# -------------------------
# HTML TEMPLATES (INLINE)
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

<h3>Add PSA Order</h3>
<form method="POST">
    <input name="customer_name" placeholder="Customer Name" required>
    <input name="email" placeholder="Email" required>
    <input name="order_code" placeholder="PSA Order #" required>
    <input name="status" placeholder="Status (e.g. Received)" required>
    <button>Add Order</button>
</form>

<h3>All Orders</h3>
<ul>
{% for o in orders %}
    <li>
        {{ o[3] }} | {{ o[2] }} |
        <a href="/track/{{ o[1] }}">Track</a>
    </li>
{% endfor %}
</ul>
"""

TRACK_HTML = """
<h2>Order Tracking</h2>

{% if order %}
    <p><b>Order #:</b> {{ order[1] }}</p>
    <p><b>Status:</b> {{ order[4] }}</p>
    <p><b>Customer:</b> {{ order[2] }}</p>
{% else %}
    <p>Order not found</p>
{% endif %}
"""


# -------------------------
# INIT DB
# -------------------------

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id SERIAL PRIMARY KEY,
        order_code TEXT,
        customer_name TEXT,
        email TEXT,
        status TEXT
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


@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if not session.get("admin"):
        return redirect("/admin")

    init_db()

    conn = get_db()
    c = conn.cursor()

    if request.method == "POST":
        c.execute(
            "INSERT INTO orders (order_code, customer_name, email, status) VALUES (%s,%s,%s,%s)",
            (
                request.form["order_code"],
                request.form["customer_name"],
                request.form["email"],
                request.form["status"],
            ),
        )
        conn.commit()

    c.execute("SELECT * FROM orders ORDER BY id DESC")
    orders = c.fetchall()

    conn.close()

    return render_template_string(DASHBOARD_HTML, orders=orders)


@app.route("/track/<order_code>")
def track(order_code):
    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT * FROM orders WHERE order_code=%s", (order_code,))
    order = c.fetchone()

    conn.close()

    return render_template_string(TRACK_HTML, order=order)


# -------------------------
# RUN
# -------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
