from flask import Flask, request, redirect

app = Flask(__name__)

def page(content):
    return f'''
    <!DOCTYPE html>
    <html lang="en">
    <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Giant Sports Cards</title>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Oswald:wght@500;600&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
    * {{
        box-sizing: border-box;
    }}
    body {{ 
        margin:0; 
        font-family: 'Inter', sans-serif; 
        background-color: #111;
        display: flex;
        flex-direction: column;
        min-height: 100vh;
    }}

    .header {{
        background: #040b09;
        color: white;
        padding: 0 40px;
        height: 70px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-bottom: 2px solid #113824;
    }}

    .header-left {{
        font-family: 'Oswald', sans-serif;
        font-size: 24px;
        font-weight: 600;
        color: white;
        display: flex;
        align-items: center;
    }}
    
    .header-left span {{
        color: #198754;
    }}

    .header-nav {{
        display: flex;
        gap: 30px;
    }}

    .header-nav a {{
        color: #e0e0e0;
        text-decoration: none;
        font-size: 14px;
        font-weight: 500;
        display: flex;
        align-items: center;
        gap: 8px;
        transition: color 0.2s;
    }}

    .header-nav a:hover {{
        color: #198754;
    }}

    .header-right a {{
        color: #e0e0e0;
        text-decoration: none;
        font-size: 14px;
        font-weight: 500;
        display: flex;
        align-items: center;
        gap: 8px;
    }}

    .hero {{
        display: flex;
        flex: 1;
        /* Replace with actual background image path */
        background: url('/static/images/bg.png') center center no-repeat;
        background-size: cover;
        position: relative;
    }}

    /* Adding a dark overlay to make the panel pop just like the mockup */
    .hero::before {{
        content: '';
        position: absolute;
        top: 0; right: 0; bottom: 0; left: 0;
        background: rgba(0,0,0,0.4);
        z-index: 0;
    }}

    .left {{
        flex: 1;
        z-index: 1;
    }}

    .right {{
        flex: 1.2;
        display: flex;
        justify-content: center;
        align-items: center;
        z-index: 1;
        padding: 40px;
    }}

    .panel {{
        width: 100%;
        max-width: 520px;
        background: white;
        padding: 50px 60px;
        border-radius: 12px;
        box-shadow: 0 20px 60px rgba(0,0,0,0.5);
    }}

    .panel-icon-top {{
        width: 70px;
        height: 70px;
        background: #f8f9fa;
        border-radius: 50%;
        display: flex;
        justify-content: center;
        align-items: center;
        margin: 0 auto 20px auto;
        border: 2px solid #e9ecef;
        color: #0d462b;
        font-size: 30px;
    }}

    .panel h2 {{
        text-align: center;
        font-family: 'Oswald', sans-serif;
        font-size: 36px;
        font-weight: 600;
        margin: 0 0 10px 0;
        color: #111;
        text-transform: uppercase;
        letter-spacing: -0.5px;
    }}

    .panel-divider {{
        width: 60px;
        height: 3px;
        background: #198754;
        margin: 0 auto 20px auto;
    }}

    .panel p.desc {{
        text-align: center;
        color: #555;
        font-size: 16px;
        line-height: 1.5;
        margin-bottom: 30px;
    }}

    .input-group {{
        display: flex;
        align-items: center;
        border: 1px solid #dcdfe3;
        border-radius: 6px;
        padding: 14px 16px;
        margin-bottom: 16px;
        background: white;
        transition: border-color 0.2s;
    }}
    
    .input-group:focus-within {{
        border-color: #198754;
    }}

    .input-group i {{
        color: #888;
        font-size: 18px;
        margin-right: 12px;
        width: 20px;
        text-align: center;
    }}

    .input-group input {{
        border: none;
        outline: none;
        flex: 1;
        font-size: 15px;
        font-family: 'Inter', sans-serif;
        color: #333;
    }}

    .input-group input::placeholder {{
        color: #999;
    }}

    button {{
        width: 100%;
        padding: 16px;
        background: #085a31;
        color: white;
        border: none;
        border-radius: 6px;
        font-family: 'Inter', sans-serif;
        font-size: 16px;
        font-weight: 600;
        cursor: pointer;
        display: flex;
        justify-content: center;
        align-items: center;
        gap: 10px;
        margin-top: 10px;
        transition: background 0.2s;
    }}

    button:hover {{
        background: #064023;
    }}

    .panel-footer {{
        margin-top: 40px;
        text-align: center;
        position: relative;
    }}

    .panel-footer::before {{
        content: '';
        display: block;
        width: 100%;
        height: 1px;
        background: #e0e0e0;
        position: absolute;
        top: 20px;
        left: 0;
        z-index: 1;
    }}

    .panel-footer-logo {{
        display: inline-block;
        background: white;
        padding: 0 15px;
        position: relative;
        z-index: 2;
        font-family: 'Oswald', sans-serif;
        font-weight: 600;
        color: #085a31;
        font-size: 18px;
        margin-bottom: 15px;
    }}

    .panel-footer p {{
        margin: 0;
        color: #777;
        font-size: 13px;
        line-height: 1.5;
    }}

    .footer {{
        background: #040b09;
        color: white;
        display: flex;
        justify-content: center;
        gap: 60px;
        padding: 25px 40px;
    }}

    .footer-feature {{
        display: flex;
        align-items: center;
        gap: 15px;
    }}

    .feature-icon {{
        width: 45px;
        height: 45px;
        border-radius: 50%;
        border: 2px solid #198754;
        display: flex;
        justify-content: center;
        align-items: center;
        font-size: 18px;
        color: white;
    }}

    .feature-text h4 {{
        margin: 0 0 4px 0;
        font-size: 14px;
        font-weight: 600;
        letter-spacing: 0.5px;
    }}

    .feature-text p {{
        margin: 0;
        font-size: 13px;
        color: #aaa;
    }}

    @media (max-width: 1000px) {{
        .hero {{ flex-direction: column; }}
        .header-nav {{ display: none; }} /* Mobile menu needed */
        .footer {{ flex-direction: column; gap: 20px; align-items: center; text-align: center; }}
        .footer-feature {{ flex-direction: column; }}
    }}
    </style>
    </head>

    <body>
    <div class="header">
        <div class="header-left">
            <!-- Mockup Logo Text -->
            GIANT <span>SPORTS CARDS</span>
        </div>
        <div class="header-nav">
            <a href="#"><i class="fa-solid fa-chart-simple"></i> Dashboard</a>
            <a href="#"><i class="fa-solid fa-magnifying-glass"></i> Search</a>
            <a href="#"><i class="fa-regular fa-file-excel"></i> Upload Excel</a>
            <a href="#"><i class="fa-solid fa-file-lines"></i> Upload PSA</a>
            <a href="#"><i class="fa-solid fa-users"></i> Customer Portal</a>
        </div>
        <div class="header-right">
            <a href="#"><i class="fa-solid fa-arrow-right-from-bracket"></i> Logout</a>
        </div>
    </div>

    {content}

    <div class="footer">
        <div class="footer-feature">
            <div class="feature-icon"><i class="fa-solid fa-shield-halved"></i></div>
            <div class="feature-text">
                <h4>SECURE & RELIABLE</h4>
                <p>Your information is safe with us.</p>
            </div>
        </div>
        <div class="footer-feature">
            <div class="feature-icon"><i class="fa-regular fa-clock"></i></div>
            <div class="feature-text">
                <h4>REAL-TIME UPDATES</h4>
                <p>Stay informed every step of the way.</p>
            </div>
        </div>
        <div class="footer-feature">
            <div class="feature-icon"><i class="fa-solid fa-check"></i></div>
            <div class="feature-text">
                <h4>EXPERT CARE</h4>
                <p>We treat every card like our own.</p>
            </div>
        </div>
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
                
                <div class="panel-icon-top">
                    <i class="fa-solid fa-clipboard-check"></i>
                </div>

                <h2>Track Your Submission</h2>
                <div class="panel-divider"></div>
                <p class="desc">Enter your information below to view<br>the real-time status of your PSA submission.</p>

                <form method="post">

                    <div class="input-group">
                        <i class="fa-solid fa-mobile-screen"></i>
                        <input name="phone" placeholder="Phone number" required>
                    </div>

                    <div class="input-group">
                        <i class="fa-solid fa-user"></i>
                        <input name="last" placeholder="Last name" required>
                    </div>

                    <button type="submit">VIEW STATUS <i class="fa-solid fa-arrow-right"></i></button>

                </form>

                <div class="panel-footer">
                    <div class="panel-footer-logo">GIANT SPORTS CARDS</div>
                    <p>Thank you for trusting Giant Sports Cards<br>with your valuable collection.</p>
                </div>

            </div>
        </div>

    </div>
    ''')

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000)
