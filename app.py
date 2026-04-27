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


# ------------------------------------------------------------------ helpers --

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


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
        cat = cats_by_id.get(row.get("cat_id"), {})
        user = users_by_id.get(row.get("user_id"), {})
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
            delivery.get("pickup_location")
            or delivery.get("location")
            or row.get("delivery_address")
            or row.get("address")
            or user.get("address")
        )
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
            "pickup_contact_person": pickup_contact_person,
            "pickup_contact_number": pickup_contact_number,
            "delivery_photo_url": row.get("delivery_photo_url"),
            "messages": messages_by_request.get(row.get("id"), []),
        }
        cards.append(card)
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
            print("LOGIN ERROR:", err)
            if "Invalid login credentials" in err:
                return render_template("login.html", error="Invalid email or password")
            if "Email not confirmed" in err:
                return render_template("login.html", error="Please verify your email first")
            return render_template("login.html", error=err)
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
    try:
        db = _admin_db()
        ar = db.table("adoption_requests").select("cat_id").eq("id", req_id).single().execute().data
        db.table("adoption_requests").update({"status": new_status}).eq("id", req_id).execute()
        if ar and ar.get("cat_id"):
            if new_status == "Approved":
                db.table("cats").update({"status": "adopted"}).eq("id", ar["cat_id"]).execute()
            elif new_status == "Rejected":
                db.table("cats").update({"status": "available"}).eq("id", ar["cat_id"]).execute()
            elif new_status == "Completed":
                db.table("cats").update({"status": "adopted"}).eq("id", ar["cat_id"]).execute()
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
    try:
        ar_data = _fetch_requests(_admin_db())
        conversations = build_request_cards(ar_data, admin=True)
        # Mark all user messages as read
        try:
            _admin_db().table("messages").update({"read": True}).eq("sender", "user").eq("read", False).execute()
        except Exception:
            pass
        # Flag which conversations have unread messages
        for conv in conversations:
            conv["has_unread"] = any(
                not m.get("read", True) and m.get("sender") == "user"
                for m in (conv.get("messages") or [])
            )
    except Exception as e:
        log.error("admin_messages failed: %s", e)
        conversations = []
    return render_template("admin_messages.html", conversations=conversations, active_page="messages")




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
        "adoption_fee":       float(request.form.get("adoption_fee")) if request.form.get("adoption_fee", "").strip() else None,
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
        "adoption_fee":       float(request.form.get("adoption_fee")) if request.form.get("adoption_fee", "").strip() else None,
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
        ar_data = _fetch_requests(supabase, filters={"user_id": session["user_id"]})
        # Exclude soft-deleted threads from badge count
        request_ids = [r["id"] for r in ar_data if r.get("id") and not r.get("user_deleted_chat")]
        if not request_ids:
            return jsonify({"unread": 0})
        rows = supabase.table("messages").select("id").eq("sender", "admin").eq("read", False).in_("adoption_id", request_ids).execute().data or []
        return jsonify({"unread": len(rows)})
    except Exception:
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
        unread = len(_admin_db().table("messages").select("id").eq("sender", "user").eq("read", False).execute().data or [])
    except Exception:
        unread = 0
    return jsonify({"pending_requests": pending, "unread_messages": unread})




