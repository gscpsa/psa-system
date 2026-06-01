from flask import Flask, request, session, redirect
import pandas as pd
import psycopg2
import os, io, json, re, traceback
from functools import wraps

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

# =========================
# DATABASE
# =========================
def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS submissions (
        submission_number TEXT PRIMARY KEY,
        status TEXT DEFAULT 'Submitted',
        raw_data JSONB,
        last_updated TIMESTAMP DEFAULT NOW()
    )
    """)
    conn.commit()
    cur.close()
    conn.close()

@app.before_request
def setup():
    try:
        init_db()
    except Exception:
        pass

@app.errorhandler(Exception)
def error_handler(e):
    return page(f"""
    <div class="card">
        <h2>Application Error</h2>
        <p>The app hit an internal error. Details below:</p>
        <pre>{traceback.format_exc()}</pre>
        <a href="/admin">Back to Admin</a>
    </div>
    """)

# =========================
# SECURITY
# =========================
def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect("/admin/login")
        return f(*args, **kwargs)
    return wrapper

# =========================
# HELPERS
# =========================
def clean(v):
    try:
        if pd.isna(v):
            return ""
    except Exception:
        pass
    return str(v).strip()

def normalize_submission(v):
    if not v:
        return None
    return re.sub(r"\D", "", str(v).split(".")[0])

def normalize_phone(v):
    return re.sub(r"\D", "", str(v or ""))

def get_field(data, names):
    for wanted in names:
        for k, v in data.items():
            if str(k).strip().lower() == wanted.strip().lower():
                return v
    return ""

def customer_status_label(status):
    if status == "Delivered to Us":
        return "Ready For Pickup"
    return status or "Submitted"

def clean_service_display(service):
    value = str(service or "").strip()
    if " - " in value:
        return value.split(" - ", 1)[0].strip()
    if " – " in value:
        return value.split(" – ", 1)[0].strip()
    return value

def parse_arrived_completed_value(value):
    text = str(value or "").strip()
    result = {"arrived": "", "estimated": "", "completed": "", "display": text}

    if not text:
        return result

    date_pattern = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}"

    completed_match = re.search(r"Completed\s+(" + date_pattern + r")", text, re.IGNORECASE)
    if completed_match:
        result["completed"] = completed_match.group(1)

    estimated_match = re.search(r"Est\.\s*by\s+(" + date_pattern + r")", text, re.IGNORECASE)
    if estimated_match:
        result["estimated"] = estimated_match.group(1)

    first_date_match = re.search(date_pattern, text, re.IGNORECASE)
    if first_date_match:
        first_date = first_date_match.group(0)
        if first_date != result["completed"] and first_date != result["estimated"]:
            result["arrived"] = first_date

    parts = []
    if result["arrived"]:
        parts.append(f"Arrived: {result['arrived']}")
    if result["estimated"]:
        parts.append(f"Estimated Completion: {result['estimated']}")
    if result["completed"]:
        parts.append(f"Completed: {result['completed']}")

    if parts:
        result["display"] = " | ".join(parts)

    return result

def read_file(file):
    name = (file.filename or "").lower()

    if name.endswith(("xlsx", "xls")):
        return pd.read_excel(file)

    raw = file.read()
    file.seek(0)

    try:
        return pd.read_csv(io.StringIO(raw.decode("utf-8")), on_bad_lines="skip")
    except Exception:
        return pd.read_csv(io.StringIO(raw.decode("latin1")), on_bad_lines="skip")

# =========================
# STATUS LOGIC
# =========================
def normalize_psa_status(status):
    s = re.sub(r"\s+", " ", str(status or "")).strip().lower()

    if s == "order arrived":
        return "Order Arrived"
    if s == "research & id":
        return "Research & ID"
    if s == "grading":
        return "Grading"
    if s == "qa checks":
        return "QA Checks"
    if s == "assembly":
        return "Assembly"
    if s == "shipping soon":
        return "Shipping Soon"
    if s == "complete":
        return "Complete"

    return None

def status_rank(status):
    ranks = {
        "Submitted": 0,
        "Order Arrived": 1,
        "Research & ID": 2,
        "Grading": 3,
        "QA Checks": 4,
        "Assembly": 5,
        "Shipping Soon": 6,
        "Complete": 7,
        "Delivered to Us": 8,
        "Picked Up": 9,
    }
    return ranks.get(status or "Submitted", 0)

def detect_internal_status(raw):
    full_text = " ".join([f"{k} {v}" for k, v in raw.items()]).lower()

    if "not picked up" in full_text or "not picked-up" in full_text:
        return None

    if "picked up" in full_text or "customer picked up" in full_text:
        return "Picked Up"

    if (
        "delivered to us" in full_text
        or "received by us" in full_text
        or "arrived at store" in full_text
        or "delivered back" in full_text
    ):
        return "Delivered to Us"

    return None

def save_row(sub, raw):
    conn = get_conn()
    cur = conn.cursor()

    internal_status = detect_internal_status(raw)

    cur.execute("""
    SELECT status FROM submissions
    WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
    """, (sub,))
    existing = cur.fetchone()
    existing_status = existing[0] if existing else None

    if existing_status == "Picked Up":
        cur.execute("""
        UPDATE submissions
        SET raw_data=%s, last_updated=NOW()
        WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
        """, (json.dumps(raw), sub))

    elif internal_status:
        cur.execute("""
        INSERT INTO submissions (submission_number, status, raw_data)
        VALUES (%s, %s, %s)
        ON CONFLICT (submission_number)
        DO UPDATE SET
            status=%s,
            raw_data=EXCLUDED.raw_data,
            last_updated=NOW()
        """, (sub, internal_status, json.dumps(raw), internal_status))

    else:
        cur.execute("""
        INSERT INTO submissions (submission_number, status, raw_data)
        VALUES (%s, 'Submitted', %s)
        ON CONFLICT (submission_number)
        DO UPDATE SET
            raw_data=EXCLUDED.raw_data,
            status=COALESCE(submissions.status, 'Submitted'),
            last_updated=NOW()
        """, (sub, json.dumps(raw)))

    conn.commit()
    cur.close()
    conn.close()

# =========================
# UI
# =========================
def page(content, mode="admin"):
    if mode == "admin":
        nav = """
        <a href="/admin">Dashboard</a>
        <a href="/admin/search">Search</a>
        <a href="/admin/upload">Excel</a>
        <a href="/admin/upload_psa">PSA PDF</a>
        <a href="/portal">Portal</a>
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
            font-family: Arial;
            margin:0;
            background:#f4f6f8;
            color:#111827;
        }}
        .topbar {{
            background:#0f5132;
            color:white;
            padding:14px 20px;
            display:flex;
            justify-content:space-between;
            align-items:center;
            gap:18px;
            flex-wrap:wrap;
        }}
        .brand {{
            font-weight:bold;
            font-size:20px;
            display:flex;
            align-items:center;
            gap:12px;
            min-width:260px;
        }}
        .brand img {{
            max-height:78px;
            width:auto;
            display:block;
        }}
        .brand span {{
            white-space:nowrap;
        }}
        .links {{
            display:flex;
            flex-wrap:wrap;
            gap:8px;
            justify-content:flex-end;
            align-items:center;
        }}
        .links a {{
            color:white;
            background:rgba(255,255,255,.12);
            padding:8px 11px;
            border-radius:8px;
            text-decoration:none;
            font-weight:bold;
            font-size:13px;
            margin:0;
            line-height:1;
        }}
        .links a:hover {{
            background:rgba(255,255,255,.22);
            color:white;
        }}
        .container {{
            padding:16px;
            overflow-x:auto;
        }}
        table {{
            width:100%;
            border-collapse:collapse;
            background:white;
            font-size:12px;
            table-layout:auto;
        }}
        th {{
            background:#0f5132;
            color:white;
            padding:5px;
            text-align:left;
            position:sticky;
            top:0;
            white-space:nowrap;
        }}
        td {{
            padding:5px;
            border-bottom:1px solid #ddd;
            white-space:nowrap;
            max-width:180px;
            overflow:hidden;
            text-overflow:ellipsis;
        }}
        td.notes-col {{
            white-space:normal;
            max-width:220px;
            min-width:160px;
            overflow-wrap:break-word;
            word-break:break-word;
        }}
        tr:hover {{
            background:#eef6f2;
        }}
        .status {{
            font-weight:bold;
            color:#198754;
        }}
        .card {{
            background:white;
            padding:18px;
            margin-bottom:15px;
            border-radius:10px;
            box-shadow:0 2px 8px rgba(0,0,0,.08);
        }}
        .btn {{
            display:inline-block;
            padding:8px 12px;
            background:#198754;
            color:white;
            text-decoration:none;
            border-radius:6px;
            margin:5px 8px 15px 0;
            font-weight:bold;
        }}
        input, button {{
            padding:10px;
            margin:5px;
        }}
        .bar {{
            display:flex;
            gap:6px;
            flex-wrap:wrap;
            margin-top:10px;
        }}
        .step {{
            padding:7px 11px;
            border-radius:20px;
            background:#e5e7eb;
            font-size:13px;
        }}
        .done {{
            background:#d1e7dd;
            color:#0f5132;
            font-weight:bold;
        }}
        .current {{
            background:#198754;
            color:white;
            font-weight:bold;
        }}
        pre {{
            background:#111827;
            color:white;
            padding:12px;
            overflow:auto;
            border-radius:8px;
            font-size:12px;
        }}
        .filterbar {{
            background:white;
            padding:14px;
            margin-bottom:15px;
            border-radius:10px;
            box-shadow:0 2px 8px rgba(0,0,0,.08);
        }}
        .filterbar form {{
            display:flex;
            flex-wrap:wrap;
            align-items:end;
            gap:10px;
            margin:0;
        }}
        .filterbar label {{
            display:block;
            font-size:12px;
            font-weight:bold;
            margin-bottom:4px;
            color:#374151;
        }}
        .filterbar select {{
            min-width:180px;
            padding:9px 10px;
            border:1px solid #cbd5e1;
            border-radius:8px;
            background:white;
            font-size:14px;
        }}
        .filterbar button {{
            padding:10px 13px;
            background:#198754;
            color:white;
            border:0;
            border-radius:8px;
            font-weight:bold;
            cursor:pointer;
            margin:0;
        }}
        .filterbar .reset-link {{
            display:inline-block;
            padding:10px 13px;
            background:#e5e7eb;
            color:#111827;
            border-radius:8px;
            text-decoration:none;
            font-weight:bold;
            font-size:14px;
        }}
        @media (max-width: 700px) {{
            .topbar {{
                align-items:flex-start;
            }}
            .brand {{
                min-width:100%;
            }}
            .brand img {{
                max-height:68px;
            }}
            .links {{
                justify-content:flex-start;
            }}
            .filterbar form {{
                display:block;
            }}
            .filterbar select, .filterbar button, .filterbar .reset-link {{
                width:100%;
                margin:5px 0 10px 0;
                box-sizing:border-box;
            }}
        }}
    </style>
    </head>
    <body>
        <div class="topbar">
            <div class="brand"><img src="data:image/png;base64,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Y0hSTQAAeiYAAICEAAD6AAAAgOgAAHUwAADqYAAAOpgAABdwnLpRPAAAAAlwSFlzAAAuIwAALiMBeKU/dgAABR1pVFh0WE1MOmNvbS5hZG9iZS54bXAAAAAAADw/eHBhY2tldCBiZWdpbj0i77u/IiBpZD0iVzVNME1wQ2VoaUh6cmVTek5UY3prYzlkIj8+IDx4OnhtcG1ldGEgeG1sbnM6eD0iYWRvYmU6bnM6bWV0YS8iIHg6eG1wdGs9IkFkb2JlIFhNUCBDb3JlIDkuMS1jMDAxIDc5LmE4ZDQ3NTM0OSwgMjAyMy8wMy8yMy0xMzowNTo0NSAgICAgICAgIj4gPHJkZjpSREYgeG1sbnM6cmRmPSJodHRwOi8vd3d3LnczLm9yZy8xOTk5LzAyLzIyLXJkZi1zeW50YXgtbnMjIj4gPHJkZjpEZXNjcmlwdGlvbiByZGY6YWJvdXQ9IiIgeG1sbnM6eG1wPSJodHRwOi8vbnMuYWRvYmUuY29tL3hhcC8xLjAvIiB4bWxuczpkYz0iaHR0cDovL3B1cmwub3JnL2RjL2VsZW1lbnRzLzEuMS8iIHhtbG5zOnBob3Rvc2hvcD0iaHR0cDovL25zLmFkb2JlLmNvbS9waG90b3Nob3AvMS4wLyIgeG1sbnM6eG1wTU09Imh0dHA6Ly9ucy5hZG9iZS5jb20veGFwLzEuMC9tbS8iIHhtbG5zOnN0RXZ0PSJodHRwOi8vbnMuYWRvYmUuY29tL3hhcC8xLjAvc1R5cGUvUmVzb3VyY2VFdmVudCMiIHhtcDpDcmVhdG9yVG9vbD0iQWRvYmUgUGhvdG9zaG9wIDI0LjYgKE1hY2ludG9zaCkiIHhtcDpDcmVhdGVEYXRlPSIyMDIzLTEwLTE2VDE3OjAzOjM2LTA0OjAwIiB4bXA6TW9kaWZ5RGF0ZT0iMjAyMy0xMC0xNlQxNzowOTowOC0wNDowMCIgeG1wOk1ldGFkYXRhRGF0ZT0iMjAyMy0xMC0xNlQxNzowOTowOC0wNDowMCIgZGM6Zm9ybWF0PSJpbWFnZS9wbmciIHBob3Rvc2hvcDpDb2xvck1vZGU9IjMiIHBob3Rvc2hvcDpJQ0NQcm9maWxlPSJzUkdCIElFQzYxOTY2LTIuMSIgeG1wTU06SW5zdGFuY2VJRD0ieG1wLmlpZDpjMTkwMjJiZC1jNWU5LTRhY2YtYWYwMy0wMzNiODBmOGVlZWMiIHhtcE1NOkRvY3VtZW50SUQ9InhtcC5kaWQ6YzE5MDIyYmQtYzVlOS00YWNmLWFmMDMtMDMzYjgwZjhlZWVjIiB4bXBNTTpPcmlnaW5hbERvY3VtZW50SUQ9InhtcC5kaWQ6YzE5MDIyYmQtYzVlOS00YWNmLWFmMDMtMDMzYjgwZjhlZWVjIj4gPHhtcE1NOkhpc3Rvcnk+IDxyZGY6U2VxPiA8cmRmOmxpIHN0RXZ0OmFjdGlvbj0iY3JlYXRlZCIgc3RFdnQ6aW5zdGFuY2VJRD0ieG1wLmlpZDpjMTkwMjJiZC1jNWU5LTRhY2YtYWYwMy0wMzNiODBmOGVlZWMiIHN0RXZ0OndoZW49IjIwMjMtMTAtMTZUMTc6MDM6MzYtMDQ6MDAiIHN0RXZ0OnNvZnR3YXJlQWdlbnQ9IkFkb2JlIFBob3Rvc2hvcCAyNC42IChNYWNpbnRvc2gpIi8+IDwvcmRmOlNlcT4gPC94bXBNTTpIaXN0b3J5PiA8L3JkZjpEZXNjcmlwdGlvbj4gPC9yZGY6UkRGPiA8L3g6eG1wbWV0YT4gPD94cGFja2V0IGVuZD0iciI/PkD4qSIAAEWUSURBVHic7d13uGVXQT7+NyShJSEhIQmElANEICBFpCiEZsEIiIiigEiTooJSFAWCNGkCShEU+UoRRPpPFBREqgJioSO9BAgEk0AKAVJnfn+sjDN35s7Mvefsvdfee30+zzOPkdy795s7+56z37PWXmufrVu3BgAAgPZcpnYAAAAA6lAIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQ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UQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjfr/ARwPrTF8DYWaAAAAAElFTkSuQmCC" alt="Giant Sports Cards"> <span>PSA Order Tracker</span></div>
            <div class="links">{nav}</div>
        </div>
        <div class="container">{content}</div>
    </body>
    </html>
    """

def status_bar(status):
    steps = [
        "Submitted",
        "Order Arrived",
        "Research & ID",
        "Grading",
        "QA Checks",
        "Assembly",
        "Shipping Soon",
        "Complete",
        "Delivered to Us",
        "Picked Up"
    ]

    status = status or "Submitted"
    idx = steps.index(status) if status in steps else 0

    html = "<div class='bar'>"
    for i, step in enumerate(steps):
        cls = "step"
        if i < idx:
            cls += " done"
        if i == idx:
            cls += " current"
        html += f"<div class='{cls}'>{step}</div>"
    html += "</div>"
    return html

def should_hide_column(column_name):
    key = str(column_name).strip().lower()

    return key in [
        "status",
        "current status",
        "psa status",
        "order status",
        "customer status"
    ]

def build_table(rows):
    keys = []
    clean_rows = []
    force_keys = ["Arrived / Completed", "Estimated Completion Date"]

    for r in rows:
        data = r[0] or {}
        row = {}

        for k, v in data.items():
            key_text = str(k).strip()

            if "unnamed" in key_text.lower():
                continue

            if should_hide_column(key_text):
                continue

            if key_text == "S":
                display_key = "Customer Drop-Off Date"
            elif key_text.strip().lower() == "submission date":
                display_key = "Customer Drop-Off Date"
            else:
                display_key = key_text

            display_value = v

            if display_key.strip().lower() == "service type":
                display_value = clean_service_display(v)

            if display_key == "Arrived / Completed":
                parsed_ac = parse_arrived_completed_value(v)
                display_value = parsed_ac["display"]

                if parsed_ac["estimated"]:
                    row["Estimated Completion Date"] = parsed_ac["estimated"]
                    if "Estimated Completion Date" not in keys:
                        keys.append("Estimated Completion Date")

            row[display_key] = display_value

            if display_key not in keys:
                keys.append(display_key)

        row["PSA Status"] = customer_status_label(r[1] or "Submitted")

        if "PSA Status" not in keys:
            keys.append("PSA Status")

        clean_rows.append(row)

    for forced_key in force_keys:
        if forced_key not in keys:
            keys.append(forced_key)

    if not clean_rows:
        return "<div class='card'>No records found.</div>"

    html = "<table><tr>"
    for k in keys:
        html += f"<th>{k}</th>"
    html += "</tr>"

    for row in clean_rows:
        html += "<tr>"
        for k in keys:
            val = row.get(k, "")
            col_class = "notes-col" if "note" in k.lower() else ""

            if k == "PSA Status":
                html += f"<td class='status {col_class}'>{val}</td>"
            else:
                html += f"<td class='{col_class}'>{val}</td>"
        html += "</tr>"

    html += "</table>"
    return html

def get_sort_date(row):
    data = row[0] or {}
    date_value = get_field(data, ["Customer Drop-Off Date", "Submission Date", "S", "Date"])

    try:
        if date_value:
            return pd.to_datetime(date_value)
    except Exception:
        pass

    return pd.Timestamp.min

# =========================
# ADMIN ROUTES
# =========================
@app.route("/")
def root():
    return redirect("/admin")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/admin")

        return page("<div class='card'>Wrong password. <a href='/admin/login'>Try again</a></div>")

    return page("""
    <div class="card">
        <h2>Admin Login</h2>
        <form method="post">
            <input type="password" name="password" placeholder="Admin password">
            <button>Login</button>
        </form>
    </div>
    """)

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect("/admin/login")


@app.route("/admin/clear_submissions")
@admin_required
def clear_submissions():
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM submissions")
        conn.commit()
        cur.close()
        conn.close()

        return page("""
        <div class="card">
            <h2>All submission data cleared</h2>
            <p>Excel and PSA PDF submission records have been removed.</p>
            <p>You can now re-upload the Excel file and PSA PDF from a clean database.</p>
            <a class="btn" href="/admin">Back to Admin</a>
        </div>
        """)
    except Exception:
        return page(f"""
        <div class="card">
            <h2>Error Clearing Submission Data</h2>
            <pre>{traceback.format_exc()}</pre>
            <a class="btn" href="/admin">Back to Admin</a>
        </div>
        """)

@app.route("/admin")
@admin_required
def admin_dashboard():
    sort = request.args.get("sort", "new")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT raw_data, status FROM submissions")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    rows = sorted(rows, key=get_sort_date, reverse=(sort != "old"))

    html = """
    <h2>Admin Dashboard</h2>
    <a class="btn" href="/admin?sort=new">Newest First</a>
    <a class="btn" href="/admin?sort=old">Oldest First</a>
    <a class="btn" href="/admin/search">Search</a>
    <a class="btn" href="/admin/upload">Upload Excel</a>
    <a class="btn" href="/admin/upload_psa">Upload PSA PDF</a>
    <a class="btn" href="/portal">Customer Portal</a>
    <br><br>
    """

    html += build_table(rows)
    return page(html)

@app.route("/admin/search")
@admin_required
def admin_search():
    q = request.args.get("q", "")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    SELECT raw_data, status
    FROM submissions
    WHERE raw_data::text ILIKE %s
       OR submission_number ILIKE %s
       OR status ILIKE %s
    ORDER BY last_updated DESC
    """, (f"%{q}%", f"%{q}%", f"%{q}%"))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    html = f"""
    <h2>Admin Search</h2>
    <form>
        <input name="q" value="{q}" placeholder="Search name, phone, submission, status">
        <button>Search</button>
    </form>
    <br>
    """

    html += build_table(rows)
    return page(html)

