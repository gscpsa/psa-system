from flask import Flask, request, redirect

app = Flask(__name__)

def page(content):
    return f"""
    <html>
    <head>
    <meta name='viewport' content='width=device-width, initial-scale=1.0'>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">

    <style>
    body {{
        margin:0;
        font-family: Arial, sans-serif;
        background: linear-gradient(180deg, #02140f 0%, #031c16 100%);
    }}

    .header {{
        background:#031c16;
        padding:30px 40px;
        border-bottom:2px solid #0f5132;
    }}

    .logo {{
        font-size:44px;
        font-weight:900;
        letter-spacing:3px;
        color:white;
    }}

    .logo span {{
        color:#198754;
    }}

    .hero {{
        display:flex;
        height:calc(100vh - 110px);
        align-items:center;
        justify-content:center;
    }}

    .panel {{
        width:650px;
        background:#f8f9fa;
        color:black;
        padding:55px;
        border-radius:20px;
        box-shadow:0 30px 90px rgba(0,0,0,.6);
        text-align:center;
    }}

    .panel h2 {{
        font-size:44px;
        font-weight:900;
        margin-bottom:10px;
    }}

    .divider {{
        width:70px;
        height:5px;
        background:#198754;
        margin:15px auto 25px;
        border-radius:4px;
    }}

    .desc {{
        color:#555;
        margin-bottom:30px;
        font-size:18px;
    }}

    .input-group {{
        display:flex;
        align-items:center;
        border:1px solid #ddd;
        border-radius:14px;
        padding:18px;
        margin-bottom:20px;
        background:white;
    }}

    .input-group i {{
        margin-right:15px;
        color:#888;
        font-size:18px;
    }}

    .input-group input {{
        border:none;
        outline:none;
        width:100%;
        font-size:18px;
    }}

    button {{
        width:100%;
        padding:20px;
        background:#198754;
        color:white;
        border:none;
        border-radius:14px;
        font-weight:800;
        font-size:18px;
        cursor:pointer;
    }}
    </style>
    </head>

    <body>

    <div class="header">
        <div class="logo">
            GIANT <span>SPORTS CARDS</span>
        </div>
    </div>

    {content}

    </body>
    </html>
    """

# -------------------------
# PORTAL PAGE
# -------------------------
@app.route("/portal", methods=["GET", "POST"])
def portal():

    if request.method == "POST":
        phone = request.form.get("phone")
        last = request.form.get("last")

        # SAFE redirect
        return redirect(f"/portal/orders?phone={phone}&last={last}")

    return page("""
    <div class="hero">
        <div class="panel">

            <i class="fa-solid fa-magnifying-glass" style="font-size:28px;margin-bottom:15px;"></i>

            <h2>TRACK YOUR ORDER</h2>
            <div class="divider"></div>

            <p class="desc">
            Enter your details to see the real-time<br>
            status of your PSA submission.
            </p>

            <form method="post">

                <div class="input-group">
                    <i class="fa-solid fa-mobile-screen"></i>
                    <input name="phone" placeholder="Phone Number" required>
                </div>

                <div class="input-group">
                    <i class="fa-solid fa-user"></i>
                    <input name="last" placeholder="Last Name" required>
                </div>

                <button type="submit">
                    VIEW STATUS →
                </button>

            </form>

        </div>
    </div>
    """)

# -------------------------
# RESULTS PAGE (FIXED)
# -------------------------
@app.route("/portal/orders")
def orders():

    phone = request.args.get("phone")
    last = request.args.get("last")

    # THIS is what was missing / broken before
    return page(f"""
    <div class="hero">
        <div class="panel">
            <h2>RESULTS</h2>
            <div class="divider"></div>

            <p><b>Phone:</b> {phone}</p>
            <p><b>Last Name:</b> {last}</p>

            <br>

            <p>Status: <b>Received → Processing</b></p>

        </div>
    </div>
    """)

# -------------------------
if __name__ == "__main__":
    app.run(debug=True)
