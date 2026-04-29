import os
import time
import random
import logging
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.utils import secure_filename
from supabase_client import supabase as _supabase_client, supabase_admin as _supabase_admin

_log_level = os.environ.get("LOG_LEVEL", "WARNING").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.WARNING),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

supabase       = _supabase_client
supabase_admin = _supabase_admin

_root = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(_root, "templates"),
    static_folder=os.path.join(_root, "static"),
    static_url_path="/static",
)
app.secret_key = os.environ.get("SECRET_KEY", "cat_adoption_secret_2026")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

CSS_VERSION = "1.1.5"

@app.context_processor
def inject_globals():
    return {
        "css_version": CSS_VERSION,
        "is_admin_user": session.get("role") == "admin",
    }

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "pdf"}
ALLOWED_AVATAR_EXTENSIONS = {"png", "jpg", "jpeg"}
STORAGE_BUCKET = "valid-ids"
AVATAR_BUCKET  = "avatars"
MAX_AVATAR_BYTES = 2 * 1024 * 1024  # 2 MB

PAYMENT_BUCKET = "receipts"
COMPLETE_PHOTO_BUCKET = "adoption-completions"
GCASH_NUMBER   = os.environ.get("GCASH_NUMBER", "09XX-XXX-XXXX")
GCASH_NAME     = os.environ.get("GCASH_NAME",   "Cat Adoption PH")
DELIVERY_FEE   = int(os.environ.get("DELIVERY_FEE", "50"))  # configurable delivery fee in PHP

ADOPTION_REQUEST_COLUMNS = (
    "id, user_id, cat_id, status, created_at, payment_status, payment_proof, payment_method, "
    "delivery_method, delivery_status, meetup_location, meetup_map_link, meetup_date, meetup_time, "
    "schedule_date, schedule_time, full_name, email, contact_number, address, reason, "
    "experience_with_pets, completion_photo_url"
)

# Extended columns added later — fetched separately so old DBs still work
ADOPTION_REQUEST_COLUMNS_EXT = (
    "delivery_date, delivery_time_start, delivery_time_end, delivery_address, "
    "rider_name, rider_contact, delivery_photo_url, delivery_status"
)

ADMIN_USERNAME  = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD  = os.environ.get("ADMIN_PASSWORD", "admin123")
GMAIL_USER      = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASS  = os.environ.get("GMAIL_APP_PASSWORD", "")


def send_verification_email(to_email, user_name, code):
    if not GMAIL_USER or not GMAIL_APP_PASS:
        log.warning("GMAIL_USER or GMAIL_APP_PASSWORD not set — skipping email")
        return
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    html = render_template("email_verification.html", user_name=user_name, code=code)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your Verification Code - Cat Adoption PH"
    msg["From"]    = f"Cat Adoption PH <{GMAIL_USER}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_USER, GMAIL_APP_PASS)
        smtp.sendmail(GMAIL_USER, to_email, msg.as_string())


def send_status_email(to_email, user_name, subject, body_html):
    """Generic status notification email."""
    if not GMAIL_USER or not GMAIL_APP_PASS:
        log.warning("send_status_email: GMAIL_USER or GMAIL_APP_PASSWORD not set — skipping email to %s", to_email)
        return
    if not to_email or "@" not in to_email:
        log.warning("send_status_email: invalid or missing recipient email — skipping")
        return
    try:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"Cat Adoption PH <{GMAIL_USER}>"
        msg["To"]      = to_email
        html = f"""<div style='font-family:sans-serif;max-width:480px;margin:auto;padding:24px;'>
            <h2 style='color:#7c3aed;'>Cat Adoption PH</h2>
            <p>Hi <strong>{user_name}</strong>,</p>
            {body_html}
            <p style='color:#64748b;font-size:12px;margin-top:24px;'>Cat Adoption PH &mdash; Thank you for adopting!</p>
        </div>"""
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_APP_PASS)
            smtp.sendmail(GMAIL_USER, to_email, msg.as_string())
        log.warning("send_status_email: sent '%s' to %s", subject, to_email)
    except Exception as e:
        log.error("send_status_email to %s failed: %s", to_email, e)


def notify_adoption_status(to_email, user_name, cat_name, adoption_status, extra_html=""):
    messages = {
        "Approved":  ("Your Adoption Request is Approved! 🎉",
                      f"<p>Great news! Your adoption request for <strong>{cat_name}</strong> has been <strong>approved</strong>.</p><p>Please proceed with the payment to continue.</p>"),
        "Scheduled": ("Your Meet-up / Delivery is Scheduled 📅",
                      f"<p>Your adoption of <strong>{cat_name}</strong> has been <strong>scheduled</strong>.</p>{extra_html}"),
        "Completed": ("Adoption Completed! 🐱",
                      f"<p>Your adoption of <strong>{cat_name}</strong> is now <strong>complete</strong>. Welcome to the family!</p>"),
        "Rejected":  ("Adoption Request Update",
                      f"<p>Unfortunately, your adoption request for <strong>{cat_name}</strong> was <strong>not approved</strong> at this time.</p>{extra_html}"),
    }
    if adoption_status not in messages:
        return
    subject, body = messages[adoption_status]
    send_status_email(to_email, user_name, subject, body)


def send_delivery_email(to_email, name, cat_name, delivery_details, photo_url=None):
    """
    Send a styled HTML delivery notification email.
    delivery_details = {date, start_time, end_time, rider, contact, address, status}
    """
    if not GMAIL_USER or not GMAIL_APP_PASS:
        log.warning("send_delivery_email: Gmail credentials not set — skipping")
        return
    if not to_email or "@" not in to_email:
        log.warning("send_delivery_email: invalid recipient '%s' — skipping", to_email)
        return

    d = delivery_details or {}

    def _row(label, value):
        v = value or "—"
        return (
            f"<tr>"
            f"<td style='padding:8px 12px;color:#64748b;font-size:13px;white-space:nowrap;'>{label}</td>"
            f"<td style='padding:8px 12px;font-size:13px;font-weight:600;color:#1a1d2e;'>{v}</td>"
            f"</tr>"
        )

    time_window = (
        f"{d.get('start_time')} – {d.get('end_time')}"
        if d.get('start_time') and d.get('end_time')
        else (d.get('start_time') or d.get('end_time') or "—")
    )

    photo_block = ""
    if photo_url:
        photo_block = f"""
        <div style='margin:20px 0;text-align:center;'>
            <p style='font-size:13px;color:#64748b;margin-bottom:8px;'>📸 Proof of Delivery</p>
            <img src='{photo_url}' alt='Proof of Delivery'
                 style='max-width:100%;border-radius:10px;border:2px solid #e8eaf0;
                        box-shadow:0 4px 12px rgba(0,0,0,0.10);'>
            <div style='margin-top:12px;'>
                <a href='{photo_url}' target='_blank'
                   style='display:inline-block;background:#ff6b35;color:#fff;
                          padding:10px 24px;border-radius:30px;font-size:13px;
                          font-weight:700;text-decoration:none;
                          box-shadow:0 4px 12px rgba(255,107,53,0.35);'>
                    View Full Image
                </a>
            </div>
        </div>"""

    html = f"""
    <!DOCTYPE html>
    <html>
    <body style='margin:0;padding:0;background:#f7f8fc;font-family:Inter,sans-serif;'>
    <div style='max-width:520px;margin:32px auto;background:#fff;border-radius:16px;
                box-shadow:0 4px 24px rgba(0,0,0,0.08);overflow:hidden;'>

        <!-- Header -->
        <div style='background:linear-gradient(135deg,#ff6b35,#e85520);
                    padding:28px 32px;text-align:center;'>
            <div style='font-size:32px;margin-bottom:6px;'>🚚</div>
            <h1 style='margin:0;color:#fff;font-size:20px;font-weight:700;
                       letter-spacing:-0.3px;'>Delivery Update</h1>
            <p style='margin:6px 0 0;color:rgba(255,255,255,0.85);font-size:13px;'>
                Cat Adoption PH
            </p>
        </div>

        <!-- Body -->
        <div style='padding:28px 32px;'>
            <p style='font-size:15px;color:#1a1d2e;margin:0 0 6px;'>
                Hi <strong>{name}</strong>,
            </p>
            <p style='font-size:13px;color:#64748b;margin:0 0 20px;line-height:1.6;'>
                Your cat <strong>{cat_name}</strong> is on its way!
                Here are the delivery details:
            </p>

            <!-- Details table -->
            <table style='width:100%;border-collapse:collapse;
                          background:#f8fafc;border-radius:10px;overflow:hidden;
                          border:1px solid #e8eaf0;margin-bottom:20px;'>
                {_row('📅 Delivery Date', d.get('date'))}
                {_row('⏰ Time Window',   time_window)}
                {_row('🏍️ Rider',         d.get('rider'))}
                {_row('📞 Rider Contact', d.get('contact'))}
                {_row('📍 Address',       d.get('address'))}
                {_row('📦 Status',        d.get('status'))}
            </table>

            {photo_block}

            <p style='font-size:12px;color:#94a3b8;margin:20px 0 0;
                      border-top:1px solid #e8eaf0;padding-top:16px;'>
                Cat Adoption PH &mdash; Thank you for adopting! 🐱
            </p>
        </div>
    </div>
    </body>
    </html>
    """

    try:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🚚 Delivery Update for {cat_name} — Cat Adoption PH"
        msg["From"]    = f"Cat Adoption PH <{GMAIL_USER}>"
        msg["To"]      = to_email
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_APP_PASS)
            smtp.sendmail(GMAIL_USER, to_email, msg.as_string())
        log.warning("send_delivery_email: sent to %s for cat '%s'", to_email, cat_name)
    except Exception as e:
        log.error("send_delivery_email to %s failed: %s", to_email, e)


def notify_delivery_scheduled(to_email, user_name, cat_name, delivery_date, time_start,
                               time_end, rider_name, rider_contact, delivery_address, delivery_status):
    """Thin wrapper kept for backward compatibility — delegates to send_delivery_email."""
    if not to_email:
        log.warning("notify_delivery_scheduled: no email address — skipping")
        return
    send_delivery_email(
        to_email, user_name, cat_name,
        {
            "date":       delivery_date,
            "start_time": time_start,
            "end_time":   time_end,
            "rider":      rider_name,
            "contact":    rider_contact,
            "address":    delivery_address,
            "status":     delivery_status,
        },
        photo_url=None,
    )


