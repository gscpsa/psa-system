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
        background:#02140f;
    }}

    /* HEADER */
    .header {{
        background:#031c16;
        padding:30px 50px;
        border-bottom:2px solid #0f5132;
    }}

    .logo {{
        font-weight:900;
        letter-spacing:4px;
        line-height:1;
    }}

    .logo .giant {{
        font-size:52px;
        color:white;
    }}

    .logo .sports {{
        font-size:22px;
        color:#198754;
        letter-spacing:6px;
    }}

    /* LAYOUT */
    .hero {{
        display:flex;
        height:calc(100vh - 110px);
    }}

    .left {{
        flex:1;
        background: radial-gradient(circle at 20% 20%, #0f5132, #02140f);
    }}

    .right {{
        flex:1;
        display:flex;
        align-items:center;
        justify-content:center;
        background:#f4f4f4;
    }}

    /* FORM PANEL */
    .panel {{
        width:720px;
        background:#ffffff;
        padding:60px;
        border-radius:22px;
        box-shadow:0 35px 120px rgba(0,0,0,.6);
        text-align:center;
    }}

    .icon-top {{
        font-size:32px;
        margin-bottom:15px;
        color:#031c16;
    }}

    .panel h2 {{
        font-size:46px;
        font-weight:900;
        letter-spacing:2px;
        margin:10px 0;
    }}

    .divider {{
        width:80px;
        height:6px;
        background:#198754;
        margin:20px auto 30px;
        border-radius:6px;
    }}

    .desc {{
        font-size:18px;
        color:#555;
        margin-bottom:35px;
        line-height:1.5;
    }}

    /* INPUTS */
    .input-group {{
        display:flex;
        align-items:center;
        border:1px solid #ddd;
        border-radius:16px;
        padding:22px;
        margin-bottom:22px;
        background:white;
    }}

    .input-group i {{
        font-size:20px;
        margin-right:15px;
        color:#777;
    }}

    .input-group input {{
        border:none;
        outline:none;
        width:100%;
        font-size:20px;
    }}

    /* BUTTON */
    button {{
        width:100%;
        padding:22px;
        background:#198754;
        color:white;
        border:none;
        border-radius:16px;
        font-size:20px;
        font-weight:900;
        cursor:pointer;
    }}

    button:hover {{
        background:#157347;
    }}
    </style>
    </head>

    <body>

    <div class="header">
        <div class="logo">
            <div class="giant">GIANT</div>
            <div class="sports">SPORTS CARDS</div>
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
        return redirect(f"/portal/orders?phone={phone}&last={last}")

    return page("""
    <div class="hero">

        <div class="left"></div>

        <div class="right">
            <div class="panel">

                <div class="icon-top">
                    <i class="fa-solid fa-clipboard-check"></i>
                </div>

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

    </div>
    """)

# -------------------------
# RESULTS PAGE
# -------------------------
@app.route("/portal/orders")
def orders():

    phone = request.args.get("phone")
    last = request.args.get("last")

    return page(f"""
    <div class="hero">

        <div class="left"></div>

        <div class="right">
            <div class="panel">

                <h2>RESULTS</h2>
                <div class="divider"></div>

                <p><b>Phone:</b> {phone}</p>
                <p><b>Last Name:</b> {last}</p>

                <br>

                <p>Status: <b>Received → Processing</b></p>

            </div>
        </div>

    </div>
    """)

# -------------------------
if __name__ == "__main__":
    app.run(debug=True)