@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))
    search = request.args.get("search", "")
    breed  = request.args.get("breed", "")
    try:
        q = supabase.table("cats").select("*")
        if search:
            q = q.ilike("name", f"%{search}%")
        if breed and breed != "All Breeds":
            q = q.eq("breed", breed)
        cats = [c for c in (q.execute().data or [])]
        pending_count = len(
            supabase.table("adoption_requests").select("id")
            .eq("user_id", session["user_id"]).eq("status", "Pending")
            .execute().data or []
        )
    except Exception as e:
        log.error("dashboard failed: %s", e)
        cats = []
        pending_count = 0

    user = get_user_profile(session["user_id"])
    # Fetch ALL breeds independently so filter dropdown is always complete
    try:
        all_cats_for_breeds = supabase.table("cats").select("breed").execute().data or []
        breeds = sorted({c.get("breed") for c in all_cats_for_breeds if c.get("breed")})
    except Exception:
        breeds = sorted({c.get("breed") for c in cats if c.get("breed")})
    return render_template("dashboard.html", cats=cats, user=user, pending_count=pending_count, active_page="dashboard", breeds=breeds, delivery_fee=DELIVERY_FEE)


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
        log.warning("upload_avatar: saved %s/%s for user %s", AVATAR_BUCKET, storage_path, session["user_id"])
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
        log.warning("api_cat_detail(%s) returned keys: %s", cat_id, list(data.keys()))
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
        log.warning("update_payment_method: req=%s method=%s user=%s",
                    request_id, payment_method, session.get("user_id"))
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
        _admin_db().table("adoption_requests").update(
            {"payment_status": new_payment_status}
        ).eq("id", req_id).execute()
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
        flash(f"Delivery status updated to {delivery_status}.", "success")
    except Exception as e:
        log.error("admin_update_delivery(%s) failed: %s", req_id, e)
        flash(f"Failed to update delivery status: {e}", "error")
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

    log.warning("admin_schedule_delivery req=%s date=%r rider=%r contact=%r status=%r",
                req_id, delivery_date, rider_name, rider_contact, delivery_status)

    if not delivery_date:
        flash("Delivery date is required.", "error")
        return redirect(url_for("admin_requests"))
    if delivery_status not in {"Preparing", "Out for Delivery", "Delivered"}:
        flash("Invalid delivery status.", "error")
        return redirect(url_for("admin_requests"))

    # Only include non-empty values so missing optional columns don't break the update
    update_data = {
        "status":          "Scheduled",
        "delivery_status": delivery_status,
        "delivery_date":   delivery_date,
    }
    if delivery_time_start:
        update_data["delivery_time_start"] = delivery_time_start
    if delivery_time_end:
        update_data["delivery_time_end"] = delivery_time_end
    if delivery_address:
        update_data["delivery_address"] = delivery_address
    if rider_name:
        update_data["rider_name"] = rider_name
    if rider_contact:
        update_data["rider_contact"] = rider_contact

    log.warning("admin_schedule_delivery payload=%s", update_data)

    db = _admin_db()
    try:
        # NOTE: do NOT chain .select() after .update() — the Supabase Python
        # client does not support it and will silently fail the entire call.
        db.table("adoption_requests").update(update_data).eq("id", req_id).execute()
    except Exception as e:
        log.error("admin_schedule_delivery(%s) failed: %s", req_id, e)
        flash(f"Failed to schedule delivery: {e}", "error")
        return redirect(url_for("admin_requests"))

    # Best-effort mirror to deliveries table (non-fatal)
    try:
        sync_delivery_record(req_id, {
            "delivery_status": delivery_status,
            "delivery_date":   delivery_date,
            "rider_name":      rider_name or None,
            "rider_contact":   rider_contact or None,
            "rider_phone":     rider_contact or None,
        })
    except Exception as e:
        log.warning("admin_schedule_delivery sync_delivery_record failed (non-fatal): %s", e)

    flash("Delivery scheduled.", "success")
    return redirect(url_for("admin_requests"))

# ------------------------------------------------------------------ admin upload delivery photo --

@app.route("/admin/upload_delivery_photo/<int:req_id>", methods=["POST"])
def admin_upload_delivery_photo(req_id):
    if not admin_required():
        return redirect(url_for("login"))
    file = request.files.get("delivery_photo")
    if not file or not file.filename:
        flash("Please choose a photo.", "error")
        return redirect(url_for("admin_requests"))
    ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_AVATAR_EXTENSIONS:
        flash("Only JPG and PNG files are allowed.", "error")
        return redirect(url_for("admin_requests"))
    file_bytes = file.read()
    if len(file_bytes) > MAX_AVATAR_BYTES:
        flash("Photo exceeds 2 MB limit.", "error")
        return redirect(url_for("admin_requests"))
    try:
        path = f"delivery_{req_id}.{ext}"
        public_url = upload_public_file(COMPLETE_PHOTO_BUCKET, path, file_bytes, file.content_type)
        _admin_db().table("adoption_requests").update({
            "delivery_photo_url": public_url,
            "delivery_status": "Delivered",
            "status": "Completed",
        }).eq("id", req_id).execute()
        sync_delivery_record(req_id, {
            "status": "Delivered",
            "delivery_status": "Delivered",
        })
        flash("Delivery photo uploaded and status set to Delivered.", "success")
    except Exception as e:
        log.error("admin_upload_delivery_photo(%s) failed: %s", req_id, e)
        flash(f"Failed to upload photo: {e}", "error")
    return redirect(url_for("admin_requests"))


