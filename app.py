import os
import logging
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from supabase_client import supabase as _supabase_client, supabase_admin as _supabase_admin

# Vercel captures stdout/stderr — logging.WARNING and above appear in
# the function logs tab of your Vercel dashboard.
# Set to DEBUG locally via LOG_LEVEL=DEBUG in your .env file.
_log_level = os.environ.get("LOG_LEVEL", "WARNING").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.WARNING),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

# If env vars were missing at boot, supabase_client sets the value to None.
# We re-export it here so all existing code continues to work unchanged.
supabase       = _supabase_client
supabase_admin = _supabase_admin  # service-role client — bypasses RLS for storage

# Resolve the project root so Flask finds /templates and /static
# whether the app is started from the project root (locally)
# or from api/ (Vercel imports api/index.py which adds root to sys.path,
# but __file__ here is app.py which lives at the root, so this is safe).
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

# Inject CSS_VERSION into every template for cache-busting.
# Bump this string whenever you update style.css.
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

# Admin credentials from environment (fallback for local dev only)
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


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


def get_user_profile(user_id):
    """Return user as tuple (id, email, password, full_name, phone, address, valid_id_url, avatar_url)."""
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
    """Upload file to Supabase Storage and return its public URL, or None on failure."""
    try:
        filename = secure_filename(f"uid_{user_id}_{file.filename}")
        file_bytes = file.read()
        supabase.storage.from_(STORAGE_BUCKET).upload(
            filename, file_bytes,
            {"content-type": file.content_type, "upsert": "true"}
        )
        return supabase.storage.from_(STORAGE_BUCKET).get_public_url(filename)
    except Exception as e:
        log.error("upload_valid_id(%s) failed: %s", user_id, e)
        return None


def db_query(table, filters=None, columns="*", order=None, limit=None, single=False):
    """Generic safe SELECT helper. Returns list (or dict if single=True), or [] / None on error."""
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


# ------------------------------------------------------------------ browse --

@app.route("/browse")
def browse():
    try:
        res = supabase.table("cats").select("*").execute()
        cats = [
            (c["id"], c["name"], c["breed"], c["age"], c["gender"],
             c.get("image", "cat1.jpg"), c["status"])
            for c in (res.data or [])
        ]
    except Exception as e:
        log.error("browse — cats fetch failed: %s", e)
        cats = []
    return render_template("browse.html", cats=cats, logged_in="user_id" in session)


# ------------------------------------------------------------------ root → browse --

def login_redirect_by_role():
    """Redirect an authenticated user to the correct destination for their role."""
    if session.get("role") == "admin":
        return redirect(url_for("admin_dashboard"))
    return redirect(url_for("dashboard"))