def send_pickup_email(to_email, user_name, cat_name, pickup_date, pickup_time, pickup_location, pickup_notes=None):
    """Send a 📦 Pickup Scheduled email."""
    if not GMAIL_USER or not GMAIL_APP_PASS:
        log.warning("send_pickup_email: Gmail credentials not set — skipping")
        return
    if not to_email or "@" not in to_email:
        log.warning("send_pickup_email: invalid recipient '%s' — skipping", to_email)
        return

    notes_block = f"<tr><td style='padding:8px 12px;color:#64748b;font-size:13px;'>📝 Notes</td><td style='padding:8px 12px;font-size:13px;font-weight:600;color:#1a1d2e;'>{pickup_notes}</td></tr>" if pickup_notes else ""

    html = f"""
    <!DOCTYPE html><html><body style='margin:0;padding:0;background:#f7f8fc;font-family:Inter,sans-serif;'>
    <div style='max-width:520px;margin:32px auto;background:#fff;border-radius:16px;box-shadow:0 4px 24px rgba(0,0,0,0.08);overflow:hidden;'>
        <div style='background:linear-gradient(135deg,#7c3aed,#5b21b6);padding:28px 32px;text-align:center;'>
            <div style='font-size:32px;margin-bottom:6px;'>📦</div>
            <h1 style='margin:0;color:#fff;font-size:20px;font-weight:700;'>Pickup Scheduled</h1>
            <p style='margin:6px 0 0;color:rgba(255,255,255,0.85);font-size:13px;'>Cat Adoption PH</p>
        </div>
        <div style='padding:28px 32px;'>
            <p style='font-size:15px;color:#1a1d2e;margin:0 0 6px;'>Hi <strong>{user_name}</strong>,</p>
            <p style='font-size:13px;color:#64748b;margin:0 0 20px;line-height:1.6;'>
                Your pickup for <strong>{cat_name}</strong> has been scheduled. Here are the details:
            </p>
            <table style='width:100%;border-collapse:collapse;background:#f8fafc;border-radius:10px;overflow:hidden;border:1px solid #e8eaf0;margin-bottom:20px;'>
                <tr><td style='padding:8px 12px;color:#64748b;font-size:13px;'>📅 Pickup Date</td><td style='padding:8px 12px;font-size:13px;font-weight:600;color:#1a1d2e;'>{pickup_date or '—'}</td></tr>
                <tr><td style='padding:8px 12px;color:#64748b;font-size:13px;'>⏰ Pickup Time</td><td style='padding:8px 12px;font-size:13px;font-weight:600;color:#1a1d2e;'>{pickup_time or '—'}</td></tr>
                <tr><td style='padding:8px 12px;color:#64748b;font-size:13px;'>📍 Location</td><td style='padding:8px 12px;font-size:13px;font-weight:600;color:#1a1d2e;'>{pickup_location or '—'}</td></tr>
                {notes_block}
            </table>
            <p style='font-size:12px;color:#94a3b8;margin:20px 0 0;border-top:1px solid #e8eaf0;padding-top:16px;'>Cat Adoption PH &mdash; Thank you for adopting! 🐱</p>
        </div>
    </div>
    </body></html>
    """
    try:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"📦 Pickup Scheduled for {cat_name} — Cat Adoption PH"
        msg["From"]    = f"Cat Adoption PH <{GMAIL_USER}>"
        msg["To"]      = to_email
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_APP_PASS)
            smtp.sendmail(GMAIL_USER, to_email, msg.as_string())
        log.warning("send_pickup_email: sent to %s for cat '%s'", to_email, cat_name)
    except Exception as e:
        log.error("send_pickup_email to %s failed: %s", to_email, e)


def save_delivery_details(req_id, delivery_date, start_time, end_time, rider_name,
                           rider_contact, delivery_address, delivery_status):
    """Persist delivery fields to adoption_requests and mirror to deliveries table."""
    if not delivery_date:
        raise ValueError("delivery_date is required")
    if delivery_status not in {"Preparing", "Out for Delivery", "Delivered"}:
        raise ValueError(f"Invalid delivery_status: {delivery_status}")
    if not rider_name or not delivery_address:
        raise ValueError("Delivery requires rider_name and delivery_address")

    update_data = {
        "status":          "Scheduled",
        "delivery_status": delivery_status,
        "delivery_date":   delivery_date,
    }
    if start_time:
        update_data["delivery_time_start"] = start_time
        update_data["start_time"] = start_time
    if end_time:
        update_data["delivery_time_end"] = end_time
        update_data["end_time"] = end_time
    if rider_name:
        update_data["rider_name"] = rider_name
    if rider_contact:
        update_data["rider_contact"] = rider_contact
    if delivery_address:
        update_data["delivery_address"] = delivery_address

    _admin_db().table("adoption_requests").update(update_data).eq("id", req_id).execute()
    sync_delivery_record(req_id, {
        "delivery_status": delivery_status,
        "delivery_date":   delivery_date,
        "rider_name":      rider_name or None,
        "rider_contact":   rider_contact or None,
    })


def save_pickup_details(req_id, pickup_date, pickup_time, pickup_location, pickup_notes=None):
    """Persist pickup fields to adoption_requests. Rider fields are NOT required."""
    if not pickup_date or not pickup_time or not pickup_location:
        raise ValueError("pickup_date, pickup_time, and pickup_location are required")

    update_data = {
        "status":          "Scheduled",
        "delivery_status": "Ready for Pickup",
        "pickup_date":     pickup_date,
        "pickup_time":     pickup_time,
        "pickup_location": pickup_location,
    }
    if pickup_notes:
        update_data["pickup_notes"] = pickup_notes

    _admin_db().table("adoption_requests").update(update_data).eq("id", req_id).execute()


def _get_cat_name(cat_id):
    try:
        res = _admin_db().table("cats").select("name").eq("id", cat_id).single().execute()
        return (res.data or {}).get("name") or "your cat"
    except Exception:
        return "your cat"


def notify_payment_status(to_email, user_name, cat_name, payment_status):
    messages = {
        "Pending Payment":  ("Payment Pending",
                             f"<p>Your payment for <strong>{cat_name}</strong> is now <strong>pending</strong>. Please complete your payment.</p>"),
        "For Verification": ("Payment Under Verification 🔍",
                             f"<p>We received your payment proof for <strong>{cat_name}</strong> and it is currently being <strong>verified</strong>.</p>"),
        "Paid":             ("Payment Confirmed! ✅",
                             f"<p>Your payment for <strong>{cat_name}</strong> has been <strong>confirmed</strong>. Thank you!</p>"),
    }
    if payment_status not in messages:
        return
    subject, body = messages[payment_status]
    send_status_email(to_email, user_name, subject, body)


# ------------------------------------------------------------------ helpers --

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _safe_float(value):
    """Safely convert a form value to float, returning None on failure."""
    try:
        v = str(value).strip()
        return float(v) if v else None
    except (ValueError, TypeError):
        return None


def parse_dt(val):
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except Exception:
        return None


