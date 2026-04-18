from flask import Flask, request, redirect, session, render_template_string
import psycopg2
import os

app = Flask(__name__)
app.secret_key = "secret123"

def get_db():
    url = os.environ.get("DATABASE_URL")
    return psycopg2.connect(url, sslmode="require")

# -------------------------
# STATUS STAGES
# -------------------------
STAGES = ["Received", "Grading", "Assembly", "QA Check", "Shipped"]

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

<h3>Add PSA Order</h3>
<form method="POST">
    <input name="customer_name" placeholder="Customer Name" required>
    <input name="email" placeholder="Email" required>
    <input name="order_code" placeholder="PSA Order #" required>

    <select name="status">
        {% for s in stages %}
            <option value="{{s}}">{{s}}</option>
        {% endfor %}
    </select>

    <button>Add Order</button>
</form>

<h3>All Orders</h3>
<table border="1" cellpadding="5">
<tr>
<th>Order</th><th>Name</th><th>Status</th><th>Track</th>
</tr>

{% for o in orders %}
<tr>
<td>{{ o[1] }}</td>
<td>{{ o[2] }}</td>
<td>{{ o[4] }}</td>
<td><a href="/track/{{ o[1] }}">View</a></td>
</tr>
{% endfor %}
</table>
"""

TRACK_HTML = """
<h2>Order Tracking</h2>

{% if order %}
    <h3>Order #: {{ order[1] }}</h3>
    <p>Customer: {{ order[2] }}</p>

    <h3>Status Progress</h3>

    {% for s in stages %}
        {% if stages.index(s) < stages.index(order[4]) %}
            <div style="color:green;">✔ {{ s }}</div>
        {% elif s == order[4] %}
            <div style="color:orange;">➡ {{ s }}</div>
        {% else %}
            <div style="color:gray;">⬜ {{ s }}</div>
        {% endif %}
    {% endfor %}

{% else %}
    <p>Order not found</p>
{% endif %}
"""

# -------------------------
# DB INIT
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

    return render_template_string(DASHBOARD_HTML, orders=orders, stages=STAGES)

@app.route("/track/<order_code>")
def track(order_code):
    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT * FROM orders WHERE order_code=%s", (order_code,))
    order = c.fetchone()
    conn.close()

    return render_template_string(TRACK_HTML, order=order, stages=STAGES)

# -------------------------
# RUN
# -------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
