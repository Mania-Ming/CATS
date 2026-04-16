import os
import logging
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
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
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        if not email or not password:
            return render_template("login.html", error="Please fill all fields")
        try:
            res = supabase.table("users").select("*").eq("email", email).execute()
            user = res.data[0] if res.data else None
        except Exception as e:
            log.error("login — users fetch failed: %s", e)
            return render_template("login.html", error="Something went wrong. Please try again.")

        if user and check_password_hash(user.get("password", ""), password):
            session.clear()
            session["user_id"] = user["id"]
            session["email"]   = user["email"]
            session["role"]    = user.get("role") or "user"
            if session["role"] == "admin":
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="Invalid email or password")
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
        ar_res = _admin_db().table("adoption_requests").select(
            "id, status, created_at, cat_id, user_id"
        ).order("created_at", desc=True).limit(5).execute()
        requests_list = _build_requests_list(ar_res.data or [])
    except Exception as e:
        log.error("admin_dashboard requests failed: %s", e)
        requests_list = []
    return render_template("admin_dashboard.html",
                           requests=requests_list,
                           total_cats=total_cats,
                           pending_count=pending_count,
                           total_users=total_users,
                           active_page="dashboard")


def _build_requests_list(ar_data):
    """Enrich adoption request rows with cat name/breed and user name/email."""
    db = _admin_db()
    result = []
    for ar in ar_data:
        cat_name = cat_breed = user_name = user_email = user_phone = user_addr = valid_id = None
        try:
            cat_res = db.table("cats").select("name, breed").eq("id", ar["cat_id"]).single().execute()
            if cat_res.data:
                cat_name  = cat_res.data.get("name")
                cat_breed = cat_res.data.get("breed")
        except Exception:
            pass
        try:
            u_res = db.table("users").select(
                "full_name, email, phone, address, valid_id_url"
            ).eq("id", ar["user_id"]).single().execute()
            if u_res.data:
                user_name  = u_res.data.get("full_name")
                user_email = u_res.data.get("email")
                user_phone = u_res.data.get("phone")
                user_addr  = u_res.data.get("address")
                valid_id   = u_res.data.get("valid_id_url")
        except Exception:
            pass
        result.append((
            ar["id"], cat_name, cat_breed,
            user_name, user_phone, user_addr,
            ar.get("status", "Pending"),
            parse_dt(ar.get("created_at")),
            valid_id, user_email,
        ))
    return result


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


# ------------------------------------------------------------------ admin requests page --

@app.route("/admin/requests")
def admin_requests():
    if not admin_required():
        return redirect(url_for("login"))
    try:
        ar_res = _admin_db().table("adoption_requests").select(
            "id, status, created_at, cat_id, user_id"
        ).order("created_at", desc=True).execute()
        requests_list = _build_requests_list(ar_res.data or [])
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
    return render_template("admin_cats.html", cats=cats, search=search, active_page="cats")


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
        "age":         int(age) if age.isdigit() else None,
        "origin":      request.form.get("origin", "").strip() or None,
        "weight":      request.form.get("weight", "").strip() or None,
        "size":        request.form.get("size", "").strip() or None,
        "lifespan":    request.form.get("lifespan", "").strip() or None,
        "coat_colors": request.form.get("coat_colors", "").strip() or None,
        "temperament": request.form.get("temperament", "").strip() or None,
        "about":       request.form.get("about", "").strip() or None,
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
        "age":         int(age) if age.isdigit() else None,
        "origin":      request.form.get("origin", "").strip() or None,
        "weight":      request.form.get("weight", "").strip() or None,
        "size":        request.form.get("size", "").strip() or None,
        "lifespan":    request.form.get("lifespan", "").strip() or None,
        "coat_colors": request.form.get("coat_colors", "").strip() or None,
        "temperament": request.form.get("temperament", "").strip() or None,
        "about":       request.form.get("about", "").strip() or None,
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

@app.route("/admin/users")
def admin_users():
    if not admin_required():
        return redirect(url_for("login"))
    try:
        users = _admin_db().table("users").select(
            "id, full_name, email, phone, address, role, created_at"
        ).execute().data or []
        users.sort(key=lambda u: u.get("created_at") or "", reverse=True)
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

        log.warning("register — received: email=%s fullname=%s password_len=%d",
                    email, fullname, len(password))

        if not email or not password or not fullname:
            return render_template("register.html", error="Please fill all fields")

        if len(password) < 6:
            return render_template("register.html", error="Password must be at least 6 characters")

        if supabase is None:
            log.error("register — supabase client is None")
            return render_template("register.html", error="Service unavailable.")

        try:
            existing = supabase.table("users").select("id").eq("email", email).execute()
            log.warning("register — duplicate check: %s", existing.data)
            if existing.data:
                return render_template("register.html", error="Email already registered")
        except Exception as e:
            log.error("DUPLICATE CHECK ERROR: %s", str(e))
            log.error("FULL ERROR: %r", e)
            return render_template("register.html", error="Registration failed.")

        try:
            res = supabase.table("users").insert({
                "email": email,
                "password": generate_password_hash(password),
                "full_name": fullname,
            }).execute()

            log.warning("INSERT RAW RESPONSE: %s", res)
            log.warning("INSERT DATA: %s", res.data)

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
    if "user_id" not in session:
        return redirect(url_for("login"))
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
    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(debug=True)