def _first_non_empty(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        return value
    return None


def get_user_profile(user_id):
    try:
        res = supabase.table("users").select("*").eq("id", user_id).single().execute()
        u = res.data
        if not u:
            return None
        return (u.get("id"), u.get("email"), u.get("password"), u.get("full_name"),
                u.get("phone"), u.get("address"), u.get("valid_id_url"), u.get("avatar_url"))
    except Exception as e:
        log.error("get_user_profile(%s) failed: %s", user_id, e)
        return None


def upload_valid_id(file, user_id):
    try:
        import uuid
        ext = file.filename.rsplit(".", 1)[-1].lower()
        filename = f"{uuid.uuid4()}_{user_id}.{ext}"
        file_bytes = file.read()
        client = supabase_admin or supabase
        client.storage.from_(STORAGE_BUCKET).upload(
            filename, file_bytes,
            {"content-type": file.content_type, "upsert": "true"}
        )
        public_url = client.storage.from_(STORAGE_BUCKET).get_public_url(filename)
        log.warning("upload_valid_id: saved %s/%s for user %s", STORAGE_BUCKET, filename, user_id)
        return public_url
    except Exception as e:
        log.error("upload_valid_id(%s) failed: %s", user_id, e)
        return None


def db_query(table, filters=None, columns="*", order=None, limit=None, single=False):
    try:
        q = supabase.table(table).select(columns)
        for col, val in (filters or {}).items():
            q = q.eq(col, val)
        if order:
            q = q.order(order, desc=True)
        if limit:
            q = q.limit(limit)
        if single:
            return (q.single().execute().data) or None
        return (q.execute().data) or []
    except Exception:
        return None if single else []


def profile_to_dict(profile):
    if not profile:
        return {}
    return {
        "id": profile[0],
        "email": profile[1],
        "password": profile[2],
        "full_name": profile[3],
        "phone": profile[4],
        "address": profile[5],
        "valid_id_url": profile[6],
        "avatar_url": profile[7],
    }


def _storage_client():
    return supabase_admin or supabase


def upload_public_file(bucket, path, file_bytes, content_type):
    client = _storage_client()
    client.storage.from_(bucket).upload(
        path,
        file_bytes,
        {"content-type": content_type, "upsert": "true"},
    )
    return client.storage.from_(bucket).get_public_url(path)



def fetch_messages_for_requests(request_ids, admin=False):
    if not request_ids:
        return {}
    db = _admin_db() if admin else supabase
    grouped = {req_id: [] for req_id in request_ids}
    try:
        rows = db.table("messages").select(
            "id, adoption_id, sender, message, created_at"
        ).in_("adoption_id", request_ids).order("created_at").execute().data or []
        for row in rows:
            grouped.setdefault(row["adoption_id"], []).append({
                "id": row.get("id"),
                "sender": row.get("sender") or "user",
                "message": row.get("message") or "",
                "created_at": parse_dt(row.get("created_at")),
            })
    except Exception as e:
        log.error("fetch_messages_for_requests(%s) failed: %s", request_ids, e)
    return grouped


def fetch_deliveries_for_requests(request_ids, admin=False):
    if not request_ids:
        return {}
    db = _admin_db() if admin else supabase
    deliveries_by_request = {}
    for request_key in ("adoption_request_id", "request_id", "adoption_id"):
        try:
            rows = db.table("deliveries").select("*").in_(request_key, request_ids).execute().data or []
            for row in rows:
                request_id = row.get(request_key)
                if request_id is not None and request_id not in deliveries_by_request:
                    deliveries_by_request[request_id] = row
            if deliveries_by_request:
                return deliveries_by_request
        except Exception:
            continue
    return deliveries_by_request


def sync_delivery_record(request_id, data):
    db = _admin_db()
    payload = {key: value for key, value in (data or {}).items() if value is not None}
    if not request_id or not payload:
        return False
    for request_key in ("adoption_request_id", "request_id", "adoption_id"):
        try:
            existing = db.table("deliveries").select("id").eq(request_key, request_id).limit(1).execute().data or []
            if existing:
                db.table("deliveries").update(payload).eq("id", existing[0]["id"]).execute()
            else:
                insert_payload = dict(payload)
                insert_payload[request_key] = request_id
                db.table("deliveries").insert(insert_payload).execute()
            return True
        except Exception:
            continue
    return False


def _fetch_requests(db, filters=None, order_desc=True, limit=None):
    """Fetch adoption_requests rows using wildcard select to avoid missing-column errors."""
    try:
        q = db.table("adoption_requests").select("*")
        for col, val in (filters or {}).items():
            q = q.eq(col, val)
        if order_desc:
            q = q.order("created_at", desc=True)
        if limit:
            q = q.limit(limit)
        result = q.execute().data
        return result if result is not None else []
    except Exception as e:
        log.error("_fetch_requests failed: %s", e)
        return []


def fetch_request_row(request_id, user_id=None, admin=False):
    """Fetch a single adoption_requests row by id."""
    db = _admin_db() if admin else supabase
    try:
        query = db.table("adoption_requests").select("*").eq("id", request_id)
        if user_id:
            query = query.eq("user_id", user_id)
        return query.single().execute().data
    except Exception as e:
        log.error("fetch_request_row(%s) failed: %s", request_id, e)
        return None


def build_request_cards(ar_rows, admin=False, include_messages=True):
    db = _admin_db() if admin else supabase
    if not ar_rows:
        return []

    cat_ids = sorted({row.get("cat_id") for row in ar_rows if row.get("cat_id") is not None})
    user_ids = sorted({row.get("user_id") for row in ar_rows if row.get("user_id")})
    request_ids = [row.get("id") for row in ar_rows if row.get("id") is not None]

    cats_by_id = {}
    users_by_id = {}
    messages_by_request = fetch_messages_for_requests(request_ids, admin=admin) if include_messages else {}
    deliveries_by_request = fetch_deliveries_for_requests(request_ids, admin=admin)

    try:
        if cat_ids:
            cat_rows = db.table("cats").select("id, name, breed, adoption_fee").in_("id", cat_ids).execute().data or []
            cats_by_id = {row["id"]: row for row in cat_rows}
    except Exception as e:
        log.error("build_request_cards cat lookup failed: %s", e)

    try:
        if user_ids:
            user_rows = db.table("users").select(
                "id, full_name, email, phone, address, valid_id_url"
            ).in_("id", user_ids).execute().data or []
            users_by_id = {row["id"]: row for row in user_rows}
    except Exception as e:
        log.error("build_request_cards user lookup failed: %s", e)

    cards = []
    for row in ar_rows:
        try:
            cat = cats_by_id.get(row.get("cat_id") or 0, {})
            user = users_by_id.get(row.get("user_id") or "", {})
            # deliveries table is secondary — adoption_requests is the primary source of truth
            delivery = deliveries_by_request.get(row.get("id"), {})

            payment_status = row.get("payment_status") or "Pending Payment"

            # delivery_method: prefer adoption_requests, fall back to deliveries
            raw_delivery_method = row.get("delivery_method") or delivery.get("delivery_method") or delivery.get("method") or "Meet-up"
            delivery_method = "Pick-up" if str(raw_delivery_method).strip().lower() in {"pickup", "pick-up"} else raw_delivery_method

            # delivery_status: adoption_requests first, then deliveries
            delivery_status = (
                row.get("delivery_status")
                or delivery.get("delivery_status")
                or delivery.get("status")
                or ("Preparing" if delivery_method in ("Delivery", "Pick-up") else None)
            )

            # estimated delivery date: adoption_requests columns first, then deliveries
            estimated_delivery_date = (
                row.get("delivery_date")
                or row.get("estimated_delivery")
                or delivery.get("delivery_date")
                or delivery.get("estimated_delivery")
                or delivery.get("estimated_delivery_date")
                or delivery.get("scheduled_date")
            )

            # rider fields: adoption_requests first, then deliveries
            rider_name = row.get("rider_name") or delivery.get("rider_name")
            rider_contact = row.get("rider_contact") or delivery.get("rider_contact") or delivery.get("rider_phone")

            # time window: always from adoption_requests
            delivery_time_start = row.get("delivery_time_start") or delivery.get("delivery_time_start")
            delivery_time_end   = row.get("delivery_time_end")   or delivery.get("delivery_time_end")

            # delivery address: adoption_requests first
            delivery_address = row.get("delivery_address") or delivery.get("delivery_address") or row.get("address") or ""

            # pickup-specific fields (only relevant for Pick-up method)
            pickup_location = (
                row.get("pickup_location")
                or delivery.get("pickup_location")
                or delivery.get("location")
                or row.get("delivery_address")
                or row.get("address")
                or user.get("address")
            )
            pickup_date    = row.get("pickup_date") or delivery.get("pickup_date")
            pickup_time    = row.get("pickup_time") or delivery.get("pickup_time")
            pickup_notes   = row.get("pickup_notes") or delivery.get("pickup_notes")
            pickup_contact_person = delivery.get("contact_person") or delivery.get("contact_name") or row.get("rider_name")
            pickup_contact_number = delivery.get("contact_number") or delivery.get("contact_phone") or row.get("rider_contact")

            payment_method = row.get("payment_method") or ("Cash on Delivery" if delivery_method == "Delivery" else "Cash on Arrival")

            card = {
                "id": row.get("id"),
                "user_id": row.get("user_id"),
                "cat_id": row.get("cat_id"),
                "cat_name": cat.get("name") or "Unknown Cat",
                "cat_breed": cat.get("breed") or "",
                "cat_fee": cat.get("adoption_fee"),
                "status": row.get("status") or "Pending",
                "created_at": parse_dt(row.get("created_at")),
                "payment_status": payment_status,
                "payment_proof": row.get("payment_proof"),
                "payment_method": payment_method,
                "delivery_fee": DELIVERY_FEE if delivery_method == "Delivery" else 0,
                "delivery_method": delivery_method,
                "delivery_status": delivery_status,
                "meetup_location": row.get("meetup_location"),
                "meetup_map_link": row.get("meetup_map_link"),
                "meetup_date": row.get("meetup_date") or row.get("schedule_date"),
                "meetup_time": row.get("meetup_time") or row.get("schedule_time"),
                "full_name": row.get("full_name") or user.get("full_name") or "",
                "email": row.get("email") or user.get("email") or "",
                "contact_number": row.get("contact_number") or user.get("phone") or "",
                "address": row.get("address") or user.get("address") or "",
                "reason": row.get("reason") or "",
                "experience_with_pets": row.get("experience_with_pets") or "",
                "valid_id_url": user.get("valid_id_url"),
                "completion_photo_url": row.get("completion_photo_url"),
                "delivery_date": estimated_delivery_date,
                "estimated_delivery": estimated_delivery_date,
                "delivery_time_start": delivery_time_start,
                "delivery_time_end": delivery_time_end,
                "delivery_address": delivery_address,
                "rider_name": rider_name,
                "rider_contact": rider_contact,
                "pickup_location": pickup_location,
                "pickup_date": pickup_date,
                "pickup_time": pickup_time,
                "pickup_notes": pickup_notes,
                "pickup_contact_person": pickup_contact_person,
                "pickup_contact_number": pickup_contact_number,
                "delivery_photo_url": row.get("delivery_photo_url"),
                "messages": messages_by_request.get(row.get("id"), []),
            }
            cards.append(card)
        except Exception as e:
            log.error("build_request_cards row=%s failed: %s", row.get("id"), e)
            continue
    return cards



# ------------------------------------------------------------------ browse --

@app.route("/browse")
def browse():
    try:
        res = supabase.table("cats").select("*").execute()
        cats = [
            (c["id"], c["name"], c["breed"], c["age"], c["gender"],
             c.get("image", "cat1.jpg"), c.get("status", "available"),
             c.get("adoption_fee"))
            for c in (res.data or [])
        ]
    except Exception as e:
        log.error("browse — cats fetch failed: %s", e)
        cats = []
    return render_template("browse.html", cats=cats, logged_in="user_id" in session)


# ------------------------------------------------------------------ root --

@app.route("/")
def index():
    if "user_id" in session:
        if session.get("role") == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("dashboard"))
    return redirect(url_for("browse"))


# ------------------------------------------------------------------ login --

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        if session.get("role") == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email    = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        if not email or not password:
            return render_template("login.html", error="Please fill all fields")
        try:
            res = supabase.auth.sign_in_with_password({"email": email, "password": password})
            user_data = supabase.table("users").select("role").eq("email", email).single().execute().data
            role = user_data["role"] if user_data else "user"
            session.clear()
            session["user_id"] = res.user.id
            session["email"]   = res.user.email
            session["role"]    = role
            if role == "admin":
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("dashboard"))
        except Exception as e:
            err = str(e)
            log.warning("login failed for %s: %s", email, err)
            if "Invalid login credentials" in err:
                return render_template("login.html", error="Invalid email or password")
            if "Email not confirmed" in err:
                return render_template("login.html", error="Please verify your email first")
            return render_template("login.html", error="Login failed. Please try again.")
    return render_template("login.html")


# ------------------------------------------------------------------ admin debug --

@app.route("/admin/debug")
def admin_debug():
    """Diagnostic page — shows exactly what Supabase returns for each table.
    Remove or protect this route before going to production."""
    if not admin_required():
        return redirect(url_for("login"))
    from flask import jsonify
    db = _admin_db()
    results = {}
    service_key_set = bool(os.environ.get("SUPABASE_SERVICE_KEY", "").strip())
    results["service_key_configured"] = service_key_set
    results["using_service_role"] = (db is not supabase)
    for table in ("users", "cats", "adoption_requests"):
        try:
            res = db.table(table).select("*").limit(3).execute()
            results[table] = {
                "count": len(res.data or []),
                "sample": (res.data or [])[:2],
                "error": None,
            }
        except Exception as e:
            results[table] = {"count": 0, "sample": [], "error": str(e)}
    return jsonify(results)


# ------------------------------------------------------------------ admin login (legacy) --

@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    return redirect(url_for("login"))


# ------------------------------------------------------------------ admin guard --

def admin_required():
    return "user_id" in session and session.get("role") == "admin"


def _admin_db():
    """Return the service-role client for admin queries so RLS is bypassed.
    Falls back to the anon client if the service key is not configured."""
    from supabase_client import SUPABASE_SERVICE_KEY
    if SUPABASE_SERVICE_KEY and supabase_admin and supabase_admin is not supabase:
        return supabase_admin
    return supabase


# ------------------------------------------------------------------ admin dashboard --

@app.route("/admin")
def admin_dashboard():
    if not admin_required():
        return redirect(url_for("login"))
    try:
        cats_res = _admin_db().table("cats").select("id").execute()
        total_cats = len(cats_res.data or [])
    except Exception:
        total_cats = 0
    try:
        users_res = _admin_db().table("users").select("id").execute()
        total_users = len(users_res.data or [])
    except Exception:
        total_users = 0
    try:
        pending_res = _admin_db().table("adoption_requests").select("id").eq("status", "Pending").execute()
        pending_count = len(pending_res.data or [])
    except Exception:
        pending_count = 0
    try:
        ar_data = _fetch_requests(_admin_db(), limit=5)
        requests_list = build_request_cards(ar_data, admin=True, include_messages=False)
    except Exception as e:
        log.error("admin_dashboard requests failed: %s", e)
        requests_list = []
    return render_template("admin_dashboard.html",
                           requests=requests_list,
                           total_cats=total_cats,
                           pending_count=pending_count,
                           total_users=total_users,
                           active_page="dashboard")