@app.route("/admin/upload", methods=["GET", "POST"])
@admin_required
def admin_upload():
    if request.method == "POST":
        try:
            file = request.files.get("file")

            if not file:
                return page("<div class='card'>No file uploaded.</div>")

            df = read_file(file)
            df.columns = [str(c).strip() for c in df.columns]

            count = 0
            skipped = 0

            for _, row in df.iterrows():
                raw = {c: clean(row[c]) for c in df.columns}
                sub = normalize_submission(raw.get("Submission #") or raw.get("Submission Number"))

                if sub:
                    save_row(sub, raw)
                    count += 1
                else:
                    skipped += 1

            return page(f"""
            <div class="card">
                <h2>Excel uploaded</h2>
                <p>Rows processed: {count}</p>
                <p>Rows skipped: {skipped}</p>
                <a href="/admin">Back to Admin</a>
            </div>
            """)

        except Exception:
            return page(f"""
            <div class="card">
                <h2>Excel Upload Error</h2>
                <pre>{traceback.format_exc()}</pre>
                <a href="/admin/upload">Try again</a>
            </div>
            """)

    return page("""
    <div class="card">
        <h2>Upload Excel / CSV</h2>
        <form method="post" enctype="multipart/form-data">
            <input type="file" name="file">
            <button>Upload Excel</button>
        </form>
    </div>
    """)

