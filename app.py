from flask import Flask, render_template, request, redirect, session
import psycopg2
import os

app = Flask(__name__)
app.secret_key = "secret123"

def get_db():
    url = os.environ.get("DATABASE_URL")
    return psycopg2.connect(url, sslmode="require")

def init_db():
    conn = get_db()
    c = conn.cursor()

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
        grade TEXT,
        status TEXT
    )
    """)

    conn.commit()
    conn.close()

@app.route("/", methods=["GET","POST"])
def index():
    if request.method == "POST":
        code = request.form["order_code"]
        email = request.form["email"]

        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM orders WHERE order_code=%s AND email=%s", (code,email))
        order = c.fetchone()

        if order:
            return redirect(f"/status/{code}")
        else:
            return "Order not found"

    return render_template("index.html")

@app.route("/status/<code>")
def status(code):
    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT * FROM orders WHERE order_code=%s", (code,))
    order = c.fetchone()

    c.execute("SELECT name, grade, status FROM cards WHERE order_code=%s", (code,))
    cards = c.fetchall()

    return render_template("status.html", order=order, cards=cards)

@app.route("/admin", methods=["GET","POST"])
def admin():
    if request.method == "POST":
        if request.form["password"] == "shopadmin":
            session["admin"] = True
            return redirect("/dashboard")

    return render_template("admin.html")

@app.route("/dashboard", methods=["GET","POST"])
def dashboard():
    if not session.get("admin"):
        return redirect("/admin")

    conn = get_db()
    c = conn.cursor()

    if request.method == "POST":
        code = request.form["order_code"]
        status = request.form["status"]

        c.execute("UPDATE orders SET status=%s WHERE order_code=%s", (status,code))
        c.execute("UPDATE cards SET status=%s WHERE order_code=%s", (status,code))
        conn.commit()

    c.execute("SELECT * FROM orders")
    orders = c.fetchall()

    return render_template("dashboard.html", orders=orders)

if __name__ == "__main__":
    try:
        init_db()
    except Exception as e:
        print("DB init failed:", e)

    port = int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0", port=port)