# ------------------------------------------------------------------ admin schedule meet-up --

@app.route("/admin/schedule/<int:req_id>", methods=["POST"])
def admin_schedule(req_id):
    if not admin_required():
        return redirect(url_for("login"))
    schedule_date    = request.form.get("schedule_date", "").strip()
    schedule_time    = request.form.get("schedule_time", "").strip()
    meetup_location  = request.form.get("meetup_location", "").strip()
    meetup_map_link  = request.form.get("meetup_map_link", "").strip()
    row = fetch_request_row(req_id, admin=True)
    if not row:
        flash("Adoption request not found.", "error")
        return redirect(url_for("admin_requests"))
    if (row.get("delivery_method") or "Meet-up") != "Meet-up":
        flash("Meet-up scheduling is only available for meet-up requests.", "error")
        return redirect(url_for("admin_requests"))
    if not schedule_date or not schedule_time or not meetup_location:
        flash("Location, date, and time are required.", "error")
        return redirect(url_for("admin_requests"))
    try:
        _admin_db().table("adoption_requests").update({
            "status":           "Scheduled",
            "meetup_date":      schedule_date,
            "meetup_time":      schedule_time,
            "schedule_date":    schedule_date,
            "schedule_time":    schedule_time,
            "meetup_location":  meetup_location or None,
            "meetup_map_link":  meetup_map_link or None,
        }).eq("id", req_id).execute()
        try:
            cat_name = _get_cat_name(row.get("cat_id"))
            map_link = f"<br><a href='{meetup_map_link}'>View on Map</a>" if meetup_map_link else ""
            extra = (f"<p><strong>Date:</strong> {schedule_date}<br>"
                     f"<strong>Time:</strong> {schedule_time}<br>"
                     f"<strong>Location:</strong> {meetup_location}{map_link}</p>")
            notify_adoption_status(row.get("email", ""), row.get("full_name", "Adopter"),
                                   cat_name, "Scheduled", extra_html=extra)
        except Exception as ne:
            log.warning("admin_schedule notify failed: %s", ne)
        flash("Meet-up scheduled.", "success")
    except Exception as e:
        log.error("admin_schedule(%s) failed: %s", req_id, e)
        flash(f"Failed to schedule: {e}", "error")
    return redirect(url_for("admin_requests"))


# ------------------------------------------------------------------ admin mark completed --

@app.route("/admin/complete/<int:req_id>", methods=["POST"])
def admin_complete(req_id):
    if not admin_required():
        return redirect(url_for("login"))
    try:
        db = _admin_db()
        ar = db.table("adoption_requests").select("cat_id").eq("id", req_id).single().execute().data
        db.table("adoption_requests").update({"status": "Completed"}).eq("id", req_id).execute()
        if ar and ar.get("cat_id"):
            db.table("cats").update({"status": "adopted"}).eq("id", ar["cat_id"]).execute()
        flash("Adoption marked as Completed.", "success")
    except Exception as e:
        log.error("admin_complete(%s) failed: %s", req_id, e)
        flash(f"Failed: {e}", "error")
    return redirect(url_for("admin_requests"))


# ------------------------------------------------------------------ admin update status --

@app.route("/admin/update_status/<int:req_id>", methods=["POST"])
def update_status(req_id):
    if not admin_required():
        return redirect(url_for("login"))
    new_status = request.form.get("status")
    valid_statuses = {"Pending", "Approved", "Scheduled", "Completed", "Rejected",
                      "Preparing", "Out for Delivery", "Delivered", "Ready for Pickup", "Claimed"}
    if new_status not in valid_statuses:
        flash("Invalid status.", "error")
        return redirect(url_for("admin_requests"))
    try:
        db = _admin_db()
        ar = db.table("adoption_requests").select("cat_id, email, full_name").eq("id", req_id).single().execute().data
        update_payload = {"status": new_status}
        if new_status == "Approved":
            update_payload["payment_status"] = "Pending Payment"
        db.table("adoption_requests").update(update_payload).eq("id", req_id).execute()
        if ar and ar.get("cat_id"):
            if new_status in ("Approved", "Completed"):
                db.table("cats").update({"status": "adopted"}).eq("id", ar["cat_id"]).execute()
            elif new_status == "Rejected":
                db.table("cats").update({"status": "available"}).eq("id", ar["cat_id"]).execute()
        if ar:
            try:
                cat_name = _get_cat_name(ar.get("cat_id"))
                reject_reason = request.form.get("reject_reason", "").strip()
                extra = f"<p><em>Reason: {reject_reason}</em></p>" if reject_reason and new_status == "Rejected" else ""
                notify_adoption_status(ar.get("email", ""), ar.get("full_name", "Adopter"),
                                       cat_name, new_status, extra_html=extra)
            except Exception as notify_err:
                log.warning("notify_adoption_status failed: %s", notify_err)
        flash(f"Request updated to {new_status}.", "success")
    except Exception as e:
        log.error("update_status(%s) failed: %s", req_id, e)
        flash(f"Failed to update status: {e}", "error")
    return redirect(url_for("admin_requests"))


# ------------------------------------------------------------------ admin delete request --

@app.route("/admin/delete_request/<int:req_id>", methods=["POST"])
def admin_delete_request(req_id):
    if not admin_required():
        return redirect(url_for("login"))
    try:
        _admin_db().table("adoption_requests").delete().eq("id", req_id).execute()
        flash("Request deleted.", "success")
    except Exception as e:
        log.error("admin_delete_request(%s) failed: %s", req_id, e)
        flash(f"Failed to delete request: {e}", "error")
    return redirect(url_for("admin_requests"))


# ------------------------------------------------------------------ admin messages --

@app.route("/admin/messages")
def admin_messages():
    if not admin_required():
        return redirect(url_for("login"))
    db = _admin_db()
    conversations = []
    try:
        convo_rows = db.table("conversations") \
            .select("id, user_id, cat_id, created_at") \
            .order("created_at", desc=True).execute().data or []

        if convo_rows:
            convo_ids = [c["id"] for c in convo_rows]
            user_ids  = list({c["user_id"] for c in convo_rows if c.get("user_id")})
            cat_ids   = list({c["cat_id"]  for c in convo_rows if c.get("cat_id")})

            users_by_id = {}
            if user_ids:
                u_rows = db.table("users").select("id, full_name, email") \
                    .in_("id", user_ids).execute().data or []
                users_by_id = {r["id"]: r for r in u_rows}

            cats_by_id = {}
            if cat_ids:
                c_rows = db.table("cats").select("id, name") \
                    .in_("id", cat_ids).execute().data or []
                cats_by_id = {r["id"]: r["name"] for r in c_rows}

            msg_rows = db.table("messages") \
                .select("id, conversation_id, sender, message, created_at, is_read") \
                .in_("conversation_id", convo_ids) \
                .order("created_at").execute().data or []

            msgs_by_convo = {cid: [] for cid in convo_ids}
            for m in msg_rows:
                msgs_by_convo.setdefault(m["conversation_id"], []).append({
                    "id":         m.get("id"),
                    "sender":     m.get("sender") or "user",
                    "message":    m.get("message") or "",
                    "created_at": parse_dt(m.get("created_at")),
                    "is_read":    m.get("is_read", True),
                })

            for c in convo_rows:
                msgs = msgs_by_convo.get(c["id"], [])
                user = users_by_id.get(c["user_id"], {})
                has_unread = any(
                    not m["is_read"] and m["sender"] == "user" for m in msgs
                )
                conversations.append({
                    "id":        c["id"],
                    "user_id":   c["user_id"],
                    "cat_id":    c["cat_id"],
                    "full_name": user.get("full_name") or "Unknown User",
                    "email":     user.get("email") or "",
                    "cat_name":  cats_by_id.get(c["cat_id"], "Unknown Cat"),
                    "messages":  msgs,
                    "has_unread": has_unread,
                })

        # Mark all user messages as read now that admin has opened the page
        if convo_rows:
            try:
                db.table("messages").update({"is_read": True}) \
                    .eq("sender", "user").eq("is_read", False) \
                    .in_("conversation_id", [c["id"] for c in convo_rows]).execute()
            except Exception:
                pass

    except Exception as e:
        log.error("admin_messages failed: %s", e)
    return render_template("admin_messages.html", conversations=conversations, active_page="messages")


# ------------------------------------------------------------------ admin reply in conversation --

@app.route("/admin/convo/reply/<int:convo_id>", methods=["POST"])
def admin_convo_reply(convo_id):
    if not admin_required():
        return redirect(url_for("login"))
    text = request.form.get("message", "").strip()
    if not text:
        flash("Message cannot be empty.", "error")
        return redirect(url_for("admin_messages"))
    try:
        _admin_db().table("messages").insert({
            "conversation_id": convo_id,
            "sender": "admin",
            "message": text,
        }).execute()
    except Exception as e:
        log.error("admin_convo_reply(%s) failed: %s", convo_id, e)
        flash("Failed to send message.", "error")
    return redirect(url_for("admin_messages"))


# ------------------------------------------------------------------ admin delete conversation --

@app.route("/admin/convo/delete/<int:convo_id>", methods=["POST"])
def admin_delete_convo(convo_id):
    if not admin_required():
        return redirect(url_for("login"))
    try:
        _admin_db().table("messages").delete().eq("conversation_id", convo_id).execute()
        _admin_db().table("conversations").delete().eq("id", convo_id).execute()
        flash("Conversation deleted.", "success")
    except Exception as e:
        log.error("admin_delete_convo(%s) failed: %s", convo_id, e)
        flash("Failed to delete conversation.", "error")
    return redirect(url_for("admin_messages"))




@app.route("/admin/requests")
def admin_requests():
    if not admin_required():
        return redirect(url_for("login"))
    try:
        ar_data = _fetch_requests(_admin_db())
        requests_list = build_request_cards(ar_data, admin=True)
    except Exception as e:
        log.error("admin_requests failed: %s", e)
        requests_list = []
    return render_template("admin_requests.html", requests=requests_list, active_page="requests")


# ------------------------------------------------------------------ admin cats --

@app.route("/admin/cats")
def admin_cats():
    if not admin_required():
        return redirect(url_for("login"))
    search = request.args.get("search", "").strip()
    try:
        cats = _admin_db().table("cats").select("*").order("id").execute().data or []
        if search:
            cats = [c for c in cats
                    if search.lower() in (c.get("name") or "").lower()
                    or search.lower() in (c.get("breed") or "").lower()]
    except Exception as e:
        log.error("admin_cats failed: %s", e)
        cats = []
    return render_template("admin_cats.html", cats=cats, search=search, active_page="cats", breeds=sorted({c.get("breed") for c in cats if c.get("breed")}))