@app.route("/admin/upload_psa", methods=["GET", "POST"])
@admin_required
def admin_upload_psa():
    if request.method == "POST":
        try:
            import tempfile

            file = request.files.get("file")

            if not file:
                return page("<div class='card'>No PDF uploaded.</div>")

            filename = (file.filename or "").lower()

            if not filename.endswith(".pdf"):
                return page(f"""
                <div class="card">
                    <h2>Wrong File Type</h2>
                    <p>You uploaded: <b>{filename}</b></p>
                    <p>This uploader only accepts PDF files from PSA.</p>
                    <p>If you uploaded a PSD/image by mistake, export or print the PSA Orders page as a PDF first.</p>
                    <a href="/admin/upload_psa">Back to PDF Upload</a>
                </div>
                """)

            temp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            file.save(temp.name)

            best = {}
            ac_map = {}
            pdf_text_parts = []
            pages_read = 0

            status_regex = re.compile(
                r"(Order\s+Arrived|Research\s*&\s*ID|Grading|QA\s+Checks|Assembly|Shipping\s+Soon|Complete)",
                re.IGNORECASE
            )

            def find_status(text_value):
                match = status_regex.search(text_value or "")
                if not match:
                    return None
                return normalize_psa_status(match.group(1))

            def extract_arrived_completed_from_full_text(text_value):
                found = {}

                normalized = re.sub(r"\s+", " ", text_value or "").strip()
                normalized = re.sub(r",\s+(\d{4})", r", \1", normalized)

                date_pattern = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}"
                value_pattern = re.compile(
                    rf"(Completed\s+{date_pattern}|Est\.\s*by\s+{date_pattern}|{date_pattern})",
                    re.IGNORECASE
                )

                sub_matches = list(re.finditer(r"Sub\s*#\s*(\d+)", normalized, re.IGNORECASE))

                for idx, sub_match in enumerate(sub_matches):
                    sub = normalize_submission(sub_match.group(1))
                    if not sub:
                        continue

                    start = sub_match.end()
                    end = sub_matches[idx + 1].start() if idx + 1 < len(sub_matches) else len(normalized)
                    block = normalized[start:end]

                    matches = value_pattern.findall(block)

                    if matches:
                        cleaned_matches = []
                        seen_matches = set()

                        for m in matches:
                            m = re.sub(r"\s+", " ", m).strip()
                            key = m.lower()

                            if key not in seen_matches:
                                seen_matches.add(key)
                                cleaned_matches.append(m)

                        found[sub] = " | ".join(cleaned_matches)

                return found

            def parse_text(text):
                nonlocal best

                lines = [line.strip() for line in (text or "").splitlines()]
                i = 0

                while i < len(lines):
                    line = lines[i]
                    sub = None
                    search_parts = []

                    # Normal case:
                    # Sub #14577350
                    # Research & ID
                    sub_match = re.search(r"Sub\s*#\s*(\d+)", line, re.IGNORECASE)

                    if sub_match:
                        sub = normalize_submission(sub_match.group(1))

                        after_sub_text = line[sub_match.end():].strip()
                        if after_sub_text:
                            search_parts.append(after_sub_text)

                        j = i + 1
                        while j < len(lines):
                            next_line = lines[j].strip()

                            # Stop if the next submission starts before a status is found.
                            if re.search(r"Sub\s*#\s*\d+", next_line, re.IGNORECASE):
                                break

                            if next_line:
                                search_parts.append(next_line)

                            j += 1

                    # Split case:
                    # • Sub
                    # #14550482
                    # Research & ID
                    elif re.search(r"\bSub\b\s*$", line, re.IGNORECASE) and i + 1 < len(lines):
                        number_match = re.search(r"#\s*(\d+)", lines[i + 1], re.IGNORECASE)

                        if number_match:
                            sub = normalize_submission(number_match.group(1))

                            j = i + 2
                            while j < len(lines):
                                next_line = lines[j].strip()

                                if re.search(r"Sub\s*#\s*\d+", next_line, re.IGNORECASE):
                                    break

                                if next_line:
                                    search_parts.append(next_line)

                                j += 1

                    if sub:
                        status = None

                        # The actual PSA row status is the first valid status after the submission number.
                        for part in search_parts:
                            status = find_status(part)
                            if status:
                                break

                        if status:
                            best[sub] = status
                            # Extract Arrived / Completed
                            block_text = " ".join(search_parts)

                            # Fix broken PDF line breaks (Apr 22, \n 2026)
                            block_text = re.sub(r",\s+(\d{4})", r", \1", block_text)

                            matches = re.findall(
                                r"(Completed\s+[A-Za-z]{3}\s+\d{1,2},\s+\d{4}|Est\.\s+by\s+[A-Za-z]{3}\s+\d{1,2},\s+\d{4}|[A-Za-z]{3}\s+\d{1,2},\s+\d{4})",
                                block_text
                            )

                            if matches:
                                ac_map[sub] = " | ".join(matches)

                    i += 1

            try:
                # Fast path: PyMuPDF reads this PSA PDF much more reliably than pdfplumber.
                # Fallback keeps the route working if PyMuPDF is not installed on the host.
                try:
                    import fitz

                    doc = fitz.open(temp.name)
                    pages_read = len(doc)

                    for pdf_page in doc:
                        try:
                            text = pdf_page.get_text("text") or ""
                        except Exception:
                            continue

                        if text:
                            pdf_text_parts.append(text)
                            parse_text(text)

                    doc.close()

                except Exception:
                    import pdfplumber

                    with pdfplumber.open(temp.name) as pdf:
                        for pdf_page in pdf.pages:
                            pages_read += 1

                            try:
                                text = pdf_page.extract_text() or ""
                            except Exception:
                                continue

                            if text:
                                pdf_text_parts.append(text)
                                parse_text(text)

            finally:
                try:
                    os.unlink(temp.name)
                except Exception:
                    pass

            # Full-text pass for Arrived / Completed.
            # This is independent from status updates so protected/skipped statuses do not block the date field.
            full_text_ac_map = extract_arrived_completed_from_full_text("\n".join(pdf_text_parts))
            for ac_sub, ac_value in full_text_ac_map.items():
                if ac_value:
                    ac_map[ac_sub] = ac_value

            conn = get_conn()
            cur = conn.cursor()

            updated = 0
            skipped = 0

            for sub, status in best.items():
                cur.execute("""
                UPDATE submissions
                SET status=%s,
                    raw_data = jsonb_set(
                        COALESCE(raw_data, '{}'::jsonb),
                        '{Arrived / Completed}',
                        to_jsonb(%s::text),
                        true
                    ),
                    last_updated=NOW()
                WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
                  AND COALESCE(status, '') NOT IN ('Picked Up', 'Delivered to Us')
                """, (status, parse_arrived_completed_value(ac_map.get(sub, ""))["display"], sub))

                if cur.rowcount:
                    updated += 1
                else:
                    skipped += 1

            for sub, arrived_completed_value in ac_map.items():
                if arrived_completed_value:
                    parsed_ac = parse_arrived_completed_value(arrived_completed_value)

                    cur.execute("""
                    UPDATE submissions
                    SET raw_data =
                        jsonb_set(
                            jsonb_set(
                                COALESCE(raw_data, '{}'::jsonb),
                                '{Arrived / Completed}',
                                to_jsonb(%s::text),
                                true
                            ),
                            '{Estimated Completion Date}',
                            to_jsonb(%s::text),
                            true
                        ),
                        last_updated=NOW()
                    WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
                    """, (parsed_ac["display"], parsed_ac["estimated"], sub))

            conn.commit()

            verification_rows = []
            mismatch_count = 0
            checked_count = 0

            # Show the first 150 parsed submissions so verification is clearly visible after upload.
            for sub, parsed_status in list(best.items())[:150]:
                cur.execute("""
                SELECT status, COALESCE(raw_data->>'Arrived / Completed', ''), COALESCE(raw_data->>'Estimated Completion Date', '') FROM submissions
                WHERE REGEXP_REPLACE(submission_number, '\D', '', 'g')=%s
                """, (sub,))

                row = cur.fetchone()
                db_status = row[0] if row else "NOT FOUND"
                db_arrived_completed = row[1] if row else ""
                db_estimated_completion = row[2] if row else ""
                is_match = (db_status == parsed_status)
                match = "MATCH" if is_match else "MISMATCH"

                checked_count += 1
                if not is_match:
                    mismatch_count += 1

                result_style = "color:#198754;font-weight:bold;" if is_match else "color:#dc3545;font-weight:bold;"

                verification_rows.append(
                    f"<tr>"
                    f"<td>{sub}</td>"
                    f"<td>{parsed_status}</td>"
                    f"<td>{db_status}</td>"
                    f"<td>{db_arrived_completed}</td>"
                    f"<td>{db_estimated_completion}</td>"
                    f"<td style='{result_style}'>{match}</td>"
                    f"</tr>"
                )

            cur.close()
            conn.close()

            warning = ""
            if len(best) == 0:
                warning += """
                <p><b>Warning:</b> No PSA statuses were found. This usually means the PDF is not the PSA Orders page, or the PDF is image-only / unreadable text.</p>
                """

            if verification_rows:
                verification_html = "".join(verification_rows)
            else:
                verification_html = """
                <tr>
                    <td colspan="4">No verification rows were created because no matching parsed submissions were found.</td>
                </tr>
                """

            return page(f"""
            <div class="card" style="border:3px solid #198754;">
                <h2>PDF processed</h2>
                {warning}
                <p><b>Pages read:</b> {pages_read}</p>
                <p><b>Statuses found:</b> {len(best)}</p>
                <p><b>Updated:</b> {updated}</p>
                <p><b>Skipped:</b> {skipped}</p>

                <hr>

                <h2 style="color:#0f5132;">VERIFICATION RESULTS</h2>
                <p><b>Verification rows shown:</b> {checked_count}</p>
                <p><b>Mismatches in sample:</b> {mismatch_count}</p>
                <p>This compares what the PDF parser read against what is actually stored in the database after upload.</p>

                <table>
                    <tr>
                        <th>Submission #</th>
                        <th>PDF Parsed Status</th>
                        <th>Database Status After Upload</th>
                        <th>Arrived / Completed</th>
                        <th>Estimated Completion Date</th>
                        <th>Result</th>
                    </tr>
                    {verification_html}
                </table>

                <br>
                <a class="btn" href="/admin">Back to Admin</a>
            </div>
            """)

        except Exception:
            return page(f"""
            <div class="card">
                <h2>PDF Upload Error</h2>
                <p>The file could not be processed.</p>
                <pre>{traceback.format_exc()}</pre>
                <a href="/admin/upload_psa">Try again</a>
            </div>
            """)

    return page("""
    <div class="card">
        <h2>Upload PSA PDF</h2>
        <form method="post" enctype="multipart/form-data">
            <input type="file" name="file" accept=".pdf,application/pdf">
            <button>Upload PDF</button>
        </form>
    </div>
    """)
