# FULL APP FILE - ONLY UI LAYOUT CHANGED

from flask import Flask, request, redirect, session

app = Flask(__name__)
app.secret_key = "secret"

def page(content, mode="admin"):

    if mode == "admin":
        nav = """
        <a href="/admin">Dashboard</a>
        <a href="/admin/search">Search</a>
        <a href="/admin/upload">Upload Excel</a>
        <a href="/admin/upload_psa">Upload PSA</a>
        <a href="/portal">Customer Portal</a>
        <a href="/admin/logout">Logout</a>
        """
    else:
        nav = """
        <a href="/portal">Home</a>
        <a href="/portal/logout">Logout</a>
        """

    return f"""
<html>
<head>
<meta name='viewport' content='width=device-width, initial-scale=1.0'>

<style>
body {{
    margin:0;
    font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto;
    background:#0e1110;
    color:white;
}}

.topbar {{
    background:#0f5132;
    padding:14px 20px;
    display:flex;
    justify-content:space-between;
    align-items:center;
}}

.brand {{
    display:flex;
    align-items:center;
    gap:10px;
    font-weight:800;
}}

.brand img {{
    width:34px;
}}

.links a {{
    color:white;
    text-decoration:none;
    margin-left:12px;
    font-size:13px;
    font-weight:600;
}}

.hero {{
    display:flex;
    height:440px;
}}

.left {{
    width:55%;
    padding:30px;
    display:flex;
    align-items:center;
    gap:18px;
}}

.card-img {{
    background:#111;
    border-radius:8px;
    padding:6px;
    box-shadow:0 15px 35px rgba(0,0,0,0.6);
}}

.card-img img {{
    height:270px;
    display:block;
}}

.right {{
    width:45%;
    background:#f5f5f5;
    display:flex;
    align-items:center;
    justify-content:center;
}}

.form-card {{
    background:white;
    padding:25px;
    border-radius:12px;
    width:320px;
    color:black;
}}

input {{
    width:100%;
    padding:10px;
    margin-bottom:10px;
    border-radius:6px;
    border:1px solid #ccc;
}}

button {{
    width:100%;
    padding:10px;
    background:#198754;
    border:none;
    color:white;
    font-weight:bold;
    border-radius:6px;
}}

.footer {{
    background:#0f5132;
    display:flex;
    justify-content:space-around;
    padding:15px;
    font-size:13px;
}}
</style>
</head>

<body>

<div class="topbar">
    <div class="brand">
        <img src="/static/images/logo.png">
        Giant Sports Cards
    </div>
    <div class="links">{nav}</div>
</div>

{"<div class='hero'>" + content + "</div>" if mode == "portal" else "<div class='container'>" + content + "</div>"}

<div class="footer">
    <div>Secure & Reliable</div>
    <div>Real-Time Updates</div>
    <div>Expert Care</div>
</div>

</body>
</html>
"""

@app.route("/portal", methods=["GET", "POST"])
def portal():
    if request.method == "POST":
        return redirect("/portal")

    return page("""
<div class="left">
    <div class="card-img"><img src="/static/images/card1.png"></div>
    <div class="card-img"><img src="/static/images/card2.png"></div>
    <div class="card-img"><img src="/static/images/card3.png"></div>
</div>

<div class="right">
    <div class="form-card">
        <h3>Track Your Submission</h3>
        <form method="post">
            <input name="phone" placeholder="Phone number">
            <input name="last" placeholder="Last name">
            <button>View Status</button>
        </form>
    </div>
</div>
""", mode="portal")

if __name__ == "__main__":
    app.run(debug=True)