@app.route("/admin/cats/add", methods=["POST"])
def admin_cats_add():
    if not admin_required():
        return redirect(url_for("login"))
    name   = request.form.get("name", "").strip()
    breed  = request.form.get("breed", "").strip()
    age    = request.form.get("age", "").strip()
    gender = request.form.get("gender", "").strip()
    status = request.form.get("status", "available").strip()
    image  = request.form.get("image", "cat1.jpg").strip() or "cat1.jpg"
    if not name or not breed or not gender:
        flash("Name, breed, and gender are required.", "error")
        return redirect(url_for("admin_cats"))
    payload = {
        "name": name, "breed": breed, "gender": gender,
        "status": status, "image": image,
        "age":                int(age) if age.isdigit() else None,
        "origin":             request.form.get("origin", "").strip() or None,
        "weight":             request.form.get("weight", "").strip() or None,
        "size":               request.form.get("size", "").strip() or None,
        "lifespan":           request.form.get("lifespan", "").strip() or None,
        "coat_colors":        request.form.get("coat_colors", "").strip() or None,
        "temperament":        request.form.get("temperament", "").strip() or None,
        "about":              request.form.get("about", "").strip() or None,
        "adoption_fee":       _safe_float(request.form.get("adoption_fee", "")),
        "vaccination_status": request.form.get("vaccination_status", "").strip() or None,
        "health_status":      request.form.get("health_status", "").strip() or None,
        "spayed_neutered":    request.form.get("spayed_neutered") == "yes",
    }
    try:
        _admin_db().table("cats").insert(payload).execute()
        flash(f"{name} added successfully.", "success")
    except Exception as e:
        log.error("admin_cats_add failed: %s", e)
        flash(f"Failed to add cat: {e}", "error")
    return redirect(url_for("admin_cats"))


@app.route("/admin/cats/edit/<int:cat_id>", methods=["POST"])
def admin_cats_edit(cat_id):
    if not admin_required():
        return redirect(url_for("login"))
    name   = request.form.get("name", "").strip()
    breed  = request.form.get("breed", "").strip()
    age    = request.form.get("age", "").strip()
    gender = request.form.get("gender", "").strip()
    status = request.form.get("status", "available").strip()
    image  = request.form.get("image", "").strip() or "cat1.jpg"
    payload = {
        "name": name, "breed": breed, "gender": gender,
        "status": status, "image": image,
        "age":                int(age) if age.isdigit() else None,
        "origin":             request.form.get("origin", "").strip() or None,
        "weight":             request.form.get("weight", "").strip() or None,
        "size":               request.form.get("size", "").strip() or None,
        "lifespan":           request.form.get("lifespan", "").strip() or None,
        "coat_colors":        request.form.get("coat_colors", "").strip() or None,
        "temperament":        request.form.get("temperament", "").strip() or None,
        "about":              request.form.get("about", "").strip() or None,
        "adoption_fee":       _safe_float(request.form.get("adoption_fee", "")),
        "vaccination_status": request.form.get("vaccination_status", "").strip() or None,
        "health_status":      request.form.get("health_status", "").strip() or None,
        "spayed_neutered":    request.form.get("spayed_neutered") == "yes",
    }
    try:
        _admin_db().table("cats").update(payload).eq("id", cat_id).execute()
        flash(f"{name} updated successfully.", "success")
    except Exception as e:
        log.error("admin_cats_edit(%s) failed: %s", cat_id, e)
        flash(f"Failed to update cat: {e}", "error")
    return redirect(url_for("admin_cats"))


@app.route("/admin/cats/delete/<int:cat_id>", methods=["POST"])
def admin_cats_delete(cat_id):
    if not admin_required():
        return redirect(url_for("login"))
    try:
        _admin_db().table("cats").delete().eq("id", cat_id).execute()
        flash("Cat deleted.", "success")
    except Exception as e:
        log.error("admin_cats_delete(%s) failed: %s", cat_id, e)
        flash(f"Failed to delete cat: {e}", "error")
    return redirect(url_for("admin_cats"))


# ------------------------------------------------------------------ admin users --



@app.route('/api/breeds')
def api_breeds():
    try:
        rows = supabase.table('cats').select('breed').execute().data or []
        breeds = sorted({r['breed'] for r in rows if r.get('breed')})
        return jsonify(breeds)
    except Exception:
        return jsonify([]), 200

@app.route("/admin/users")
def admin_users():
    if not admin_required():
        return redirect(url_for("login"))
    try:
        users = _admin_db().table("users").select(
            "id, full_name, email, phone, address, role"
        ).execute().data or []
    except Exception as e:
        log.error("admin_users failed: %s", e)
        users = []
    return render_template("admin_users.html", users=users, active_page="users")


@app.route("/admin/users/update_role/<user_id>", methods=["POST"])
def admin_users_update_role(user_id):
    if not admin_required():
        return redirect(url_for("login"))
    role = request.form.get("role", "user")
    if role not in ("user", "admin"):
        flash("Invalid role.", "error")
        return redirect(url_for("admin_users"))
    try:
        _admin_db().table("users").update({"role": role}).eq("id", user_id).execute()
        flash("Role updated.", "success")
    except Exception as e:
        log.error("admin_users_update_role(%s) failed: %s", user_id, e)
        flash(f"Failed to update role: {e}", "error")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/delete/<user_id>", methods=["POST"])
def admin_users_delete(user_id):
    if not admin_required():
        return redirect(url_for("login"))
    if user_id == session.get("user_id"):
        flash("You cannot delete your own account.", "error")
        return redirect(url_for("admin_users"))
    try:
        _admin_db().table("users").delete().eq("id", user_id).execute()
        flash("User deleted.", "success")
    except Exception as e:
        log.error("admin_users_delete(%s) failed: %s", user_id, e)
        flash(f"Failed to delete user: {e}", "error")
    return redirect(url_for("admin_users"))


# ------------------------------------------------------------------ register --

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        fullname = request.form.get("fullname", "").strip()
        if not email or not password or not fullname:
            return render_template("register.html", error="Please fill all fields")
        if len(password) < 6:
            return render_template("register.html", error="Password must be at least 6 characters")
        try:
            res = supabase.auth.sign_up({
                "email": email,
                "password": password,
                "options": {"data": {"full_name": fullname}},
            })
            if res.user:
                code = str(random.randint(100000, 999999))
                session["pending_verify_email"]    = email
                session["pending_verify_name"]     = fullname
                session["pending_verify_code"]     = code
                session["pending_verify_expires"]  = time.time() + 600  # 10 min
                try:
                    send_verification_email(email, fullname, code)
                except Exception as mail_err:
                    log.error("send_verification_email failed: %s", mail_err)
                flash("Enter the verification code sent to your email.", "success")
                return redirect(url_for("verify"))
            else:
                return render_template("register.html", error="Registration failed.")
        except Exception as e:
            return render_template("register.html", error=str(e))
    return render_template("register.html")


# ------------------------------------------------------------------ verify --

@app.route("/verify", methods=["GET", "POST"])
def verify():
    email = session.get("pending_verify_email")
    if not email:
        return redirect(url_for("login"))
    if request.method == "POST":
        otp = request.form.get("otp", "").strip()
        if not otp:
            return render_template("verify.html", error="Enter the code.", email=email)
        stored_code    = session.get("pending_verify_code", "")
        expires_at     = session.get("pending_verify_expires", 0)
        if time.time() > expires_at:
            return render_template("verify.html", error="Code expired. Please register again.", email=email)
        if otp != stored_code:
            return render_template("verify.html", error="Invalid code. Please try again.", email=email)
        for key in ("pending_verify_email", "pending_verify_name", "pending_verify_code", "pending_verify_expires"):
            session.pop(key, None)
        flash("Email verified! You can now sign in.", "success")
        return redirect(url_for("login"))
    return render_template("verify.html", email=email)


# ------------------------------------------------------------------ forgot password --

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not email:
            return render_template("forgot_password.html", error="Please enter your email.")
        try:
            supabase.auth.reset_password_email(
                email,
                {"redirect_to": url_for("reset_password", _external=True)}
            )
        except Exception as e:
            log.error("forgot_password failed: %s", e)
        flash("If that email exists, a reset link has been sent.", "success")
        return redirect(url_for("forgot_password"))
    return render_template("forgot_password.html")


@app.route("/reset-password", methods=["GET"])
def reset_password():
    return render_template(
        "reset_password.html",
        SUPABASE_URL=os.environ.get("SUPABASE_URL", ""),
        SUPABASE_KEY=os.environ.get("SUPABASE_KEY", ""),
    )


# ------------------------------------------------------------------ user unread messages API --

@app.route("/api/user/unread_messages")
def api_user_unread_messages():
    if "user_id" not in session:
        return jsonify({"unread": 0})
    try:
        user_id = session["user_id"]
        unread = 0
        # Count unread admin messages in conversations belonging to this user
        convo_rows = supabase.table("conversations").select("id") \
            .eq("user_id", user_id).execute().data or []
        if convo_rows:
            convo_ids = [r["id"] for r in convo_rows]
            rows = supabase.table("messages").select("id") \
                .eq("sender", "admin").eq("is_read", False) \
                .in_("conversation_id", convo_ids).execute().data or []
            unread += len(rows)
        return jsonify({"unread": unread})
    except Exception as e:
        log.error("api_user_unread_messages failed: %s", e)
        return jsonify({"unread": 0})




@app.route("/api/admin/badges")
def api_admin_badges():
    if not admin_required():
        return jsonify({"pending_requests": 0, "unread_messages": 0})
    try:
        pending = len(_admin_db().table("adoption_requests").select("id").eq("status", "Pending").execute().data or [])
    except Exception:
        pending = 0
    try:
        # Only count unread messages that belong to a conversation (conversation_id is not null)
        rows = _admin_db().table("messages").select("id") \
            .eq("sender", "user").eq("is_read", False) \
            .not_.is_("conversation_id", "null").execute().data or []
        unread = len(rows)
    except Exception:
        unread = 0
    return jsonify({"pending_requests": pending, "unread_messages": unread})