def require_login_redirect():
    """Return a redirect response when the user is not authenticated."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    return None


def password_matches(stored_password, plain_password):
    """Support both Werkzeug-hashed and legacy plain-text passwords."""
    if not stored_password or plain_password is None:
        return False
    try:
        if check_password_hash(stored_password, plain_password):
            return True
    except (ValueError, TypeError):
        pass
    return stored_password == plain_password


@app.route("/")
def home():
    return render_template("landing.html")


# ------------------------------------------------------------------ login --

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return login_redirect_by_role()
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        if not email or not password:
            return render_template("login.html", error="Please fill all fields")
        try:
            if supabase is None:
                raise RuntimeError("Supabase client is not configured")
            res = supabase.table("users").select("*").eq("email", email).execute()
            user = res.data[0] if res.data else None
        except Exception as e:
            log.error("login — users fetch failed: %s", e)
            return render_template("login.html", error="Something went wrong. Please try again.")

        try:
            if user and password_matches(user.get("password"), password):
                session.clear()
                session["user_id"] = user.get("id")
                session["email"] = user.get("email", email)
                session["role"] = (user.get("role") or "user").strip().lower()
                if not session["user_id"]:
                    raise RuntimeError("User record is missing an id")
                return login_redirect_by_role()
        except Exception as e:
            log.error("login session setup failed: %s", e)
            return render_template("login.html", error="Login failed. Please try again.")
        return render_template("login.html", error="Invalid email or password")
    return render_template("login.html")


# ------------------------------------------------------------------ admin helpers --

def is_admin():
    """Return True if the current session user has role='admin'."""
    return session.get("role") == "admin"


def require_admin_redirect():
    """Return a redirect response when the user is not an authenticated admin."""
    guard = require_login_redirect()
    if guard:
        return guard
    if not is_admin():
        return redirect(url_for("dashboard"))
    return None


# Columns that actually exist in the cats table.
_CAT_FIELDS = {"name", "breed", "age", "gender", "status", "image",
               "origin", "weight", "size", "lifespan",
               "coat_colors", "temperament", "about"}

def parse_cat_form(form_data):
    """Normalize cat payload from form data or JSON.
    Only includes keys that exist in the cats table.
    Maps the UI 'description' field → 'about' (the actual DB column).
    """
    age_raw = str(form_data.get("age") or "").strip()
    payload = {
        "name":   (form_data.get("name")   or "").strip(),
        "breed":  (form_data.get("breed")  or "").strip(),
        "age":    int(age_raw) if age_raw.isdigit() else None,
        "gender": (form_data.get("gender") or "").strip(),
        "status": (form_data.get("status") or "available").strip().lower(),
        "image":  (form_data.get("image")  or "cat1.jpg").strip(),
        "about":  (form_data.get("description") or form_data.get("about") or "").strip() or None,
    }
    # Include any extra optional columns if provided
    for col in ("origin", "weight", "size", "lifespan", "coat_colors", "temperament"):
        val = (form_data.get(col) or "").strip()
        if val:
            payload[col] = val
    return payload


def validate_cat_payload(data, partial=False):
    required = ["name", "breed", "age", "gender", "status"]
    if not partial:
        for field in required:
            if data.get(field) in (None, ""):
                return f"{field.replace('_', ' ').title()} is required."
    if data.get("age") is not None:
        try:
            if int(data["age"]) < 0:
                return "Age must be zero or greater."
        except (ValueError, TypeError):
            return "Age must be a valid number."
    valid_statuses = {"available", "pending", "adopted"}
    if data.get("status") and data["status"] not in valid_statuses:
        return f"Status must be one of: {', '.join(sorted(valid_statuses))}."
    return None


def sync_cat_status_for_request(req_id, new_status):
    """Keep the related cat in sync when an adoption request status changes."""
    ar = supabase.table("adoption_requests").select("cat_id").eq("id", req_id).single().execute().data
    if not ar or not ar.get("cat_id"):
        return

    if new_status == "Approved":
        supabase.table("cats").update({"status": "adopted"}).eq("id", ar["cat_id"]).execute()
    elif new_status == "Rejected":
        supabase.table("cats").update({"status": "available"}).eq("id", ar["cat_id"]).execute()


def build_admin_context(active_section="dashboard"):
    """Fetch admin dashboard data and prepare view-friendly context."""
    try:
        cats_data = supabase.table("cats").select("*").order("id").execute().data or []
    except Exception as e:
        log.error("build_admin_context cats failed: %s", e)
        cats_data = []

    try:
        users_data = (
            supabase.table("users")
            .select("id,email,full_name,role,phone,address,valid_id_url,created_at")
            .order("created_at", desc=True)
            .execute()
            .data
            or []
        )
    except Exception as e:
        log.warning("build_admin_context users with created_at failed: %s", e)
        try:
            users_data = (
                supabase.table("users")
                .select("id,email,full_name,role,phone,address,valid_id_url")
                .execute()
                .data
                or []
            )
        except Exception as inner:
            log.error("build_admin_context users fallback failed: %s", inner)
            users_data = []

    try:
        adoption_requests = (
            supabase.table("adoption_requests")
            .select(
                "id,status,created_at,living_situation,has_other_pets,experience_level,reason,"
                "user_id,cat_id,"
                "cats(id,name,breed,image,status),"
                "users(id,full_name,email,phone,address,valid_id_url)"
            )
            .order("created_at", desc=True)
            .execute()
            .data
            or []
        )
    except Exception as e:
        log.error("build_admin_context adoption_requests failed: %s", e)
        # Fallback: fetch without joins so the page still loads
        try:
            adoption_requests = (
                supabase.table("adoption_requests")
                .select("id,status,created_at,user_id,cat_id")
                .order("created_at", desc=True)
                .execute()
                .data
                or []
            )
        except Exception as inner:
            log.error("build_admin_context adoption_requests fallback failed: %s", inner)
            adoption_requests = []

    # Normalise every request row so the template can safely access
    # req.cats.name / req.users.email without AttributeError when the
    # join returns None (e.g. RLS blocks the related row).
    for req in adoption_requests:
        if not isinstance(req.get("cats"), dict):
            req["cats"] = {}
        if not isinstance(req.get("users"), dict):
            req["users"] = {}

    for user_row in users_data:
        user_row["created_at_parsed"] = parse_dt(user_row.get("created_at"))

    for req in adoption_requests:
        req["created_at_parsed"] = parse_dt(req.get("created_at"))

    cat_search = (request.args.get("search") or "").strip().lower()
    cat_status = (request.args.get("status") or "").strip().lower()
    cat_breed = (request.args.get("breed") or "").strip().lower()

    filtered_cats = []
    for cat in cats_data:
        name = (cat.get("name") or "").lower()
        breed = (cat.get("breed") or "").lower()
        status = (cat.get("status") or "").lower()
        matches_search = not cat_search or cat_search in name or cat_search in breed
        matches_status = not cat_status or cat_status == "all" or status == cat_status
        matches_breed = not cat_breed or cat_breed == "all" or breed == cat_breed
        if matches_search and matches_status and matches_breed:
            filtered_cats.append(cat)

    breed_options = sorted({(cat.get("breed") or "").strip() for cat in cats_data if (cat.get("breed") or "").strip()})

    available = sum(1 for c in cats_data if (c.get("status") or "").lower() == "available")
    adopted = sum(1 for c in cats_data if (c.get("status") or "").lower() == "adopted")
    pending = sum(1 for r in adoption_requests if (r.get("status") or "").lower() == "pending")
    approved = sum(1 for r in adoption_requests if (r.get("status") or "").lower() == "approved")

    return {
        "active_page": "admin",
        "active_section": active_section,
        "cats": filtered_cats,
        "all_cats": cats_data,
        "breed_options": breed_options,
        "users": users_data,
        "adoption_requests": adoption_requests,
        "stats": {
            "total_cats": len(cats_data),
            "available_cats": available,
            "adopted_cats": adopted,
            "total_users": len(users_data),
            "pending_requests": pending,
            "approved_requests": approved,
        },
        "filters": {
            "search": request.args.get("search", ""),
            "status": request.args.get("status", "all"),
            "breed": request.args.get("breed", "all"),
        },
        "user": get_user_profile(session["user_id"]),
    }


def render_admin_page(active_section="dashboard"):
    guard = require_admin_redirect()
    if guard:
        return guard
    return render_template("admin_dashboard.html", **build_admin_context(active_section))


# ------------------------------------------------------------------ admin login (legacy kept) --

@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    return redirect(url_for("login"))


# ------------------------------------------------------------------ /admin (new role-based) --

@app.route("/admin")
def admin_dashboard():
    return render_admin_page("dashboard")


@app.route("/admin/cats")
def admin_cats():
    return render_admin_page("cats")


@app.route("/admin/users")
def admin_users():
    return render_admin_page("users")


@app.route("/admin/requests")
def admin_requests():
    return render_admin_page("requests")


# ------------------------------------------------------------------ admin API: cats --

@app.route("/admin/api/cats", methods=["POST"])
def admin_cat_create():
    if not is_admin(): return jsonify({"error": "forbidden"}), 403
    data = parse_cat_form(request.get_json() or {})
    error = validate_cat_payload(data)
    if error:
        return jsonify({"error": error}), 400
    try:
        res = supabase.table("cats").insert(data).execute()
        return jsonify({"ok": True, "data": res.data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/api/cats/<int:cat_id>", methods=["PUT"])
def admin_cat_update(cat_id):
    if not is_admin(): return jsonify({"error": "forbidden"}), 403
    data = parse_cat_form(request.get_json() or {})
    data.pop("id", None)
    error = validate_cat_payload(data)
    if error:
        return jsonify({"error": error}), 400
    try:
        res = supabase.table("cats").update(data).eq("id", cat_id).execute()
        return jsonify({"ok": True, "data": res.data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/api/cats/<int:cat_id>", methods=["DELETE"])
def admin_cat_delete(cat_id):
    if not is_admin(): return jsonify({"error": "forbidden"}), 403
    try:
        supabase.table("cats").delete().eq("id", cat_id).execute()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ------------------------------------------------------------------ admin API: users --

@app.route("/admin/api/users/<user_id>", methods=["PUT"])
def admin_user_update(user_id):
    if not is_admin(): return jsonify({"error": "forbidden"}), 403
    data = request.get_json() or {}
    allowed = {k: data[k] for k in ["role"] if k in data}
    if allowed.get("role") not in {"user", "admin"}:
        return jsonify({"error": "Invalid role"}), 400
    try:
        res = supabase.table("users").update(allowed).eq("id", user_id).execute()
        return jsonify({"ok": True, "data": res.data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/api/users/<user_id>", methods=["DELETE"])
def admin_user_delete(user_id):
    if not is_admin(): return jsonify({"error": "forbidden"}), 403
    if user_id == session.get("user_id"):
        return jsonify({"error": "You cannot delete the currently signed-in admin."}), 400
    try:
        supabase.table("users").delete().eq("id", user_id).execute()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ------------------------------------------------------------------ admin API: adoption requests --

@app.route("/admin/api/requests/<int:req_id>", methods=["PUT"])
def admin_request_update(req_id):
    if not is_admin(): return jsonify({"error": "forbidden"}), 403
    data = request.get_json() or {}
    new_status = data.get("status")
    if new_status not in ("Pending", "Approved", "Rejected"):
        return jsonify({"error": "Invalid status"}), 400
    try:
        supabase.table("adoption_requests").update({"status": new_status}).eq("id", req_id).execute()
        sync_cat_status_for_request(req_id, new_status)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/api/requests/<int:req_id>", methods=["DELETE"])
def admin_request_delete(req_id):
    if not is_admin(): return jsonify({"error": "forbidden"}), 403
    try:
        existing = supabase.table("adoption_requests").select("status,cat_id").eq("id", req_id).single().execute().data
        supabase.table("adoption_requests").delete().eq("id", req_id).execute()
        if existing and existing.get("status") == "Approved" and existing.get("cat_id"):
            supabase.table("cats").update({"status": "available"}).eq("id", existing["cat_id"]).execute()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ------------------------------------------------------------------ admin update status (legacy) --

@app.route("/admin/update_status/<int:req_id>", methods=["POST"])
def update_status(req_id):
    guard = require_admin_redirect()
    if guard:
        return guard
    new_status = request.form.get("status")
    if new_status not in ("Pending", "Approved", "Rejected"):
        flash("Invalid request status.", "error")
        return redirect(url_for("admin_requests"))
    try:
        supabase.table("adoption_requests").update({"status": new_status}).eq("id", req_id).execute()
        sync_cat_status_for_request(req_id, new_status)
        flash(f"Request #{req_id} updated to {new_status}", "success")
    except Exception as e:
        log.error("update_status(%s) failed: %s", req_id, e)
        flash("Failed to update status. Please try again.", "error")
    return redirect(url_for("admin_requests"))


@app.route("/delete_request/<int:req_id>", methods=["POST"])
def delete_request(req_id):
    guard = require_admin_redirect()
    if guard:
        return guard
    try:
        existing = supabase.table("adoption_requests").select("status,cat_id").eq("id", req_id).single().execute().data
        supabase.table("adoption_requests").delete().eq("id", req_id).execute()
        if existing and existing.get("status") == "Approved" and existing.get("cat_id"):
            supabase.table("cats").update({"status": "available"}).eq("id", existing["cat_id"]).execute()
        flash(f"Request #{req_id} deleted.", "success")
    except Exception as e:
        log.error("delete_request(%s) failed: %s", req_id, e)
        flash("Failed to delete request. Please try again.", "error")
    return redirect(url_for("admin_requests"))


@app.route("/add_cat", methods=["POST"])
def add_cat():
    guard = require_admin_redirect()
    if guard:
        return guard
    data = parse_cat_form(request.form)
    error = validate_cat_payload(data)
    if error:
        flash(error, "error")
        return redirect(url_for("admin_cats"))
    try:
        supabase.table("cats").insert(data).execute()
        flash(f"{data['name']} was added successfully.", "success")
    except Exception as e:
        log.error("add_cat failed: %s", e)
        flash("Failed to add cat. Please try again.", "error")
    return redirect(url_for("admin_cats"))


@app.route("/edit_cat/<int:cat_id>", methods=["POST"])
def edit_cat(cat_id):
    guard = require_admin_redirect()
    if guard:
        return guard
    data = parse_cat_form(request.form)
    error = validate_cat_payload(data)
    if error:
        flash(error, "error")
        return redirect(url_for("admin_cats"))
    try:
        supabase.table("cats").update(data).eq("id", cat_id).execute()
        flash(f"{data['name']} was updated successfully.", "success")
    except Exception as e:
        log.error("edit_cat(%s) failed: %s", cat_id, e)
        flash("Failed to update cat. Please try again.", "error")
    return redirect(url_for("admin_cats"))


@app.route("/delete_cat/<int:cat_id>", methods=["POST"])
def delete_cat(cat_id):
    guard = require_admin_redirect()
    if guard:
        return guard
    try:
        supabase.table("cats").delete().eq("id", cat_id).execute()
        flash(f"Cat #{cat_id} deleted successfully.", "success")
    except Exception as e:
        log.error("delete_cat(%s) failed: %s", cat_id, e)
        flash("Failed to delete cat. Please try again.", "error")
    return redirect(url_for("admin_cats"))


@app.route("/update_role/<user_id>", methods=["POST"])
def update_role(user_id):
    guard = require_admin_redirect()
    if guard:
        return guard
    role = (request.form.get("role") or "").strip().lower()
    if role not in {"user", "admin"}:
        flash("Invalid role selected.", "error")
        return redirect(url_for("admin_users"))
    try:
        supabase.table("users").update({"role": role}).eq("id", user_id).execute()
        flash("User role updated successfully.", "success")
    except Exception as e:
        log.error("update_role(%s) failed: %s", user_id, e)
        flash("Failed to update role. Please try again.", "error")
    return redirect(url_for("admin_users"))


@app.route("/delete_user/<user_id>", methods=["POST"])
def delete_user(user_id):
    guard = require_admin_redirect()
    if guard:
        return guard
    if user_id == session.get("user_id"):
        flash("You cannot delete the currently signed-in admin.", "error")
        return redirect(url_for("admin_users"))
    try:
        supabase.table("users").delete().eq("id", user_id).execute()
        flash("User deleted successfully.", "success")
    except Exception as e:
        log.error("delete_user(%s) failed: %s", user_id, e)
        flash("Failed to delete user. Please try again.", "error")
    return redirect(url_for("admin_users"))


# ------------------------------------------------------------------ register --

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        fullname = request.form.get("fullname", "").strip()

        log.warning("register — received: email=%s fullname=%s password_len=%d",
                    email, fullname, len(password))

        if not email or not password or not fullname:
            return render_template("register.html", error="Please fill all fields")

        if len(password) < 6:
            return render_template("register.html", error="Password must be at least 6 characters")

        if supabase is None:
            log.error("register — supabase client is None")
            return render_template("register.html", error="Service unavailable.")

        # Duplicate check
        try:
            existing = supabase.table("users").select("id").eq("email", email).execute()
            log.warning("register — duplicate check: %s", existing.data)

            if existing.data:
                return render_template("register.html", error="Email already registered")

        except Exception as e:
            log.error("DUPLICATE CHECK ERROR: %s", str(e))
            log.error("FULL ERROR: %r", e)
            return render_template("register.html", error="Registration failed.")

        # INSERT
        try:
            res = supabase.table("users").insert({
                "email": email,
                "password": generate_password_hash(password),
                "full_name": fullname,
                "role": "user",
            }).execute()

            #  PRINT EVERYTHING
            log.warning("INSERT RAW RESPONSE: %s", res)
            log.warning("INSERT DATA: %s", res.data)

            #  CRITICAL CHECK
            if not res.data:
                log.error("INSERT FAILED — EMPTY DATA")
                if hasattr(res, "error"):
                    log.error("SUPABASE ERROR: %s", res.error)
                return render_template("register.html", error="Registration failed. Check logs.")

        except Exception as e:
            log.error("INSERT EXCEPTION: %s", str(e))
            log.error("FULL INSERT ERROR: %r", e)

            return render_template("register.html", error="Registration failed. Check logs.")

        flash("Account created! Please login.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")

# ------------------------------------------------------------------ dashboard --

@app.route("/dashboard")
def dashboard():
    guard = require_login_redirect()
    if guard:
        return guard
    search = request.args.get("search", "")
    breed  = request.args.get("breed", "")
    try:
        q = supabase.table("cats").select("*").eq("status", "available")
        if search:
            q = q.ilike("breed", f"%{search}%")
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
    return render_template("dashboard.html", cats=cats, user=user, pending_count=pending_count, active_page="dashboard")


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
        # Use the service-role client for storage so RLS is bypassed.
        # The anon key cannot write to storage unless explicit policies exist.
        storage_client = supabase_admin or supabase
        storage_path   = f"{session['user_id']}.{ext}"  # e.g. abc-123.jpg

        storage_client.storage.from_(AVATAR_BUCKET).upload(
            storage_path, file_bytes,
            {"content-type": file.content_type, "upsert": "true"}
        )
        public_url = storage_client.storage.from_(AVATAR_BUCKET).get_public_url(storage_path)

        # Save URL to users table (anon client is fine here — RLS allows own-row update)
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


# ------------------------------------------------------------------ adopt request --

@app.route("/adopt_request", methods=["POST"])
def adopt_request():
    if "user_id" not in session:
        return redirect(url_for("login"))
    user = get_user_profile(session["user_id"])
    if not user or not user[3] or not user[4] or not user[5]:
        flash("Please complete your profile (name, contact, address) before adopting.", "error")
        return redirect(url_for("profile"))
    cat_id           = request.form.get("cat_id")
    living_situation = request.form.get("living_situation", "").strip()
    has_other_pets   = request.form.get("has_other_pets", "").strip()
    experience       = request.form.get("experience", "").strip()
    reason           = request.form.get("reason", "").strip()
    if not living_situation or not has_other_pets or not reason:
        flash("Please answer all required questions.", "error")
        return redirect(url_for("dashboard"))
    try:
        supabase.table("adoption_requests").insert({
            "user_id":          session["user_id"],
            "cat_id":           int(cat_id),
            "living_situation": living_situation,
            "has_other_pets":   has_other_pets,
            "experience_level": experience,
            "reason":           reason,
            "status":           "Pending",
        }).execute()
        flash("Adoption request submitted! We will review it shortly.", "success")
    except Exception as e:
        log.error("adopt_request failed for user %s: %s", session.get("user_id"), e)
        flash("Failed to submit request. Please try again.", "error")
    return redirect(url_for("dashboard"))


# ------------------------------------------------------------------ history --
@app.route("/history")
def history():
    if "user_id" not in session:
        return redirect(url_for("login"))
    try:
        ar_res = supabase.table("adoption_requests").select(
            "status, created_at, cat_id"
        ).eq("user_id", session["user_id"]).order("created_at", desc=True).execute()

        requests = []
        for ar in (ar_res.data or []):
            # fetch cat separately if join isn't working
            cat_name = cat_breed = None
            try:
                cat_res = supabase.table("cats").select("name, breed").eq("id", ar["cat_id"]).single().execute()
                if cat_res.data:
                    cat_name  = cat_res.data.get("name")
                    cat_breed = cat_res.data.get("breed")
            except Exception:
                pass
            requests.append((
                cat_name,
                cat_breed,
                ar["status"],
                parse_dt(ar.get("created_at")),
            ))
    except Exception as e:
        log.error("history failed: %s", e)
        requests = []

    user = get_user_profile(session["user_id"])
    return render_template("history.html", requests=requests, user=user, active_page="history")
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
            "id, status, created_at, cats(name)"
        ).eq("user_id", session["user_id"]).order("created_at", desc=True).limit(5).execute()

        recent = [
            (ar["id"], (ar.get("cats") or {}).get("name"), ar["status"], parse_dt(ar.get("created_at")))
            for ar in (ar_res.data or [])
        ]
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
    session.clear()
    return redirect(url_for("home"))


# app.run() is intentionally kept inside the __name__ guard.
# Vercel never calls this block — it imports the `app` object directly
# via api/index.py and calls it as a WSGI app.
if __name__ == "__main__":
    app.run(debug=True)