@app.route("/messages/<int:req_id>", methods=["POST"])
def send_message(req_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    message = request.form.get("message", "").strip()
    if not message:
        flash("Message cannot be empty.", "error")
        return redirect(request.referrer or url_for("history"))

    is_admin = admin_required()
    row = fetch_request_row(req_id, admin=is_admin, user_id=None if is_admin else session["user_id"])
    if not row:
        flash("Adoption request not found.", "error")
        return redirect(request.referrer or url_for("history"))
    try:
        (_admin_db() if is_admin else supabase).table("messages").insert({
            "adoption_id": req_id,
            "sender": "admin" if is_admin else "user",
            "message": message,
        }).execute()
        flash("Message sent.", "success")
    except Exception as e:
        log.error("send_message(%s) failed: %s", req_id, e)
        flash("Failed to send message.", "error")
    return redirect(request.referrer or (url_for("admin_requests") if is_admin else url_for("history")))


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


# ------------------------------------------------------------------ delete thread --

@app.route("/delete_thread/<int:req_id>", methods=["POST"])
def delete_thread(req_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    try:
        supabase.table("messages").delete().eq("adoption_id", req_id).execute()
        # Soft-delete: hide thread from user's view without removing the request
        try:
            supabase.table("adoption_requests").update({"user_deleted_chat": True}) \
                .eq("id", req_id).eq("user_id", session["user_id"]).execute()
        except Exception:
            pass  # column may not exist yet; messages are still cleared
        flash("Conversation deleted.", "success")
    except Exception as e:
        log.error("delete_thread(%s) failed: %s", req_id, e)
        flash("Failed to delete conversation.", "error")
    return redirect(url_for("user_messages"))


# ------------------------------------------------------------------ mark messages read --

@app.route("/api/mark_read/<int:req_id>", methods=["POST"])
def api_mark_read(req_id):
    if "user_id" not in session:
        return jsonify({"ok": False}), 401
    try:
        supabase.table("messages").update({"read": True}).eq("adoption_id", req_id).eq("sender", "admin").execute()
    except Exception:
        pass
    return jsonify({"ok": True})




@app.route("/user/messages")
def user_messages():
    if "user_id" not in session:
        return redirect(url_for("login"))
    user_id = session["user_id"]
    cat_id = request.args.get("cat_id")
    auto_open_id = None
    try:
        ar_data = _fetch_requests(supabase, filters={"user_id": user_id})

        # If cat_id provided, restore a previously deleted thread so user can message again
        if cat_id:
            for row in ar_data:
                if str(row.get("cat_id")) == str(cat_id) and row.get("user_deleted_chat"):
                    try:
                        supabase.table("adoption_requests").update({"user_deleted_chat": False}) \
                            .eq("id", row["id"]).eq("user_id", user_id).execute()
                        row["user_deleted_chat"] = False
                    except Exception:
                        pass

        # Filter out soft-deleted threads
        ar_data = [r for r in ar_data if not r.get("user_deleted_chat")]

        conversations = build_request_cards(ar_data, admin=False)

        # Compute unread BEFORE marking as read
        request_ids = [c["id"] for c in conversations if c.get("id")]
        unread_ids = set()
        if request_ids:
            try:
                unread_rows = supabase.table("messages").select("adoption_id") \
                    .eq("sender", "admin").eq("read", False) \
                    .in_("adoption_id", request_ids).execute().data or []
                unread_ids = {r["adoption_id"] for r in unread_rows}
            except Exception:
                pass

        for conv in conversations:
            conv["has_unread"] = conv["id"] in unread_ids

        # Only show threads that have messages
        conversations = [c for c in conversations if c.get("messages")]

        # Mark all as read now that we've computed badges
        if request_ids:
            try:
                supabase.table("messages").update({"read": True}) \
                    .eq("sender", "admin").in_("adoption_id", request_ids).execute()
            except Exception:
                pass

        if cat_id:
            match = next((c for c in conversations if str(c.get("cat_id")) == str(cat_id)), None)
            if match:
                auto_open_id = match["id"]
    except Exception as e:
        log.error("user_messages failed: %s", e)
        conversations = []
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
