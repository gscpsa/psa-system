from flask import Flask, render_template, request, redirect, session
import psycopg2
import os

app = Flask(__name__)
app.secret_key = "secret123"


def get_db():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise Exception("DATABASE_URL is missing")
    return psycopg2.connect(url, sslmode="require")


def safe_init_db():
    conn = None
    try:
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
            status TEXT
        )
        """)

        conn.commit()
    except Exception as e:
        print("SAFE INIT ERROR:", e)
    finally:
        if conn:
            conn.close()


@app.route("/")
def home():
    return redirect("/admin")


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == "shopadmin":
            session["admin"] = True
            return redirect("/dashboard")
        return "Wrong password"

    return render_template("admin.html")


@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if not session.get("admin"):
        return redirect("/admin")

    safe_init_db()

    conn = None
    orders = []

    try:
        conn = get_db()
        c = conn.cursor()

        if request.method == "POST":
            code = request.form.get("order_code", "").strip()
            status = request.form.get("status", "").strip()

            if code and status:
                c.execute(
                    "UPDATE orders SET status=%s WHERE order_code=%s",
                    (status, code)
                )
                c.execute(
                    "UPDATE cards SET status=%s WHERE order_code=%s",
                    (status, code)
                )
                conn.commit()

        c.execute("SELECT * FROM orders ORDER BY id DESC")
        orders = c.fetchall()

    except Exception as e:
        print("DASHBOARD ERROR:", e)
        return "Dashboard database error"

    finally:
        if conn:
            conn.close()

    return render_template("dashboard.html", orders=orders)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