@app.route("/dashboard")
def dashboard():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login"))
    search = request.args.get("search", "")
    breed  = request.args.get("breed", "")
    try:
        q = supabase.table("cats").select("*")
        if search:
            q = q.ilike("name", f"%{search}%")
        if breed and breed != "All Breeds":
            q = q.eq("breed", breed)
        cats = q.execute().data or []
        pending_count = len(
            supabase.table("adoption_requests").select("id")
            .eq("user_id", user_id).eq("status", "Pending")
            .execute().data or []
        )
    except Exception as e:
        log.error("dashboard failed: %s", e)
        cats = []
        pending_count = 0
    user = get_user_profile(user_id)
    try:
        all_cats_for_breeds = supabase.table("cats").select("breed").execute().data or []
        breeds = sorted({c.get("breed") for c in all_cats_for_breeds if c.get("breed")})
    except Exception:
        breeds = sorted({c.get("breed") for c in cats if c.get("breed")})
    return render_template("dashboard.html", cats=cats, user=user, pending_count=pending_count,
                           active_page="dashboard", breeds=breeds, delivery_fee=DELIVERY_FEE)


# ------------------------------------------------------------------ avatar upload API --

@app.route("/api/upload_avatar", methods=["POST"])
def upload_avatar():
    from flask import jsonify
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    file = request.files.get("avatar")
    if not file or not file.filename:
        return jsonify({"error": "No file provided"}), 400

    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_AVATAR_EXTENSIONS:
        return jsonify({"error": "Only JPG and PNG files are allowed"}), 400

    file_bytes = file.read()
    if len(file_bytes) > MAX_AVATAR_BYTES:
        return jsonify({"error": "File exceeds 2 MB limit"}), 400

    try:
        storage_client = supabase_admin or supabase
        storage_path   = f"{session['user_id']}.{ext}"

        storage_client.storage.from_(AVATAR_BUCKET).upload(
            storage_path, file_bytes,
            {"content-type": file.content_type, "upsert": "true"}
        )
        public_url = storage_client.storage.from_(AVATAR_BUCKET).get_public_url(storage_path)

        supabase.table("users").update({"avatar_url": public_url}).eq("id", session["user_id"]).execute()
        log.info("upload_avatar: saved avatar for user")
        return jsonify({"ok": True, "url": public_url})
    except Exception as e:
        err_detail = repr(e)
        log.error("upload_avatar failed — user=%s error=%s", session.get("user_id"), err_detail)
        err_lower = err_detail.lower()
        if "row-level security" in err_lower or "403" in err_lower or "unauthorized" in err_lower:
            return jsonify({
                "error": "Storage permission denied. Add SUPABASE_SERVICE_KEY to your environment variables.",
                "detail": err_detail
            }), 403
        return jsonify({"error": str(e), "detail": err_detail}), 500


# ------------------------------------------------------------------ cat detail API --

@app.route("/api/cat/<int:cat_id>")
def api_cat_detail(cat_id):
    from flask import jsonify
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401
    try:
        res = supabase.table("cats").select("*").eq("id", cat_id).single().execute()
        data = res.data or {}
        log.debug("api_cat_detail(%s) returned %d keys", cat_id, len(data))
        return jsonify(data)
    except Exception as e:
        log.error("api_cat_detail(%s) failed: %r", cat_id, e)
        return jsonify({"error": str(e)}), 500


# ------------------------------------------------------------------ cat update API --

@app.route("/api/cat/update", methods=["POST"])
def api_cat_update():
    from flask import jsonify
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json()
    cat_id = data.pop("id", None)
    if not cat_id:
        return jsonify({"error": "missing id"}), 400
    try:
        res = supabase.table("cats").update(data).eq("id", cat_id).execute()
        return jsonify({"ok": True, "data": res.data})
    except Exception as e:
        log.error("api_cat_update(%s) failed: %s", cat_id, e)
        return jsonify({"error": str(e)}), 500


# ------------------------------------------------------------------ cat delete API --

@app.route("/api/cat/delete", methods=["POST"])
def api_cat_delete():
    from flask import jsonify
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json()
    cat_id = data.get("id")
    if not cat_id:
        return jsonify({"error": "missing id"}), 400
    try:
        supabase.table("cats").delete().eq("id", cat_id).execute()
        return jsonify({"ok": True})
    except Exception as e:
        log.error("api_cat_delete(%s) failed: %s", cat_id, e)
        return jsonify({"error": str(e)}), 500


# ------------------------------------------------------------------ update payment method (JSON API) --

@app.route("/update-payment-method/<int:request_id>", methods=["POST"])
def update_payment_method(request_id):
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401
    try:
        data = request.get_json(force=True) or {}
        payment_method = data.get("payment_method", "").strip()
        log.info("update_payment_method: req=%s", request_id)
        if payment_method not in ("GCash", "COD"):
            return jsonify({"error": "Invalid payment method. Must be GCash or COD."}), 400
        supabase.table("adoption_requests").update({
            "payment_method": payment_method,
            "payment_status": "Pending Payment",
        }).eq("id", request_id).eq("user_id", session["user_id"]).execute()
        latest = fetch_request_row(request_id, user_id=session["user_id"])
        if not latest:
            return jsonify({"error": "Request not found."}), 404
        return jsonify({"success": True, "data": build_request_cards([latest], include_messages=False)[0]})
    except Exception as e:
        log.error("update_payment_method(%s) failed: %s", request_id, e)
        return jsonify({"error": str(e)}), 500


# ------------------------------------------------------------------ select payment method --

@app.route("/select_payment_method/<int:req_id>", methods=["POST"])
def select_payment_method(req_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    method = request.form.get("payment_method", "GCash")
    if method not in ("GCash", "COD"):
        flash("Invalid payment method.", "error")
        return redirect(url_for("history"))
    try:
        supabase.table("adoption_requests").update({
            "payment_method": method,
            "payment_status": "Pending Payment",
        }).eq(
            "id", req_id).eq("user_id", session["user_id"]).execute()
        latest = fetch_request_row(req_id, user_id=session["user_id"])
        flash(f"Payment method set to {(latest or {}).get('payment_method', method)}.", "success")
    except Exception as e:
        log.error("select_payment_method(%s) failed: %s", req_id, e)
        flash("Failed to save payment method.", "error")
    return redirect(url_for("history"))


# ------------------------------------------------------------------ upload payment receipt --

@app.route("/upload_receipt/<int:req_id>", methods=["POST"])
def upload_receipt(req_id):
    # ✅ Check login
    if "user_id" not in session:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    # ✅ Get file
    file = request.files.get("receipt")
    if not file or file.filename.strip() == "":
        return jsonify({"ok": False, "error": "No file provided"}), 400

    # ✅ Validate extension
    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"ok": False, "error": "Invalid file type. Use JPG, PNG, or PDF."}), 400

    # ✅ Read file safely
    file_bytes = file.read()
    if not file_bytes:
        return jsonify({"ok": False, "error": "Empty file"}), 400

    # ✅ Validate file size
    if len(file_bytes) > MAX_AVATAR_BYTES:
        return jsonify({"ok": False, "error": "File exceeds 2 MB"}), 400

    try:
        # ✅ Generate unique filename (prevents overwrite)
        path = f"receipt_{req_id}_{session['user_id']}_{int(time.time())}.{ext}"

        # ✅ Upload to Supabase Storage
        public_url = upload_public_file(
            PAYMENT_BUCKET,
            path,
            file_bytes,
            file.content_type
        )

        if not public_url:
            return jsonify({"ok": False, "error": "Upload failed"}), 500

        # ✅ Update database
        supabase.table("adoption_requests").update({
            "payment_proof": public_url,
            "payment_status": "For Verification",
        }).eq("id", req_id).eq("user_id", session["user_id"]).execute()

        # Notify admin-side (user uploaded receipt)
        try:
            row_for_notify = fetch_request_row(req_id, user_id=session["user_id"])
            if row_for_notify:
                cat_name = _get_cat_name(row_for_notify.get("cat_id"))
                notify_payment_status(
                    row_for_notify.get("email", ""),
                    row_for_notify.get("full_name", "Adopter"),
                    cat_name, "For Verification"
                )
        except Exception as notify_err:
            log.warning("notify_payment_status failed: %s", notify_err)

        # ✅ Fetch updated data
        latest = fetch_request_row(req_id, user_id=session["user_id"])
        data = build_request_cards([latest], include_messages=False)[0] if latest else None

        return jsonify({
            "ok": True,
            "url": public_url,
            "data": data
        })

    except Exception as e:
        err = str(e)
        log.error("upload_receipt(%s) failed: %s", req_id, err)

        # ✅ Friendly Supabase error
        if "bucket" in err.lower() or "not found" in err.lower():
            return jsonify({
                "ok": False,
                "error": f"Storage bucket '{PAYMENT_BUCKET}' not found. Create it in Supabase Storage."
            }), 500

        return jsonify({"ok": False, "error": err}), 500


# ------------------------------------------------------------------ admin update payment status --

@app.route("/admin/update_payment/<int:req_id>", methods=["POST"])
def admin_update_payment(req_id):
    if not admin_required():
        return redirect(url_for("login"))
    new_payment_status = request.form.get("payment_status")
    valid = {"Pending Payment", "For Verification", "Paid"}
    if new_payment_status not in valid:
        flash("Invalid payment status.", "error")
        return redirect(url_for("admin_requests"))
    try:
        db = _admin_db()
        db.table("adoption_requests").update(
            {"payment_status": new_payment_status}
        ).eq("id", req_id).execute()
        row = fetch_request_row(req_id, admin=True)
        if row:
            try:
                cat_name = _get_cat_name(row.get("cat_id"))
                notify_payment_status(row.get("email", ""), row.get("full_name", "Adopter"),
                                      cat_name, new_payment_status)
            except Exception as notify_err:
                log.warning("notify_payment_status failed: %s", notify_err)
        flash(f"Payment status updated to {new_payment_status}.", "success")
    except Exception as e:
        log.error("admin_update_payment(%s) failed: %s", req_id, e)
        flash(f"Failed: {e}", "error")
    return redirect(url_for("admin_requests"))


@app.route("/admin/update_delivery/<int:req_id>", methods=["POST"])
def admin_update_delivery(req_id):
    if not admin_required():
        return redirect(url_for("login"))
    delivery_status = request.form.get("delivery_status", "").strip()
    delivery_date   = request.form.get("delivery_date", "").strip() or None
    rider_name      = request.form.get("rider_name", "").strip() or None
    rider_contact   = request.form.get("rider_contact", "").strip() or None
    if delivery_status not in {"Preparing", "Out for Delivery", "Delivered"}:
        flash("Invalid delivery status.", "error")
        return redirect(url_for("admin_requests"))
    try:
        update_payload = {"status": "Scheduled", "delivery_status": delivery_status}
        if delivery_date:
            update_payload["delivery_date"] = delivery_date
        if rider_name:
            update_payload["rider_name"] = rider_name
        if rider_contact:
            update_payload["rider_contact"] = rider_contact
        _admin_db().table("adoption_requests").update(update_payload).eq("id", req_id).execute()
        sync_delivery_record(req_id, {
            "status": delivery_status,
            "delivery_status": delivery_status,
            "delivery_date": delivery_date,
            "rider_name": rider_name,
            "rider_contact": rider_contact,
            "rider_phone": rider_contact,
        })
        try:
            row = fetch_request_row(req_id, admin=True)
            if row:
                cat_name = _get_cat_name(row.get("cat_id"))
                notify_delivery_scheduled(
                    row.get("email", ""), row.get("full_name", "Adopter"), cat_name,
                    delivery_date, None, None, rider_name, rider_contact,
                    row.get("delivery_address") or row.get("address", ""),
                    delivery_status,
                )
        except Exception as ne:
            log.warning("admin_update_delivery notify failed: %s", ne)
        flash(f"Delivery status updated to {delivery_status}.", "success")
    except Exception as e:
        log.error("admin_update_delivery(%s) failed: %s", req_id, e)
        flash(f"Failed to update delivery status: {e}", "error")
    return redirect(url_for("admin_requests"))


