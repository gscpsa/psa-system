from flask import Flask, render_template, request, redirect, session
import psycopg2
import os

app = Flask(__name__)
app.secret_key = "secret123"


def get_db():
    url = os.environ.get("DATABASE_URL")
    return psycopg2.connect(url, sslmode="require")


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        if request.form.get("password") == "shopadmin":
            session["admin"] = True
            return redirect("/dashboard")
    return render_template("admin.html")


@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if not session.get("admin"):
        return redirect("/admin")

    conn = get_db()
    c = conn.cursor()

    # 🔥 CREATE TABLES RIGHT HERE (guaranteed)
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

    # UPDATE
    if request.method == "POST":
        code = request.form["order_code"]
        status = request.form["status"]

        c.execute("UPDATE orders SET status=%s WHERE order_code=%s", (status, code))
        c.execute("UPDATE cards SET status=%s WHERE order_code=%s", (status, code))
        conn.commit()

    # FETCH
    c.execute("SELECT * FROM orders")
    orders = c.fetchall()

    conn.close()

    return render_template("dashboard.html", orders=orders)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
