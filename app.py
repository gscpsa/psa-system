from flask import Flask, request, redirect

app = Flask(__name__)


def page(content):
    return f"""
<html>
<head>
<meta name='viewport' content='width=device-width, initial-scale=1.0'>

<style>

body {{
    margin:0;
    font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto;
    overflow:hidden;
}}

/* ===== BACKGROUND ===== */
.bg {{
    position:fixed;
    top:0;
    left:0;
    width:100vw;
    height:100vh;
    background:url('/static/images/bg.png') no-repeat center center;
    background-size:cover;
    z-index:0;
}}

/* ===== HEADER ===== */
.header {{
    position:absolute;
    top:18px;
    left:40px;
    right:40px;
    display:flex;
    justify-content:space-between;
    align-items:center;
    color:white;
    z-index:2;
}}

.nav a {{
    margin-left:18px;
    font-size:13px;
    color:white;
    text-decoration:none;
}}

/* ===== FORM (ALIGNED TO IMAGE) ===== */
.form-box {{
    position:absolute;
    top:52%;
    right:9%;
    transform:translateY(-50%);
    width:360px;
    z-index:2;
}}

/* MAKE FORM BLEND WITH IMAGE */
.form-box input {{
    width:100%;
    height:48px;
    margin-bottom:12px;
    border-radius:8px;
    border:1px solid rgba(0,0,0,.15);
    padding-left:12px;
    background:rgba(255,255,255,0.85);
}}

.form-box button {{
    width:100%;
    height:52px;
    border:none;
    border-radius:8px;
    background:#198754;
    color:white;
    font-weight:bold;
    cursor:pointer;
}}

/* ===== FOOTER ===== */
.footer {{
    position:absolute;
    bottom:20px;
    left:0;
    width:100%;
    display:flex;
    justify-content:center;
    z-index:2;
}}

.footer-inner {{
    width:900px;
    display:flex;
    justify-content:space-between;
    color:white;
    font-size:13px;
}}

</style>
</head>

<body>

<div class="bg"></div>

<!-- HEADER -->
<div class="header">
    <div><b>Giant Sports Cards</b></div>
    <div class="nav">
        <a>Dashboard</a>
        <a>Search</a>
        <a>Upload</a>
    </div>
</div>

<!-- FORM -->
<div class="form-box">
    {content}
</div>

<!-- FOOTER -->
<div class="footer">
    <div class="footer-inner">
        <div>Secure & Reliable</div>
        <div>Real-Time Updates</div>
        <div>Expert Care</div>
    </div>
</div>

</body>
</html>
"""


@app.route("/", methods=["GET","POST"])
def portal():
    if request.method == "POST":
        return redirect("/")
    return page("""
<form method="post">
    <input name="phone" placeholder="Phone number">
    <input name="last" placeholder="Last name">
    <button>VIEW STATUS</button>
</form>
""")


if __name__ == "__main__":
    app.run(debug=True)
