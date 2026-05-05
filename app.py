from flask import Flask, request, redirect

app = Flask(__name__)

def page(content):
    return f'''
    <!DOCTYPE html>
    <html lang="en">
    <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Giant Sports Cards Portal</title>
    <!-- Importing fonts and icons to match the mockup -->
    <link href="https://fonts.googleapis.com/css2?family=Oswald:wght@500;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    
    <style>
    * {{
        box-sizing: border-box;
    }}
    
    body {{ 
        margin:0; 
        font-family: 'Inter', Arial, sans-serif; 
        background-color: #06100d;
        display: flex;
        flex-direction: column;
        min-height: 100vh;
    }}

    /* --- HEADER STYLES --- */
    .header {{
        background: #06100d;
        color: white;
        padding: 0 30px;
        height: 80px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-bottom: 2px solid #113824;
    }}

    .header-logo {{
        font-family: 'Oswald', sans-serif;
        font-size: 26px;
        font-weight: 700;
        color: white;
        letter-spacing: -0.5px;
        text-decoration: none;
    }}
    
    .header-logo span {{
        color: #198754;
    }}

    .header-nav {{
        display: flex;
        gap: 35px;
    }}

    .header-nav a, .header-logout a {{
        color: #fff;
        text-decoration: none;
        font-size: 14px;
        font-weight: 500;
        display: flex;
        align-items: center;
        gap: 10px;
        transition: color 0.2s;
    }}

    .header-nav a:hover, .header-logout a:hover {{
        color: #198754;
    }}

    .header-nav i, .header-logout i {{
        font-size: 16px;
    }}

    /* --- HERO & LAYOUT --- */
    .hero {{
        display: flex;
        flex: 1;
        background: url('/static/images/bg.png') center center no-repeat;
        background-size: cover;
        position: relative;
    }}

    .left {{
        flex: 1;
    }}

    .right {{
        flex: 1.2;
        display: flex;
        justify-content: center;
        align-items: center;
        padding: 40px;
    }}

    /* --- FORM PANEL (MOCKUP) --- */
    .panel {{
        width: 100%;
        max-width: 500px;
        background: white;
        padding: 50px 60px;
        border-radius: 12px;
        box-shadow: 0 20px 60px rgba(0,0,0,0.4);
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
        font-size: 28px;
    }}

    .panel h2 {{
        text-align: center;
        font-family: 'Oswald', sans-serif;
        font-size: 32px;
        font-weight: 600;
        margin: 0 0 15px 0;
        color: #111;
        text-transform: uppercase;
        letter-spacing: 0.5px;
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
        font-size: 15px;
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
        font-size: 15px;
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

    /* --- FORM BOTTOM LOGO --- */
    .panel-footer {{
        margin-top: 35px;
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
        font-weight: 700;
        color: #085a31;
        font-size: 18px;
        margin-bottom: 10px;
        line-height: 1;
    }}
    
    .panel-footer-logo span {{
        display: block;
        font-size: 10px;
        color: #111;
        letter-spacing: 1px;
        margin-top: 2px;
    }}

    .panel-footer p {{
        margin: 0;
        color: #777;
        font-size: 13px;
        line-height: 1.5;
    }}

    /* --- FOOTER STYLES --- */
    .footer {{
        background: #06100d;
        color: white;
        display: flex;
        justify-content: center;
        gap: 80px;
        padding: 30px 40px;
        border-top: 1px solid #113824;
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
        font-family: 'Inter', sans-serif;
        letter-spacing: 0.5px;
    }}

    .feature-text p {{
        margin: 0;
        font-size: 13px;
        color: #aaa;
    }}

    @media (max-width: 1000px) {{
        .hero {{ flex-direction: column; }}
        .header-nav {{ display: none; }}
        .footer {{ flex-direction: column; gap: 25px; align-items: center; text-align: center; }}
        .footer-feature {{ flex-direction: column; text-align: center; }}
    }}
    </style>
    </head>

    <body>
    <div class="header">
        <a href="/" class="header-logo">
            GIANT <span>SPORTS CARDS</span>
        </a>
        
        <div class="header-nav">
            <a href="/"><i class="fa-solid fa-chart-simple"></i> Dashboard</a>
            <a href="/"><i class="fa-solid fa-magnifying-glass"></i> Search</a>
            <a href="/"><i class="fa-regular fa-file-excel"></i> Upload Excel</a>
            <a href="/"><i class="fa-solid fa-file-lines"></i> Upload PSA</a>
            <a href="/portal"><i class="fa-solid fa-users"></i> Customer Portal</a>
        </div>
        
        <div class="header-logout">
            <a href="/"><i class="fa-solid fa-arrow-right-from-bracket"></i> Logout</a>
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
    # Preserving your exact Python logic
    if request.method == "POST":
        return redirect("/portal")

    # Only one hero and one form are returned here, replacing the old block
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
                    <div class="panel-footer-logo">
                        GIANT<br><span>SPORTS CARDS</span>
                    </div>
                    <p>Thank you for trusting Giant Sports Cards<br>with your valuable collection.</p>
                </div>

            </div>
        </div>

    </div>
    ''')

if __name__ == "__main__":
    app.run(debug=True)