# ------------------------------------------------------------------ admin schedule pickup --

@app.route("/admin/schedule_pickup/<int:req_id>", methods=["POST"])
def admin_schedule_pickup(req_id):
    if not admin_required():
        return redirect(url_for("login"))

    pickup_date     = request.form.get("pickup_date", "").strip()
    pickup_time     = request.form.get("pickup_time", "").strip()
    pickup_location = request.form.get("pickup_location", "").strip()
    pickup_notes    = request.form.get("pickup_notes", "").strip() or None

    try:
        save_pickup_details(req_id, pickup_date, pickup_time, pickup_location, pickup_notes)
    except ValueError as ve:
        flash(str(ve), "error")
        return redirect(url_for("admin_requests"))
    except Exception as e:
        log.error("admin_schedule_pickup(%s) failed: %s", req_id, e)
        flash(f"Failed to schedule pickup: {e}", "error")
        return redirect(url_for("admin_requests"))

    try:
        row = fetch_request_row(req_id, admin=True)
        if row:
            cat_name = _get_cat_name(row.get("cat_id"))
            send_pickup_email(
                row.get("email", ""), row.get("full_name", "Adopter"), cat_name,
                pickup_date, pickup_time, pickup_location, pickup_notes
            )
    except Exception as ne:
        log.warning("admin_schedule_pickup notify failed: %s", ne)

    flash("Pickup scheduled.", "success")
    return redirect(url_for("admin_requests"))


# ------------------------------------------------------------------ admin schedule delivery --

@app.route("/admin/schedule_delivery/<int:req_id>", methods=["POST"])
def admin_schedule_delivery(req_id):
    if not admin_required():
        return redirect(url_for("login"))

    delivery_date       = request.form.get("delivery_date", "").strip()
    delivery_time_start = request.form.get("delivery_time_start", "").strip()
    delivery_time_end   = request.form.get("delivery_time_end", "").strip()
    delivery_address    = request.form.get("delivery_address", "").strip()
    delivery_status     = request.form.get("delivery_status", "Preparing").strip() or "Preparing"
    rider_name          = request.form.get("rider_name", "").strip()
    rider_contact       = request.form.get("rider_contact", "").strip()

    try:
        save_delivery_details(req_id, delivery_date, delivery_time_start, delivery_time_end,
                              rider_name, rider_contact, delivery_address, delivery_status)
    except ValueError as ve:
        flash(str(ve), "error")
        return redirect(url_for("admin_requests"))
    except Exception as e:
        log.error("admin_schedule_delivery(%s) failed: %s", req_id, e)
        flash(f"Failed to schedule delivery: {e}", "error")
        return redirect(url_for("admin_requests"))

    try:
        row = fetch_request_row(req_id, admin=True)
        if row:
            cat_name = _get_cat_name(row.get("cat_id"))
            notify_delivery_scheduled(
                row.get("email", ""), row.get("full_name", "Adopter"), cat_name,
                delivery_date, delivery_time_start, delivery_time_end,
                rider_name, rider_contact,
                delivery_address or row.get("address", ""),
                delivery_status,
            )
    except Exception as ne:
        log.warning("admin_schedule_delivery notify failed: %s", ne)

    flash("Delivery scheduled.", "success")
    return redirect(url_for("admin_requests"))

# ------------------------------------------------------------------ admin upload delivery photo --

DELIVERY_PHOTO_BUCKET = "delivery-photos"

@app.route("/admin/upload_delivery_photo/<int:req_id>", methods=["POST"])
def admin_upload_delivery_photo(req_id):
    if not admin_required():
        return redirect(url_for("login"))
    import uuid
    file = request.files.get("delivery_photo")
    if not file or not file.filename.strip():
        flash("Please choose a photo.", "error")
        return redirect(url_for("admin_requests"))
    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_AVATAR_EXTENSIONS:
        flash("Only JPG and PNG files are allowed.", "error")
        return redirect(url_for("admin_requests"))
    file_bytes = file.read()
    if not file_bytes:
        flash("Empty file — please choose a valid photo.", "error")
        return redirect(url_for("admin_requests"))
    if len(file_bytes) > MAX_AVATAR_BYTES:
        flash("Photo exceeds 2 MB limit.", "error")
        return redirect(url_for("admin_requests"))
    try:
        client = _storage_client()
        file_name = f"{uuid.uuid4()}_{req_id}.{ext}"
        client.storage.from_(DELIVERY_PHOTO_BUCKET).upload(
            file_name,
            file_bytes,
            {"content-type": file.content_type}
        )
        public_url = client.storage.from_(DELIVERY_PHOTO_BUCKET).get_public_url(file_name)
        _admin_db().table("adoption_requests").update({
            "delivery_photo_url": public_url,
            "delivery_status":    "Delivered",
            "status":             "Completed",
        }).eq("id", req_id).execute()
        sync_delivery_record(req_id, {"status": "Delivered", "delivery_status": "Delivered"})
        log.warning("admin_upload_delivery_photo: req=%s url=%s", req_id, public_url)

        # Send proof-of-delivery email
        try:
            row = fetch_request_row(req_id, admin=True)
            if row:
                cat_name = _get_cat_name(row.get("cat_id"))
                send_delivery_email(
                    to_email=row.get("email", ""),
                    name=row.get("full_name") or "Adopter",
                    cat_name=cat_name,
                    delivery_details={
                        "date":       row.get("delivery_date"),
                        "start_time": row.get("delivery_time_start"),
                        "end_time":   row.get("delivery_time_end"),
                        "rider":      row.get("rider_name"),
                        "contact":    row.get("rider_contact"),
                        "address":    row.get("delivery_address") or row.get("address"),
                        "status":     "Delivered",
                    },
                    photo_url=public_url,
                )
        except Exception as mail_err:
            log.error("admin_upload_delivery_photo: email failed for req=%s: %s", req_id, mail_err)

        flash("Delivery photo uploaded and status set to Delivered.", "success")
    except Exception as e:
        log.error("admin_upload_delivery_photo(%s) failed: %s", req_id, e)
        err = str(e).lower()
        if "bucket" in err or "not found" in err:
            flash("Storage bucket 'delivery-photos' not found. Create it in Supabase Storage.", "error")
        elif "delivery_photo_url" in err or "column" in err:
            flash("Column 'delivery_photo_url' missing. Run the latest supabase_setup.sql migration.", "error")
        else:
            flash(f"Failed to upload photo: {e}", "error")
    return redirect(url_for("admin_requests"))


# ------------------------------------------------------------------ send_message (legacy adoption-based, admin only) --

@app.route("/messages/<int:req_id>", methods=["POST"])
def send_message(req_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    if not admin_required():
        return redirect(url_for("user_messages"))
    message = request.form.get("message", "").strip()
    if not message:
        flash("Message cannot be empty.", "error")
        return redirect(request.referrer or url_for("admin_messages"))
    try:
        _admin_db().table("messages").insert({
            "adoption_id": req_id,
            "sender": "admin",
            "message": message,
        }).execute()
        flash("Message sent.", "success")
    except Exception as e:
        log.error("send_message(%s) failed: %s", req_id, e)
        flash("Failed to send message.", "error")
    return redirect(request.referrer or url_for("admin_messages"))


@app.route("/upload_completion_photo/<int:req_id>", methods=["POST"])
def upload_completion_photo(req_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    row = fetch_request_row(req_id, user_id=session["user_id"])
    if not row or row.get("status") != "Completed":
        flash("You can only upload a photo after completion.", "error")
        return redirect(url_for("history"))
    file = request.files.get("completion_photo")
    if not file or not file.filename:
        flash("Please choose a photo to upload.", "error")
        return redirect(url_for("history"))
    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_AVATAR_EXTENSIONS:
        flash("Only JPG and PNG completion photos are allowed.", "error")
        return redirect(url_for("history"))
    file_bytes = file.read()
    if len(file_bytes) > MAX_AVATAR_BYTES:
        flash("Photo exceeds the 2 MB limit.", "error")
        return redirect(url_for("history"))
    try:
        path = f"completion_{req_id}_{session['user_id']}.{ext}"
        public_url = upload_public_file(COMPLETE_PHOTO_BUCKET, path, file_bytes, file.content_type)
        supabase.table("adoption_requests").update({
            "completion_photo_url": public_url,
        }).eq("id", req_id).eq("user_id", session["user_id"]).execute()
        flash("Completion photo uploaded.", "success")
    except Exception as e:
        log.error("upload_completion_photo(%s) failed: %s", req_id, e)
        flash("Failed to upload completion photo.", "error")
    return redirect(url_for("history"))


# ------------------------------------------------------------------ gcash info API --

@app.route("/api/gcash_info")
def api_gcash_info():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"number": GCASH_NUMBER, "name": GCASH_NAME})


# ------------------------------------------------------------------ user payment status API --

@app.route("/api/my_requests")
def api_my_requests():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401
    try:
        ar_data = _fetch_requests(_admin_db(), filters={"user_id": session["user_id"]})
        return jsonify(build_request_cards(ar_data, admin=True, include_messages=False))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ------------------------------------------------------------------ adopt request --

