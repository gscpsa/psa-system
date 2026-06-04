from flask import Flask, request, session, redirect
import pandas as pd
import psycopg2
import os, io, json, re, traceback, base64
from functools import wraps

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
PUBLIC_PORTAL_URL = os.getenv("PUBLIC_PORTAL_URL", "https://psa.giantsportscards.com")
SMS_PROVIDER = os.getenv("SMS_PROVIDER", "queue_only").lower()
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")

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

    cur.execute("""
    ALTER TABLE submissions
    ADD COLUMN IF NOT EXISTS sms_opt_in BOOLEAN DEFAULT FALSE
    """)

    cur.execute("""
    ALTER TABLE submissions
    ADD COLUMN IF NOT EXISTS sms_pickup_only BOOLEAN DEFAULT TRUE
    """)

    cur.execute("""
    ALTER TABLE submissions
    ADD COLUMN IF NOT EXISTS sms_mode TEXT DEFAULT 'none'
    """)

    cur.execute("""
    ALTER TABLE submissions
    ADD COLUMN IF NOT EXISTS last_sms_status TEXT
    """)

    cur.execute("""
    ALTER TABLE submissions
    ADD COLUMN IF NOT EXISTS last_sms_sent TIMESTAMP
    """)

    cur.execute("""
    ALTER TABLE submissions
    ADD COLUMN IF NOT EXISTS card_pdf_uploaded_at TIMESTAMP
    """)

    cur.execute("""
    ALTER TABLE submissions
    ADD COLUMN IF NOT EXISTS card_pdf_order_number TEXT
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sms_notifications (
        id SERIAL PRIMARY KEY,
        submission_number TEXT,
        phone TEXT,
        old_status TEXT,
        new_status TEXT,
        message TEXT,
        send_status TEXT DEFAULT 'Queued',
        provider_response TEXT,
        created_at TIMESTAMP DEFAULT NOW(),
        sent_at TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS card_buyback_items (
        id SERIAL PRIMARY KEY,
        submission_number TEXT NOT NULL,
        cert_number TEXT NOT NULL,
        item_details TEXT,
        grade TEXT,
        image_data TEXT,
        interested BOOLEAN DEFAULT FALSE,
        buyback_status TEXT DEFAULT 'New',
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW(),
        UNIQUE (submission_number, cert_number)
    )
    """)

    cur.execute("""
    ALTER TABLE card_buyback_items
    ADD COLUMN IF NOT EXISTS buyback_status TEXT DEFAULT 'New'
    """)

    cur.execute("""
    ALTER TABLE card_buyback_items
    ADD COLUMN IF NOT EXISTS card_type TEXT
    """)

    cur.execute("""
    ALTER TABLE card_buyback_items
    ADD COLUMN IF NOT EXISTS description TEXT
    """)

    cur.execute("""
    ALTER TABLE card_buyback_items
    ADD COLUMN IF NOT EXISTS after_service TEXT
    """)

    cur.execute("""
    ALTER TABLE card_buyback_items
    ADD COLUMN IF NOT EXISTS images_url TEXT
    """)

    cur.execute("""
    ALTER TABLE card_buyback_items
    ADD COLUMN IF NOT EXISTS psa_estimate TEXT
    """)

    cur.execute("""
    ALTER TABLE card_buyback_items
    ADD COLUMN IF NOT EXISTS card_ladder_value TEXT
    """)

    cur.execute("""
    ALTER TABLE card_buyback_items
    ADD COLUMN IF NOT EXISTS pop TEXT
    """)

    cur.execute("""
    ALTER TABLE card_buyback_items
    ADD COLUMN IF NOT EXISTS pop_higher TEXT
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
    details = traceback.format_exc()
    try:
        return page(f"""
        <div class="card">
            <h2>Application Error</h2>
            <p>The app hit an internal error. Details below:</p>
            <pre>{html_escape(details)}</pre>
            <a href="/admin">Back to Admin</a>
        </div>
        """), 500
    except Exception:
        safe_details = str(details).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return "<pre>" + safe_details + "</pre>", 500

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

def display_blank_loading(value):
    text = str(value or "").strip()
    if text.lower() in ["loading", "loadin"]:
        return ""
    if text.lower().startswith("loading"):
        return ""
    return text

def normalize_submission(v):
    if not v:
        return None
    return re.sub(r"\D", "", str(v).split(".")[0])

def normalize_phone(v):
    return re.sub(r"\D", "", str(v or ""))


def normalize_key_text(value):
    text = str(value or "").strip().lower()
    text = text.replace("ƒ", "f")
    text = text.replace("æ", "f")
    text = text.replace("â\x80\x99", "'")
    text = text.replace(".", "")
    text = re.sub(r"\s+", " ", text)
    return text

def is_dropoff_date_key(key):
    normalized = normalize_key_text(key)

    if normalized in [
        "fand",
        "and",
        "submission date",
        "customer drop-off date",
        "customer drop off date",
        "drop-off date",
        "drop off date",
        "date",
        "s"
    ]:
        return True

    # Some Excel exports produce broken short text around the original date field.
    if "fand" in normalized:
        return True

    if normalized.startswith("f") and "and" in normalized and len(normalized) <= 12:
        return True

    return False


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

def psa_status_steps():
    return [
        "Submitted",
        "Order Arrived",
        "Research & ID",
        "Grading",
        "Assembly",
        "QA Checks",
        "Shipping Soon",
        "Complete",
        "Delivered to Us",
        "Picked Up"
    ]

def customer_status_options():
    return [customer_status_label(s) for s in psa_status_steps()]

def clean_service_display(service):
    value = str(service or "").strip()
    if " - " in value:
        return value.split(" - ", 1)[0].strip()
    if " – " in value:
        return value.split(" – ", 1)[0].strip()
    return value

def date_only_display(value):
    text = str(value or "").strip()

    if not text:
        return ""

    try:
        parsed = pd.to_datetime(text, errors="coerce")
        if not pd.isna(parsed):
            return parsed.strftime("%Y-%m-%d")
    except Exception:
        pass

    # Fallback for values like "2026-03-31 00:00:00"
    if " " in text:
        possible_date = text.split(" ", 1)[0].strip()
        if re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", possible_date):
            return possible_date

    return text

def get_dropoff_date(data):
    # First use known normal names.
    value = get_field(data, [
        "Customer Drop-Off Date",
        "Customer Drop Off Date",
        "Submission Date",
        "ƒand",
        "ƒand.",
        "Æand",
        "Æand.",
        "fand",
        "Fand",
        "S",
        "s",
        "Date",
        "date"
    ])

    if value:
        return date_only_display(value)

    # Then scan every raw key because the Excel column may be mojibake.
    for k, v in (data or {}).items():
        if is_dropoff_date_key(k):
            return date_only_display(v)

    return ""


def parse_arrived_completed_value(value):
    text = str(value or "").strip()
    result = {"arrived": "", "estimated": "", "completed": "", "display": text}

    if not text:
        return result

    month = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|January|February|March|April|May|June|July|August|September|October|November|December)"
    date_single = month + r"\s+\d{1,2},\s+\d{4}"
    date_range_same_year = month + r"\s+\d{1,2}\s*[-–]\s*" + month + r"?\s*\d{1,2},\s+\d{4}"
    date_range_full = date_single + r"\s*[-–]\s*" + date_single

    completed_match = re.search(r"Completed\s+(" + date_single + r")", text, re.IGNORECASE)
    if completed_match:
        result["completed"] = re.sub(r"\s+", " ", completed_match.group(1)).strip()

    estimated_patterns = [
        r"Est\.\s*Complete\s*by\s+(" + date_range_full + r")",
        r"Est\.\s*Complete\s*by\s+(" + date_range_same_year + r")",
        r"Est\.\s*Complete\s*by\s+(" + date_single + r")",
        r"Estimated\s*Complete\s*by\s+(" + date_range_full + r")",
        r"Estimated\s*Complete\s*by\s+(" + date_range_same_year + r")",
        r"Estimated\s*Complete\s*by\s+(" + date_single + r")",
        r"Est\.\s*by\s+(" + date_range_full + r")",
        r"Est\.\s*by\s+(" + date_range_same_year + r")",
        r"Est\.\s*by\s+(" + date_single + r")",
        r"Estimated\s*Completion\s*Date\s*:?\s*(" + date_range_full + r")",
        r"Estimated\s*Completion\s*Date\s*:?\s*(" + date_range_same_year + r")",
        r"Estimated\s*Completion\s*Date\s*:?\s*(" + date_single + r")",
    ]

    for estimated_pattern in estimated_patterns:
        estimated_match = re.search(estimated_pattern, text, re.IGNORECASE)
        if estimated_match:
            result["estimated"] = re.sub(r"\s+", " ", estimated_match.group(1)).strip()
            break

    first_date_match = re.search(date_single, text, re.IGNORECASE)
    if first_date_match:
        first_date = re.sub(r"\s+", " ", first_date_match.group(0)).strip()
        if first_date != result["completed"] and first_date != result["estimated"]:
            estimated_start = result["estimated"].split("-")[0].strip() if result["estimated"] else ""
            if not estimated_start or first_date.lower() != estimated_start.lower():
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


def html_escape(value):
    text = str(value or "")
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
    )

def sms_status_is_textable(new_status, sms_mode="pickup"):
    new_status = new_status or ""

    # Backward compatibility: old code passed pickup_only as True/False.
    if sms_mode is True:
        sms_mode = "pickup"
    elif sms_mode is False:
        sms_mode = "all"

    sms_mode = str(sms_mode or "none").lower()

    if sms_mode == "none":
        return False

    if sms_mode == "pickup":
        return new_status == "Delivered to Us"

    if sms_mode == "all":
        return new_status not in ["", "Submitted", "Picked Up"]

    return False

def build_sms_message(submission_number, old_status, new_status):
    display_new = customer_status_label(new_status)
    display_old = customer_status_label(old_status) if old_status else ""

    if new_status == "Delivered to Us":
        return (
            f"Giant Sports Cards: Your PSA submission #{submission_number} "
            f"is ready for pickup. Track it here: {PUBLIC_PORTAL_URL}"
        )

    if display_old:
        return (
            f"Giant Sports Cards: Your PSA submission #{submission_number} "
            f"moved from {display_old} to {display_new}. "
            f"Track it here: {PUBLIC_PORTAL_URL}"
        )

    return (
        f"Giant Sports Cards: Your PSA submission #{submission_number} "
        f"status is now {display_new}. Track it here: {PUBLIC_PORTAL_URL}"
    )

def send_sms_or_queue(submission_number, phone, old_status, new_status, message):
    send_status = "Queued"
    provider_response = ""

    if SMS_PROVIDER == "twilio" and TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER:
        try:
            from twilio.rest import Client
            client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            sms = client.messages.create(
                body=message,
                from_=TWILIO_FROM_NUMBER,
                to=phone
            )
            send_status = "Sent"
            provider_response = getattr(sms, "sid", "")
        except Exception:
            send_status = "Error"
            provider_response = traceback.format_exc()

    return send_status, provider_response

def maybe_queue_status_sms(cur, submission_number, phone, old_status, new_status, sms_opt_in, sms_mode, last_sms_status):
    sms_mode = str(sms_mode or "none").lower()

    if not sms_opt_in or sms_mode == "none":
        return False

    if not phone:
        return False

    if not new_status:
        return False

    if old_status == new_status:
        return False

    if last_sms_status == new_status:
        return False

    if not sms_status_is_textable(new_status, sms_mode):
        return False

    message = build_sms_message(submission_number, old_status, new_status)
    send_status, provider_response = send_sms_or_queue(submission_number, phone, old_status, new_status, message)

    cur.execute("""
    INSERT INTO sms_notifications
        (submission_number, phone, old_status, new_status, message, send_status, provider_response, sent_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, CASE WHEN %s='Sent' THEN NOW() ELSE NULL END)
    """, (submission_number, phone, old_status, new_status, message, send_status, provider_response, send_status))

    cur.execute("""
    UPDATE submissions
    SET last_sms_status=%s,
        last_sms_sent=CASE WHEN %s='Sent' THEN NOW() ELSE last_sms_sent END
    WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
    """, (new_status, send_status, submission_number))

    return True


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
        "Assembly": 4,
        "QA Checks": 5,
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
        <a href="/admin/upload_cards">Cards PDF</a>
        <a href="/admin/buyback_requests">Buyback</a>
        <a href="/admin/sms_notifications">SMS Queue</a>
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
            font-size:24px;
            display:flex;
            align-items:center;
            gap:12px;
            min-width:360px;
        }}
        .brand img {{
            max-height:135px;
            max-width:420px;
            width:auto;
            display:block;
        }}
        .brand span {{
            white-space:nowrap;
            font-size:24px;
            font-weight:900;
            letter-spacing:.2px;
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
        .filterbar .reset-link.active {{
            background:#198754;
            color:white;
        }}
        .card-grid {{
            display:grid;
            grid-template-columns:repeat(auto-fill, minmax(230px, 1fr));
            gap:14px;
            margin-top:12px;
        }}
        .buy-card {{
            background:#ffffff;
            border:1px solid #e5e7eb;
            border-radius:12px;
            padding:12px;
            box-shadow:0 2px 8px rgba(0,0,0,.06);
        }}
        .buy-card img {{
            width:100%;
            max-height:260px;
            object-fit:contain;
            background:#f9fafb;
            border-radius:8px;
            margin-bottom:10px;
        }}
        .buy-card .cert {{
            font-weight:bold;
            color:#0f5132;
        }}

        .buyback-collapsible {{
            margin-top:16px;
            border:1px solid #e5e7eb;
            border-radius:12px;
            background:#ffffff;
            overflow:hidden;
        }}
        .buyback-collapsible summary {{
            cursor:pointer;
            padding:14px 16px;
            background:#f3f7f5;
            color:#0f5132;
            font-weight:900;
            font-size:17px;
            list-style:none;
            display:flex;
            justify-content:space-between;
            align-items:center;
            gap:12px;
        }}
        .buyback-collapsible summary::-webkit-details-marker {{
            display:none;
        }}
        .buyback-collapsible summary:after {{
            content:"+";
            font-size:24px;
            font-weight:900;
            color:#198754;
        }}
        .buyback-collapsible[open] summary:after {{
            content:"–";
        }}
        .buyback-collapsible .buyback-inner {{
            padding:14px;
        }}

        .sell-check {{
            display:flex;
            align-items:center;
            gap:8px;
            margin-top:10px;
            font-weight:bold;
        }}
        .sell-check input {{
            width:20px;
            height:20px;
            margin:0;
        }}
        body.portal-body {{
            background:#f4f6f8;
        }}

        body.portal-body .container {{
            background:#f4f6f8;
            max-width:100%;
            width:100%;
            padding:0;
            margin:0;
            overflow-x:hidden;
            box-sizing:border-box;
        }}

        body.portal-body .gsc-portal-wrap {{
            background:#f4f6f8;
            width:100%;
            max-width:100%;
            padding:48px 18px 58px;
            margin:0;
            box-sizing:border-box;
        }}

        body.portal-body .gsc-portal-card {{
            margin:0 auto;
            box-sizing:border-box;
        }}

        body.portal-body .gsc-benefits {{
            background:#f4f6f8;
            width:100%;
            max-width:100%;
            margin:0;
            box-sizing:border-box;
        }}

        body.portal-body .safe-portal-wrap {{
            background:#f4f6f8;
            width:100%;
            max-width:100%;
            padding:48px 18px 58px;
            margin:0;
            box-sizing:border-box;
        }}

        body.portal-body .safe-benefits {{
            background:#f4f6f8;
            width:100%;
            max-width:100%;
            margin:0;
            box-sizing:border-box;
        }}



        .admin-section-title {{
            font-size:22px;
            margin:18px 0 10px;
            color:#111827;
        }}

        .compact-table th:nth-child(1), .compact-table td:nth-child(1) {{ width:105px; }}
        .compact-table th:nth-child(2), .compact-table td:nth-child(2) {{ width:145px; }}
        .compact-table th:nth-child(3), .compact-table td:nth-child(3) {{ width:135px; }}
        .compact-table th:nth-child(4), .compact-table td:nth-child(4) {{ width:70px; }}
        .compact-table th:nth-child(5), .compact-table td:nth-child(5) {{ width:135px; }}
        .compact-table th:nth-child(6), .compact-table td:nth-child(6) {{ width:120px; }}
        .compact-table th:nth-child(7), .compact-table td:nth-child(7) {{ width:110px; }}

        .details-cell {{
            white-space:normal;
            max-width:260px;
            color:#374151;
            line-height:1.35;
        }}

        details.row-details summary {{
            cursor:pointer;
            color:#0f5132;
            font-weight:bold;
            display:inline-block;
            padding:3px 6px;
            border-radius:6px;
            background:#eef6f2;
        }}

        details.row-details div {{
            margin-top:7px;
            background:#f9fafb;
            border:1px solid #e5e7eb;
            border-radius:8px;
            padding:8px;
            white-space:normal;
            max-width:320px;
        }}


        /* Customer portal bottom feature text readability fix */
        .gsc-benefit-title {{
            color:#0f5132 !important;
            font-weight:900 !important;
        }}
        .gsc-benefit-text {{
            color:#111827 !important;
            font-weight:600 !important;
        }}
        .safe-benefit-title {{
            color:#0f5132 !important;
            font-weight:900 !important;
        }}
        .safe-benefit-text {{
            color:#111827 !important;
            font-weight:600 !important;
        }}
        .portal-feature-title {{
            color:#0f5132 !important;
            font-weight:900 !important;
        }}
        .portal-feature-text {{
            color:#111827 !important;
            font-weight:600 !important;
        }}

        @media (max-width: 700px) {{
            .topbar {{
                align-items:flex-start;
            }}
            .brand {{
                min-width:100%;
            }}
            .brand img {{
                max-height:105px;
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
    <body class="{mode}-body">
        <div class="topbar">
            <div class="brand"><img src="data:image/png;base64,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Y0hSTQAAeiYAAICEAAD6AAAAgOgAAHUwAADqYAAAOpgAABdwnLpRPAAAAAlwSFlzAAAuIwAALiMBeKU/dgAABR1pVFh0WE1MOmNvbS5hZG9iZS54bXAAAAAAADw/eHBhY2tldCBiZWdpbj0i77u/IiBpZD0iVzVNME1wQ2VoaUh6cmVTek5UY3prYzlkIj8+IDx4OnhtcG1ldGEgeG1sbnM6eD0iYWRvYmU6bnM6bWV0YS8iIHg6eG1wdGs9IkFkb2JlIFhNUCBDb3JlIDkuMS1jMDAxIDc5LmE4ZDQ3NTM0OSwgMjAyMy8wMy8yMy0xMzowNTo0NSAgICAgICAgIj4gPHJkZjpSREYgeG1sbnM6cmRmPSJodHRwOi8vd3d3LnczLm9yZy8xOTk5LzAyLzIyLXJkZi1zeW50YXgtbnMjIj4gPHJkZjpEZXNjcmlwdGlvbiByZGY6YWJvdXQ9IiIgeG1sbnM6eG1wPSJodHRwOi8vbnMuYWRvYmUuY29tL3hhcC8xLjAvIiB4bWxuczpkYz0iaHR0cDovL3B1cmwub3JnL2RjL2VsZW1lbnRzLzEuMS8iIHhtbG5zOnBob3Rvc2hvcD0iaHR0cDovL25zLmFkb2JlLmNvbS9waG90b3Nob3AvMS4wLyIgeG1sbnM6eG1wTU09Imh0dHA6Ly9ucy5hZG9iZS5jb20veGFwLzEuMC9tbS8iIHhtbG5zOnN0RXZ0PSJodHRwOi8vbnMuYWRvYmUuY29tL3hhcC8xLjAvc1R5cGUvUmVzb3VyY2VFdmVudCMiIHhtcDpDcmVhdG9yVG9vbD0iQWRvYmUgUGhvdG9zaG9wIDI0LjYgKE1hY2ludG9zaCkiIHhtcDpDcmVhdGVEYXRlPSIyMDIzLTEwLTE2VDE3OjAzOjM2LTA0OjAwIiB4bXA6TW9kaWZ5RGF0ZT0iMjAyMy0xMC0xNlQxNzowOTowOC0wNDowMCIgeG1wOk1ldGFkYXRhRGF0ZT0iMjAyMy0xMC0xNlQxNzowOTowOC0wNDowMCIgZGM6Zm9ybWF0PSJpbWFnZS9wbmciIHBob3Rvc2hvcDpDb2xvck1vZGU9IjMiIHBob3Rvc2hvcDpJQ0NQcm9maWxlPSJzUkdCIElFQzYxOTY2LTIuMSIgeG1wTU06SW5zdGFuY2VJRD0ieG1wLmlpZDpjMTkwMjJiZC1jNWU5LTRhY2YtYWYwMy0wMzNiODBmOGVlZWMiIHhtcE1NOkRvY3VtZW50SUQ9InhtcC5kaWQ6YzE5MDIyYmQtYzVlOS00YWNmLWFmMDMtMDMzYjgwZjhlZWVjIiB4bXBNTTpPcmlnaW5hbERvY3VtZW50SUQ9InhtcC5kaWQ6YzE5MDIyYmQtYzVlOS00YWNmLWFmMDMtMDMzYjgwZjhlZWVjIj4gPHhtcE1NOkhpc3Rvcnk+IDxyZGY6U2VxPiA8cmRmOmxpIHN0RXZ0OmFjdGlvbj0iY3JlYXRlZCIgc3RFdnQ6aW5zdGFuY2VJRD0ieG1wLmlpZDpjMTkwMjJiZC1jNWU5LTRhY2YtYWYwMy0wMzNiODBmOGVlZWMiIHN0RXZ0OndoZW49IjIwMjMtMTAtMTZUMTc6MDM6MzYtMDQ6MDAiIHN0RXZ0OnNvZnR3YXJlQWdlbnQ9IkFkb2JlIFBob3Rvc2hvcCAyNC42IChNYWNpbnRvc2gpIi8+IDwvcmRmOlNlcT4gPC94bXBNTTpIaXN0b3J5PiA8L3JkZjpEZXNjcmlwdGlvbj4gPC9yZGY6UkRGPiA8L3g6eG1wbWV0YT4gPD94cGFja2V0IGVuZD0iciI/PkD4qSIAAEWUSURBVHic7d13uGVXQT7+NyShJSEhIQmElANEICBFpCiEZsEIiIiigEiTooJSFAWCNGkCShEU+UoRRPpPFBREqgJioSO9BAgEk0AKAVJnfn+sjDN35s7Mvefsvdfee30+zzOPkdy795s7+56z37PWXmufrVu3BgAAgPZcpnYAAAAA6lAIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQ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UQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjfr/ARwPrTF8DYWaAAAAAElFTkSuQmCC" alt="Giant Sports Cards"> <span>PSA Submission Tracker</span></div>
            <div class="links">{nav}</div>
        </div>
        <div class="container">{content}</div>
    </body>
    </html>
    """

def status_bar(status):
    steps = psa_status_steps()

    status = status or "Submitted"
    idx = steps.index(status) if status in steps else 0

    html = "<div class='bar'>"
    for i, step in enumerate(steps):
        cls = "step"
        if i < idx:
            cls += " done"
        if i == idx:
            cls += " current"
        html += f"<div class='{cls}'>{customer_status_label(step)}</div>"
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


def status_needs_card_pdf(status):
    return (status or "") in ["Shipping Soon", "Complete"]

def card_pdf_needs_attention(row):
    """
    True when a PSA submission is Shipping Soon or Complete but no card-detail
    PDF records have been uploaded for that submission.
    """
    try:
        data = row_raw_data(row)
        status = row_status(row)

        if not status_needs_card_pdf(status):
            return False

        sub = normalize_submission(get_field(data, ["Submission #", "Submission Number"]))

        if not sub:
            return False

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
        SELECT COUNT(*)
        FROM card_buyback_items
        WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
        """, (sub,))
        count = cur.fetchone()[0] or 0
        cur.close()
        conn.close()

        return count == 0
    except Exception:
        return False

def card_pdf_alert_text(row):
    try:
        status = row[1] or "Submitted"
        if status == "Shipping Soon":
            return "Grades ready / card PDF needed"
        if status == "Complete":
            return "Completed / card PDF needed"
        return "Card PDF needed"
    except Exception:
        return "Card PDF needed"



def row_raw_data(row):
    try:
        return row[0] if len(row) > 0 else {}
    except Exception:
        return {}

def row_status(row):
    try:
        return row[1] if len(row) > 1 else "Submitted"
    except Exception:
        return "Submitted"


def build_table(rows):
    """
    Professional compact admin table.

    Instead of exposing every raw Excel/PSA column like a giant spreadsheet,
    this table shows the operational columns staff actually need.
    Extra/raw fields are placed behind a Details expander.
    """
    if not rows:
        return "<div class='card'>No records found.</div>"

    html = """
    <h3 class="admin-section-title">Submissions</h3>
    <div class='table-wrap'>
    <table class='compact-table'>
        <tr>
            <th>Submission #</th>
            <th>Customer</th>
            <th>Phone</th>
            <th>Cards</th>
            <th>Service</th>
            <th>Status</th>
            <th>Drop-Off</th>
            <th>Est. Complete</th>
            <th>Details</th>
        </tr>
    """

    for row in rows:
        raw_data = row[0] if len(row) > 0 else {}
        status = row[1] if len(row) > 1 else "Submitted"
        data = raw_data or {}

        sub = normalize_submission(get_field(data, ["Submission #", "Submission Number"])) or ""
        customer = get_field(data, ["Customer Name", "Name"])
        phone = get_field(data, ["Contact Info", "Phone", "Phone Number"])
        cards = get_field(data, ["# Of Cards", "# of Cards", "Cards"])
        service = clean_service_display(get_field(data, ["Service Type", "Service"]))
        dropoff = get_dropoff_date(data)
        display_status = customer_status_label(status or "Submitted")

        arrived_completed_raw = get_field(data, ["Arrived / Completed"])
        arrived_completed_data = parse_arrived_completed_value(arrived_completed_raw)
        estimated_completion = get_field(data, ["Estimated Completion Date"]) or arrived_completed_data["estimated"]

        details_parts = []

        for k, v in data.items():
            key_text = str(k).strip()

            if not key_text or "unnamed" in key_text.lower():
                continue

            if should_hide_column(key_text):
                continue

            if is_dropoff_date_key(key_text):
                continue

            normalized_key = key_text.strip().lower()
            if normalized_key in [
                "submission #", "submission number", "customer name", "name",
                "contact info", "phone", "phone number", "# of cards", "# Of Cards".lower(),
                "cards", "service type", "service", "arrived / completed",
                "estimated completion date"
            ]:
                continue

            val = clean(v)
            if val:
                details_parts.append(f"<b>{html_escape(key_text)}:</b> {html_escape(val)}")

        if not details_parts:
            details_html = "<span style='color:#6b7280;'>—</span>"
        else:
            details_html = "<details class='row-details'><summary>Details</summary><div>" + "<br>".join(details_parts[:12]) + "</div></details>"

        html += f"""
        <tr>
            <td><b>{html_escape(sub)}</b></td>
            <td>{html_escape(customer)}</td>
            <td>{html_escape(phone)}</td>
            <td>{html_escape(cards)}</td>
            <td>{html_escape(service)}</td>
            <td class="status">{html_escape(display_status)}</td>
            <td>{html_escape(dropoff)}</td>
            <td>{html_escape(estimated_completion)}</td>
            <td class="details-cell">{details_html}</td>
        </tr>
        """

    html += "</table></div>"
    return html


def get_sort_date(row):
    data = row[0] or {}

    # Use the same drop-off date detector used for portal/display.
    date_value = ""

    for k, v in (data or {}).items():
        if is_dropoff_date_key(k):
            date_value = v
            break

    if not date_value:
        date_value = get_field(data, [
            "Customer Drop-Off Date",
            "Customer Drop Off Date",
            "Submission Date",
            "ƒand",
            "ƒand.",
            "Æand",
            "Æand.",
            "fand",
            "Fand",
            "S",
            "s",
            "Date",
            "date"
        ])

    try:
        if date_value:
            parsed = pd.to_datetime(date_value, errors="coerce")
            if not pd.isna(parsed):
                return parsed
    except Exception:
        pass

    return pd.Timestamp.min



def is_psa_grade_line(line):
    text = re.sub(r"\s+", " ", str(line or "")).strip().upper()
    if not text:
        return False

    qualifiers = r"(?:OC|MC|ST|PD|OF|MK|MKD|QUAL|Q)"
    grade_words = (
        r"POOR|FAIR|GOOD|VERY GOOD\+?|VERY GOOD-EXCELLENT|EXCELLENT|"
        r"EXCELLENT-MINT|NEAR MINT|NEAR MINT-MINT|NM-MT|MINT|GEM MINT|"
        r"AUTHENTIC|PR|FR|GD|VG|VG-EX|EX|EX-MT|NM|MT|GM"
    )

    numeric_grade = re.compile(
        r"^(?:" + grade_words + r")?\s*\d{1,2}(?:\.\d)?(?:\s+" + qualifiers + r")?$",
        re.IGNORECASE
    )

    if numeric_grade.match(text):
        return True

    if re.match(r"^N\d+\s*:\s*.+", text, re.IGNORECASE):
        return True

    return text in [
        "AUTHENTIC",
        "AUTHENTIC ALTERED",
        "MINIMUM SIZE REQUIREMENT",
        "ALTERED STOCK",
        "EVIDENCE OF TRIMMING",
        "QUESTIONABLE AUTHENTICITY",
        "MISCUT",
        "OFF CENTER",
        "STAINING",
        "MARKED"
    ]


def extract_card_items_from_pdf(pdf_path):
    """
    PSA card-detail PDF parser.

    Fixes:
    - supports half grades like 1.5 / 3.5
    - supports qualifiers like OC / MC / ST / PD / MK
    - supports N-grade lines like N6: MINIMUM SIZE REQUIREMENT
    - avoids PSA banner/header images
    - if embedded thumbnail extraction fails, crops the visible left thumbnail itself
      from the rendered PDF row, one card at a time.
    """
    items = []
    submission_number = ""
    order_number = ""

    try:
        import fitz
    except Exception as e:
        raise RuntimeError("PyMuPDF / fitz is required for card PDF import.") from e

    doc = fitz.open(pdf_path)

    def norm_text(value):
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def block_text(block):
        parts = []
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                txt = span.get("text", "")
                if txt:
                    parts.append(txt)
        return norm_text(" ".join(parts))

    def to_data_uri(image_bytes, ext="png"):
        try:
            if not image_bytes:
                return ""
            image_b64 = base64.b64encode(image_bytes).decode("ascii")
            return f"data:image/{ext};base64,{image_b64}"
        except Exception:
            return ""

    def image_data_from_block(block):
        try:
            image_bytes = block.get("image")
            ext = block.get("ext", "png")
            return to_data_uri(image_bytes, ext)
        except Exception:
            return ""

    def is_card_thumbnail_block(block):
        if block.get("type") != 1:
            return False

        bbox = block.get("bbox") or (0, 0, 0, 0)
        x0, y0, x1, y1 = bbox
        box_w = abs(x1 - x0)
        box_h = abs(y1 - y0)

        width_px = int(block.get("width") or 0)
        height_px = int(block.get("height") or 0)

        if width_px <= 0 or height_px <= 0:
            return False

        if not (20 <= x0 <= 160):
            return False
        if not (8 <= box_w <= 110):
            return False
        if not (16 <= box_h <= 130):
            return False
        if width_px > height_px * 1.6:
            return False

        return True

    def crop_thumbnail_from_page(pdf_page, row_y):
        try:
            if row_y is None:
                return ""

            page_rect = pdf_page.rect

            # Wider tolerance because PSA card thumbnail x position changes by print layout.
            # Still only crops the left image column, not the text row.
            x0 = max(0, page_rect.width * 0.055)
            x1 = min(page_rect.width * 0.18, x0 + 95)

            y0 = max(0, row_y - 42)
            y1 = min(page_rect.height, row_y + 42)

            clip = fitz.Rect(x0, y0, x1, y1)
            pix = pdf_page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=clip, alpha=False)

            if pix.width < 40 or pix.height < 60:
                return ""

            return to_data_uri(pix.tobytes("png"), "png")
        except Exception:
            return ""

    all_page_text = []
    page_start_offsets = []
    page_data = []
    running_offset = 0

    for page_index, pdf_page in enumerate(doc):
        try:
            page_text = pdf_page.get_text("text") or ""
        except Exception:
            page_text = ""

        page_start_offsets.append(running_offset)
        all_page_text.append(page_text)
        running_offset += len(page_text) + 1

        text_blocks = []
        image_blocks = []

        try:
            page_dict = pdf_page.get_text("dict")
            for block in page_dict.get("blocks", []):
                bbox = block.get("bbox") or (0, 0, 0, 0)
                x0, y0, x1, y1 = bbox
                y_mid = (y0 + y1) / 2

                if block.get("type") == 0:
                    txt = block_text(block)
                    if txt:
                        text_blocks.append({
                            "text": txt,
                            "bbox": bbox,
                            "x0": x0,
                            "y0": y0,
                            "x1": x1,
                            "y1": y1,
                            "y_mid": y_mid
                        })

                elif is_card_thumbnail_block(block):
                    img_data = image_data_from_block(block)
                    if img_data:
                        image_blocks.append({
                            "image_data": img_data,
                            "bbox": bbox,
                            "x0": x0,
                            "y0": y0,
                            "x1": x1,
                            "y1": y1,
                            "y_mid": y_mid
                        })
        except Exception:
            pass

        text_blocks.sort(key=lambda b: (b["y_mid"], b["x0"]))
        image_blocks.sort(key=lambda b: (b["y_mid"], b["x0"]))

        page_data.append({
            "page": pdf_page,
            "text": page_text,
            "text_blocks": text_blocks,
            "image_blocks": image_blocks
        })

    full_text = "\n".join(all_page_text)

    sub_match = re.search(r"Submission\s*#\s*(\d+)", full_text, re.IGNORECASE)
    if sub_match:
        submission_number = normalize_submission(sub_match.group(1)) or ""

    order_match = re.search(r"Order\s*#\s*(\d+)", full_text, re.IGNORECASE)
    if order_match:
        order_number = normalize_submission(order_match.group(1)) or ""

    noise = re.compile(
        r"(Due to extraordinary demand|Learn more|Order Arrived|Order Prep|Research & ID|Grading|Assembly|QA Checks|Grades Ready|Complete|Track Package|Tracking number|Payment Method|Payment & Address|Return Address|Download CSV|View Grades|Your grades are ready|Grader Notes|Show More|Status|Changing your address|Customer Service|Vault Terms|Amex ending|Collectors Holdings|All rights reserved|https?://|Items\s+\d+|PSA Estimate|Card Ladder|Pop\b)",
        re.IGNORECASE
    )

    def page_for_offset(global_offset):
        selected = 0
        for i, page_offset in enumerate(page_start_offsets):
            if global_offset >= page_offset:
                selected = i
            else:
                break
        return selected

    def cert_y_on_page(page_index, cert_number):
        if page_index is None or page_index < 0 or page_index >= len(page_data):
            return None

        for block in page_data[page_index]["text_blocks"]:
            if re.search(r"Cert\s*#?\s*" + re.escape(cert_number), block["text"], re.IGNORECASE):
                return block["y_mid"]

        return None

    def title_y_on_page(page_index, description, fallback_y=None):
        if not description or page_index is None or page_index < 0 or page_index >= len(page_data):
            return None

        desc_key = re.sub(r"[^A-Z0-9]+", " ", description.upper()).strip()
        desc_words = [w for w in desc_key.split() if len(w) > 1]

        if not desc_words:
            return None

        best_y = None
        best_score = 0

        for block in page_data[page_index]["text_blocks"]:
            if fallback_y is not None and abs(block["y_mid"] - fallback_y) > 230:
                continue

            block_key = re.sub(r"[^A-Z0-9]+", " ", block["text"].upper()).strip()
            score = sum(1 for w in desc_words[:10] if w in block_key)

            if score > best_score:
                best_score = score
                best_y = block["y_mid"]

        if best_score >= min(2, len(desc_words)):
            return best_y

        return None

    def assign_image(page_index, row_y, page_order_index, used_images_by_page):
        if page_index is None or page_index < 0 or page_index >= len(page_data):
            return ""

        images = page_data[page_index]["image_blocks"]
        used = used_images_by_page.setdefault(page_index, set())

        if row_y is not None:
            scored = []
            for img_index, img in enumerate(images):
                if img_index in used:
                    continue
                dy = abs(img["y_mid"] - row_y)
                if dy <= 95:
                    scored.append((dy, img["x0"], img_index, img))
            if scored:
                scored.sort(key=lambda x: (x[0], x[1]))
                _, _, idx, img = scored[0]
                used.add(idx)
                return img["image_data"]

        available = [(idx, img) for idx, img in enumerate(images) if idx not in used]
        available.sort(key=lambda x: x[1]["y_mid"])
        if page_order_index is not None and 0 <= page_order_index < len(available):
            idx, img = available[page_order_index]
            used.add(idx)
            return img["image_data"]

        return crop_thumbnail_from_page(page_data[page_index]["page"], row_y)

    cert_matches = list(re.finditer(r"Cert\s*#\s*(\d+)", full_text, re.IGNORECASE))
    seen_certs = set()
    parsed_items = []
    page_item_counts = {}

    for idx, cert_match in enumerate(cert_matches):
        cert_number = normalize_submission(cert_match.group(1)) or ""
        if not cert_number or cert_number in seen_certs:
            continue

        block_start = cert_matches[idx - 1].end() if idx > 0 else 0
        block_end = cert_matches[idx + 1].start() if idx + 1 < len(cert_matches) else len(full_text)

        before_text = full_text[block_start:cert_match.start()]
        after_text = full_text[cert_match.end():block_end]

        before_lines = [clean(line) for line in before_text.splitlines() if clean(line)]
        after_lines = [clean(line) for line in after_text.splitlines() if clean(line)]

        grade = ""
        description = ""

        for line in reversed(before_lines[-45:]):
            low = line.lower()

            if not grade and is_psa_grade_line(line):
                grade = line
                continue

            if not description:
                if (
                    not noise.search(line)
                    and not is_psa_grade_line(line)
                    and "order " not in low
                    and "submission #" not in low
                    and not low.startswith("cert")
                    and not low.startswith("item ")
                    and "estimate" not in low
                    and "ladder" not in low
                    and not low.startswith("pop")
                    and "shipped" not in low
                ):
                    description = line
                    continue

            if grade and description:
                break

        for line in after_lines[:45]:
            low = line.lower()
            if low.startswith("item "):
                item_desc = re.sub(r"^Item\s+", "", line, flags=re.IGNORECASE).strip()
                if item_desc and item_desc.lower() != "details" and (not description or description.lower() == "item details"):
                    description = item_desc
                break

        page_index = page_for_offset(cert_match.start())
        cert_y = cert_y_on_page(page_index, cert_number)
        row_y = title_y_on_page(page_index, description, cert_y) or cert_y

        page_order_index = page_item_counts.get(page_index, 0)
        page_item_counts[page_index] = page_order_index + 1

        parsed_items.append({
            "submission_number": submission_number,
            "order_number": order_number,
            "cert_number": cert_number,
            "card_type": "Card",
            "description": description,
            "item_details": description,
            "grade": grade,
            "after_service": "",
            "images_url": "",
            "image_data": "",
            "psa_estimate": "",
            "card_ladder_value": "",
            "pop": "",
            "pop_higher": "",
            "_page_index": page_index,
            "_row_y": row_y,
            "_page_order_index": page_order_index
        })

        seen_certs.add(cert_number)

    used_images_by_page = {}

    for item in parsed_items:
        item["image_data"] = assign_image(
            item.get("_page_index"),
            item.get("_row_y"),
            item.get("_page_order_index"),
            used_images_by_page
        )

        item.pop("_page_index", None)
        item.pop("_row_y", None)
        item.pop("_page_order_index", None)

        items.append(item)

    doc.close()
    return submission_number, order_number, items

def extract_card_items_from_csv(file):
    df = read_file(file)
    df.columns = [str(c).strip() for c in df.columns]

    items = []

    def field(row, names):
        for name in names:
            for col in df.columns:
                if str(col).strip().lower() == name.strip().lower():
                    return clean(row[col])
        return ""

    for _, row in df.iterrows():
        cert_number = normalize_submission(field(row, ["Cert #", "Cert", "Certification Number", "Certification #"]))
        if not cert_number:
            continue

        card_type = field(row, ["Type"])
        description = field(row, ["Description"])
        grade = field(row, ["Grade"])
        images_url = field(row, ["Images", "Image", "Image URL", "Images URL"])

        items.append({
            "submission_number": "",
            "order_number": "",
            "cert_number": cert_number,
            "card_type": card_type,
            "description": description,
            "item_details": description,
            "grade": grade,
            "after_service": "",
            "images_url": images_url,
            "image_data": "",
            "psa_estimate": "",
            "card_ladder_value": "",
            "pop": "",
            "pop_higher": ""
        })

    return "", "", items

def get_buyback_items_for_submission(submission_number):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    SELECT cert_number,
           COALESCE(description, item_details, ''),
           grade,
           image_data,
           interested,
           COALESCE(card_type, ''),
           COALESCE(after_service, ''),
           COALESCE(images_url, ''),
           COALESCE(psa_estimate, ''),
           COALESCE(card_ladder_value, ''),
           COALESCE(pop, ''),
           COALESCE(pop_higher, '')
    FROM card_buyback_items
    WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
    ORDER BY cert_number
    """, (submission_number,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


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
    view = request.args.get("view", "all")
    status_filter = request.args.get("status", "all").replace("+", " ")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    SELECT s.raw_data,
           s.status,
           s.card_pdf_uploaded_at,
           COALESCE(COUNT(c.id), 0) AS card_pdf_item_count
    FROM submissions s
    LEFT JOIN card_buyback_items c
      ON REGEXP_REPLACE(c.submission_number, '\\D', '', 'g') = REGEXP_REPLACE(s.submission_number, '\\D', '', 'g')
    GROUP BY s.submission_number, s.raw_data, s.status, s.card_pdf_uploaded_at
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    rows = sorted(rows, key=get_sort_date, reverse=(sort != "old"))
    all_rows = rows[:]

    if view == "active":
        rows = [r for r in rows if (row_status(r) or "Submitted") not in ["Complete", "Delivered to Us", "Picked Up"]]
    elif view == "complete":
        rows = [r for r in rows if (row_status(r) or "") == "Complete"]
    elif view == "shipping":
        rows = [r for r in rows if (row_status(r) or "") == "Shipping Soon"]
    elif view == "pickup":
        rows = [r for r in rows if (row_status(r) or "") == "Delivered to Us"]
    elif view == "pdf_needed":
        rows = [r for r in rows if card_pdf_needs_attention(r)]

    if status_filter != "all":
        rows = [r for r in rows if customer_status_label(row_status(r) or "Submitted") == status_filter]

    total_count = len(all_rows)
    active_count = sum(1 for r in all_rows if (row_status(r) or "Submitted") not in ["Complete", "Delivered to Us", "Picked Up"])
    complete_count = sum(1 for r in all_rows if (row_status(r) or "") == "Complete")
    shipping_count = sum(1 for r in all_rows if (row_status(r) or "") == "Shipping Soon")
    pickup_count = sum(1 for r in all_rows if (row_status(r) or "") == "Delivered to Us")
    pdf_needed_count = sum(1 for r in all_rows if card_pdf_needs_attention(r))

    html = f"""
    <h2 style="font-size:28px;margin:6px 0 14px;">Admin Dashboard</h2>

    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(135px,1fr));gap:10px;margin:10px 0 18px;">
        <a href="/admin?view=all&sort={sort}" style="text-decoration:none;color:inherit;">
            <div style="background:white;border-radius:12px;padding:15px;box-shadow:0 2px 8px rgba(0,0,0,.08);border-left:5px solid #198754;">
                <div style="font-size:12px;color:#6b7280;font-weight:bold;text-transform:uppercase;letter-spacing:.4px;">Total</div>
                <div style="font-size:24px;font-weight:900;color:#111827;margin-top:3px;">{total_count}</div>
            </div>
        </a>
        <a href="/admin?view=active&sort={sort}" style="text-decoration:none;color:inherit;">
            <div style="background:white;border-radius:12px;padding:15px;box-shadow:0 2px 8px rgba(0,0,0,.08);border-left:5px solid #198754;">
                <div style="font-size:12px;color:#6b7280;font-weight:bold;text-transform:uppercase;letter-spacing:.4px;">Active</div>
                <div style="font-size:24px;font-weight:900;color:#111827;margin-top:3px;">{active_count}</div>
            </div>
        </a>
        <a href="/admin?view=complete&sort={sort}" style="text-decoration:none;color:inherit;">
            <div style="background:white;border-radius:12px;padding:15px;box-shadow:0 2px 8px rgba(0,0,0,.08);border-left:5px solid #198754;">
                <div style="font-size:12px;color:#6b7280;font-weight:bold;text-transform:uppercase;letter-spacing:.4px;">Complete</div>
                <div style="font-size:24px;font-weight:900;color:#111827;margin-top:3px;">{complete_count}</div>
            </div>
        </a>
        <a href="/admin?view=shipping&sort={sort}" style="text-decoration:none;color:inherit;">
            <div style="background:white;border-radius:12px;padding:15px;box-shadow:0 2px 8px rgba(0,0,0,.08);border-left:5px solid #198754;">
                <div style="font-size:12px;color:#6b7280;font-weight:bold;text-transform:uppercase;letter-spacing:.4px;">Shipping Soon</div>
                <div style="font-size:24px;font-weight:900;color:#111827;margin-top:3px;">{shipping_count}</div>
            </div>
        </a>
        <a href="/admin?view=pickup&sort={sort}" style="text-decoration:none;color:inherit;">
            <div style="background:white;border-radius:12px;padding:15px;box-shadow:0 2px 8px rgba(0,0,0,.08);border-left:5px solid #198754;">
                <div style="font-size:12px;color:#6b7280;font-weight:bold;text-transform:uppercase;letter-spacing:.4px;">Ready Pickup</div>
                <div style="font-size:24px;font-weight:900;color:#111827;margin-top:3px;">{pickup_count}</div>
            </div>
        </a>
        <a href="/admin?view=pdf_needed&sort={sort}" style="text-decoration:none;color:inherit;">
            <div style="background:white;border-radius:12px;padding:15px;box-shadow:0 2px 8px rgba(0,0,0,.08);border-left:5px solid #dc3545;">
                <div style="font-size:12px;color:#6b7280;font-weight:bold;text-transform:uppercase;letter-spacing:.4px;">PDF Needed</div>
                <div style="font-size:24px;font-weight:900;color:#111827;margin-top:3px;">{pdf_needed_count}</div>
            </div>
        </a>
    </div>
    """

    status_options = customer_status_options()
    status_select_html = ""
    for option in status_options:
        selected_attr = "selected" if option == status_filter else ""
        status_select_html += f"<option value='{option}' {selected_attr}>{option}</option>"

    html += f"""
    <div class="filterbar">
        <form method="get" action="/admin">
            <div>
                <label>Sort</label>
                <select name="sort">
                    <option value="new" {'selected' if sort == 'new' else ''}>Newest First</option>
                    <option value="old" {'selected' if sort == 'old' else ''}>Oldest First</option>
                </select>
            </div>
            <div>
                <label>View</label>
                <select name="view">
                    <option value="all" {'selected' if view == 'all' else ''}>All Submissions</option>
                    <option value="active" {'selected' if view == 'active' else ''}>Active</option>
                    <option value="complete" {'selected' if view == 'complete' else ''}>Complete</option>
                    <option value="shipping" {'selected' if view == 'shipping' else ''}>Shipping Soon</option>
                    <option value="pickup" {'selected' if view == 'pickup' else ''}>Ready Pickup</option>
                    <option value="pdf_needed" {'selected' if view == 'pdf_needed' else ''}>PDF Needed</option>
                </select>
            </div>
            <div>
                <label>Status</label>
                <select name="status">
                    <option value="all" {'selected' if status_filter == 'all' else ''}>All Statuses</option>
                    {status_select_html}
                </select>
            </div>
            <button type="submit">Apply</button>
            <a class="reset-link" href="/admin">Reset</a>
        </form>
    </div>
    """

    alert_rows = [row for row in rows if card_pdf_needs_attention(row)]

    if alert_rows:
        html += f"""
        <div class="alert-summary">
            <h2>Card PDFs Needed: {len(alert_rows)}</h2>
            <p>These submissions are at Shipping Soon / Complete, but the card-detail PDF has not been uploaded in the last 30 days.</p>
            <details>
                <summary>View submissions needing PDFs</summary>
                <div class="table-wrap">
                    <table>
                        <tr><th>Submission #</th><th>Customer</th><th>Status</th><th>Cards PDF Status</th><th>Action</th></tr>
        """

        for alert_row in alert_rows:
            alert_data = alert_row[0] or {}
            alert_status = alert_row[1] or "Submitted"
            alert_sub = normalize_submission(get_field(alert_data, ["Submission #", "Submission Number"])) or ""
            alert_customer = get_field(alert_data, ["Customer Name", "Name"])
            html += f"""
            <tr>
                <td><b>{alert_sub}</b></td>
                <td>{alert_customer}</td>
                <td>{customer_status_label(alert_status)}</td>
                <td><b style='color:#dc3545;'>{card_pdf_alert_text(alert_row)}</b></td>
                <td><a class='btn' href='/admin/upload_cards'>Upload</a></td>
            </tr>
            """

        html += "</table></div></details></div>"

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

                month_pattern = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|January|February|March|April|May|June|July|August|September|October|November|December)"
                date_pattern = month_pattern + r"\s+\d{1,2},\s+\d{4}"
                date_range_same_year = month_pattern + r"\s+\d{1,2}\s*[-–]\s*" + month_pattern + r"?\s*\d{1,2},\s+\d{4}"
                date_range_full = date_pattern + r"\s*[-–]\s*" + date_pattern

                value_pattern = re.compile(
                    rf"(Completed\s+{date_pattern}|Est\.\s*Complete\s*by\s+{date_range_full}|Est\.\s*Complete\s*by\s+{date_range_same_year}|Est\.\s*Complete\s*by\s+{date_pattern}|Estimated\s*Complete\s*by\s+{date_range_full}|Estimated\s*Complete\s*by\s+{date_range_same_year}|Estimated\s*Complete\s*by\s+{date_pattern}|Est\.\s*by\s+{date_range_full}|Est\.\s*by\s+{date_range_same_year}|Est\.\s*by\s+{date_pattern}|{date_pattern})",
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
                                r"(Completed\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}|Est\.\s*Complete\s*by\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}\s*[-–]\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|January|February|March|April|May|June|July|August|September|October|November|December)?\s*\d{1,2},\s+\d{4}|Est\.\s*Complete\s*by\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}|Est\.\s*by\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}\s*[-–]\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|January|February|March|April|May|June|July|August|September|October|November|December)?\s*\d{1,2},\s+\d{4}|Est\.\s*by\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4})",
                                block_text,
                                re.IGNORECASE
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
                SELECT status,
                       COALESCE(sms_opt_in, FALSE),
                       COALESCE(sms_mode, CASE WHEN COALESCE(sms_opt_in, FALSE)=FALSE THEN 'none' WHEN COALESCE(sms_pickup_only, TRUE)=TRUE THEN 'pickup' ELSE 'all' END),
                       COALESCE(last_sms_status, ''),
                       raw_data
                FROM submissions
                WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
                """, (sub,))
                existing_sms_row = cur.fetchone()

                old_status = existing_sms_row[0] if existing_sms_row else None
                sms_opt_in = existing_sms_row[1] if existing_sms_row else False
                sms_mode = existing_sms_row[2] if existing_sms_row else "none"
                last_sms_status = existing_sms_row[3] if existing_sms_row else ""
                existing_raw_data = existing_sms_row[4] if existing_sms_row and existing_sms_row[4] else {}

                sms_phone = normalize_phone(get_field(existing_raw_data or {}, ["Contact Info", "Phone", "Phone Number"]))
                if sms_phone and not sms_phone.startswith("+1") and len(sms_phone) == 10:
                    sms_phone = "+1" + sms_phone

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
                    maybe_queue_status_sms(cur, sub, sms_phone, old_status, status, sms_opt_in, sms_mode, last_sms_status)
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
            <input type="file" name="file" accept=".pdf,.csv,application/pdf,text/csv">
            <button>Upload PDF</button>
        </form>
    </div>
    """)

@app.route("/admin/upload_cards", methods=["GET", "POST"])
@admin_required
def admin_upload_cards():
    if request.method == "POST":
        try:
            import tempfile
            file = request.files.get("file")
            if not file:
                return page("<div class='card'>No card PDF uploaded.</div>")
            filename = (file.filename or "").lower()
            if not (filename.endswith(".pdf") or filename.endswith(".csv")):
                return page("""
                <div class="card">
                    <h2>Wrong File Type</h2>
                    <p>This uploader accepts PSA card detail PDF or PSA CSV files.</p>
                    <a href="/admin/upload_cards">Back to Card Upload</a>
                </div>
                """)

            if filename.endswith(".csv"):
                submission_number = normalize_submission(request.form.get("submission_number"))
                order_number, items = "", extract_card_items_from_csv(file)[2]
            else:
                temp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
                file.save(temp.name)
                try:
                    submission_number, order_number, items = extract_card_items_from_pdf(temp.name)
                finally:
                    try:
                        os.unlink(temp.name)
                    except Exception:
                        pass
            if not submission_number:
                return page("""
                <div class="card">
                    <h2>No Submission Number Found</h2>
                    <p>For PDF files, use the PSA order details PDF. For CSV files, enter the Submission # on the upload form.</p>
                    <a href="/admin/upload_cards">Try Again</a>
                </div>
                """)
            conn = get_conn()
            cur = conn.cursor()
            saved = 0
            for item in items:
                if not item.get("submission_number"):
                    item["submission_number"] = submission_number
                cur.execute("""
                INSERT INTO card_buyback_items
                    (submission_number, cert_number, item_details, grade, image_data, card_type, description, after_service, images_url, psa_estimate, card_ladder_value, pop, pop_higher)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (submission_number, cert_number)
                DO UPDATE SET
                    item_details=EXCLUDED.item_details,
                    grade=EXCLUDED.grade,
                    image_data=COALESCE(NULLIF(EXCLUDED.image_data, ''), card_buyback_items.image_data),
                    card_type=EXCLUDED.card_type,
                    description=EXCLUDED.description,
                    after_service=EXCLUDED.after_service,
                    images_url=EXCLUDED.images_url,
                    psa_estimate=EXCLUDED.psa_estimate,
                    card_ladder_value=EXCLUDED.card_ladder_value,
                    pop=EXCLUDED.pop,
                    pop_higher=EXCLUDED.pop_higher,
                    updated_at=NOW()
                """, (
                    item["submission_number"],
                    item["cert_number"],
                    item["item_details"],
                    item["grade"],
                    item["image_data"],
                    item.get("card_type", ""),
                    item.get("description", item.get("item_details", "")),
                    item.get("after_service", ""),
                    item.get("images_url", ""),
                    item.get("psa_estimate", ""),
                    item.get("card_ladder_value", ""),
                    item.get("pop", ""),
                    item.get("pop_higher", "")
                ))
                saved += 1
            cur.execute("""
            UPDATE submissions
            SET card_pdf_uploaded_at=NOW(),
                card_pdf_order_number=%s,
                last_updated=NOW()
            WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
            """, (order_number, submission_number))

            conn.commit()
            cur.close()
            conn.close()
            preview_rows = ""
            for item in items[:50]:
                img_html = f"<img src='{item['image_data']}' style='max-height:120px;max-width:90px;'>" if item.get("image_data") else ""
                image_link = ""
                preview_rows += f"""
                <tr>
                    <td>{img_html}</td>
                    <td>{item.get('cert_number','')}</td>
                    <td>{item.get('card_type','')}</td>
                    <td>{item.get('description', item.get('item_details',''))}</td>
                    <td>{item.get('grade','')}</td>
                </tr>
                """
            return page(f"""
            <div class="card">
                <h2>Card PDF Imported</h2>
                <p><b>Submission #:</b> {submission_number}</p>
                <p><b>Order #:</b> {order_number}</p>
                <p><b>Cards found:</b> {len(items)}</p>
                <p><b>Cards saved:</b> {saved}</p>
                <table><tr><th>Card Image</th><th>Cert #</th><th>Type</th><th>Description</th><th>Grade</th></tr>{preview_rows}</table>
                <br><a class="btn" href="/admin/upload_cards">Upload Another</a>
                <a class="btn" href="/admin/buyback_requests">View Buyback Requests</a>
                <a class="btn" href="/admin">Back to Admin</a>
            </div>
            """)
        except Exception:
            return page(f"""
            <div class="card">
                <h2>Card PDF Upload Error</h2>
                <p>The file could not be processed.</p>
                <pre>{traceback.format_exc()}</pre>
                <a href="/admin/upload_cards">Try again</a>
            </div>
            """)
    return page("""
    <div class="card">
        <h2>Upload PSA Card Details PDF / CSV</h2>
        <p>Use this for completed/card-detail PDFs that include cert numbers, card details, grades, and card images.</p>
        <form method="post" enctype="multipart/form-data">
            <input type="file" name="file" accept=".pdf,application/pdf">
            <button>Upload Card File</button>
        </form>
    </div>
    """)

@app.route("/admin/buyback_requests")
@admin_required
def admin_buyback_requests():
    selected_queue = request.args.get("queue", "new").lower()

    allowed_queues = {
        "new": "New",
        "sold": "Sold",
        "pass": "Pass",
        "all": "All"
    }

    if selected_queue not in allowed_queues:
        selected_queue = "new"

    conn = get_conn()
    cur = conn.cursor()

    if selected_queue == "all":
        cur.execute("""
        SELECT c.submission_number, c.cert_number, COALESCE(c.description, c.item_details, ''), c.grade, c.image_data,
               c.interested, COALESCE(c.buyback_status, 'New'), s.raw_data,
               COALESCE(c.card_type, ''), COALESCE(c.after_service, ''), COALESCE(c.images_url, ''),
               COALESCE(c.psa_estimate, ''), COALESCE(c.card_ladder_value, ''), COALESCE(c.pop, ''), COALESCE(c.pop_higher, '')
        FROM card_buyback_items c
        LEFT JOIN submissions s
          ON REGEXP_REPLACE(s.submission_number, '\\D', '', 'g') = REGEXP_REPLACE(c.submission_number, '\\D', '', 'g')
        WHERE c.interested=TRUE
        ORDER BY
            CASE COALESCE(c.buyback_status, 'New')
                WHEN 'New' THEN 0
                WHEN 'Sold' THEN 1
                WHEN 'Pass' THEN 2
                ELSE 3
            END,
            c.updated_at DESC
        """)
    else:
        cur.execute("""
        SELECT c.submission_number, c.cert_number, COALESCE(c.description, c.item_details, ''), c.grade, c.image_data,
               c.interested, COALESCE(c.buyback_status, 'New'), s.raw_data,
               COALESCE(c.card_type, ''), COALESCE(c.after_service, ''), COALESCE(c.images_url, ''),
               COALESCE(c.psa_estimate, ''), COALESCE(c.card_ladder_value, ''), COALESCE(c.pop, ''), COALESCE(c.pop_higher, '')
        FROM card_buyback_items c
        LEFT JOIN submissions s
          ON REGEXP_REPLACE(s.submission_number, '\\D', '', 'g') = REGEXP_REPLACE(c.submission_number, '\\D', '', 'g')
        WHERE c.interested=TRUE
          AND COALESCE(c.buyback_status, 'New')=%s
        ORDER BY c.updated_at DESC
        """, (allowed_queues[selected_queue],))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    def active(q):
        return "active" if selected_queue == q else ""

    html = """
    <h2>Buyback Interest</h2>
    <div class="card">
        <p><b>Workflow:</b> Cards selected by customers appear under <b>New Interest</b>. If a card is moved to Sold or Pass by mistake, use <b>Back to Interest</b>.</p>
    </div>
    <div class="filterbar">
        <a class="reset-link {new_active}" href="/admin/buyback_requests?queue=new">Interest</a>
        <a class="reset-link {sold_active}" href="/admin/buyback_requests?queue=sold">Sold</a>
        <a class="reset-link {pass_active}" href="/admin/buyback_requests?queue=pass">Pass</a>
        <a class="reset-link {all_active}" href="/admin/buyback_requests?queue=all">All Interested</a>
    </div>
    """.format(
        new_active=active("new"),
        sold_active=active("sold"),
        pass_active=active("pass"),
        all_active=active("all")
    )

    if not rows:
        if selected_queue == "new":
            html += "<div class='card'>No customer-selected buyback cards right now.</div>"
        else:
            html += "<div class='card'>No cards in this queue.</div>"
        return page(html)

    html += "<div class='card'><div class='table-wrap'><table>"
    html += "<tr><th>Status</th><th>Card Image</th><th>Customer</th><th>Submission #</th><th>Cert #</th><th>Type</th><th>Description</th><th>Grade</th><th>Actions</th></tr>"

    for row in rows:
        submission_number, cert_number, item_details, grade, image_data, interested, buyback_status, raw_data = row[:8]
        card_type = row[8] if len(row) > 8 else ""
        after_service = row[9] if len(row) > 9 else ""
        images_url = row[10] if len(row) > 10 else ""
        psa_estimate = display_blank_loading(row[11] if len(row) > 11 else "")
        card_ladder_value = display_blank_loading(row[12] if len(row) > 12 else "")
        pop = display_blank_loading(row[13] if len(row) > 13 else "")
        pop_higher = display_blank_loading(row[14] if len(row) > 14 else "")

        customer_name = get_field(raw_data or {}, ["Customer Name", "Name"])
        phone = get_field(raw_data or {}, ["Contact Info", "Phone", "Phone Number"])
        img_html = f"<img src='{image_data}' style='max-height:120px;max-width:90px;'>" if image_data else ""
        image_link = ""

        action_html = f"""
        <form method="post" action="/admin/buyback_status" style="display:inline;">
            <input type="hidden" name="submission_number" value="{submission_number}">
            <input type="hidden" name="cert_number" value="{cert_number}">
            <button name="status" value="Sold">Sold</button>
            <button name="status" value="Pass">Pass</button>
            <button name="status" value="New">Back to Interest</button>
        </form>
        """

        html += f"""
        <tr>
            <td><b>{'Interest' if buyback_status == 'New' else buyback_status}</b></td>
            <td>{img_html}</td>
            <td>{customer_name}<br><small>{phone}</small></td>
            <td>{submission_number}</td>
            <td>{cert_number}</td>
            <td>{card_type}</td>
            <td>{item_details}</td>
            <td>{grade}</td>
            <td>{action_html}</td>
        </tr>
        """

    html += "</table></div>"
    return page(html)

@app.route("/admin/buyback_status", methods=["POST"])
@admin_required
def admin_buyback_status():
    submission_number = normalize_submission(request.form.get("submission_number"))
    cert_number = normalize_submission(request.form.get("cert_number"))
    status = clean(request.form.get("status"))

    if status not in ["New", "Sold", "Pass"]:
        status = "New"

    if submission_number and cert_number:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
        UPDATE card_buyback_items
        SET buyback_status=%s,
            updated_at=NOW()
        WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
          AND REGEXP_REPLACE(cert_number, '\\D', '', 'g')=%s
        """, (status, submission_number, cert_number))
        conn.commit()
        cur.close()
        conn.close()

    return redirect("/admin/buyback_requests")


@app.route("/portal/sms_preferences", methods=["POST"])
def portal_sms_preferences():
    phone = normalize_phone(session.get("phone"))
    last = clean(session.get("last")).lower()

    if not phone or not last:
        return redirect("/portal")

    submission_number = normalize_submission(request.form.get("submission_number"))
    sms_mode = request.form.get("sms_mode", "none")
    if sms_mode not in ["none", "pickup", "all"]:
        sms_mode = "none"

    sms_opt_in = sms_mode != "none"
    sms_pickup_only = sms_mode == "pickup"

    if not submission_number:
        return redirect("/portal/orders")

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    SELECT raw_data
    FROM submissions
    WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
    """, (submission_number,))
    row = cur.fetchone()

    if not row:
        cur.close()
        conn.close()
        return redirect("/portal/orders")

    data = row[0] or {}
    name = str(get_field(data, ["Customer Name", "Name"])).lower()
    contact = normalize_phone(get_field(data, ["Contact Info", "Phone", "Phone Number"]))

    phone_match = bool(contact) and (phone in contact or contact in phone)
    name_match = bool(last) and last in name

    if phone_match and name_match:
        cur.execute("""
        UPDATE submissions
        SET sms_opt_in=%s,
            sms_pickup_only=%s,
            last_updated=NOW()
        WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
        """, (sms_opt_in, sms_pickup_only, submission_number))
        conn.commit()

    cur.close()
    conn.close()
    return redirect("/portal/orders")

