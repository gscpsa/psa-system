from flask import Flask, render_template, request, redirect, session
import psycopg2
import os

app = Flask(__name__)
app.secret_key = "secret123"


def get_db():
    try:
        url = os.environ.get("DATABASE_URL")
        print("DB URL:", url)
        return psycopg2.connect(url, sslmode="require")
    except Exception as e:
        print("DB CONNECTION ERROR:", e)
        raise


@app.route("/admin", methods=["GET", "POST"])
def admin():
    try:
        if request.method == "POST":
            if request.form.get("password") == "shopadmin":
                session["admin"] = True
                return redirect("/dashboard")
        return render_template("admin.html")
    except Exception as e:
        return f"ADMIN ERROR: {str(e)}"


@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    try:
        if not session.get("admin"):
            return redirect("/admin")

        conn = get_db()
        c = conn.cursor()

        print("CONNECTED TO DB")

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
        print("TABLES CREATED")

        if request.method == "POST":
            code = request.form["order_code"]
            status = request.form["status"]

            c.execute("UPDATE orders SET status=%s WHERE order_code=%s", (status, code))
            c.execute("UPDATE cards SET status=%s WHERE order_code=%s", (status, code))
            conn.commit()

        c.execute("SELECT * FROM orders")
        orders = c.fetchall()

        conn.close()

        return render_template("dashboard.html", orders=orders)

    except Exception as e:
        print("DASHBOARD ERROR:", e)
        return f"DASHBOARD ERROR: {str(e)}"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