@app.route("/adopt_request", methods=["POST"])
def adopt_request():
    if "user_id" not in session:
        return redirect(url_for("login"))
    cat_id = request.form.get("cat_id")
    # Profile fields come from hidden inputs (pre-filled from user profile)
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip()
    contact_number = request.form.get("contact_number", "").strip()
    address = request.form.get("address", "").strip()
    # Decision fields — required
    reason = request.form.get("reason", "").strip()
    experience = request.form.get("experience_with_pets", "").strip()
    delivery_method = request.form.get("delivery_method", "").strip()
    # Optional fields
    living_environment = request.form.get("living_environment", "").strip() or None
    has_other_pets_raw = request.form.get("has_other_pets", "").strip()
    has_other_pets = True if has_other_pets_raw == "yes" else (False if has_other_pets_raw == "no" else None)
    if not cat_id or not reason or not experience or not delivery_method:
        flash("Please complete all required fields before submitting.", "error")
        return redirect(url_for("dashboard"))
    if delivery_method not in ("Meet-up", "Delivery", "Pickup"):
        flash("Please select a valid delivery method.", "error")
        return redirect(url_for("dashboard"))
    # Fall back to profile data if hidden fields are empty
    if not full_name or not email:
        profile = get_user_profile(session["user_id"])
        if profile:
            full_name = full_name or profile[3] or ""
            email = email or profile[1] or ""
            contact_number = contact_number or profile[4] or ""
            address = address or profile[5] or ""
    try:
        insert_data = {
            "user_id": session["user_id"],
            "cat_id": int(cat_id),
            "full_name": full_name,
            "email": email,
            "contact_number": contact_number,
            "address": address,
            "reason": reason,
            "experience_with_pets": experience,
            "status": "Pending",
            "payment_status": "Pending Payment",
            "payment_method": "Cash on Delivery" if delivery_method == "Delivery" else "Cash on Arrival",
            "delivery_method": delivery_method,
            "delivery_status": "Preparing" if delivery_method in ("Delivery", "Pickup") else None,
        }
        if living_environment:
            insert_data["living_environment"] = living_environment
        if has_other_pets is not None:
            insert_data["has_other_pets"] = has_other_pets
        try:
            supabase.table("adoption_requests").insert(insert_data).execute()
        except Exception as e1:
            # Retry without optional columns that may not exist in the schema
            log.warning("adopt_request insert with optional fields failed (%s), retrying without them", e1)
            insert_data.pop("living_environment", None)
            insert_data.pop("has_other_pets", None)
            supabase.table("adoption_requests").insert(insert_data).execute()
        # Keep profile in sync
        if full_name or contact_number or address:
            supabase.table("users").update({
                "full_name": full_name,
                "phone": contact_number,
                "address": address,
            }).eq("id", session["user_id"]).execute()
        flash("Adoption request submitted! We will review it shortly.", "success")
    except Exception as e:
        log.error("adopt_request failed for user %s: %s", session.get("user_id"), e)
        flash(f"Failed to submit request: {e}", "error")
    return redirect(url_for("dashboard"))


# ------------------------------------------------------------------ conversations helper --

def _get_or_create_convo(user_id, cat_id):
    """Return existing conversation id or create a new one."""
    existing = supabase.table("conversations") \
        .select("id") \
        .eq("user_id", user_id) \
        .eq("cat_id", int(cat_id)) \
        .execute().data
    if existing:
        return existing[0]["id"]
    result = supabase.table("conversations").insert({
        "user_id": user_id,
        "cat_id":  int(cat_id),
    }).execute()
    return result.data[0]["id"]


# ------------------------------------------------------------------ delete conversation --

@app.route("/delete_thread/<int:convo_id>", methods=["POST"])
def delete_thread(convo_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    try:
        supabase.table("messages").delete().eq("conversation_id", convo_id).execute()
        supabase.table("conversations").delete() \
            .eq("id", convo_id).eq("user_id", session["user_id"]).execute()
        flash("Conversation deleted.", "success")
    except Exception as e:
        log.error("delete_thread(%s) failed: %s", convo_id, e)
        flash("Failed to delete conversation.", "error")
    return redirect(url_for("user_messages"))


# ------------------------------------------------------------------ reply in conversation --

@app.route("/convo/reply/<int:convo_id>", methods=["POST"])
def convo_reply(convo_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    text = request.form.get("message", "").strip()
    if not text:
        flash("Message cannot be empty.", "error")
        return redirect(url_for("user_messages"))
    try:
        supabase.table("messages").insert({
            "conversation_id": convo_id,
            "sender": "user",
            "message": text,
        }).execute()
    except Exception as e:
        log.error("convo_reply(%s) failed: %s", convo_id, e)
        flash("Failed to send message.", "error")
    return redirect(url_for("user_messages"))


# ------------------------------------------------------------------ mark messages read --

@app.route("/api/mark_read/<int:convo_id>", methods=["POST"])
def api_mark_read(convo_id):
    if "user_id" not in session:
        return jsonify({"ok": False}), 401
    try:
        supabase.table("messages").update({"is_read": True}) \
            .eq("conversation_id", convo_id).eq("sender", "admin").execute()
    except Exception:
        pass
    return jsonify({"ok": True})


# ------------------------------------------------------------------ send first message (from cat card modal) --

@app.route("/api/send-first-message", methods=["POST"])
def api_send_first_message():
    if "user_id" not in session:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data    = request.get_json(force=True) or {}
    cat_id  = data.get("cat_id")
    text    = (data.get("message") or "").strip()
    if not cat_id or not text:
        return jsonify({"ok": False, "error": "cat_id and message are required"}), 400
    user_id = session.get("user_id")
    try:
        convo_id = _get_or_create_convo(user_id, cat_id)
        supabase.table("messages").insert({
            "conversation_id": convo_id,
            "sender": "user",
            "message": text,
        }).execute()
        return jsonify({"ok": True, "cat_id": cat_id, "convo_id": convo_id})
    except Exception as e:
        log.error("api_send_first_message failed: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500




@app.route("/user/messages")
def user_messages():
    if "user_id" not in session:
        return redirect(url_for("login"))
    user_id  = session["user_id"]
    cat_id   = request.args.get("cat_id")
    auto_open_id = None
    conversations = []
    try:
        # If cat_id passed, ensure a conversation exists then redirect cleanly
        if cat_id:
            convo_id = _get_or_create_convo(user_id, cat_id)
            auto_open_id = convo_id

        # Fetch all conversations for this user
        convo_rows = supabase.table("conversations") \
            .select("id, cat_id, created_at") \
            .eq("user_id", user_id) \
            .order("created_at", desc=True) \
            .execute().data or []

        if convo_rows:
            convo_ids = [c["id"] for c in convo_rows]
            cat_ids   = list({c["cat_id"] for c in convo_rows if c.get("cat_id")})

            # Fetch cat names
            cats_by_id = {}
            if cat_ids:
                cat_rows = supabase.table("cats").select("id, name") \
                    .in_("id", cat_ids).execute().data or []
                cats_by_id = {r["id"]: r["name"] for r in cat_rows}

            # Fetch messages grouped by conversation
            msg_rows = supabase.table("messages") \
                .select("id, conversation_id, sender, message, created_at, is_read") \
                .in_("conversation_id", convo_ids) \
                .order("created_at").execute().data or []

            msgs_by_convo = {cid: [] for cid in convo_ids}
            for m in msg_rows:
                msgs_by_convo.setdefault(m["conversation_id"], []).append({
                    "id":         m.get("id"),
                    "sender":     m.get("sender") or "user",
                    "message":    m.get("message") or "",
                    "created_at": parse_dt(m.get("created_at")),
                    "is_read":    m.get("is_read", True),
                })

            # Mark admin messages as read
            try:
                supabase.table("messages").update({"is_read": True}) \
                    .eq("sender", "admin").eq("is_read", False) \
                    .in_("conversation_id", convo_ids).execute()
            except Exception:
                pass

            for c in convo_rows:
                msgs = msgs_by_convo.get(c["id"], [])
                if not msgs:
                    continue  # hide empty conversations
                has_unread = any(
                    not m["is_read"] and m["sender"] == "admin" for m in msgs
                )
                conversations.append({
                    "id":        c["id"],
                    "cat_id":    c["cat_id"],
                    "cat_name":  cats_by_id.get(c["cat_id"], "Unknown Cat"),
                    "messages":  msgs,
                    "has_unread": has_unread,
                })

        if cat_id and not auto_open_id:
            match = next((c for c in conversations if str(c["cat_id"]) == str(cat_id)), None)
            if match:
                auto_open_id = match["id"]

    except Exception as e:
        log.error("user_messages failed: %s", e)
    return render_template("user_messages.html", conversations=conversations,
                           user=get_user_profile(user_id),
                           active_page="messages", auto_open_id=auto_open_id)




@app.route("/history")
def history():
    if "user_id" not in session:
        return redirect(url_for("login"))
    try:
        ar_data = _fetch_requests(_admin_db(), filters={"user_id": session["user_id"]})
        requests = build_request_cards(ar_data, admin=True)
    except Exception as e:
        log.error("history failed: %s", e)
        requests = []

    user = get_user_profile(session["user_id"])
    return render_template("history.html", requests=requests, user=user,
                           gcash_number=GCASH_NUMBER, gcash_name=GCASH_NAME,
                           delivery_fee=DELIVERY_FEE,
                           active_page="history")


# ------------------------------------------------------------------ profile --

@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "user_id" not in session:
        return redirect(url_for("login"))
    if request.method == "POST":
        fullname = request.form.get("fullname", "").strip()
        contact  = request.form.get("contact", "").strip()
        address  = request.form.get("address", "").strip()
        update_data = {"full_name": fullname, "phone": contact, "address": address}

        file = request.files.get("valid_id")
        if file and file.filename and allowed_file(file.filename):
            public_url = upload_valid_id(file, session["user_id"])
            if public_url:
                update_data["valid_id_url"] = public_url
            else:
                flash("ID upload failed — profile info was still saved.", "error")

        try:
            supabase.table("users").update(update_data).eq("id", session["user_id"]).execute()
            flash("Profile updated successfully!", "success")
        except Exception as e:
            log.error("profile update failed for %s: %s", session.get("user_id"), e)
            flash("Failed to save profile. Please try again.", "error")
        return redirect(url_for("profile"))

    user = get_user_profile(session["user_id"])
    try:
        ar_res = supabase.table("adoption_requests").select(
            "id, status, created_at, cat_id"
        ).eq("user_id", session["user_id"]).order("created_at", desc=True).limit(5).execute()

        recent = []
        for ar in (ar_res.data or []):
            cat_name = None
            try:
                cat_res = supabase.table("cats").select("name").eq("id", ar["cat_id"]).single().execute()
                if cat_res.data:
                    cat_name = cat_res.data.get("name")
            except Exception:
                pass
            recent.append((ar["id"], cat_name, ar["status"], parse_dt(ar.get("created_at"))))
    except Exception as e:
        log.error("profile — recent requests failed: %s", e)
        recent = []

    return render_template("profile.html", user=user, recent=recent, user_id=session["user_id"], active_page="profile")


# ------------------------------------------------------------------ delete account --

@app.route("/delete_account", methods=["POST"])
def delete_account():
    if "user_id" not in session:
        return redirect(url_for("login"))
    try:
        supabase.table("users").delete().eq("id", session["user_id"]).execute()
    except Exception as e:
        log.error("delete_account failed for %s: %s", session.get("user_id"), e)
        flash("Failed to delete account.", "error")
        return redirect(url_for("profile"))
    session.clear()
    return redirect(url_for("login"))


# ------------------------------------------------------------------ logout --

@app.route("/logout")
def logout():
    try:
        supabase.auth.sign_out()
    except Exception:
        pass
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(debug=True)