@app.route("/portal/sell_interest", methods=["POST"])
def portal_sell_interest():
    phone = normalize_phone(session.get("phone"))
    last = clean(session.get("last")).lower()
    if not phone or not last:
        return redirect("/portal")
    certs = request.form.getlist("cert")
    submission_number = normalize_submission(request.form.get("submission_number"))
    if not submission_number:
        return redirect("/portal/orders")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    SELECT raw_data FROM submissions
    WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
    """, (submission_number,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close(); return redirect("/portal/orders")
    data = row[0] or {}
    name = str(get_field(data, ["Customer Name", "Name"])).lower()
    contact = normalize_phone(get_field(data, ["Contact Info", "Phone", "Phone Number"]))
    phone_match = bool(contact) and (phone in contact or contact in phone)
    name_match = bool(last) and last in name
    if not (phone_match and name_match):
        cur.close(); conn.close(); return redirect("/portal/orders")
    cur.execute("""
    UPDATE card_buyback_items SET interested=FALSE, updated_at=NOW()
    WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
    """, (submission_number,))
    for cert in certs:
        cert_clean = normalize_submission(cert)
        if cert_clean:
            cur.execute("""
            UPDATE card_buyback_items SET interested=TRUE, updated_at=NOW()
            WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
              AND REGEXP_REPLACE(cert_number, '\\D', '', 'g')=%s
            """, (submission_number, cert_clean))
    conn.commit(); cur.close(); conn.close()
    return redirect("/portal/orders")


@app.route("/admin/sms_notifications")
@admin_required
def admin_sms_notifications():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    SELECT submission_number, phone, old_status, new_status, message, send_status, provider_response, created_at, sent_at
    FROM sms_notifications
    ORDER BY created_at DESC
    LIMIT 200
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    html = "<h2>SMS Queue / Text History</h2>"
    html += f"<div class='card'><p><b>Provider Mode:</b> {SMS_PROVIDER}</p><p><b>Portal URL:</b> {PUBLIC_PORTAL_URL}</p></div>"

    if not rows:
        html += "<div class='card'>No SMS notifications have been queued yet.</div>"
        return page(html)

    html += "<div class='card'><div class='table-wrap'><table>"
    html += "<tr><th>Created</th><th>Submission #</th><th>Phone</th><th>Old</th><th>New</th><th>Status</th><th>Message</th></tr>"

    for submission_number, phone, old_status, new_status, message, send_status, provider_response, created_at, sent_at in rows:
        html += f"""
        <tr>
            <td>{created_at}</td>
            <td>{submission_number}</td>
            <td>{phone}</td>
            <td>{customer_status_label(old_status)}</td>
            <td>{customer_status_label(new_status)}</td>
            <td><b>{send_status}</b></td>
            <td>{html_escape(message)}</td>
        </tr>
        """

    html += "</table></div>"
    return page(html)


# =========================
# CUSTOMER PORTAL
# =========================
@app.route("/portal", methods=["GET", "POST"])
def portal():
    if request.method == "POST":
        session["phone"] = normalize_phone(request.form.get("phone"))
        session["last"] = clean(request.form.get("last")).lower()
        return redirect("/portal/orders")

    portal_logo_b64 = "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"

    return page("""
    <style>
        .gsc-portal-wrap {
            min-height: calc(100vh - 135px);
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 46px 16px;
            background:#f4f6f8;
        }
        .gsc-portal-card {
            width: 100%;
            max-width: 760px;
            background: #ffffff;
            border-radius: 18px;
            padding: 44px 58px 34px;
            box-shadow:0 16px 38px rgba(15,81,50,.18);
            text-align: center;
            border:3px solid #198754;
        }
        .gsc-portal-icon {
            width: 112px;
            height: 112px;
            border-radius: 999px;
            border: 5px solid #e5e7eb;
            margin: 0 auto 24px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #0f5132;
            font-size: 54px;
            font-weight: 900;
        }
        .gsc-portal-card h2 {
            font-size: 42px;
            letter-spacing: .5px;
            text-transform: uppercase;
            margin: 8px 0 14px;
            color: #07110d;
            font-weight: 900;
        }
        .gsc-green-line {
            width: 106px;
            height: 5px;
            background: #198754;
            margin: 0 auto 24px;
            border-radius: 999px;
        }
        .gsc-subtitle {
            font-size: 22px;
            line-height: 1.45;
            color: #374151;
            max-width: 560px;
            margin: 0 auto 32px;
        }
        .gsc-portal-form {
            max-width: 560px;
            margin: 0 auto;
        }
        .gsc-input-row {
            display: flex;
            align-items: center;
            gap: 14px;
            border: 1px solid #cfd4dc;
            border-radius: 10px;
            padding: 0 18px;
            margin-bottom: 16px;
            height: 70px;
            background: #fff;
        }
        .gsc-input-icon {
            font-size: 23px;
            color: #4b5563;
            width: 32px;
            text-align: center;
            font-weight: 900;
        }
        .gsc-input-row input {
            border: 0;
            outline: 0;
            flex: 1;
            font-size: 21px;
            padding: 0;
            margin: 0;
            color: #111827;
            background: transparent;
        }
        .gsc-input-row input::placeholder {
            color: #6b7280;
        }
        .gsc-submit {
            width: 100%;
            height: 72px;
            border: 0;
            border-radius: 10px;
            margin: 8px 0 26px;
            background: linear-gradient(180deg, #08783f 0%, #006b39 100%);
            color: #fff;
            font-size: 22px;
            font-weight: 900;
            letter-spacing: .3px;
            cursor: pointer;
            box-shadow: 0 8px 16px rgba(0,0,0,.22);
        }
        .gsc-submit span {
            font-size: 34px;
            margin-left: 28px;
            vertical-align: -3px;
        }
        .gsc-logo-divider {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 18px;
            margin: 8px auto 18px;
        }
        .gsc-logo-divider:before,
        .gsc-logo-divider:after {
            content: "";
            height: 1px;
            background: #d1d5db;
            width: 170px;
        }
        .gsc-form-logo {
            width: 150px;
            max-width: 40%;
            height: auto;
            display: block;
        }
        .gsc-trust {
            color: #374151;
            font-size: 18px;
            line-height: 1.35;
            margin: 0;
        }
        .gsc-benefits {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 16px;
            color: #fff;
            padding: 28px 20px 22px;
            background:#f4f6f8;
            border-top: 1px solid #198754;
            max-width: 100%;
            margin: 0;
        }
        .gsc-benefit {
            display: flex;
            gap: 14px;
            align-items: center;
            background:#ffffff;
            border: 1px solid rgba(25,135,84,.75);
            border-radius: 14px;
            padding: 16px;
            box-shadow: none;
        }
        .gsc-benefit-icon {
            width: 52px;
            height: 52px;
            border-radius: 14px;
            background: #0f5132;
            border: 2px solid #198754;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 25px;
            color: #fff;
            flex: 0 0 auto;
            font-weight: 900;
            line-height:1;
        }
        .gsc-benefit-title {
            font-weight: 900;
            font-size: 17px;
            text-transform: uppercase;
            margin-bottom: 4px;
            letter-spacing:.3px;
        }
        .gsc-benefit-text {
            color: #d1d5db;
            font-size: 14px;
            line-height: 1.35;
        }
        @media (max-width: 700px) {
            .gsc-portal-wrap {
                min-height: auto;
                padding: 34px 20px 44px;
                background:#f4f6f8;
                width:100%;
                box-sizing:border-box;
            }
            .gsc-portal-card {
                padding: 32px 22px 28px;
                border-radius: 16px;
            }
            .gsc-portal-card h2 {
                font-size: 30px;
            }
            .gsc-subtitle {
                font-size: 17px;
            }
            .gsc-input-row {
                height: 58px;
            }
            .gsc-input-row input {
                font-size: 17px;
            }
            .gsc-submit {
                height: 60px;
                font-size: 18px;
            }
            .gsc-benefits {
                display: block;
                padding: 22px 18px;
            }
            .gsc-benefit {
                margin: 0 auto 18px;
            }
            .gsc-logo-divider:before,
            .gsc-logo-divider:after {
                width: 85px;
            }
            .gsc-form-logo {
                width: 115px;
                max-width: 42%;
            }
        }
    </style>

    <div class="gsc-portal-wrap">
        <div class="gsc-portal-card">
            <div class="gsc-portal-icon">&#10003;</div>
            <h2>Track Your Submission</h2>
            <div class="gsc-green-line"></div>
            <p class="gsc-subtitle">Enter your information below to view the real-time status of your PSA submission.</p>

            <form class="gsc-portal-form" method="post">
                <div class="gsc-input-row">
                    <div class="gsc-input-icon">&#9742;</div>
                    <input name="phone" placeholder="Phone number">
                </div>

                <div class="gsc-input-row">
                    <div class="gsc-input-icon">&#128100;</div>
                    <input name="last" placeholder="Last name">
                </div>

                <button class="gsc-submit" type="submit">VIEW STATUS <span>&rarr;</span></button>
            </form>

            <div class="gsc-logo-divider">
                <img class="gsc-form-logo" src="data:image/png;base64,__PORTAL_LOGO__" alt="Giant Sports Cards">
            </div>

            <p class="gsc-trust">Thank you for trusting Giant Sports Cards<br>with your valuable collection.</p>
        </div>
    </div>

    <div class="gsc-benefits">
        <div class="gsc-benefit">
            <div class="gsc-benefit-icon">&#128337;</div>
            <div>
                <div class="gsc-benefit-title">Real-Time Updates</div>
                <div class="gsc-benefit-text">Get the latest status on your submission in real time.</div>
            </div>
        </div>
        <div class="gsc-benefit">
            <div class="gsc-benefit-icon">&#9733;</div>
            <div>
                <div class="gsc-benefit-title">Expert Care</div>
                <div class="gsc-benefit-text">Your cards are handled with expert care.</div>
            </div>
        </div>
    </div>
    """.replace("__PORTAL_LOGO__", portal_logo_b64), mode="portal")

@app.route("/portal/orders")
def portal_orders():
    phone = normalize_phone(session.get("phone"))
    last = clean(session.get("last")).lower()

    if not phone or not last:
        return redirect("/portal")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    SELECT raw_data, status,
           COALESCE(sms_opt_in, FALSE),
           COALESCE(sms_pickup_only, TRUE),
           COALESCE(sms_mode, CASE WHEN COALESCE(sms_opt_in, FALSE)=FALSE THEN 'none' WHEN COALESCE(sms_pickup_only, TRUE)=TRUE THEN 'pickup' ELSE 'all' END)
    FROM submissions
    ORDER BY last_updated DESC
    """)
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
            sms_opted = r[2] if len(r) > 2 else False
            sms_pickup_only = r[3] if len(r) > 3 else True
            sms_mode = r[4] if len(r) > 4 else ("pickup" if sms_opted and sms_pickup_only else ("all" if sms_opted else "none"))
            grouped[sub] = (data, r[1] or "Submitted", sms_opted, sms_pickup_only, sms_mode)

    if not grouped:
        html += "<div class='card'>No matching orders found. Check phone number and last name.</div>"
        return page(html, mode="portal")

    statuses_available = customer_status_options()

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

    for sub, grouped_values in grouped.items():
        data, status = grouped_values[0], grouped_values[1]
        internal_status = status or "Submitted"
        label_status = customer_status_label(internal_status)

        if selected_view == "active" and internal_status in completed_statuses:
            continue

        if selected_view == "completed" and internal_status not in completed_statuses:
            continue

        if selected_status != "all" and label_status != selected_status:
            continue

        filtered_grouped[sub] = grouped_values

    if not filtered_grouped:
        html += "<div class='card'>No submissions match the selected filters.</div>"
        html += "<a href='/portal/logout'>Log out</a>"
        return page(html, mode="portal")

    for sub, grouped_values in filtered_grouped.items():
        data, status = grouped_values[0], grouped_values[1]
        sms_opted = grouped_values[2] if len(grouped_values) > 2 else False
        sms_pickup_only = grouped_values[3] if len(grouped_values) > 3 else True
        sms_mode = grouped_values[4] if len(grouped_values) > 4 else ("pickup" if sms_opted and sms_pickup_only else ("all" if sms_opted else "none"))
        customer_name = get_field(data, ["Customer Name", "Name"])
        cards = get_field(data, ["# Of Cards", "# of Cards", "Cards"])
        service = clean_service_display(get_field(data, ["Service Type", "Service"]))
        date = get_dropoff_date(data)
        arrived_completed_raw = get_field(data, ["Arrived / Completed"])
        arrived_completed_data = parse_arrived_completed_value(arrived_completed_raw)
        arrived_completed = arrived_completed_data["display"]
        estimated_completion = get_field(data, ["Estimated Completion Date"]) or arrived_completed_data["estimated"]
        display_status = status or "Submitted"
        display_status_label = customer_status_label(display_status)

        buyback_rows = get_buyback_items_for_submission(sub)
        buyback_html = ""

        if buyback_rows:
            buyback_count = len(buyback_rows)
            buyback_html += f"""
            <hr>
            <details class="buyback-collapsible">
                <summary>View cards / select cards to sell ({buyback_count})</summary>
                <div class="buyback-inner">
                    <p>Select any cards you may be interested in selling to Giant Sports Cards.</p>
                    <form method="post" action="/portal/sell_interest">
                        <input type="hidden" name="submission_number" value="{sub}">
                        <div class="card-grid">
            """

            for row in buyback_rows:
                cert_number, item_details, grade, image_data, interested = row[0], row[1], row[2], row[3], row[4]
                card_type = row[5] if len(row) > 5 else ""
                after_service = row[6] if len(row) > 6 else ""
                images_url = row[7] if len(row) > 7 else ""
                psa_estimate = display_blank_loading(row[8] if len(row) > 8 else "")
                card_ladder_value = display_blank_loading(row[9] if len(row) > 9 else "")
                pop = display_blank_loading(row[10] if len(row) > 10 else "")
                pop_higher = display_blank_loading(row[11] if len(row) > 11 else "")

                checked = "checked" if interested else ""
                img_html = f"<img src='{image_data}' alt='Card image'>" if image_data else ""

                buyback_html += f"""
                <div class="buy-card">
                    {img_html}
                    <div class="cert">Certification #: {cert_number}</div>
                    <div><b>Type:</b> {card_type}</div>
                    <div>{item_details}</div>
                    <div><b>Grade:</b> {grade}</div>
                    <label class="sell-check"><input type="checkbox" name="cert" value="{cert_number}" {checked}> Interested in selling</label>
                </div>
                """

            buyback_html += """
                        </div>
                        <br>
                        <button type="submit">Save</button>
                    </form>
                </div>
            </details>
            """

        sms_mode = sms_mode or "none"
        none_checked = "checked" if sms_mode == "none" else ""
        pickup_checked = "checked" if sms_mode == "pickup" else ""
        all_checked = "checked" if sms_mode == "all" else ""

        sms_html = f"""
        <hr>
        <form method="post" action="/portal/sms_preferences">
            <input type="hidden" name="submission_number" value="{sub}">
            <h4>Text Notifications</h4>

            <label class="sell-check">
                <input type="radio" name="sms_mode" value="none" {none_checked}>
                No text messages
            </label>

            <label class="sell-check">
                <input type="radio" name="sms_mode" value="pickup" {pickup_checked}>
                Text me when this submission is ready for pickup
            </label>

            <label class="sell-check">
                <input type="radio" name="sms_mode" value="all" {all_checked}>
                Text me for every PSA status change on this submission
            </label>

            <button type="submit">Save Text Settings</button>
            <p><small>Texts go to the phone number on this order. Each text identifies the exact submission number. Message/data rates may apply.</small></p>
        </form>
        """

        html += f"""
        <div class="card">
            <h3>{customer_name}</h3>
            <p><b>Submission #:</b> {sub}</p>
            <p><b>Status:</b> <span class="status">{display_status_label}</span></p>
            <p><b>Arrived:</b> {arrived_completed}</p>
            <p><b>Estimated Completion Date:</b> {estimated_completion}</p>
            <p><b>Cards:</b> {cards}</p>
            <p><b>Service:</b> {service}</p>
            <p><b>Customer Drop-Off Date:</b> {date}</p>
            {status_bar(display_status)}
            {sms_html}
            {buyback_html}
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
