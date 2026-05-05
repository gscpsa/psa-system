from flask import Flask, request, redirect

app = Flask(__name__)

def page(content):
    return f'''
    <html>
    <head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
    body {{ margin:0; font-family:Arial; }}

    .header {{
        background:#06100d;
        color:white;
        padding:15px 25px;
        display:flex;
        justify-content:space-between;
    }}

    .hero {{
        display:flex;
        height:calc(100vh - 60px);
    }}

    .left {{
        flex:1;
        background:url('/static/images/bg.png') left center no-repeat;
        background-size:cover;
    }}

    .right {{
        flex:1;
        display:flex;
        justify-content:center;
        align-items:center;
        background:url('/static/images/bg.png') right center no-repeat;
        background-size:cover;
    }}

    .panel {{
        width:420px;
        background:white;
        padding:30px;
        border-radius:16px;
        box-shadow:0 20px 60px rgba(0,0,0,.3);
    }}

    .panel h2 {{
        text-align:center;
        margin-bottom:10px;
    }}

    .panel p {{
        text-align:center;
        color:#666;
        margin-bottom:20px;
    }}

    .input {{
        display:flex;
        align-items:center;
        border:1px solid #ddd;
        border-radius:8px;
        padding:10px;
        margin-bottom:12px;
    }}

    .input span {{
        margin-right:8px;
    }}

    .input input {{
        border:none;
        outline:none;
        flex:1;
    }}

    button {{
        width:100%;
        padding:14px;
        background:#198754;
        color:white;
        border:none;
        border-radius:8px;
        font-weight:bold;
    }}

    .footer {{
        background:#06100d;
        color:white;
        display:flex;
        justify-content:space-around;
        padding:15px;
    }}

    </style>
    </head>

    <body>
    <div class="header">
        <div>Giant Sports Cards</div>
        <div>Dashboard | Search | Upload | Portal</div>
    </div>

    {content}

    <div class="footer">
        <div>Secure & Reliable</div>
        <div>Real-Time Updates</div>
        <div>Expert Care</div>
    </div>

    </body>
    </html>
    '''

@app.route("/", methods=["GET","POST"])
@app.route("/portal", methods=["GET","POST"])
def portal():
    if request.method == "POST":
        return redirect("/portal")

    return page('''
    <div class="hero">

        <div class="left"></div>

        <div class="right">
            <div class="panel">
                <h2>Track Your Submission</h2>
                <p>Enter your information below to view the real-time status of your PSA submission.</p>

                <form method="post">

                    <div class="input">
                        <span>📱</span>
                        <input name="phone" placeholder="Phone number">
                    </div>

                    <div class="input">
                        <span>👤</span>
                        <input name="last" placeholder="Last name">
                    </div>

                    <button>VIEW STATUS</button>

                </form>

            </div>
        </div>

    </div>
    ''')

if __name__ == "__main__":
    app.run(debug=True)