# =========================
# CUSTOMER PORTAL
# =========================
@app.route("/portal", methods=["GET", "POST"])
def portal():
    if request.method == "POST":
        session["phone"] = normalize_phone(request.form.get("phone"))
        session["last"] = clean(request.form.get("last")).lower()
        return redirect("/portal/orders")

    return page("""
    <div class="card" style="max-width:420px">
        <h2>Customer Portal</h2>
        <p>Enter your phone number and last name.</p>
        <form method="post">
            <input name="phone" placeholder="Phone number" style="width:95%"><br>
            <input name="last" placeholder="Last name" style="width:95%"><br>
            <button>View My Orders</button>
        </form>
    </div>
    """, mode="portal")

@app.route("/portal/orders")
def portal_orders():
    phone = normalize_phone(session.get("phone"))
    last = clean(session.get("last")).lower()

    if not phone or not last:
        return redirect("/portal")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT raw_data, status FROM submissions ORDER BY last_updated DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    selected_view = request.args.get("view", "active")
    selected_status = request.args.get("status", "all").replace("+", " ")

    html = "<h2>Your Orders</h2>"
    grouped = {}

    for r in rows:
        data = r[0] or {}
        name = str(get_field(data, ["Customer Name", "Name"])).lower()
        contact = normalize_phone(get_field(data, ["Contact Info", "Phone", "Phone Number"]))
        sub = normalize_submission(get_field(data, ["Submission #", "Submission Number"]))

        phone_match = bool(contact) and (phone in contact or contact in phone)
        name_match = bool(last) and last in name

        if phone_match and name_match and sub and sub not in grouped:
            grouped[sub] = (data, r[1] or "Submitted")

    if not grouped:
        html += "<div class='card'>No matching orders found. Check phone number and last name.</div>"
        return page(html, mode="portal")

    statuses_available = sorted(set([customer_status_label(status) for _, status in grouped.values()]))

    def selected(option_value, current_value):
        return "selected" if option_value == current_value else ""

    html += """
    <div class="filterbar">
        <form method="get" action="/portal/orders">
            <div>
                <label for="view">View</label>
                <select id="view" name="view">
    """
    html += f"<option value='active' {selected('active', selected_view)}>Active Orders</option>"
    html += f"<option value='completed' {selected('completed', selected_view)}>Completed / Picked Up</option>"
    html += f"<option value='all' {selected('all', selected_view)}>All Orders</option>"
    html += """
                </select>
            </div>
            <div>
                <label for="status">Status</label>
                <select id="status" name="status">
    """
    html += f"<option value='all' {selected('all', selected_status)}>All Statuses</option>"

    for status_option in statuses_available:
        html += f"<option value='{status_option}' {selected(status_option, selected_status)}>{status_option}</option>"

    html += """
                </select>
            </div>
            <button type="submit">Apply Filters</button>
            <a class="reset-link" href="/portal/orders?view=active&status=all">Reset</a>
        </form>
    </div>
    """

    completed_statuses = set(["Complete", "Delivered to Us", "Picked Up"])
    filtered_grouped = {}

    for sub, (data, status) in grouped.items():
        internal_status = status or "Submitted"
        label_status = customer_status_label(internal_status)

        if selected_view == "active" and internal_status in completed_statuses:
            continue

        if selected_view == "completed" and internal_status not in completed_statuses:
            continue

        if selected_status != "all" and label_status != selected_status:
            continue

        filtered_grouped[sub] = (data, status)

    if not filtered_grouped:
        html += "<div class='card'>No submissions match the selected filters.</div>"
        html += "<a href='/portal/logout'>Log out</a>"
        return page(html, mode="portal")

    for sub, (data, status) in filtered_grouped.items():
        customer_name = get_field(data, ["Customer Name", "Name"])
        cards = get_field(data, ["# Of Cards", "# of Cards", "Cards"])
        service = clean_service_display(get_field(data, ["Service Type", "Service"]))
        date = get_field(data, ["Customer Drop-Off Date", "S", "Submission Date", "Date"])
        arrived_completed_raw = get_field(data, ["Arrived / Completed"])
        arrived_completed_data = parse_arrived_completed_value(arrived_completed_raw)
        arrived_completed = arrived_completed_data["display"]
        estimated_completion = get_field(data, ["Estimated Completion Date"]) or arrived_completed_data["estimated"]
        display_status = status or "Submitted"
        display_status_label = customer_status_label(display_status)

        html += f"""
        <div class="card">
            <h3>{customer_name}</h3>
            <p><b>Submission #:</b> {sub}</p>
            <p><b>Status:</b> <span class="status">{display_status_label}</span></p>
            <p><b>Arrived / Completed:</b> {arrived_completed}</p>
            <p><b>Estimated Completion Date:</b> {estimated_completion}</p>
            <p><b>Cards:</b> {cards}</p>
            <p><b>Service:</b> {service}</p>
            <p><b>Customer Drop-Off Date:</b> {date}</p>
            {status_bar(display_status)}
        </div>
        """

    html += "<a href='/portal/logout'>Log out</a>"
    return page(html, mode="portal")

@app.route("/portal/logout")
def portal_logout():
    session.pop("phone", None)
    session.pop("last", None)
    return redirect("/portal")

if __name__ == "__main__":
    app.run()
