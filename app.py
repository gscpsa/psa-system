from flask import Flask, render_template, request, redirect, session
import psycopg2
import os

app = Flask(__name__)
app.secret_key = "secret123"


# -------------------------
# DATABASE CONNECTION
# -------------------------
def get_db():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise Exception("DATABASE_URL is missing")
    return psycopg2.connect(url, sslmode="require")


# -------------------------
# SAFE TABLE CREATION
# -------------------------
def safe_init_db():
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
        conn.close()

    except Exception as e:
        print("SAFE INIT ERROR:", e)


# -------------------------
# ADMIN LOGIN
# -------------------------
@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        if request.form.get("password") == "shopadmin":
            session["admin"] = True
            return redirect("/dashboard")
    return render_template("admin.html")


# -------------------------
# DASHBOARD
# -------------------------
@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if not session.get("admin"):
        return redirect("/admin")

    # 🔥 THIS CREATES TABLES AUTOMATICALLY
    safe_init_db()

    conn = get_db()
    c = conn.cursor()

    # UPDATE STATUS
    if request.method == "POST":
        code = request.form["order_code"]
        status = request.form["status"]

        c.execute("UPDATE orders SET status=%s WHERE order_code=%s", (status, code))
        c.execute("UPDATE cards SET status=%s WHERE order_code=%s", (status, code))
        conn.commit()

    # FETCH DATA
    c.execute("SELECT * FROM orders")
    orders = c.fetchall()

    conn.close()

    return render_template("dashboard.html", orders=orders)


# -------------------------
# RUN APP
# -------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
