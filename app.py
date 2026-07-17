from flask import Flask, request, session, redirect
import pandas as pd
import psycopg2
import os, io, json, re, traceback, base64, smtplib
from email.message import EmailMessage
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
SELL_BUYBACK_EMAIL = os.getenv("SELL_BUYBACK_EMAIL", "sell@giantsportscards.com")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587") or "587")
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SELL_BUYBACK_EMAIL)
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "").strip().lower() in ("1", "true", "yes", "on")

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
    CREATE TABLE IF NOT EXISTS buyback_email_notifications (
        id SERIAL PRIMARY KEY,
        submission_number TEXT,
        customer_name TEXT,
        contact_info TEXT,
        recipient TEXT,
        subject TEXT,
        selected_cards JSONB,
        send_status TEXT DEFAULT 'Pending',
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

    cur.execute("""
    ALTER TABLE card_buyback_items
    ADD COLUMN IF NOT EXISTS offer_amount TEXT
    """)

    cur.execute("""
    ALTER TABLE card_buyback_items
    ADD COLUMN IF NOT EXISTS offer_notes TEXT
    """)

    cur.execute("""
    ALTER TABLE card_buyback_items
    ADD COLUMN IF NOT EXISTS offer_updated_at TIMESTAMP
    """)

    cur.execute("""
    UPDATE submissions
    SET status='Grading Complete',
        last_updated=NOW()
    WHERE status='Shipping Soon'
    """)

    cur.execute("""
    UPDATE submissions
    SET status='Shipped to Giant Sports Cards',
        last_updated=NOW()
    WHERE status='Complete'
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
    text = text.replace("Æ", "f")
    text = text.replace("Ã¦Â", "f")
    text = text.replace("Ã¢\x80\x99", "'")
    text = text.replace(".", "")
    text = re.sub(r"\s+", " ", text)
    return text

def is_dropoff_date_key(key):
    raw = str(key or "").strip()
    normalized = normalize_key_text(raw)

    direct_names = [
        "fand",
        "f and",
        "Æand",
        "Æ and",
        "Ã¦Âand",
        "Ã¦Â and",
        "submission date",
        "customer drop-off date",
        "customer drop off date",
        "drop-off date",
        "drop off date",
        "date"
    ]

    if normalized in direct_names:
        return True

    letters_only = re.sub(r"[^a-z]", "", normalized)

    if letters_only in ["fand", "and"]:
        return True

    if letters_only.startswith("f") and "and" in letters_only and len(letters_only) <= 12:
        return True

    return False

def get_field(data, names):
    for wanted in names:
        for k, v in data.items():
            if str(k).strip().lower() == wanted.strip().lower():
                return v
    return ""


def get(data, names, default=""):
    """
    Compatibility helper for older portal code.

    Supports:
    - get(data, "Customer Name")
    - get(data, ["Customer Name", "Customer", "Name"])
    - get(data, "Customer Name", "Name") style fallback
    """
    data = data or {}

    if isinstance(names, (list, tuple)):
        return get_field(data, list(names)) or default

    # If default was accidentally passed as a second possible field name,
    # treat it as another candidate when it looks like a field label.
    candidates = [names]
    if default and isinstance(default, str) and default not in ["", None]:
        candidates.append(default)

    return get_field(data, candidates) or ""



def get_psa_order_url(data, submission_number=""):
    """Return only an exact PSA URL previously extracted from an uploaded PDF.

    Never construct a URL from submission or order numbers. This prevents the
    dashboard from displaying links that were not actually present in a PSA PDF.
    """
    data = data or {}

    stored_url = str(get_field(data, [
        "PSA Order URL",
        "PSA URL",
        "PSA Link",
        "Order URL"
    ]) or "").strip()
    stored_source = str(get_field(data, ["PSA Order URL Source"]) or "").strip().lower()

    if stored_source != "pdf_embedded":
        return ""

    if re.match(
        r"^https://www\.psacard\.com/myaccount/myorders/\d+(?:/\d+)?(?:[/?#].*)?$",
        stored_url,
        re.IGNORECASE
    ):
        return stored_url

    return ""



def customer_status_label(status):
    if status == "Shipping Soon":
        return "Grading Complete"
    if status == "Complete":
        return "Shipped to Giant Sports Cards"
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
        "Grading Complete",
        "Shipped to Giant Sports Cards",
        "Delivered to Us",
        "Picked Up"
    ]


def customer_status_options():
    return [customer_status_label(s) for s in psa_status_steps()]

def clean_service_display(service):
    value = str(service or "").strip()
    if " - " in value:
        return value.split(" - ", 1)[0].strip()
    if " â " in value:
        return value.split(" â ", 1)[0].strip()
    return value

def date_only_display(value):
    text = str(value or "").strip()

    if not text or text.lower() in ["nan", "none", "null"]:
        return ""

    try:
        numeric = float(text)
        if 25000 <= numeric <= 70000:
            parsed = pd.to_datetime(numeric, unit="D", origin="1899-12-30", errors="coerce")
            if not pd.isna(parsed):
                return parsed.strftime("%Y-%m-%d")
    except Exception:
        pass

    try:
        parsed = pd.to_datetime(text, errors="coerce")
        if not pd.isna(parsed):
            return parsed.strftime("%Y-%m-%d")
    except Exception:
        pass

    if " " in text:
        possible_date = text.split(" ", 1)[0].strip()
        if re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", possible_date):
            return possible_date

    return text

def get_dropoff_date(data):
    data = data or {}

    for k, v in data.items():
        if is_dropoff_date_key(k):
            cleaned = date_only_display(v)
            if cleaned:
                return cleaned

    value = get_field(data, [
        "Customer Drop-Off Date",
        "Customer Drop Off Date",
        "Submission Date",
        "Æand",
        "Æand.",
        "ÃÂand",
        "ÃÂand.",
        "fand",
        "Fand",
        "F and",
        "Date",
        "date"
    ])

    if value:
        return date_only_display(value)

    return ""


def get_psa_received_date(data):
    data = data or {}

    value = get_field(data, [
        "Date PSA Received",
        "PSA Received Date",
        "Received at PSA",
        "PSA Received",
        "Arrived at PSA",
        "Arrived At PSA",
        "Arrived / Completed",
        "Arrived/Completed",
        "Order Arrived",
        "Order Arrived Date"
    ])

    parsed = parse_arrived_completed_value(value)
    if parsed.get("arrived"):
        return parsed["arrived"]

    cleaned = strip_arrived_at_psa_prefix(value)
    if cleaned:
        return cleaned

    return ""


def get_expected_completion_date(data):
    data = data or {}

    value = get_field(data, [
        "Expected Delivery Date",
        "Expected Completion Date",
        "Expected Complete Date",
        "Expected Date",
        "Estimated Completion Date",
        "Est. Complete by",
        "Estimated Complete by",
        "Est Complete by",
        "Est. Completion",
        "Estimated Completion",
        "ETA",
        "Due Date",
        "Arrived / Completed",
        "Arrived/Completed"
    ])

    parsed = parse_arrived_completed_value(value)

    if parsed.get("estimated"):
        return parsed["estimated"]

    if parsed.get("completed"):
        return parsed["completed"]

    completed_value = get_field(data, [
        "Completed Date",
        "Completion Date",
        "Date Completed",
        "Completed",
        "PSA Completed Date"
    ])

    if completed_value:
        completed_parsed = parse_arrived_completed_value(completed_value)
        if completed_parsed.get("completed"):
            return completed_parsed["completed"]

        cleaned_completed = date_only_display(completed_value)
        if cleaned_completed:
            return cleaned_completed

    cleaned = date_only_display(value)
    if cleaned:
        return cleaned

    return ""


def strip_arrived_at_psa_prefix(value):
    text = str(value or "").strip()
    # Remove one or more leading labels, including accidental duplicates.
    while text.lower().startswith("arrived at psa:"):
        text = text.split(":", 1)[1].strip()
    while text.lower().startswith("arrived:"):
        text = text.split(":", 1)[1].strip()
    return text


def parse_arrived_completed_value(value):
    text = str(value or "").strip()
    result = {"arrived": "", "estimated": "", "completed": "", "display": text}

    if not text:
        return result

    month = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|January|February|March|April|May|June|July|August|September|October|November|December)"
    date_single = month + r"\s+\d{1,2},\s+\d{4}"
    date_range_same_year = month + r"\s+\d{1,2}\s*[-â]\s*" + month + r"?\s*\d{1,2},\s+\d{4}"
    date_range_full = date_single + r"\s*[-â]\s*" + date_single

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

    result["arrived"] = strip_arrived_at_psa_prefix(result["arrived"])

    parts = []
    if result["arrived"]:
        parts.append(result["arrived"])
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

    if new_status in ["Complete", "Shipped to Giant Sports Cards"]:
        return (
            f"Giant Sports Cards: Your PSA submission #{submission_number} "
            f"has shipped from PSA to Giant Sports Cards. "
            f"Track it here: {PUBLIC_PORTAL_URL}"
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


def build_buyback_offer_message(submission_number, cert_number, item_details, grade, offer_amount, offer_notes=""):
    item = str(item_details or "").strip()
    grade_text = str(grade or "").strip()
    amount = str(offer_amount or "").strip()
    notes = str(offer_notes or "").strip()

    parts = [
        "Giant Sports Cards: We have a buyback offer ready.",
        f"Submission #{submission_number}."
    ]

    if cert_number:
        parts.append(f"Cert #{cert_number}.")

    if item:
        parts.append(f"Card: {item}.")

    if grade_text:
        parts.append(f"Grade: {grade_text}.")

    if amount:
        parts.append(f"Offer: {amount}.")

    if notes:
        parts.append(f"Note: {notes}.")

    parts.append(f"View and respond here: {PUBLIC_PORTAL_URL}")
    parts.append("Reply STOP to opt out.")

    return " ".join(parts)


def send_buyback_interest_email(customer_name, contact_info, submission_number, selected_cards):
    subject = "PSA Customer Interested In Selling Cards"
    manage_link = f"{PUBLIC_PORTAL_URL}/admin/buyback_requests?queue=interest"

    card_lines = []
    for card in selected_cards:
        cert = str(card.get("cert_number", "")).strip()
        desc = str(card.get("description", "")).strip()
        grade = str(card.get("grade", "")).strip()

        line = f"- Cert #{cert}"
        if desc:
            line += f": {desc}"
        if grade:
            line += f" | Grade: {grade}"
        card_lines.append(line)

    body = f"""A PSA customer selected cards they may be interested in selling.

Name: {customer_name}
Contact Info: {contact_info}
Submission #: {submission_number}

Card(s) with Cert #:
{chr(10).join(card_lines)}

Manage this request and add an offer in the Buyback admin dashboard:
{manage_link}
"""

    if not SMTP_HOST:
        return False, (
            "Email is not configured. Add SMTP_HOST, SMTP_PORT, SMTP_USER, "
            "SMTP_PASSWORD, and SMTP_FROM in Railway Variables."
        )

    if not SMTP_FROM:
        return False, "SMTP_FROM is not configured."

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = SELL_BUYBACK_EMAIL
    msg.set_content(body)

    try:
        use_ssl = SMTP_USE_SSL or SMTP_PORT == 465

        if use_ssl:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=25) as server:
                if SMTP_USER and SMTP_PASSWORD:
                    server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=25) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                if SMTP_USER and SMTP_PASSWORD:
                    server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)

        return True, f"Email sent to {SELL_BUYBACK_EMAIL}"

    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def record_buyback_email_attempt(
    submission_number,
    customer_name,
    contact_info,
    selected_cards,
    sent,
    response
):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO buyback_email_notifications (
            submission_number,
            customer_name,
            contact_info,
            recipient,
            subject,
            selected_cards,
            send_status,
            provider_response,
            sent_at
        )
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s,
                CASE WHEN %s THEN NOW() ELSE NULL END)
        """, (
            submission_number,
            customer_name,
            contact_info,
            SELL_BUYBACK_EMAIL,
            "PSA Customer Interested In Selling Cards",
            json.dumps(selected_cards),
            "Sent" if sent else "Failed",
            response,
            sent
        ))
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass



def queue_buyback_offer_sms(cur, submission_number, cert_number, offer_amount, offer_notes):
    if not offer_amount:
        return False

    cur.execute("""
    SELECT s.raw_data,
           COALESCE(s.sms_opt_in, FALSE),
           COALESCE(s.sms_mode, 'none'),
           COALESCE(c.description, c.item_details, ''),
           COALESCE(c.grade, '')
    FROM submissions s
    JOIN card_buyback_items c
      ON REGEXP_REPLACE(c.submission_number, '\\D', '', 'g') = REGEXP_REPLACE(s.submission_number, '\\D', '', 'g')
    WHERE REGEXP_REPLACE(s.submission_number, '\\D', '', 'g')=%s
      AND REGEXP_REPLACE(c.cert_number, '\\D', '', 'g')=%s
    LIMIT 1
    """, (submission_number, cert_number))

    row = cur.fetchone()
    if not row:
        return False

    raw_data, sms_opt_in, sms_mode, item_details, grade = row
    raw_data = raw_data or {}

    phone = normalize_phone(get_field(raw_data, ["Contact Info", "Phone", "Phone Number"]))
    if not phone:
        return False

    if not sms_opt_in or str(sms_mode or "none").lower() == "none":
        return False

    message = build_buyback_offer_message(
        submission_number,
        cert_number,
        item_details,
        grade,
        offer_amount,
        offer_notes
    )

    send_status, provider_response = send_sms_or_queue(
        submission_number,
        phone,
        "Buyback Interest",
        "Buyback Offer Sent",
        message
    )

    cur.execute("""
    INSERT INTO sms_notifications
        (submission_number, phone, old_status, new_status, message, send_status, provider_response, sent_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, CASE WHEN %s='Sent' THEN NOW() ELSE NULL END)
    """, (
        submission_number,
        phone,
        "Buyback Interest",
        "Buyback Offer Sent",
        message,
        send_status,
        provider_response,
        send_status
    ))

    return True


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
        return "Grading Complete"

    # PSA status-list PDFs sometimes show shipped completed rows as Track Package.
    if s in ["complete", "completed", "track package"]:
        return "Shipped to Giant Sports Cards"

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
        "Grading Complete": 6,
        "Complete": 7,
        "Shipped to Giant Sports Cards": 7,
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
        <a href="/admin/test_buyback_email">Email Test</a>
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
            content:"â";
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


        /* Buyback admin compact professional layout */
        .buyback-admin-table {{
            min-width:760px;
            table-layout:fixed;
            font-size:12px;
        }}
        .buyback-admin-table th:nth-child(1), .buyback-admin-table td:nth-child(1) {{ width:92px; }}
        .buyback-admin-table th:nth-child(2), .buyback-admin-table td:nth-child(2) {{ width:82px; }}
        .buyback-admin-table th:nth-child(3), .buyback-admin-table td:nth-child(3) {{ width:145px; }}
        .buyback-admin-table th:nth-child(4), .buyback-admin-table td:nth-child(4) {{ width:105px; }}
        .buyback-admin-table th:nth-child(5), .buyback-admin-table td:nth-child(5) {{ width:220px; }}
        .buyback-admin-table th:nth-child(6), .buyback-admin-table td:nth-child(6) {{ width:75px; }}
        .buyback-admin-table th:nth-child(7), .buyback-admin-table td:nth-child(7) {{ width:185px; }}
        .buyback-admin-table th:nth-child(8), .buyback-admin-table td:nth-child(8) {{ width:140px; }}
        .buyback-main-desc {{
            white-space:normal;
            overflow-wrap:break-word;
            word-break:break-word;
            line-height:1.35;
        }}
        .buyback-details {{
            margin-top:8px;
        }}
        .buyback-details summary {{
            cursor:pointer;
            color:#0f5132;
            font-weight:900;
            display:inline-block;
            padding:4px 8px;
            border-radius:7px;
            background:#eef6f2;
        }}
        .buyback-details-box {{
            margin-top:8px;
            background:#f9fafb;
            border:1px solid #e5e7eb;
            border-radius:8px;
            padding:9px;
            white-space:normal;
            line-height:1.4;
            color:#374151;
        }}
        .buyback-offer-form input {{
            width:150px !important;
            max-width:100%;
            box-sizing:border-box;
        }}
        .buyback-actions {{
            display:flex;
            flex-direction:column;
            gap:6px;
            min-width:125px;
        }}
        .buyback-actions button {{
            margin:0;
            padding:7px 9px;
            border-radius:6px;
            border:1px solid #cbd5e1;
            background:#f3f4f6;
            font-weight:bold;
            cursor:pointer;
        }}
        .buyback-actions button.primary {{
            background:#198754;
            color:white;
            border:0;
        }}


        /* Mobile portal form icon color consistency */

        /* HEADER ONLY SAFE PATCH */
        body.admin-body .topbar,
        body.portal-body .topbar {{
            background:#f7faf6 !important;
            color:#06442d !important;
            border-bottom:1px solid #d7dfd9 !important;
            box-shadow:0 8px 24px rgba(15,81,50,.08) !important;
        }}

        body.admin-body .brand,
        body.portal-body .brand {{
            color:#06442d !important;
        }}

        body.admin-body .brand span,
        body.portal-body .brand span {{
            color:#06442d !important;
            font-weight:950 !important;
        }}

        body.admin-body .brand img,
        body.portal-body .brand img {{
            max-height: 105px !important;
            max-width: 320px !important;
            filter:brightness(0) saturate(100%) invert(20%) sepia(44%) saturate(1023%) hue-rotate(105deg) brightness(89%) contrast(93%)
                   drop-shadow(0 1px 0 #ffffff)
                   drop-shadow(1px 0 0 #ffffff)
                   drop-shadow(-1px 0 0 #ffffff)
                   drop-shadow(0 -1px 0 #ffffff) !important;
        }}

        body.admin-body .links a,
        body.portal-body .links a {{
            color:#06442d !important;
            background:#ffffff !important;
            border:1px solid #cbd5ce !important;
            border-radius:10px !important;
            box-shadow:0 4px 12px rgba(15,81,50,.06) !important;
            font-weight:900 !important;
        }}

        body.admin-body .links a:hover,
        body.portal-body .links a:hover {{
            background:#eef6f2 !important;
            color:#06442d !important;
        }}


        /* PORTAL LANDING FIX ONLY */
        body.portal-body .gsc-logo-badge {{
            background:transparent !important;
            box-shadow:none !important;
            border-radius:0 !important;
            padding:0 !important;
            margin:0 0 14px !important;
        }}

        body.portal-body .gsc-redesign-logo {{
            width:270px !important;
            height:auto !important;
            filter:brightness(0) saturate(100%) invert(20%) sepia(44%) saturate(1023%) hue-rotate(105deg) brightness(89%) contrast(93%)
                   drop-shadow(0 1px 0 #ffffff)
                   drop-shadow(1px 0 0 #ffffff)
                   drop-shadow(-1px 0 0 #ffffff)
                   drop-shadow(0 -1px 0 #ffffff) !important;
        }}

        body.portal-body .gsc-menu-circle {{
            display:none !important;
        }}

        body.portal-body .gsc-redesign-page {{
            background:
                radial-gradient(circle at 84% 16%, rgba(15,81,50,.12), transparent 27%),
                linear-gradient(180deg, #fbfaf4 0%, #eef4ee 100%) !important;
        }}

        body.portal-body .gsc-benefit-dot {{
            width:42px !important;
            height:42px !important;
            border-radius:13px !important;
            background:linear-gradient(180deg, #eaf5ee 0%, #d9ecdf 100%) !important;
            border:1px solid #bfd8c8 !important;
            position:relative !important;
        }}

        body.portal-body .gsc-benefit:nth-child(1) .gsc-benefit-dot:before {{
            content:"" !important;
            position:absolute !important;
            width:20px !important;
            height:20px !important;
            border:3px solid #0f5132 !important;
            border-radius:50% !important;
            left:8px !important;
            top:8px !important;
            box-sizing:border-box !important;
        }}

        body.portal-body .gsc-benefit:nth-child(1) .gsc-benefit-dot:after {{
            content:"" !important;
            position:absolute !important;
            width:10px !important;
            height:8px !important;
            left:19px !important;
            top:13px !important;
            border-left:3px solid #198754 !important;
            border-bottom:3px solid #198754 !important;
            transform:rotate(-45deg) !important;
            box-sizing:border-box !important;
        }}

        body.portal-body .gsc-benefit:nth-child(2) .gsc-benefit-dot:before {{
            content:"" !important;
            position:absolute !important;
            left:10px !important;
            top:7px !important;
            width:22px !important;
            height:26px !important;
            background:#0f5132 !important;
            clip-path:polygon(50% 0%, 88% 15%, 82% 63%, 50% 100%, 18% 63%, 12% 15%) !important;
        }}

        body.portal-body .gsc-benefit:nth-child(2) .gsc-benefit-dot:after {{
            content:"" !important;
            position:absolute !important;
            left:17px !important;
            top:17px !important;
            width:10px !important;
            height:6px !important;
            border-left:3px solid #ffffff !important;
            border-bottom:3px solid #ffffff !important;
            transform:rotate(-45deg) !important;
        }}

        @media (min-width: 900px) {{
            body.portal-body .gsc-redesign-shell {{
                max-width:1120px !important;
                display:grid !important;
                grid-template-columns:minmax(0, 1fr) minmax(380px, 470px) !important;
                gap:34px 48px !important;
                align-items:center !important;
            }}

            body.portal-body .gsc-benefits {{
                grid-column:1 / -1 !important;
                max-width:820px !important;
                margin:22px auto 0 !important;
                grid-template-columns:1fr 1fr !important;
            }}

            body.portal-body .gsc-redesign-title {{
                font-size:52px !important;
                max-width:520px !important;
            }}
        }}

        @media (max-width: 700px) {{
            .gsc-field-icon,
            .safe-field-icon,
            .portal-field-icon,
            .input-icon,
            .form-icon {{
                color:#6b7280 !important;
                background:#f3f4f6 !important;
                border-color:#d1d5db !important;
                opacity:.85 !important;
            }}
        }}

        @media (max-width: 700px) {{
            .topbar {{
                align-items:flex-start;
            }}
            .brand {{
                min-width:100%;
            }}
            .brand {{
                min-width:100%;
                flex-direction:column;
                align-items:flex-start;
                gap:6px;
            }}
            .brand span {{
                white-space:normal;
                font-size:20px;
                line-height:1.15;
                max-width:100%;
            }}
            .brand img {{
                max-height:105px;
                max-width:100%;
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
    
        /* CUSTOMER RESULTS TOPBAR LOGO SIZE FIX */
        body.portal-body .topbar .brand img {{
            max-height:105px !important;
            max-width:320px !important;
            height:auto !important;
        }}
        @media (max-width: 700px) {{
            body.portal-body .topbar .brand img {{
                max-height:95px !important;
                max-width:260px !important;
            }}
        }}

        /* CUSTOMER RESULTS LOGO FINAL SIZE */
        body.portal-body .portal-results-logo {{
            width:190px !important;
            max-width:65vw !important;
            height:auto !important;
        }}
        @media (max-width: 700px) {{
            body.portal-body .portal-results-logo {{
                width:160px !important;
            }}
        }}

        /* CUSTOMER PORTAL POLISH FINAL */
        .status-badge {{
            display:inline-flex !important;
            align-items:center !important;
            gap:6px !important;
            background:#d1e7dd !important;
            color:#0f5132 !important;
            border:1px solid #9fd0b4 !important;
            border-radius:999px !important;
            padding:6px 10px !important;
            font-weight:900 !important;
            line-height:1 !important;
        }}
        body.portal-body .card h3 {{
            color:#0f5132 !important;
            font-weight:950 !important;
            letter-spacing:.1px !important;
        }}
        body.portal-body .card p {{
            line-height:1.45 !important;
        }}
        body.portal-body .card b {{
            color:#111827 !important;
        }}
        body.portal-body .sell-check {{
            font-weight:800 !important;
            color:#1f2937 !important;
        }}
        body.portal-body button[type="submit"] {{
            background:#198754 !important;
            color:#ffffff !important;
            border:0 !important;
            border-radius:10px !important;
            font-weight:900 !important;
            cursor:pointer !important;
        }}

        /* DASHBOARD ADVANCED FILTERS */
        .filterbar input[type="month"],
        .filterbar input[name="q"],
        .filterbar input[name="card_count"] {{
            padding:9px 10px;
            border:1px solid #cbd5e1;
            border-radius:8px;
            background:white;
            font-size:14px;
            box-sizing:border-box;
        }}
        .filterbar input[name="q"] {{
            min-width:260px;
        }}

        /* STATUS BADGE SYMBOL FIX */
        .status-badge {{
            display:inline-flex !important;
            align-items:center !important;
            background:#d1e7dd !important;
            color:#0f5132 !important;
            border:1px solid #9fd0b4 !important;
            border-radius:999px !important;
            padding:6px 12px !important;
            font-weight:900 !important;
            line-height:1.1 !important;
        }}

        /* BUYBACK HEADER SYMBOL FIX */
        body.portal-body .buyback-collapsible summary {{
            position:relative !important;
            padding-right:18px !important;
        }}
        body.portal-body .buyback-collapsible summary::after {{
            content:"" !important;
            display:none !important;
        }}

        /* GRADED CARDS BUYBACK POLISH */
        .buyback-success {{
            border:1px solid #9fd0b4 !important;
            background:#eef8f2 !important;
        }}
        .buyback-success h3 {{
            color:#0f5132 !important;
            margin-top:0 !important;
        }}

        /* BUYBACK EMAIL DELIVERY STATUS */
        .buyback-email-error {{
            border:1px solid #f1aeb5 !important;
            background:#fff1f2 !important;
        }}
        .buyback-email-error h3 {{
            color:#b42318 !important;
            margin-top:0 !important;
        }}

        /* PSA ORDER DASHBOARD LINK */
        .psa-order-link {{
            color:#0f6848 !important;
            text-decoration:underline !important;
            text-decoration-thickness:1.5px !important;
            text-underline-offset:3px !important;
            font-weight:950 !important;
        }}
        .psa-order-link:hover {{
            color:#198754 !important;
        }}
</style>
    </head>
    <body class="{mode}-body">
        <div class="topbar">
            <div class="brand"><img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAA4QAAAOECAYAAAD5Tv87AAAABGdBTUEAALGPC/xhBQAACktpQ0NQc1JHQiBJRUM2MTk2Ni0yLjEAAEiJnVNnVFPpFj333vRCS4iAlEtvUhUIIFJCi4BUaaISkgChhBgSQOyIqMCIoiKCFRkUccDREZCxIoqFQbH3AXkIKOPgKDZU3g/eGn2z5r03b/avvfY5Z53vnH0+AEZgsESahaoBZEoV8ogAHzw2Lh4ndwMKVCCBA4BAmC0LifSPAgDg+/Hw7IgAH/gCBODNbUAAAG7YBIbhOPx/UBfK5AoAJAwApovE2UIApBAAMnIVMgUAMgoA7KR0mQIAJQAAWx4bFw+AagEAO2WSTwMAdtIk9wIAtihTKgJAowBAJsoUiQDQDgBYl6MUiwCwYAAoypGIcwGwmwBgkqHMlABg7wCAnSkWZAMQGABgohALUwEI9gDAkEdF8AAIMwEojJSveNJXXCHOUwAA8LJki+WSlFQFbiG0xB1cXbl4oDg3Q6xQ2IQJhOkCuQjnZWXKBNLFAJMzAwCARnZEgA/O9+M5O7g6O9s42jp8taj/GvyLiI2L/5c/r8IBAQCE0/VF+7O8rBoA7hgAtvGLlrQdoGUNgNb9L5rJHgDVQoDmq1/Nw+H78fBUhULmZmeXm5trKxELbYWpX/X5nwl/AV/1s+X78fDf14P7ipMFygwFHhHggwuzMrKUcjxbJhCKcZs/HvHfLvzzd0yLECeL5WKpUIxHS8S5EmkKzsuSiiQKSZYUl0j/k4l/s+wPmLxrAGDVfgb2QltQu8oG7JcuILDogCXsAgDkd9+CqdEQBgAxBoOTdw8AMPmb/x1oGQCg2ZIUHACAFxGFC5XynMkYAQCACDRQBTZogz4YgwXYgCO4gDt4gR/MhlCIgjhYAEJIhUyQQy4shVVQBCWwEbZCFeyGWqiHRjgCLXACzsIFuALX4BY8gF4YgOcwCm9gHEEQMsJEWIg2YoCYItaII8JFZiF+SDASgcQhiUgKIkWUyFJkNVKClCNVyF6kHvkeOY6cRS4hPcg9pA8ZRn5DPqAYykDZqB5qhtqhXNQbDUKj0PloCroIzUcL0Q1oJVqDHkKb0bPoFfQW2os+R8cwwOgYBzPEbDAuxsNCsXgsGZNjy7FirAKrwRqxNqwTu4H1YiPYewKJwCLgBBuCOyGQMJcgJCwiLCeUEqoIBwjNhA7CDUIfYZTwmcgk6hKtiW5EPjGWmELMJRYRK4h1xGPE88RbxAHiGxKJxCGZk1xIgaQ4UhppCamUtJPURDpD6iH1k8bIZLI22ZrsQQ4lC8gKchF5O/kQ+TT5OnmA/I5CpxhQHCn+lHiKlFJAqaAcpJyiXKcMUsapalRTqhs1lCqiLqaWUWupbdSr1AHqOE2dZk7zoEXR0miraJW0Rtp52kPaKzqdbkR3pYfTJfSV9Er6YfpFeh/9PUODYcXgMRIYSsYGxn7GGcY9xismk2nG9GLGMxXMDcx65jnmY+Y7FZaKrQpfRaSyQqVapVnlusoLVaqqqaq36gLVfNUK1aOqV1VH1KhqZmo8NYHacrVqteNqd9TG1FnqDuqh6pnqpeoH1S+pD2mQNcw0/DREGoUa+zTOafSzMJYxi8cSslazalnnWQNsEtuczWensUvY37G72aOaGpozNKM18zSrNU9q9nIwjhmHz8nglHGOcG5zPkzRm+I9RTxl/ZTGKdenvNWaquWlJdYq1mrSuqX1QRvX9tNO196k3aL9SIegY6UTrpOrs0vnvM7IVPZU96nCqcVTj0y9r4vqWulG6C7R3afbpTump68XoCfT2653Tm9En6PvpZ+mv0X/lP6wActgloHEYIvBaYNnuCbujWfglXgHPmqoaxhoqDTca9htOG5kbjTXqMCoyeiRMc2Ya5xsvMW43XjUxMAkxGSpSYPJfVOqKdc01XSbaafpWzNzsxiztWYtZkPmWuZ883zzBvOHFkwLT4tFFjUWNy1JllzLdMudltesUCsnq1Sraqur1qi1s7XEeqd1zzTiNNdp0mk10+7YMGy8bXJsGmz6bDm2wbYFti22L+xM7OLtNtl12n22d7LPsK+1f+Cg4TDbocChzeE3RytHoWO1483pzOn+01dMb53+cob1DPGMXTPuOrGcQpzWOrU7fXJ2cZY7NzoPu5i4JLrscLnDZXPDuKXci65EVx/XFa4nXN+7Obsp3I64/epu457uftB9aKb5TPHM2pn9HkYeAo+9Hr2z8FmJs/bM6vU09BR41ng+8TL2EnnVeQ16W3qneR/yfuFj7yP3OebzlufGW8Y744v5BvgW+3b7afjN9avye+xv5J/i3+A/GuAUsCTgTCAxMChwU+Advh5fyK/nj852mb1sdkcQIygyqCroSbBVsDy4LQQNmR2yOeThHNM50jktoRDKD90c+ijMPGxR2I/hpPCw8OrwpxEOEUsjOiNZkQsjD0a+ifKJKot6MNdirnJue7RqdEJ0ffTbGN+Y8pjeWLvYZbFX4nTiJHGt8eT46Pi6+LF5fvO2zhtIcEooSrg933x+3vxLC3QWZCw4uVB1oWDh0URiYkziwcSPglBBjWAsiZ+0I2lUyBNuEz4XeYm2iIbFHuJy8WCyR3J58lCKR8rmlOFUz9SK1BEJT1IleZkWmLY77W16aPr+9ImMmIymTEpmYuZxqYY0XdqRpZ+Vl9Ujs5YVyXoXuS3aumhUHiSvy0ay52e3KtgKmaJLaaFco+zLmZVTnfMuNzr3aJ56njSva7HV4vWLB/P9879dQlgiXNK+1HDpqqV9y7yX7V2OLE9a3r7CeEXhioGVASsPrKKtSl/1U4F9QXnB69Uxq9sK9QpXFvavCVjTUKRSJC+6s9Z97e51hHWSdd3rp6/fvv5zsaj4col9SUXJx1Jh6eVvHL6p/GZiQ/KG7jLnsl0bSRulG29v8tx0oFy9PL+8f3PI5uYt+JbiLa+3Ltx6qWJGxe5ttG3Kbb2VwZWt2022b9z+sSq16la1T3XTDt0d63e83SnaeX2X167G3Xq7S3Z/2CPZc3dvwN7mGrOain2kfTn7ntZG13Z+y/22vk6nrqTu037p/t4DEQc66l3q6w/qHixrQBuUDcOHEg5d+873u9ZGm8a9TZymksNwWHn42feJ398+EnSk/Sj3aOMPpj/sOMY6VtyMNC9uHm1JbeltjWvtOT77eHube9uxH21/3H/C8ET1Sc2TZadopwpPTZzOPz12RnZm5GzK2f72he0PzsWeu9kR3tF9Puj8xQv+F851eneevuhx8cQlt0vHL3Mvt1xxvtLc5dR17Cenn451O3c3X3W52nrN9Vpbz8yeU9c9r5+94Xvjwk3+zSu35tzquT339t07CXd674ruDt3LuPfyfs798QcrHxIfFj9Se1TxWPdxzc+WPzf1Ovee7PPt63oS+eRBv7D/+T+y//FxoPAp82nFoMFg/ZDj0Ilh/+Frz+Y9G3guez4+UvSL+i87Xli8+OFXr1+7RmNHB17KX078VvpK+9X+1zNet4+FjT1+k/lm/G3xO+13B95z33d+iPkwOJ77kfyx8pPlp7bPQZ8fTmROTPwTA5jz/IzFdaUAAAAgY0hSTQAAeiYAAICEAAD6AAAAgOgAAHUwAADqYAAAOpgAABdwnLpRPAAAAAlwSFlzAAAuIwAALiMBeKU/dgAABR1pVFh0WE1MOmNvbS5hZG9iZS54bXAAAAAAADw/eHBhY2tldCBiZWdpbj0i77u/IiBpZD0iVzVNME1wQ2VoaUh6cmVTek5UY3prYzlkIj8+IDx4OnhtcG1ldGEgeG1sbnM6eD0iYWRvYmU6bnM6bWV0YS8iIHg6eG1wdGs9IkFkb2JlIFhNUCBDb3JlIDkuMS1jMDAxIDc5LmE4ZDQ3NTM0OSwgMjAyMy8wMy8yMy0xMzowNTo0NSAgICAgICAgIj4gPHJkZjpSREYgeG1sbnM6cmRmPSJodHRwOi8vd3d3LnczLm9yZy8xOTk5LzAyLzIyLXJkZi1zeW50YXgtbnMjIj4gPHJkZjpEZXNjcmlwdGlvbiByZGY6YWJvdXQ9IiIgeG1sbnM6eG1wPSJodHRwOi8vbnMuYWRvYmUuY29tL3hhcC8xLjAvIiB4bWxuczpkYz0iaHR0cDovL3B1cmwub3JnL2RjL2VsZW1lbnRzLzEuMS8iIHhtbG5zOnBob3Rvc2hvcD0iaHR0cDovL25zLmFkb2JlLmNvbS9waG90b3Nob3AvMS4wLyIgeG1sbnM6eG1wTU09Imh0dHA6Ly9ucy5hZG9iZS5jb20veGFwLzEuMC9tbS8iIHhtbG5zOnN0RXZ0PSJodHRwOi8vbnMuYWRvYmUuY29tL3hhcC8xLjAvc1R5cGUvUmVzb3VyY2VFdmVudCMiIHhtcDpDcmVhdG9yVG9vbD0iQWRvYmUgUGhvdG9zaG9wIDI0LjYgKE1hY2ludG9zaCkiIHhtcDpDcmVhdGVEYXRlPSIyMDIzLTEwLTE2VDE3OjAzOjM2LTA0OjAwIiB4bXA6TW9kaWZ5RGF0ZT0iMjAyMy0xMC0xNlQxNzowOTowOC0wNDowMCIgeG1wOk1ldGFkYXRhRGF0ZT0iMjAyMy0xMC0xNlQxNzowOTowOC0wNDowMCIgZGM6Zm9ybWF0PSJpbWFnZS9wbmciIHBob3Rvc2hvcDpDb2xvck1vZGU9IjMiIHBob3Rvc2hvcDpJQ0NQcm9maWxlPSJzUkdCIElFQzYxOTY2LTIuMSIgeG1wTU06SW5zdGFuY2VJRD0ieG1wLmlpZDpjMTkwMjJiZC1jNWU5LTRhY2YtYWYwMy0wMzNiODBmOGVlZWMiIHhtcE1NOkRvY3VtZW50SUQ9InhtcC5kaWQ6YzE5MDIyYmQtYzVlOS00YWNmLWFmMDMtMDMzYjgwZjhlZWVjIiB4bXBNTTpPcmlnaW5hbERvY3VtZW50SUQ9InhtcC5kaWQ6YzE5MDIyYmQtYzVlOS00YWNmLWFmMDMtMDMzYjgwZjhlZWVjIj4gPHhtcE1NOkhpc3Rvcnk+IDxyZGY6U2VxPiA8cmRmOmxpIHN0RXZ0OmFjdGlvbj0iY3JlYXRlZCIgc3RFdnQ6aW5zdGFuY2VJRD0ieG1wLmlpZDpjMTkwMjJiZC1jNWU5LTRhY2YtYWYwMy0wMzNiODBmOGVlZWMiIHN0RXZ0OndoZW49IjIwMjMtMTAtMTZUMTc6MDM6MzYtMDQ6MDAiIHN0RXZ0OnNvZnR3YXJlQWdlbnQ9IkFkb2JlIFBob3Rvc2hvcCAyNC42IChNYWNpbnRvc2gpIi8+IDwvcmRmOlNlcT4gPC94bXBNTTpIaXN0b3J5PiA8L3JkZjpEZXNjcmlwdGlvbj4gPC9yZGY6UkRGPiA8L3g6eG1wbWV0YT4gPD94cGFja2V0IGVuZD0iciI/PkD4qSIAAEWUSURBVHic7d13uGVXQT7+NyShJSEhIQmElANEICBFpCiEZsEIiIiigEiTooJSFAWCNGkCShEU+UoRRPpPFBREqgJioSO9BAgEk0AKAVJnfn+sjDN35s7Mvefsvdfee30+zzOPkdy795s7+56z37PWXmufrVu3BgAAgPZcpnYAAAAA6lAIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQ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UQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjVIIAQAAGqUQAgAANEohBAAAaJRCCAAA0CiFEAAAoFEKIQAAQKMUQgAAgEYphAAAAI1SCAEAABqlEAIAADRKIQQAAGiUQggAANAohRAAAKBRCiEAAECjFEIAAIBGKYQAAACNUggBAAAapRACAAA0SiEEAABolEIIAADQKIUQAACgUQohAABAoxRCAACARimEAAAAjfr/ARwPrTF8DYWaAAAAAElFTkSuQmCC" alt="Giant Sports Cards"> <span>PSA Submission Tracker</span></div>
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
    return (status or "") in [
        "Shipping Soon",
        "Grading Complete",
        "Complete",
        "Shipped to Giant Sports Cards"
    ]


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
            return "Grading complete / card PDF needed"
        if status == "Complete":
            return "Shipped / card PDF needed"
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
            <th>Customer Drop-Off</th>
            <th>Est. Complete</th>
            <th>Details</th>
        </tr>
    """

    for row in rows:
        raw_data = row[0] if len(row) > 0 else {}
        status = row[1] if len(row) > 1 else "Submitted"
        data = raw_data or {}

        sub = normalize_submission(get_field(data, ["Submission #", "Submission Number"])) or ""
        psa_order_url = get_psa_order_url(data, sub)
        if psa_order_url:
            submission_html = (
                f"<a href='{html_escape(psa_order_url)}' target='_blank' rel='noopener noreferrer' "
                f"class='psa-order-link' title='Open this order on PSA'>{html_escape(sub)}</a>"
            )
        else:
            submission_html = html_escape(sub)

        customer = get_field(data, ["Customer Name", "Name"])
        phone = get_field(data, ["Contact Info", "Phone", "Phone Number"])
        cards = get_field(data, ["# Of Cards", "# of Cards", "Cards"])
        service = clean_service_display(get_field(data, ["Service Type", "Service"]))
        dropoff = get_dropoff_date(data)
        display_status = customer_status_label(status or "Submitted")

        arrived_completed_raw = get_psa_received_date(data)
        arrived_completed_data = parse_arrived_completed_value(arrived_completed_raw)
        estimated_completion = get_expected_completion_date(data)

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
            details_html = "<span style='color:#6b7280;'>â</span>"
        else:
            details_html = "<details class='row-details'><summary>Details</summary><div>" + "<br>".join(details_parts[:12]) + "</div></details>"

        html += f"""
        <tr>
            <td><b>{submission_html}</b></td>
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
    try:
        data = row[0] if len(row) > 0 else {}
    except Exception:
        data = {}

    value = get_dropoff_date(data)

    try:
        parsed = pd.to_datetime(value, errors="coerce")
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
    - Finds cards from page text lines so it does not return 0 when Cert # lines exist.
    - Matches PSA embedded thumbnail images by page + vertical row position.
    - Uses rendered crop only as fallback when no embedded thumbnail is available.
    - Preserves wrapped description continuation lines such as "AU".
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

    def to_data_uri(image_bytes, ext="png"):
        if not image_bytes:
            return ""
        try:
            ext = str(ext or "png").lower().replace("jpg", "jpeg")
            if ext not in ["png", "jpeg", "webp"]:
                ext = "png"
            return f"data:image/{ext};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        except Exception:
            return ""

    grade_regex = re.compile(
        r"^(?:"
        r"GEM\s+MINT|MINT|NEAR\s+MINT(?:-MINT)?|EXCELLENT(?:-MINT)?|"
        r"EX(?:-MT)?|VERY\s+GOOD|GOOD|FAIR|POOR|PR|AUTHENTIC|"
        r"N\d+|NM(?:-MT)?|VG(?:-EX)?"
        r")\b.*",
        re.IGNORECASE
    )

    def is_grade_line(text):
        return bool(grade_regex.search(norm_text(text)))

    def is_bad_description_line(text):
        t = norm_text(text)
        low = t.lower()

        if not t:
            return True

        bad_fragments = [
            "due to extraordinary demand",
            "learn more",
            "value services",
            "value (1980-present) order",
            "order #",
            "submission #",
            "status",
            "complete",
            "completed jun",
            "completed ",
            "track package",
            "tracking number",
            "items",
            "please make sure",
            "download csv",
            "view grades",
            "your grades are ready",
            "show more",
            "psa dna",
            "psa d",
            "certified",
            "cert #",
            "payment",
            "address",
            "amex",
            "send to vault",
            "vault terms",
            "collectors",
            "all rights reserved",
            "https://",
            "customer service",
            "shipped back",
            "ship back",
            "grader notes",
            "card ladder",
            "psa estimate",
            "pop ",
        ]

        if any(x in low for x in bad_fragments):
            return True

        if is_grade_line(t):
            return True

        return False

    def get_cert_y(page, cert_number, fallback_y=None):
        for needle in [f"Cert #{cert_number}", f"#{cert_number}", cert_number]:
            try:
                rects = page.search_for(needle)
                if rects:
                    return (rects[0].y0 + rects[0].y1) / 2
            except Exception:
                pass
        return fallback_y

    def extract_page_image_blocks(page):
        images = []

        try:
            page_dict = page.get_text("dict")
            for block in page_dict.get("blocks", []):
                if block.get("type") != 1:
                    continue

                bbox = block.get("bbox") or (0, 0, 0, 0)
                x0, y0, x1, y1 = bbox
                box_w = abs(x1 - x0)
                box_h = abs(y1 - y0)
                width_px = int(block.get("width") or 0)
                height_px = int(block.get("height") or 0)

                if width_px <= 0 or height_px <= 0:
                    continue

                # PSA card thumbnails are small vertical images in the left item column.
                # Keep this flexible for different browser/PDF scaling.
                if x0 > 160:
                    continue
                if box_w < 8 or box_w > 125:
                    continue
                if box_h < 18 or box_h > 155:
                    continue
                if width_px < 35 or height_px < 45:
                    continue

                # Exclude banners/logos/backgrounds/wide images.
                if width_px > height_px * 1.65:
                    continue

                image_data = to_data_uri(block.get("image"), block.get("ext", "png"))
                if not image_data:
                    continue

                images.append({
                    "image_data": image_data,
                    "x0": x0,
                    "y0": y0,
                    "x1": x1,
                    "y1": y1,
                    "y_mid": (y0 + y1) / 2,
                    "width_px": width_px,
                    "height_px": height_px,
                })
        except Exception:
            pass

        images.sort(key=lambda img: (img["y_mid"], img["x0"]))
        return images

    def crop_thumbnail_from_page(page, cert_y, row_index=None, rows_on_page=None):
        try:
            page_rect = page.rect

            if cert_y is None:
                if rows_on_page and row_index is not None:
                    start_y = 445 if page.number == 0 else 65
                    spacing = 90
                    cert_y = start_y + (row_index * spacing) + 35
                else:
                    return ""

            # Flexible visible thumbnail crop. This is fallback only.
            x0 = 24
            x1 = 88
            y0 = max(0, cert_y - 52)
            y1 = min(page_rect.height, cert_y + 18)

            clip = fitz.Rect(x0, y0, x1, y1)
            pix = page.get_pixmap(matrix=fitz.Matrix(4, 4), clip=clip, alpha=False)

            if pix.width < 40 or pix.height < 55:
                return ""

            return to_data_uri(pix.tobytes("png"), "png")
        except Exception:
            return ""

    full_text_parts = []
    page_infos = []

    for page in doc:
        try:
            text = page.get_text("text") or ""
        except Exception:
            text = ""

        page_infos.append({
            "page": page,
            "text": text,
            "images": extract_page_image_blocks(page),
        })
        full_text_parts.append(text)

    full_text = "\n".join(full_text_parts)

    sub_match = re.search(r"Submission\s*#\s*(\d+)", full_text, re.IGNORECASE)
    if sub_match:
        submission_number = normalize_submission(sub_match.group(1)) or ""

    order_match = re.search(r"Order\s*#\s*(\d+)", full_text, re.IGNORECASE)
    if order_match:
        order_number = normalize_submission(order_match.group(1)) or ""

    seen_certs = set()
    used_images_by_page = {}

    for page_index, info in enumerate(page_infos):
        page = info["page"]
        page_text = info["text"]
        page_images = info["images"]
        used_images = used_images_by_page.setdefault(page_index, set())

        lines = [norm_text(line) for line in page_text.splitlines()]
        lines = [line for line in lines if line]

        cert_line_positions = []
        for i, line in enumerate(lines):
            cert_match = re.search(r"Cert\s*#\s*(\d+)", line, re.IGNORECASE)
            if cert_match:
                cert_number = normalize_submission(cert_match.group(1)) or ""
                if cert_number and cert_number not in seen_certs:
                    cert_line_positions.append((i, cert_number, line))

        rows_on_page = len(cert_line_positions)

        for row_index, (line_index, cert_number, cert_line) in enumerate(cert_line_positions):
            if cert_number in seen_certs:
                continue

            prev_lines = lines[max(0, line_index - 8):line_index]

            grade = ""
            for prev in reversed(prev_lines):
                if is_grade_line(prev):
                    grade = prev
                    break

            desc_candidates = []
            grade_seen = False
            for prev in prev_lines:
                if is_grade_line(prev):
                    grade_seen = True
                    continue

                if grade and not grade_seen:
                    continue

                if is_bad_description_line(prev):
                    continue

                if len(prev) < 8 and not desc_candidates:
                    continue

                desc_candidates.append(prev)

            if desc_candidates:
                if len(desc_candidates) >= 2 and len(desc_candidates[-1]) <= 5:
                    description = norm_text(desc_candidates[-2] + " " + desc_candidates[-1])
                else:
                    description = norm_text(
                        " ".join(desc_candidates[-2:])
                        if len(desc_candidates) >= 2 and len(desc_candidates[-1]) <= 18
                        else desc_candidates[-1]
                    )
            else:
                description = ""

            cert_y = get_cert_y(page, cert_number, None)

            image_data = ""

            # Primary mapping: closest unused embedded thumbnail image by vertical row.
            if cert_y is not None and page_images:
                candidates = []
                for image_index, img in enumerate(page_images):
                    if image_index in used_images:
                        continue

                    dy = abs(img["y_mid"] - cert_y)

                    # Broad enough for different PSA print layouts.
                    if dy <= 135:
                        candidates.append((dy, img["y_mid"], image_index, img))

                if candidates:
                    candidates.sort(key=lambda x: (x[0], x[1]))
                    _, _, image_index, img = candidates[0]
                    used_images.add(image_index)
                    image_data = img["image_data"]

            # Fallback by row order on same page if cert_y search failed.
            if not image_data and page_images:
                available = [(idx, img) for idx, img in enumerate(page_images) if idx not in used_images]
                available.sort(key=lambda x: (x[1]["y_mid"], x[1]["x0"]))
                if row_index < len(available):
                    image_index, img = available[row_index]
                    used_images.add(image_index)
                    image_data = img["image_data"]

            # Last fallback: visible crop.
            if not image_data:
                image_data = crop_thumbnail_from_page(page, cert_y, row_index=row_index, rows_on_page=rows_on_page)

            items.append({
                "submission_number": submission_number,
                "order_number": order_number,
                "cert_number": cert_number,
                "card_type": "Card",
                "description": description,
                "item_details": description,
                "grade": grade,
                "after_service": "",
                "images_url": "",
                "image_data": image_data,
                "psa_estimate": "",
                "card_ladder_value": "",
                "pop": "",
                "pop_higher": ""
            })

            seen_certs.add(cert_number)

    if not items:
        certs = re.findall(r"Cert\s*#\s*(\d+)", full_text, re.IGNORECASE)
        for cert_number in certs:
            cert_number = normalize_submission(cert_number) or ""
            if not cert_number or cert_number in seen_certs:
                continue
            items.append({
                "submission_number": submission_number,
                "order_number": order_number,
                "cert_number": cert_number,
                "card_type": "Card",
                "description": "",
                "item_details": "",
                "grade": "",
                "after_service": "",
                "images_url": "",
                "image_data": "",
                "psa_estimate": "",
                "card_ladder_value": "",
                "pop": "",
                "pop_higher": ""
            })
            seen_certs.add(cert_number)

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
           COALESCE(pop_higher, ''),
           COALESCE(offer_amount, ''),
           COALESCE(offer_notes, ''),
           COALESCE(buyback_status, 'New')
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



@app.route("/admin/test_buyback_email", methods=["GET", "POST"])
@admin_required
def admin_test_buyback_email():
    if request.method == "POST":
        test_cards = [{
            "cert_number": "TEST-12345678",
            "description": "Test PSA Card",
            "grade": "PSA 10"
        }]

        sent, response = send_buyback_interest_email(
            "Giant Sports Cards Test",
            "Test from Admin Dashboard",
            "TEST",
            test_cards
        )

        record_buyback_email_attempt(
            "TEST",
            "Giant Sports Cards Test",
            "Test from Admin Dashboard",
            test_cards,
            sent,
            response
        )

        color = "#198754" if sent else "#dc3545"
        heading = "Test Email Sent" if sent else "Test Email Failed"

        return page(f"""
        <div class="card" style="border:3px solid {color};">
            <h2 style="color:{color};">{heading}</h2>
            <p><b>Recipient:</b> {html_escape(SELL_BUYBACK_EMAIL)}</p>
            <p><b>Result:</b> {html_escape(response)}</p>
            <a class="btn" href="/admin/test_buyback_email">Test Again</a>
            <a class="btn" href="/admin/buyback_requests">Buyback Dashboard</a>
        </div>
        """)

    configured = bool(SMTP_HOST and SMTP_FROM)
    config_status = "Configured" if configured else "Not Configured"
    config_color = "#198754" if configured else "#dc3545"

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    SELECT created_at, submission_number, recipient, send_status, provider_response
    FROM buyback_email_notifications
    ORDER BY created_at DESC
    LIMIT 20
    """)
    attempts = cur.fetchall()
    cur.close()
    conn.close()

    rows = ""
    for created_at, submission_number, recipient, send_status, provider_response in attempts:
        rows += f"""
        <tr>
            <td>{html_escape(created_at)}</td>
            <td>{html_escape(submission_number)}</td>
            <td>{html_escape(recipient)}</td>
            <td>{html_escape(send_status)}</td>
            <td>{html_escape(provider_response)}</td>
        </tr>
        """

    if not rows:
        rows = "<tr><td colspan='5'>No email attempts recorded yet.</td></tr>"

    return page(f"""
    <div class="card">
        <h2>Buyback Email Test</h2>
        <p><b>Configuration:</b> <span style="color:{config_color};font-weight:bold;">{config_status}</span></p>
        <p><b>SMTP Host:</b> {html_escape(SMTP_HOST or "Missing")}</p>
        <p><b>SMTP Port:</b> {SMTP_PORT}</p>
        <p><b>SMTP User:</b> {html_escape(SMTP_USER or "Missing")}</p>
        <p><b>SMTP From:</b> {html_escape(SMTP_FROM or "Missing")}</p>
        <p><b>Recipient:</b> {html_escape(SELL_BUYBACK_EMAIL)}</p>

        <form method="post">
            <button type="submit">Send Test Buyback Email</button>
        </form>
    </div>

    <div class="card">
        <h3>Recent Email Attempts</h3>
        <table>
            <tr>
                <th>Time</th>
                <th>Submission</th>
                <th>Recipient</th>
                <th>Status</th>
                <th>Response</th>
            </tr>
            {rows}
        </table>
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
    if sort not in ["new", "old"]:
        sort = "new"

    view = request.args.get("view", "all")
    status_filter = request.args.get("status", "all").replace("+", " ")
    q = clean(request.args.get("q", "")).lower()
    dropoff_month = clean(request.args.get("dropoff_month", ""))
    card_count_filter = clean(request.args.get("card_count", ""))
    card_count_op = "eq"

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
        rows = [r for r in rows if (row_status(r) or "") in ["Complete", "Shipped to Giant Sports Cards"]]
    elif view == "shipping":
        rows = [r for r in rows if (row_status(r) or "") in ["Shipping Soon", "Grading Complete"]]
    elif view == "pickup":
        rows = [r for r in rows if (row_status(r) or "") == "Delivered to Us"]
    elif view == "pdf_needed":
        rows = [r for r in rows if card_pdf_needs_attention(r)]

    if status_filter != "all":
        rows = [r for r in rows if customer_status_label(row_status(r) or "Submitted") == status_filter]

    if q:
        def row_matches_query(row):
            data = row[0] or {}
            haystack = " ".join([
                str(get_field(data, ["Submission #", "Submission Number", "Order #", "Order Number", "PSA Order #"])),
                str(get_field(data, ["Customer Name", "Customer", "Name", "Full Name", "Billing Name", "Client"])),
                str(get_field(data, ["Contact Info", "Phone", "Phone Number", "Customer Phone", "Customer Contact", "Mobile", "Cell", "Telephone", "Billing Phone"])),
                str(get_field(data, ["Service Type", "Service"])),
                str(row_status(row) or ""),
                " ".join([str(k) + " " + str(v) for k, v in data.items()])
            ]).lower()
            return q in haystack

        rows = [r for r in rows if row_matches_query(r)]

    if dropoff_month:
        def row_matches_dropoff_month(row):
            data = row[0] or {}
            dropoff = get_dropoff_date(data)
            if not dropoff:
                return False
            try:
                parsed = pd.to_datetime(dropoff, errors="coerce")
                if pd.isna(parsed):
                    return str(dropoff).startswith(dropoff_month)
                return parsed.strftime("%Y-%m") == dropoff_month
            except Exception:
                return str(dropoff).startswith(dropoff_month)

        rows = [r for r in rows if row_matches_dropoff_month(r)]

    if card_count_filter:
        try:
            wanted_cards = int(float(card_count_filter))
        except Exception:
            wanted_cards = None

        if wanted_cards is not None:
            def row_card_count(row):
                data = row[0] or {}

                candidate_fields = [
                    "# Of Cards",
                    "# of Cards",
                    "Cards",
                    "Card Count",
                    "Number of Cards",
                    "# Cards",
                    "Qty",
                    "Quantity",
                    "Items",
                    "Item Count",
                    "Cards Submitted"
                ]

                for field_name in candidate_fields:
                    value = get_field(data, [field_name])
                    if value not in ["", None]:
                        try:
                            return int(float(str(value).replace(",", "").strip()))
                        except Exception:
                            match = re.search(r"\d+", str(value or ""))
                            if match:
                                return int(match.group(0))

                for key, value in data.items():
                    key_norm = normalize_key_text(key)
                    if "card" in key_norm and any(token in key_norm for token in ["count", "number", "qty", "quantity", "of cards"]):
                        try:
                            return int(float(str(value).replace(",", "").strip()))
                        except Exception:
                            match = re.search(r"\d+", str(value or ""))
                            if match:
                                return int(match.group(0))

                return None

            def row_matches_card_count(row):
                count = row_card_count(row)
                return count is not None and count == wanted_cards

            rows = [r for r in rows if row_matches_card_count(r)]

    total_count = len(all_rows)
    active_count = sum(1 for r in all_rows if (row_status(r) or "Submitted") not in ["Complete", "Delivered to Us", "Picked Up"])
    complete_count = sum(1 for r in all_rows if (row_status(r) or "") in ["Complete", "Shipped to Giant Sports Cards"])
    shipping_count = sum(1 for r in all_rows if (row_status(r) or "") in ["Shipping Soon", "Grading Complete"])
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
                <div style="font-size:12px;color:#6b7280;font-weight:bold;text-transform:uppercase;letter-spacing:.4px;">Grading Complete</div>
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
                <label>Search</label>
                <input name="q" value="{html_escape(q)}" placeholder="Name, phone, submission #, order #">
            </div>
            <div>
                <label>Drop-Off Month</label>
                <input type="month" name="dropoff_month" value="{html_escape(dropoff_month)}">
            </div>
            <div>
                <label>Exact Card Count</label>
                <input name="card_count" value="{html_escape(card_count_filter)}" placeholder="Exact number">
            </div>
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
                    <option value="complete" {'selected' if view == 'complete' else ''}>Shipped to Giant Sports Cards</option>
                    <option value="shipping" {'selected' if view == 'shipping' else ''}>Shipping Soon</option>
                    <option value="pickup" {'selected' if view == 'pickup' else ''}>Ready Pickup</option>
                    <option value="pdf_needed" {'selected' if view == 'pdf_needed' else ''}>Card PDF Needed</option>
                </select>
            </div>
            <div>
                <label>Status</label>
                <select name="status">
                    <option value="all" {'selected' if status_filter == 'all' else ''}>All Statuses</option>
                    {status_select_html}
                </select>
            </div>
            <button type="submit">Apply Filters</button>
            <a class="reset-link" href="/admin">Reset</a>
        </form>
    </div>

    <div style="margin:0 0 12px;color:#374151;font-size:13px;">
        Showing <b>{len(rows)}</b> of <b>{total_count}</b> submissions.
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
            psa_order_links = {}
            pdf_text_parts = []
            pages_read = 0

            month_pattern = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|January|February|March|April|May|June|July|August|September|October|November|December)"
            date_pattern = month_pattern + r"\s+\d{1,2},\s+\d{4}"

            def normalize_pdf_text(value):
                return re.sub(r"\s+", " ", str(value or "")).strip()

            def normalize_table_status(value):
                text = normalize_pdf_text(value).lower()

                if re.search(r"\border\s+arrived\b", text):
                    return "Order Arrived"
                if re.search(r"\bresearch\s*&\s*id\b", text):
                    return "Research & ID"
                if re.search(r"\bgrading\b", text):
                    return "Grading"
                if re.search(r"\bassembly\b", text):
                    return "Assembly"
                if re.search(r"\bqa\s+checks\b", text):
                    return "QA Checks"
                if re.search(r"\bshipping\s+soon\b", text):
                    return "Grading Complete"

                # PSA completed/grades-ready rows may show Complete, Track Package, or Completing.
                if re.search(r"\bcomplete\b|\bcompleted\b|\btrack\s+package\b|\bcompleting\b", text):
                    return "Shipped to Giant Sports Cards"

                return None

            def parse_row_dates(row_text):
                row_text = normalize_pdf_text(row_text)
                dates = re.findall(date_pattern, row_text, re.IGNORECASE)

                est_pattern = (
                    r"Est\.\s*by\s+("
                    + month_pattern +
                    r"\s+\d{1,2}(?:\s*[-â]\s*(?:"
                    + month_pattern +
                    r")?\s*\d{1,2})?,\s+\d{4})"
                )
                est_match = re.search(est_pattern, row_text, re.IGNORECASE)
                comp_match = re.search(r"Completed\s+(" + date_pattern + r")", row_text, re.IGNORECASE)

                parts = []

                if dates:
                    parts.append(normalize_pdf_text(dates[0]))

                if est_match:
                    parts.append("Est. by " + normalize_pdf_text(est_match.group(1)))

                if comp_match:
                    parts.append("Completed " + normalize_pdf_text(comp_match.group(1)))

                clean_parts = []
                seen = set()
                for part in parts:
                    key = part.lower()
                    if key not in seen:
                        seen.add(key)
                        clean_parts.append(part)

                return " | ".join(clean_parts)

            def extract_table_blocks(pdf_page):
                blocks = []
                try:
                    page_dict = pdf_page.get_text("dict")
                    for block in page_dict.get("blocks", []):
                        if block.get("type") != 0:
                            continue

                        parts = []
                        for line in block.get("lines", []):
                            for span in line.get("spans", []):
                                if span.get("text"):
                                    parts.append(span.get("text"))

                        text = normalize_pdf_text(" ".join(parts))
                        if not text:
                            continue

                        x0, y0, x1, y1 = block.get("bbox") or (0, 0, 0, 0)
                        blocks.append({
                            "text": text,
                            "x0": x0,
                            "y0": y0,
                            "x1": x1,
                            "y1": y1,
                            "ym": (y0 + y1) / 2,
                        })
                except Exception:
                    pass

                return blocks

            def parse_status_pdf_by_rows(pdf_path):
                found_status = {}
                found_dates = {}
                found_order_links = {}
                pages = 0
                text_parts = []

                try:
                    import fitz
                except Exception as e:
                    raise RuntimeError("PyMuPDF / fitz is required for PSA status PDF import.") from e

                doc = fitz.open(pdf_path)
                pages = len(doc)

                for pdf_page in doc:
                    try:
                        page_text = pdf_page.get_text("text") or ""
                    except Exception:
                        page_text = ""

                    if page_text:
                        text_parts.append(page_text)

                    blocks = extract_table_blocks(pdf_page)
                    links = []

                    try:
                        for link in pdf_page.get_links():
                            uri = str(link.get("uri") or "").strip()
                            rect = link.get("from")
                            if not uri or rect is None:
                                continue

                            match = re.search(
                                r"https://www\.psacard\.com/myaccount/myorders/(\d+)(?:/(\d+))?",
                                uri,
                                re.IGNORECASE
                            )
                            if not match:
                                continue

                            links.append({
                                "submission_number": normalize_submission(match.group(1)),
                                "order_number": normalize_submission(match.group(2)) if match.group(2) else "",
                                "url": uri,
                                "ym": (float(rect.y0) + float(rect.y1)) / 2.0,
                            })
                    except Exception:
                        pass

                    anchors = []
                    for block in blocks:
                        sub_match = re.search(r"Sub\s*#\s*(\d+)", block["text"], re.IGNORECASE)
                        if sub_match:
                            sub = normalize_submission(sub_match.group(1))
                            if sub:
                                anchors.append({
                                    "submission_number": sub,
                                    "block": block,
                                    "ym": block["ym"],
                                })

                    anchors.sort(key=lambda item: item["ym"])

                    for index, anchor in enumerate(anchors):
                        sub = anchor["submission_number"]
                        row_mid = anchor["ym"]
                        prev_mid = anchors[index - 1]["ym"] if index > 0 else None
                        next_mid = anchors[index + 1]["ym"] if index + 1 < len(anchors) else None

                        row_top = 0 if prev_mid is None else (prev_mid + row_mid) / 2
                        row_bottom = float(pdf_page.rect.height) if next_mid is None else (row_mid + next_mid) / 2

                        row_blocks = [b for b in blocks if row_top <= b["ym"] < row_bottom]
                        if len(row_blocks) < 2:
                            row_blocks = [b for b in blocks if abs(b["ym"] - row_mid) <= 50]

                        row_text = " ".join(
                            b["text"] for b in sorted(row_blocks, key=lambda x: (x["x0"], x["y0"]))
                        )

                        status_column_text = " ".join(
                            b["text"] for b in row_blocks if 185 <= b["x0"] < 315
                        )

                        status = normalize_table_status(status_column_text)

                        if not status:
                            has_cards = bool(re.search(r"\b\d+\s+Cards?\b", row_text, re.IGNORECASE))
                            has_date = bool(re.search(date_pattern, row_text, re.IGNORECASE))
                            has_completed = bool(
                                re.search(r"\bCompleted\b|\bTrack\s+Package\b", status_column_text, re.IGNORECASE)
                            )
                            if has_cards and has_date and not has_completed:
                                status = "Order Arrived"

                        if status:
                            found_status[sub] = status

                        dates_value = parse_row_dates(row_text)
                        if dates_value:
                            found_dates[sub] = dates_value

                        row_links = [link for link in links if row_top <= link["ym"] < row_bottom]
                        exact_link = next(
                            (link for link in row_links if link["submission_number"] == sub),
                            None
                        )

                        # Save a link only when the PDF contains an embedded PSA
                        # hyperlink whose submission number exactly matches this row.
                        # Never borrow a nearby link or construct one from visible text.
                        if exact_link:
                            found_order_links[sub] = {
                                "order_number": exact_link["order_number"],
                                "url": exact_link["url"]
                            }

                doc.close()
                return found_status, found_dates, found_order_links, text_parts, pages

            def extract_arrived_completed_from_full_text(text_value):
                found = {}

                normalized = re.sub(r"\s+", " ", text_value or "").strip()
                normalized = re.sub(r",\s+(\d{4})", r", \1", normalized)

                date_range_same_year = month_pattern + r"\s+\d{1,2}\s*[-â]\s*" + month_pattern + r"?\s*\d{1,2},\s+\d{4}"
                date_range_full = date_pattern + r"\s*[-â]\s*" + date_pattern

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
                            m = normalize_pdf_text(m)
                            key = m.lower()

                            if key not in seen_matches:
                                seen_matches.add(key)
                                cleaned_matches.append(m)

                        found[sub] = " | ".join(cleaned_matches)

                return found

            try:
                best, ac_map, psa_order_links, pdf_text_parts, pages_read = parse_status_pdf_by_rows(temp.name)
            finally:
                try:
                    os.unlink(temp.name)
                except Exception:
                    pass

            combined_pdf_text = "\n".join(pdf_text_parts)

            # Safety guard: card-detail / grades PDFs must NOT be processed by the PSA status uploader.
            # They contain Cert # lines and card descriptions and can otherwise move submissions to Complete.
            cert_count_for_guard = len(re.findall(r"Cert\s*#\s*\d+", combined_pdf_text, re.IGNORECASE))
            looks_like_card_detail_pdf = (
                cert_count_for_guard >= 2
                or re.search(r"Your grades are ready|View Grades|PSA DNA Certified|Download CSV", combined_pdf_text, re.IGNORECASE)
            )
            if looks_like_card_detail_pdf:
                return page("""
                <div class="card">
                    <h2>Wrong PDF Uploader</h2>
                    <p>This looks like a PSA card-detail / grades PDF because it contains cert numbers and card results.</p>
                    <p>Use <b>Cards PDF</b> for this file. The PSA PDF uploader is only for the PSA orders/status list.</p>
                    <a class="btn" href="/admin/upload_cards">Go to Cards PDF Upload</a>
                    <a class="btn" href="/admin/upload_psa">Back to PSA PDF Upload</a>
                </div>
                """)

            # Full-text pass for dates only. Status is intentionally NOT read from loose full text.
            full_text_ac_map = extract_arrived_completed_from_full_text(combined_pdf_text)
            for ac_sub, ac_value in full_text_ac_map.items():
                if ac_value and not ac_map.get(ac_sub):
                    ac_map[ac_sub] = ac_value

            conn = get_conn()
            cur = conn.cursor()

            updated = 0
            corrected = 0
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

                parsed_ac = parse_arrived_completed_value(ac_map.get(sub, ""))

                cur.execute("""
                UPDATE submissions
                SET status=%s,
                    raw_data = jsonb_set(
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
                  AND COALESCE(status, '') NOT IN ('Picked Up', 'Delivered to Us')
                """, (status, parsed_ac["display"], parsed_ac["estimated"], sub))

                if cur.rowcount:
                    updated += 1
                    if old_status and old_status != status:
                        corrected += 1
                    maybe_queue_status_sms(cur, sub, sms_phone, old_status, status, sms_opt_in, sms_mode, last_sms_status)
                else:
                    skipped += 1

            # Date-only update for any parsed date rows not already status-updated.
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

            # Save / repair PSA order links for dashboard one-click access.
            psa_links_saved = 0
            psa_links_repaired = 0
            for linked_sub, link_info in psa_order_links.items():
                psa_order_number = normalize_submission(link_info.get("order_number"))
                psa_order_url = str(link_info.get("url") or "").strip()

                # A PSA link is valid even when the PDF row has no separate order number.
                if not psa_order_url:
                    continue

                cur.execute("""
                SELECT COALESCE(raw_data->>'PSA Order URL', '')
                FROM submissions
                WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
                LIMIT 1
                """, (linked_sub,))
                existing_link_row = cur.fetchone()
                existing_link = existing_link_row[0] if existing_link_row else ""

                if psa_order_number:
                    cur.execute("""
                    UPDATE submissions
                    SET raw_data =
                        jsonb_set(
                            jsonb_set(
                                jsonb_set(
                                    COALESCE(raw_data, '{}'::jsonb),
                                    '{PSA Order #}',
                                    to_jsonb(%s::text),
                                    true
                                ),
                                '{PSA Order URL}',
                                to_jsonb(%s::text),
                                true
                            ),
                            '{PSA Order URL Source}',
                            to_jsonb('pdf_embedded'::text),
                            true
                        ),
                        last_updated=NOW()
                    WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
                    """, (psa_order_number, psa_order_url, linked_sub))
                else:
                    cur.execute("""
                    UPDATE submissions
                    SET raw_data =
                        jsonb_set(
                            jsonb_set(
                                COALESCE(raw_data, '{}'::jsonb),
                                '{PSA Order URL}',
                                to_jsonb(%s::text),
                                true
                            ),
                            '{PSA Order URL Source}',
                            to_jsonb('pdf_embedded'::text),
                            true
                        ),
                        last_updated=NOW()
                    WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
                    """, (psa_order_url, linked_sub))

                if cur.rowcount:
                    psa_links_saved += 1
                    if not existing_link:
                        psa_links_repaired += 1

            conn.commit()

            verification_rows = []
            mismatch_count = 0
            checked_count = 0

            for sub, parsed_status in list(best.items())[:150]:
                cur.execute("""
                SELECT status, COALESCE(raw_data->>'Arrived / Completed', ''), COALESCE(raw_data->>'Estimated Completion Date', '') FROM submissions
                WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
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
                    <td colspan="6">No verification rows were created because no matching parsed submissions were found.</td>
                </tr>
                """

            return page(f"""
            <div class="card" style="border:3px solid #198754;">
                <h2>PDF processed</h2>
                {warning}
                <p><b>Pages read:</b> {pages_read}</p>
                <p><b>Statuses found:</b> {len(best)}</p>
                <p><b>PSA order links found:</b> {len(psa_order_links)}</p>
                <p><b>PSA order links saved:</b> {psa_links_saved}</p>
                <p><b>Missing links repaired:</b> {psa_links_repaired}</p>
                <p><b>Updated:</b> {updated}</p>
                <p><b>Status corrections:</b> {corrected}</p>
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
        <p>This uploader reads the PSA Orders / Status PDF and updates submission statuses row-by-row.</p>
        <form method="post" enctype="multipart/form-data">
            <input type="file" name="file" accept=".pdf,application/pdf">
            <button>Upload PSA Status PDF</button>
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
            # Card-detail PDFs only attach cert/card data.
            # They must never change the PSA submission status.
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
                <p><b>PSA Submission:</b> {submission_number}</p>
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
    selected_queue = request.args.get("queue", "interest").lower()

    queue_map = {
        "interest": "New",
        "offer": "Offer Sent",
        "accepted": "Accepted",
        "declined": "Declined",
        "purchased": "Sold",
        "pass": "Pass",
        "all": "All"
    }

    if selected_queue not in queue_map:
        selected_queue = "interest"

    conn = get_conn()
    cur = conn.cursor()

    base_select = """
        SELECT c.submission_number,
               c.cert_number,
               COALESCE(c.description, c.item_details, ''),
               c.grade,
               c.image_data,
               c.interested,
               COALESCE(c.buyback_status, 'New'),
               s.raw_data,
               COALESCE(c.card_type, ''),
               COALESCE(c.after_service, ''),
               COALESCE(c.images_url, ''),
               COALESCE(c.psa_estimate, ''),
               COALESCE(c.card_ladder_value, ''),
               COALESCE(c.pop, ''),
               COALESCE(c.pop_higher, ''),
               COALESCE(c.offer_amount, ''),
               COALESCE(c.offer_notes, '')
        FROM card_buyback_items c
        LEFT JOIN submissions s
          ON REGEXP_REPLACE(s.submission_number, '\\D', '', 'g') = REGEXP_REPLACE(c.submission_number, '\\D', '', 'g')
        WHERE c.interested=TRUE
    """

    if selected_queue == "all":
        cur.execute(base_select + """
        ORDER BY
            CASE COALESCE(c.buyback_status, 'New')
                WHEN 'New' THEN 0
                WHEN 'Offer Sent' THEN 1
                WHEN 'Accepted' THEN 2
                WHEN 'Declined' THEN 3
                WHEN 'Sold' THEN 4
                WHEN 'Pass' THEN 5
                ELSE 6
            END,
            c.updated_at DESC
        """)
    else:
        cur.execute(base_select + """
          AND COALESCE(c.buyback_status, 'New')=%s
        ORDER BY c.updated_at DESC
        """, (queue_map[selected_queue],))

    rows = cur.fetchall()

    cur.execute("""
    SELECT COALESCE(buyback_status, 'New'), COUNT(*)
    FROM card_buyback_items
    WHERE interested=TRUE
    GROUP BY COALESCE(buyback_status, 'New')
    """)
    count_rows = cur.fetchall()

    cur.close()
    conn.close()

    counts = {"interest": 0, "offer": 0, "accepted": 0, "declined": 0, "purchased": 0, "pass": 0, "all": 0}
    for status_value, count_value in count_rows:
        if status_value == "New":
            counts["interest"] = count_value
        elif status_value == "Offer Sent":
            counts["offer"] = count_value
        elif status_value == "Accepted":
            counts["accepted"] = count_value
        elif status_value == "Declined":
            counts["declined"] = count_value
        elif status_value == "Sold":
            counts["purchased"] = count_value
        elif status_value == "Pass":
            counts["pass"] = count_value
        counts["all"] += count_value

    def active(q):
        return "active" if selected_queue == q else ""

    html = f"""
    <h2>Buyback Offers</h2>
    <div class="filterbar">
        <a class="reset-link {active("interest")}" href="/admin/buyback_requests?queue=interest">Interest ({counts["interest"]})</a>
        <a class="reset-link {active("offer")}" href="/admin/buyback_requests?queue=offer">Offers Sent ({counts["offer"]})</a>
        <a class="reset-link {active("accepted")}" href="/admin/buyback_requests?queue=accepted">Accepted ({counts["accepted"]})</a>
        <a class="reset-link {active("declined")}" href="/admin/buyback_requests?queue=declined">Declined ({counts["declined"]})</a>
        <a class="reset-link {active("purchased")}" href="/admin/buyback_requests?queue=purchased">Purchased ({counts["purchased"]})</a>
        <a class="reset-link {active("pass")}" href="/admin/buyback_requests?queue=pass">Pass ({counts["pass"]})</a>
        <a class="reset-link {active("all")}" href="/admin/buyback_requests?queue=all">All ({counts["all"]})</a>
    </div>
    """

    if not rows:
        html += "<div class='card'>No buyback cards in this queue.</div>"
        return page(html)

    def status_badge(status):
        label = "Interest" if status == "New" else ("Purchased" if status == "Sold" else (status or "Interest"))
        color = "#198754"; bg = "#d1e7dd"
        if status == "Offer Sent":
            color = "#92400e"; bg = "#fef3c7"
        elif status == "Accepted":
            color = "#065f46"; bg = "#d1fae5"
        elif status == "Declined":
            color = "#991b1b"; bg = "#fee2e2"
        elif status == "Sold":
            color = "#1e3a8a"; bg = "#dbeafe"
        elif status == "Pass":
            color = "#374151"; bg = "#e5e7eb"
        return f"<span style='display:inline-block;padding:5px 9px;border-radius:999px;background:{bg};color:{color};font-weight:900;font-size:12px;'>{html_escape(label)}</span>"

    html += """
    <div class='card'><div class='table-wrap'><table class="buyback-admin-table">
        <tr>
            <th>Status</th>
            <th>Card</th>
            <th>Customer</th>
            <th>Submission</th>
            <th>Description</th>
            <th>Grade</th>
            <th>Offer</th>
            <th>Actions</th>
        </tr>
    """

    for row in rows:
        submission_number, cert_number, item_details, grade, image_data, interested, buyback_status, raw_data = row[:8]
        card_type = row[8] if len(row) > 8 else ""
        psa_estimate = display_blank_loading(row[11] if len(row) > 11 else "")
        card_ladder_value = display_blank_loading(row[12] if len(row) > 12 else "")
        pop = display_blank_loading(row[13] if len(row) > 13 else "")
        pop_higher = display_blank_loading(row[14] if len(row) > 14 else "")
        offer_amount = row[15] if len(row) > 15 else ""
        offer_notes = row[16] if len(row) > 16 else ""

        customer_name = get_field(raw_data or {}, ["Customer Name", "Name"])
        phone = get_field(raw_data or {}, ["Contact Info", "Phone", "Phone Number"])

        img_html = f"<img src='{image_data}' style='max-height:130px;max-width:95px;object-fit:contain;background:#f9fafb;border-radius:8px;'>" if image_data else "<span style='color:#6b7280;'>No image</span>"

        offer_form = f"""
        <form method="post" action="/admin/buyback_offer" class="buyback-offer-form">
            <input type="hidden" name="submission_number" value="{html_escape(submission_number)}">
            <input type="hidden" name="cert_number" value="{html_escape(cert_number)}">
            <label style="font-size:11px;font-weight:bold;color:#374151;">Offer Amount</label><br>
            <input type="text" name="offer_amount" value="{html_escape(offer_amount)}" placeholder="$0.00" style="margin:3px 0 6px 0;">
            <br>
            <label style="font-size:11px;font-weight:bold;color:#374151;">Notes</label><br>
            <input type="text" name="offer_notes" value="{html_escape(offer_notes)}" placeholder="Optional note" style="margin:3px 0 6px 0;">
            <br>
            <button type="submit" style="background:#198754;color:white;border:0;border-radius:6px;padding:7px 10px;font-weight:bold;">Send / Update Offer</button>
        </form>
        """

        if buyback_status in ["Sold", "Pass", "Declined"]:
            actions = f"""
            <form method="post" action="/admin/buyback_status" class="buyback-actions">
                <input type="hidden" name="submission_number" value="{html_escape(submission_number)}">
                <input type="hidden" name="cert_number" value="{html_escape(cert_number)}">
                <button name="status" value="New">Reset to Interest</button>
            </form>
            """
        elif buyback_status == "Accepted":
            actions = f"""
            <form method="post" action="/admin/buyback_status" class="buyback-actions">
                <input type="hidden" name="submission_number" value="{html_escape(submission_number)}">
                <input type="hidden" name="cert_number" value="{html_escape(cert_number)}">
                <button class="primary" name="status" value="Sold">Mark Purchased</button>
                <button name="status" value="New">Reset to Interest</button>
                <button name="status" value="Pass">Pass</button>
            </form>
            """
        else:
            actions = f"""
            <form method="post" action="/admin/buyback_status" class="buyback-actions">
                <input type="hidden" name="submission_number" value="{html_escape(submission_number)}">
                <input type="hidden" name="cert_number" value="{html_escape(cert_number)}">
                <button name="status" value="New">Reset to Interest</button>
                <button class="primary" name="status" value="Sold">Mark Purchased</button>
                <button name="status" value="Pass">Pass</button>
            </form>
            """

        extra = []
        if card_type:
            extra.append(f"<b>Type:</b> {html_escape(card_type)}")
        if psa_estimate:
            extra.append(f"<b>PSA:</b> {html_escape(psa_estimate)}")
        if card_ladder_value:
            extra.append(f"<b>CL:</b> {html_escape(card_ladder_value)}")
        if pop:
            extra.append(f"<b>Pop:</b> {html_escape(pop)}")
        if pop_higher:
            extra.append(f"<b>Pop Higher:</b> {html_escape(pop_higher)}")
        extra_details = "<br>".join(extra)

        details_html = f"""
        <details class="buyback-details">
            <summary>Details</summary>
            <div class="buyback-details-box">
                <b>Cert #:</b> {html_escape(cert_number)}<br>
                {extra_details}
            </div>
        </details>
        """

        html += f"""
        <tr>
            <td>{status_badge(buyback_status)}</td>
            <td>{img_html}</td>
            <td><b>{html_escape(customer_name)}</b><br><small>{html_escape(phone)}</small></td>
            <td>{html_escape(submission_number)}</td>
            <td class="buyback-main-desc">
                {html_escape(item_details)}
                {details_html}
            </td>
            <td>{html_escape(grade)}</td>
            <td>{offer_form}</td>
            <td>{actions}</td>
        </tr>
        """

    html += "</table></div></div>"
    return page(html)


@app.route("/admin/buyback_offer", methods=["POST"])
@admin_required
def admin_buyback_offer():
    submission_number = normalize_submission(request.form.get("submission_number"))
    cert_number = normalize_submission(request.form.get("cert_number"))
    offer_amount = clean(request.form.get("offer_amount"))
    offer_notes = clean(request.form.get("offer_notes"))

    if submission_number and cert_number:
        conn = get_conn()
        cur = conn.cursor()
        next_status = "Offer Sent" if offer_amount else "New"

        cur.execute("""
        UPDATE card_buyback_items
        SET offer_amount=%s,
            offer_notes=%s,
            buyback_status=%s,
            offer_updated_at=NOW(),
            updated_at=NOW()
        WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
          AND REGEXP_REPLACE(cert_number, '\\D', '', 'g')=%s
        """, (offer_amount, offer_notes, next_status, submission_number, cert_number))

        if offer_amount:
            queue_buyback_offer_sms(cur, submission_number, cert_number, offer_amount, offer_notes)

        conn.commit()
        cur.close()
        conn.close()

    return redirect("/admin/buyback_requests?queue=offer")


@app.route("/admin/buyback_status", methods=["POST"])
@admin_required
def admin_buyback_status():
    submission_number = normalize_submission(request.form.get("submission_number"))
    cert_number = normalize_submission(request.form.get("cert_number"))
    status = clean(request.form.get("status"))

    if status not in ["New", "Offer Sent", "Accepted", "Declined", "Sold", "Pass"]:
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

    redirect_queue = "interest"
    if status == "Offer Sent":
        redirect_queue = "offer"
    elif status == "Accepted":
        redirect_queue = "accepted"
    elif status == "Declined":
        redirect_queue = "declined"
    elif status == "Sold":
        redirect_queue = "purchased"
    elif status == "Pass":
        redirect_queue = "pass"

    return redirect(f"/admin/buyback_requests?queue={redirect_queue}")


@app.route("/portal/sms_preferences", methods=["POST"])
def portal_sms_preferences():
    phone = normalize_phone(session.get("phone"))
    last = clean(session.get("last")).lower()

    if not phone or not last:
        return redirect("/portal")

    sms_mode = request.form.get("sms_mode", "none")
    sms_consent = request.form.get("sms_consent") == "yes"

    if sms_mode not in ["none", "pickup", "all"]:
        sms_mode = "none"

    if sms_mode != "none" and not sms_consent:
        sms_mode = "none"

    sms_opt_in = sms_mode != "none"
    sms_pickup_only = sms_mode == "pickup"

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    SELECT submission_number, raw_data
    FROM submissions
    ORDER BY last_updated DESC
    """)
    rows = cur.fetchall()

    matched_subs = []
    for sub_value, data in rows:
        data = data or {}
        name = str(get_field(data, [
            "Customer Name", "Customer", "Name", "Full Name", "Billing Name", "Client"
        ])).lower()
        contact = normalize_phone(get_field(data, [
            "Contact Info", "Phone", "Phone Number", "Customer Phone", "Customer Contact",
            "Mobile", "Cell", "Telephone", "Billing Phone"
        ]))
        sub_clean = normalize_submission(sub_value)
        phone_match = bool(contact) and (phone in contact or contact in phone)
        name_match = bool(last) and last in name

        if phone_match and name_match and sub_clean:
            matched_subs.append(sub_clean)

    if matched_subs:
        cur.execute("""
        UPDATE submissions
        SET sms_opt_in=%s,
            sms_pickup_only=%s,
            sms_mode=%s,
            last_updated=NOW()
        WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g') = ANY(%s)
        """, (sms_opt_in, sms_pickup_only, sms_mode, matched_subs))
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
        cur.close()
        conn.close()
        return redirect("/portal/orders")

    data = row[0] or {}
    customer_name = get_field(data, ["Customer Name", "Customer", "Name", "Full Name", "Billing Name", "Client"])
    contact_info = get_field(data, [
        "Contact Info", "Phone", "Phone Number", "Customer Phone", "Customer Contact",
        "Mobile", "Cell", "Telephone", "Billing Phone"
    ])

    name = str(customer_name or "").lower()
    contact = normalize_phone(contact_info)
    phone_match = bool(contact) and (phone in contact or contact in phone)
    name_match = bool(last) and last in name

    if not (phone_match and name_match):
        cur.close()
        conn.close()
        return redirect("/portal/orders")

    cur.execute("""
    UPDATE card_buyback_items SET interested=FALSE, updated_at=NOW()
    WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
    """, (submission_number,))

    selected_cards = []
    for cert in certs:
        cert_clean = normalize_submission(cert)
        if cert_clean:
            cur.execute("""
            UPDATE card_buyback_items
            SET interested=TRUE,
                buyback_status=CASE WHEN COALESCE(buyback_status, 'New') IN ('', 'New') THEN 'New' ELSE buyback_status END,
                updated_at=NOW()
            WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
              AND REGEXP_REPLACE(cert_number, '\\D', '', 'g')=%s
            """, (submission_number, cert_clean))

            cur.execute("""
            SELECT cert_number, COALESCE(description, item_details, ''), COALESCE(grade, '')
            FROM card_buyback_items
            WHERE REGEXP_REPLACE(submission_number, '\\D', '', 'g')=%s
              AND REGEXP_REPLACE(cert_number, '\\D', '', 'g')=%s
            LIMIT 1
            """, (submission_number, cert_clean))
            card_row = cur.fetchone()
            if card_row:
                selected_cards.append({
                    "cert_number": card_row[0],
                    "description": card_row[1],
                    "grade": card_row[2]
                })

    conn.commit()
    cur.close()
    conn.close()

    if selected_cards:
        email_sent, email_response = send_buyback_interest_email(
            customer_name or "",
            contact_info or phone,
            submission_number,
            selected_cards
        )

        record_buyback_email_attempt(
            submission_number,
            customer_name or "",
            contact_info or phone,
            selected_cards,
            email_sent,
            email_response
        )

        if email_sent:
            session["buyback_request_sent"] = True
        else:
            session["buyback_request_email_error"] = email_response

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

    portal_logo_b64 = "iVBORw0KGgoAAAANSUhEUgAAA4QAAAOECAYAAAD5Tv87AAAAAXNSR0IArs4c6QAAAIRlWElmTU0AKgAAAAgABQESAAMAAAABAAEAAAEaAAUAAAABAAAASgEbAAUAAAABAAAAUgEoAAMAAAABAAIAAIdpAAQAAAABAAAAWgAAAAAAAABIAAAAAQAAAEgAAAABAAOgAQADAAAAAQABAACgAgAEAAAAAQAAA4SgAwAEAAAAAQAAA4QAAAAAUS5HtwAAAAlwSFlzAAALEwAACxMBAJqcGAAAABxpRE9UAAAAAgAAAAAAAAHCAAAAKAAAAcIAAAHCAABJYd3dskMAAEAASURBVHgB7N0N0HVdWR92vkGCAQ0QQqieKGOQWqXooFFq1VqijjGEqhFFh/GrSqg1xqhx1Nmh+BFqjDVIEiRKDSox1jhR0Tj4EYJoHEKMtaAiah0CqVWkioKI0P/9wvuwN/dzX+ucc++z97rP/j0zx/ece+2z11q/69rref8Cz3OXu/hFgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBA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NgECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQILDfAhcuXLg85fNSviHl2SnnD6Dcs2RW43HfAzBZzPuTM9ZHpNytxKi0bo5/45SPSPnKlKenLPrf5durM47/kfJBKTcoNTmtfo7z+JRdNuka+//KOGcpn5xyq9Msut6X9oditsr2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AAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQ2GGB/x9oltS4SbACqwAAAABJRU5ErkJggg=="

    return page("""
    <style>
        body.portal-body > .topbar {
            display:none;
        }

        body.portal-body {
            background:#f4f3ed;
        }

        body.portal-body .container {
            padding:0;
            margin:0;
            width:100%;
            max-width:none;
            background:#f4f3ed;
            overflow-x:hidden;
        }

        .gsc-redesign-page {
            min-height:100vh;
            background:
                radial-gradient(circle at 84% 16%, rgba(15,81,50,.13), transparent 27%),
                linear-gradient(180deg, #fbfaf4 0%, #eef4ee 100%);
            padding:22px 18px 42px;
            box-sizing:border-box;
        }

        .gsc-redesign-shell {
            width:100%;
            max-width:520px;
            margin:0 auto;
        }

        .gsc-redesign-hero {
            position:relative;
            padding:4px 0 18px;
        }

        .gsc-logo-badge {
            display:inline-flex;
            align-items:center;
            justify-content:flex-start;
            background:transparent;
            border-radius:0;
            padding:0;
            box-shadow:none;
            margin:0 0 18px;
        }

        .gsc-redesign-logo {
            width: 240px;
            max-width:100%;
            height:auto;
            display:block;
            filter:brightness(0) saturate(100%) invert(20%) sepia(44%) saturate(1023%) hue-rotate(105deg) brightness(89%) contrast(93%)
                   drop-shadow(0 1px 0 #ffffff)
                   drop-shadow(1px 0 0 #ffffff)
                   drop-shadow(-1px 0 0 #ffffff)
                   drop-shadow(0 -1px 0 #ffffff);
        }

        .gsc-menu-circle {
            display:none;
        }

        .gsc-redesign-title {
            margin:0;
            color:#06442d;
            font-family:Arial, Helvetica, sans-serif;
            font-weight:900;
            text-transform:uppercase;
            letter-spacing:-.8px;
            line-height:.96;
            font-size:34px;
            max-width:340px;
        }

        .gsc-redesign-tag {
            display:inline-flex;
            align-items:center;
            gap:8px;
            margin:10px 0 13px;
            padding:5px 10px;
            background:rgba(255,255,255,.75);
            border:1px solid #d7d8cd;
            color:#06442d;
            box-shadow:0 3px 12px rgba(15,81,50,.08);
            font-size:12px;
            font-weight:900;
            text-transform:uppercase;
            letter-spacing:.9px;
        }

        .gsc-redesign-star {
            width:15px;
            height:15px;
            background:#8a8f36;
            clip-path:polygon(50% 0%,61% 35%,98% 35%,68% 56%,79% 91%,50% 70%,21% 91%,32% 56%,2% 35%,39% 35%);
            display:inline-block;
        }

        .gsc-redesign-copy {
            color:#111827;
            font-size:16px;
            line-height:1.38;
            max-width:300px;
            margin:0 0 20px;
        }

        .gsc-redesign-actions {
            display:flex;
            gap:12px;
            align-items:center;
            margin-bottom:20px;
        }

        .gsc-redesign-btn {
            display:inline-flex;
            align-items:center;
            justify-content:center;
            height:40px;
            padding:0 19px;
            border-radius:10px;
            text-decoration:none;
            font-weight:900;
            font-size:15px;
            color:#06442d;
            background:#ffffff;
            border:1px solid #06442d;
            box-sizing:border-box;
        }

        .gsc-redesign-btn.primary {
            background:linear-gradient(180deg, #198754 0%, #0f6f3f 100%);
            color:#ffffff;
            border:0;
            box-shadow:0 8px 15px rgba(15,81,50,.22);
        }

        .gsc-track-card {
            position:relative;
            overflow:hidden;
            border-radius:16px;
            background:
                radial-gradient(circle at 50% 0%, rgba(25,135,84,.28), transparent 31%),
                linear-gradient(145deg, #0f1b22 0%, #111c24 52%, #071016 100%);
            color:#ffffff;
            padding:34px 20px 23px;
            border:1px solid rgba(255,255,255,.09);
            box-shadow:0 18px 38px rgba(7,16,22,.25);
        }

        .gsc-track-card:before {
            content:"";
            position:absolute;
            width:230px;
            height:230px;
            border-radius:50%;
            left:50%;
            top:-105px;
            transform:translateX(-50%);
            border:1px solid rgba(25,135,84,.28);
            box-shadow:0 0 0 28px rgba(25,135,84,.05), 0 0 0 58px rgba(25,135,84,.025);
        }

        .gsc-track-icon {
            position:relative;
            width:82px;
            height:82px;
            border-radius:50%;
            background:#f5f7f4;
            margin:0 auto 17px;
            box-shadow:0 0 0 10px rgba(255,255,255,.10);
        }

        .gsc-track-icon:before {
            content:"";
            position:absolute;
            width:30px;
            height:40px;
            border:4px solid #0f5132;
            border-radius:5px;
            left:24px;
            top:18px;
            box-sizing:border-box;
        }

        .gsc-track-icon:after {
            content:"";
            position:absolute;
            right:4px;
            bottom:8px;
            width:28px;
            height:28px;
            border-radius:50%;
            background:#198754;
            border:3px solid #f5f7f4;
            box-sizing:border-box;
        }

        .gsc-track-check {
            position:absolute;
            right:12px;
            bottom:18px;
            width:12px;
            height:7px;
            border-left:3px solid #ffffff;
            border-bottom:3px solid #ffffff;
            transform:rotate(-45deg);
            z-index:2;
        }

        .gsc-track-title {
            position:relative;
            z-index:1;
            text-align:center;
            margin:0;
            color:#ffffff;
            font-family:Arial, Helvetica, sans-serif;
            text-transform:uppercase;
            font-size:25px;
            line-height:1.05;
            font-weight:900;
            letter-spacing:.1px;
        }

        .gsc-green-line {
            position:relative;
            z-index:1;
            width:86px;
            height:4px;
            background:#20a464;
            border-radius:20px;
            margin:13px auto 18px;
        }

        .gsc-subtitle {
            position:relative;
            z-index:1;
            text-align:center;
            margin:0 auto 20px;
            color:#f0f2f2;
            font-size:15px;
            line-height:1.42;
            max-width:360px;
        }

        .gsc-portal-form {
            position:relative;
            z-index:1;
            max-width:360px;
            margin:0 auto;
        }

        .gsc-input-row {
            height:52px;
            border-radius:10px;
            border:1px solid rgba(255,255,255,.24);
            background:rgba(255,255,255,.075);
            display:flex;
            align-items:center;
            gap:12px;
            padding:0 14px;
            margin:0 0 10px;
            box-sizing:border-box;
        }

        .gsc-input-icon {
            width:20px;
            height:20px;
            position:relative;
            flex:0 0 auto;
            opacity:.85;
        }

        .gsc-input-icon.phone:before {
            content:"";
            position:absolute;
            left:5px;
            top:1px;
            width:10px;
            height:17px;
            border:2px solid #d1d5db;
            border-radius:3px;
            box-sizing:border-box;
        }

        .gsc-input-icon.phone:after {
            content:"";
            position:absolute;
            left:9px;
            bottom:3px;
            width:3px;
            height:3px;
            border-radius:50%;
            background:#d1d5db;
        }

        .gsc-input-icon.user:before {
            content:"";
            position:absolute;
            left:6px;
            top:2px;
            width:8px;
            height:8px;
            border:2px solid #d1d5db;
            border-radius:50%;
            box-sizing:border-box;
        }

        .gsc-input-icon.user:after {
            content:"";
            position:absolute;
            left:3px;
            bottom:1px;
            width:14px;
            height:8px;
            border:2px solid #d1d5db;
            border-radius:10px 10px 3px 3px;
            box-sizing:border-box;
        }

        .gsc-input-row input {
            flex:1;
            min-width:0;
            border:0;
            outline:0;
            background:transparent;
            color:#ffffff;
            font-size:15px;
            margin:0;
            padding:0;
        }

        .gsc-input-row input::placeholder {
            color:#d1d5db;
            opacity:.94;
        }

        .gsc-submit {
            width:100%;
            height:54px;
            border:0;
            border-radius:10px;
            background:linear-gradient(180deg, #209d57 0%, #087a3f 100%);
            color:#ffffff;
            font-size:16px;
            font-weight:900;
            cursor:pointer;
            margin:2px 0 0;
            box-shadow:0 9px 20px rgba(0,0,0,.28);
        }

        .gsc-benefits {
            display:grid;
            grid-template-columns:1fr 1fr;
            gap:12px;
            margin:16px 0 0;
        }

        .gsc-benefit {
            display:flex;
            gap:10px;
            align-items:center;
            background:rgba(255,255,255,.78);
            border:1px solid #d7dfd9;
            border-radius:14px;
            padding:13px;
        }

        .gsc-benefit-graphic {
            width:42px;
            height:42px;
            border-radius:13px;
            background:linear-gradient(180deg, #eaf5ee 0%, #d9ecdf 100%);
            border:1px solid #bfd8c8;
            flex:0 0 auto;
            position:relative;
            box-shadow:inset 0 1px 0 rgba(255,255,255,.75), 0 3px 8px rgba(15,81,50,.08);
        }

        .gsc-benefit-graphic.updates:before {
            content:"";
            position:absolute;
            width:20px;
            height:20px;
            border:3px solid #0f5132;
            border-radius:50%;
            left:8px;
            top:8px;
            box-sizing:border-box;
        }

        .gsc-benefit-graphic.updates:after {
            content:"";
            position:absolute;
            width:10px;
            height:8px;
            left:19px;
            top:13px;
            border-left:3px solid #198754;
            border-bottom:3px solid #198754;
            transform:rotate(-45deg);
            transform-origin:left bottom;
            box-sizing:border-box;
        }

        .gsc-benefit-graphic.care:before {
            content:"";
            position:absolute;
            left:10px;
            top:7px;
            width:22px;
            height:26px;
            background:#0f5132;
            clip-path:polygon(50% 0%, 88% 15%, 82% 63%, 50% 100%, 18% 63%, 12% 15%);
        }

        .gsc-benefit-graphic.care:after {
            content:"";
            position:absolute;
            left:17px;
            top:17px;
            width:10px;
            height:6px;
            border-left:3px solid #ffffff;
            border-bottom:3px solid #ffffff;
            transform:rotate(-45deg);
        }

        .gsc-benefit-title {
            color:#06442d;
            font-weight:900;
            text-transform:uppercase;
            font-size:12px;
            letter-spacing:.2px;
            margin-bottom:2px;
        }

        .gsc-benefit-text {
            color:#374151;
            font-size:12px;
            line-height:1.3;
            font-weight:600;
        }


        @media (min-width: 900px) {
            .gsc-redesign-page {
                padding:46px 42px 54px;
            }

            .gsc-redesign-shell {
                max-width:1120px;
                display:grid;
                grid-template-columns:minmax(0, 1fr) minmax(380px, 470px);
                gap:34px 48px;
                align-items:center;
            }

            .gsc-redesign-hero {
                padding:0;
            }

            .gsc-redesign-title {
                font-size:52px;
                max-width:520px;
                letter-spacing:-1.2px;
            }

            .gsc-redesign-copy {
                font-size:19px;
                max-width:440px;
            }

            .gsc-redesign-logo {
                width: 240px;
            }

            .gsc-track-card {
                padding:42px 34px 32px;
            }

            .gsc-benefits {
                grid-column:1 / -1;
                max-width:820px;
                margin:22px auto 0;
                grid-template-columns:1fr 1fr;
            }

            .gsc-benefit {
                padding:18px;
            }

            .gsc-benefit-title {
                font-size:14px;
            }

            .gsc-benefit-text {
                font-size:13px;
            }
        }

        @media (max-width: 420px) {
            .gsc-redesign-page {
                padding:20px 18px 34px;
            }

            .gsc-logo-badge {
                padding:0;
            }

            .gsc-redesign-logo {
                width: 240px;
            }

            .gsc-redesign-title {
                font-size:32px;
            }

            .gsc-track-card {
                padding:32px 18px 23px;
            }

            .gsc-benefits {
                grid-template-columns:1fr;
            }
        }
    </style>

    <div class="gsc-redesign-page">
        <div class="gsc-redesign-shell">
            <div class="gsc-redesign-hero">
                <div class="gsc-logo-badge">
                    <img class="gsc-redesign-logo" src="data:image/png;base64,__PORTAL_LOGO__" alt="Giant Sports Cards">
                </div>

                <div class="gsc-menu-circle"><span></span></div>

                <h1 class="gsc-redesign-title">PSA Submission Tracker</h1>

                <div class="gsc-redesign-tag">
                    <span class="gsc-redesign-star"></span>
                    <span>Track &bull; Verify &bull; Relax</span>
                    <span class="gsc-redesign-star"></span>
                </div>

                <p class="gsc-redesign-copy">Track your submission every step of the way.</p>

                <div class="gsc-redesign-actions"></div>
            </div>

            <div class="gsc-track-card">
                <div class="gsc-track-icon"><span class="gsc-track-check"></span></div>
                <h2 class="gsc-track-title">Check Your Submission Status</h2>
                <div class="gsc-green-line"></div>
                <p class="gsc-subtitle">Enter your last name and phone number to view your PSA submissions.</p>

                <form class="gsc-portal-form" method="post">
                    <div class="gsc-input-row">
                        <div class="gsc-input-icon phone"></div>
                        <input name="phone" placeholder="Phone number">
                    </div>

                    <div class="gsc-input-row">
                        <div class="gsc-input-icon user"></div>
                        <input name="last" placeholder="Last name">
                    </div>

                    <button class="gsc-submit" type="submit">Find My Submission</button>
                </form>
            </div>

            <div class="gsc-benefits">
                <div class="gsc-benefit">
                    <div class="gsc-benefit-graphic updates"></div>
                    <div>
                        <div class="gsc-benefit-title">Real-Time Updates</div>
                        <div class="gsc-benefit-text">Check current PSA progress, grading status, and pickup readiness.</div>
                    </div>
                </div>

                <div class="gsc-benefit">
                    <div class="gsc-benefit-graphic care"></div>
                    <div>
                        <div class="gsc-benefit-title">Expert Care</div>
                        <div class="gsc-benefit-text">Tracked carefully from customer drop-off through pickup.</div>
                    </div>
                </div>
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

    buyback_request_sent = bool(session.pop("buyback_request_sent", False))
    buyback_request_email_error = session.pop("buyback_request_email_error", "")

    selected_view = request.args.get("view", "all")
    selected_status = request.args.get("status", "all").replace("+", " ")

    if selected_view not in ["all", "active", "completed"]:
        selected_view = "all"

    portal_logo_b64 = "iVBORw0KGgoAAAANSUhEUgAAA4QAAAOECAYAAAD5Tv87AAAAAXNSR0IArs4c6QAAAIRlWElmTU0AKgAAAAgABQESAAMAAAABAAEAAAEaAAUAAAABAAAASgEbAAUAAAABAAAAUgEoAAMAAAABAAIAAIdpAAQAAAABAAAAWgAAAAAAAABIAAAAAQAAAEgAAAABAAOgAQADAAAAAQABAACgAgAEAAAAAQAAA4SgAwAEAAAAAQAAA4QAAAAAUS5HtwAAAAlwSFlzAAALEwAACxMBAJqcGAAAABxpRE9UAAAAAgAAAAAAAAHCAAAAKAAAAcIAAAHCAABJYd3dskMAAEAASURBVHgB7N0N0HVdWR92vkGCAQ0QQqieKGOQWqXooFFq1VqijjGEqhFFh/GrSqg1xqhx1Nmh+BFqjDVIEiRKDSox1jhR0Tj4EYJoHEKMtaAiah0CqVWkioKI0P/9wvuwN/dzX+ucc++z97rP/j0zx/ece+2z11q/69rref8Cz3OXu/hFgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBAgAABAgQIECBA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NgECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQILDfAhcuXLg85fNSviHl2SnnD6Dcs2RW43HfAzBZzPuTM9ZHpNytxKi0bo5/45SPSPnKlKenLPrf5durM47/kfJBKTcoNTmtfo7z+JRdNuka+//KOGcpn5xyq9Msut6X9oditsr2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AAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQ2GGB/x9oltS4SbACqwAAAABJRU5ErkJggg=="

    html = """
    <style>
        body.portal-body .container {
            background:
                radial-gradient(circle at 94% 4%, rgba(25,135,84,.10), transparent 24%),
                linear-gradient(180deg, #fbfaf4 0%, #eef4ee 100%) !important;
            padding:18px !important;
            box-sizing:border-box !important;
        }

        body.portal-body h2 {
            color:#06442d !important;
            font-size:30px !important;
            font-weight:950 !important;
            text-transform:uppercase !important;
            letter-spacing:-.5px !important;
            margin:0 0 14px !important;
        }

        body.portal-body .filterbar,
        body.portal-body .card {
            background:#ffffff !important;
            border:1px solid #d7dfd9 !important;
            border-radius:16px !important;
            box-shadow:0 8px 24px rgba(15,81,50,.08) !important;
        }

        body.portal-body .card h3 {
            color:#06442d !important;
            font-weight:950 !important;
        }

        body.portal-body .status {
            color:#198754 !important;
            font-weight:900 !important;
        }

        body.portal-body button {
            background:linear-gradient(180deg, #198754 0%, #0f6f3f 100%) !important;
            color:#ffffff !important;
            border:0 !important;
            border-radius:10px !important;
            font-weight:900 !important;
        }

        @media (min-width: 900px) {
            body.portal-body .container {
                max-width:1120px !important;
                margin:0 auto !important;
                padding:28px 24px 44px !important;
            }
        }
    
        /* PORTAL RESULTS VISIBILITY PATCH */
        body.portal-body .card {
            display:block !important;
            visibility:visible !important;
            opacity:1 !important;
            margin:16px 0 !important;
            position:relative !important;
            z-index:2 !important;
        }

        body.portal-body .filterbar {
            display:block !important;
            visibility:visible !important;
            opacity:1 !important;
            margin:16px 0 !important;
            position:relative !important;
            z-index:2 !important;
        }
    </style>
    <h2>Your Orders</h2>
    """

    grouped = {}

    for r in rows:
        data = r[0] or {}
        name = str(get_field(data, [
            "Customer Name",
            "Customer",
            "Name",
            "Full Name",
            "First Last",
            "Billing Name",
            "Bill To",
            "Client",
            "Customer Full Name"
        ])).lower()

        contact = normalize_phone(get_field(data, [
            "Customer Contact",
            "Contact",
            "Contact Info",
            "Phone",
            "Phone Number",
            "Customer Phone",
            "Mobile",
            "Mobile Phone",
            "Cell",
            "Cell Phone",
            "Telephone",
            "Billing Phone",
            "Customer Phone Number"
        ]))

        sub = normalize_submission(get_field(data, [
            "Submission #",
            "Submission Number",
            "Submission",
            "Order #",
            "Order Number",
            "PSA Order #",
            "PSA Order Number",
            "PSA Submission #"
        ]))

        phone_match = bool(contact) and (phone in contact or contact in phone)
        name_match = bool(last) and last in name

        if phone_match and name_match and sub and sub not in grouped:
            sms_opted = r[2] if len(r) > 2 else False
            sms_pickup_only = r[3] if len(r) > 3 else True
            sms_mode = r[4] if len(r) > 4 else ("pickup" if sms_opted and sms_pickup_only else ("all" if sms_opted else "none"))
            grouped[sub] = (data, r[1] or "Submitted", sms_opted, sms_pickup_only, sms_mode)

    if not grouped:
        html += "<div class='card'>We couldn't find any PSA submissions matching the information entered. Please verify your last name and phone number or contact Giant Sports Cards for assistance.</div>"
        return page(html, mode="portal")


    # Customer-level text notification preference. Applies to all matched submissions for this logged-in customer.
    matched_modes = [str(v[4] if len(v) > 4 else "none").lower() for v in grouped.values()]
    current_sms_mode = "none"
    if matched_modes:
        if "all" in matched_modes:
            current_sms_mode = "all"
        elif "pickup" in matched_modes:
            current_sms_mode = "pickup"

    none_checked = "checked" if current_sms_mode == "none" else ""
    pickup_checked = "checked" if current_sms_mode == "pickup" else ""
    all_checked = "checked" if current_sms_mode == "all" else ""
    consent_checked = "checked" if current_sms_mode != "none" else ""

    html += f"""
    <div class="card">
        <h3>PSA Text Updates</h3>
        <p>Choose one text preference for all PSA submissions tied to this phone/name. Message and data rates may apply. You can change this anytime.</p>
        <form method="post" action="/portal/sms_preferences">
            <label class="sell-check">
                <input type="radio" name="sms_mode" value="none" {none_checked}>
                Don't send text updates
            </label>
            <label class="sell-check">
                <input type="radio" name="sms_mode" value="pickup" {pickup_checked}>
                Notify me when my order is ready for pickup
            </label>
            <label class="sell-check">
                <input type="radio" name="sms_mode" value="all" {all_checked}>
                Notify me of every PSA status update
            </label>
            <label class="sell-check">
                <input type="checkbox" name="sms_consent" value="yes" {consent_checked}>
                I agree to receive text updates from Giant Sports Cards regarding my PSA submissions. Message and data rates may apply. Reply STOP to opt out.
            </label>
            <button type="submit">Save Text Updates</button>
        </form>
    </div>
    """

    if buyback_request_sent:
        html += """
        <div class="card buyback-success">
            <h3>Buyback Request Sent</h3>
            <p>Your selected cards were sent to Giant Sports Cards for review. Our team will contact you if we are interested in making an offer.</p>
        </div>
        """

    if buyback_request_email_error:
        html += f"""
        <div class="card buyback-email-error">
            <h3>Buyback Request Saved, Email Not Sent</h3>
            <p>Your card selection was saved, but the notification email could not be delivered.</p>
            <p><b>Technical reason:</b> {html_escape(buyback_request_email_error)}</p>
            <p>Please contact Giant Sports Cards directly while the email settings are being corrected.</p>
        </div>
        """

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
    html += f"<option value='all' {selected('all', selected_view)}>All Orders</option>"
    html += f"<option value='active' {selected('active', selected_view)}>Active Orders</option>"
    html += f"<option value='completed' {selected('completed', selected_view)}>Shipped / Picked Up</option>"
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
            <a class="reset-link" href="/portal/orders?view=all&status=all">Reset</a>
        </form>
    </div>
    """

    completed_statuses = set(["Complete", "Shipped to Giant Sports Cards", "Delivered to Us", "Picked Up"])
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
        return page(html, mode="portal")

    for sub, grouped_values in filtered_grouped.items():
        data, status = grouped_values[0], grouped_values[1]
        sms_opted = grouped_values[2] if len(grouped_values) > 2 else False
        sms_pickup_only = grouped_values[3] if len(grouped_values) > 3 else True
        sms_mode = grouped_values[4] if len(grouped_values) > 4 else ("pickup" if sms_opted and sms_pickup_only else ("all" if sms_opted else "none"))
        customer_name = get_field(data, ["Customer Name", "Customer", "Name", "Full Name", "Billing Name", "Client"])
        cards = get_field(data, ["# Of Cards", "# of Cards", "Cards"])
        service = clean_service_display(get_field(data, ["Service Type", "Service"]))
        date = get_dropoff_date(data)
        arrived_completed_raw = get_psa_received_date(data)
        arrived_completed_data = parse_arrived_completed_value(arrived_completed_raw)
        arrived_completed = arrived_completed_data["display"]
        arrived_completed = strip_arrived_at_psa_prefix(arrived_completed)
        estimated_completion = get_expected_completion_date(data)
        display_status = status or "Submitted"
        display_status_label = customer_status_label(display_status)

        buyback_rows = get_buyback_items_for_submission(sub)
        buyback_html = ""

        if buyback_rows:
            buyback_count = len(buyback_rows)
            buyback_html += f"""
            <hr>
            <details class="buyback-collapsible">
                <summary>View Your Graded Cards ({buyback_count})</summary>
                <div class="buyback-inner">
                    <p>Your PSA grades are available below. Review your graded cards and select any cards you would like Giant Sports Cards to consider for a buyback offer.</p>
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
                offer_amount = row[12] if len(row) > 12 else ""
                offer_notes = row[13] if len(row) > 13 else ""
                buyback_status = row[14] if len(row) > 14 else ""

                checked = "checked" if interested else ""
                img_html = f"<img src='{image_data}' alt='Card image'>" if image_data else ""

                offer_display = ""
                if offer_amount:
                    response_buttons = ""
                    if buyback_status == "Offer Sent":
                        response_buttons = f"""
                        <form method="post" action="/portal/buyback_offer_response" style="margin-top:8px;">
                            <input type="hidden" name="submission_number" value="{sub}">
                            <input type="hidden" name="cert_number" value="{cert_number}">
                            <button name="response" value="Accepted">Accept Offer</button>
                            <button name="response" value="Declined">Decline</button>
                        </form>
                        """
                    offer_display = f"""
                    <div style="margin-top:10px;padding:10px;border:1px solid #d1e7dd;border-radius:8px;background:#f3f7f5;">
                        <b>Giant Sports Cards Buyback Offer:</b> {html_escape(offer_amount)}<br>
                        <small>{html_escape(offer_notes)}</small><br>
                        <b>Current Status:</b> {html_escape('Interest' if buyback_status == 'New' else buyback_status)}
                        {response_buttons}
                    </div>
                    """

                buyback_html += f"""
                <div class="buy-card">
                    {img_html}
                    <div class="cert">Certification #: {cert_number}</div>
                    <div><b>Type:</b> {card_type}</div>
                    <div>{item_details}</div>
                    <div><b>Grade:</b> {grade}</div>
                    {offer_display}
                    <label class="sell-check"><input type="checkbox" name="cert" value="{cert_number}" {checked}> Request an offer</label>
                </div>
                """

            buyback_html += """
                        </div>
                        <br>
                        <button type="submit">Request Buyback Offer</button>
                    </form>
                </div>
            </details>
            """

        sms_html = ""

        html += f"""
        <div class="card">
            <h3>{customer_name}</h3>
            <p><b>PSA Submission:</b> {sub}</p>
            <p><b>Current Status:</b> <span class="status status-badge">{display_status_label}</span></p>
            <p><b>Received by PSA:</b> {arrived_completed}</p>
            <p><b>Estimated Completion:</b> {estimated_completion}</p>
            <p><b>Cards:</b> {cards}</p>
            <p><b>Service Level:</b> {service}</p>
            <p><b>Customer Drop-Off:</b> {date}</p>
            {status_bar(display_status)}
            {sms_html}
            {buyback_html}
        </div>
        """

    return page(html, mode="portal")


@app.route("/portal/buyback_offer_response", methods=["POST"])
def portal_buyback_offer_response():
    phone = normalize_phone(session.get("phone"))
    last = clean(session.get("last")).lower()

    if not phone or not last:
        return redirect("/p
