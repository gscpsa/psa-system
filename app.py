from flask import Flask, request, redirect

app = Flask(__name__)

def page(content, mode="admin"):
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
        color:white;
    }}

    /* HEADER */
    .header {{
        background:#031c16;
        padding:20px 25px;
        border-bottom:2px solid #0f5132;
    }}

    .logo {{
        font-size:32px;
        font-weight:800;
        letter-spacing:2px;
    }}

    .logo span {{
        color:#198754;
    }}

    /* HERO */
    .hero {{
        display:flex;
        height:calc(100vh - 80px);
        align-items:center;
        justify-content:center;
    }}

    /* FORM PANEL */
    .panel {{
        width:420px;
        background:#f8f9fa;
        color:black;
        padding:30px;
        border-radius:16px;
        box-shadow:0 20px 60px rgba(0,0,0,.6);
        text-align:center;
    }}

    .panel h2 {{
        font-size:32px;
        margin-bottom:10px;
        font-weight:800;
    }}

    .divider {{
        width:60px;
        height:4px;
        background:#198754;
        margin:10px auto 20px;
        border-radius:4px;
    }}

    .desc {{
        color:#555;
        margin-bottom:20px;
    }}

    .input-group {{
        display:flex;
        align-items:center;
        border:1px solid #ddd;
        border-radius:10px;
        padding:12px;
        margin-bottom:15px;
        background:white;
    }}

    .input-group i {{
        color:#888;
        margin-right:10px;
    }}

    .input-group input {{
        border:none;
        outline:none;
        width:100%;
        font-size:14px;
    }}

    button {{
        width:100%;
        padding:16px;
        background:#198754;
        color:white;
        border:none;
        border-radius:10px;
        font-weight:bold;
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
            GIANT <span>SPORTS CARDS</span>
        </div>
    </div>

    {content}

    </body>
    </html>
    """


@app.route("/portal", methods=["GET","POST"])
def portal():

    if request.method == "POST":
        phone = request.form.get("phone")
        last = request.form.get("last")

        return redirect(f"/portal/orders?phone={phone}&last={last}")

    return page("""
    <div class="hero">

        <div class="panel">

            <i class="fa-solid fa-magnifying-glass" style="font-size:22px;margin-bottom:10px;"></i>

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


@app.route("/portal/orders")
def orders():
    phone = request.args.get("phone")
    last = request.args.get("last")

    return page(f"""
    <div class="hero">
        <div class="panel">
            <h2>Results</h2>
            <p>Phone: {phone}</p>
            <p>Last: {last}</p>
        </div>
    </div>
    """)


if __name__ == "__main__":
    app.run(debug=True)
