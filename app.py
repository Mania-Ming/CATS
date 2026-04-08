import os
import logging
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from supabase_client import supabase as _supabase_client

# Vercel captures stdout/stderr — logging.WARNING and above appear in
# the function logs tab of your Vercel dashboard.
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

# If env vars were missing at boot, supabase_client sets the value to None.
# We re-export it here so all existing code continues to work unchanged.
supabase = _supabase_client

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
CSS_VERSION = "1.0.2"

@app.context_processor
def inject_globals():
    return {"css_version": CSS_VERSION}

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "pdf"}
STORAGE_BUCKET = "valid-ids"

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
    """Return user as tuple (id, email, password, full_name, phone, address, valid_id_url)."""
    try:
        res = supabase.table("users").select("*").eq("id", user_id).single().execute()
        u = res.data
        if not u:
            return None
        return (u.get("id"), u.get("email"), u.get("password"), u.get("full_name"),
                u.get("phone"), u.get("address"), u.get("valid_id_url"))
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


# ------------------------------------------------------------------ login --

@app.route("/", methods=["GET", "POST"])
def login():
    if "user_id" in session:
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
            session["email"] = user["email"]
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="Invalid email or password")
    return render_template("login.html")


# ------------------------------------------------------------------ admin login --

@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if "admin_logged_in" in session:
        return redirect(url_for("admin_dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session.clear()
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))
        return render_template("admin_login.html", error="Invalid admin credentials")
    return render_template("admin_login.html")


# ------------------------------------------------------------------ admin dashboard --

@app.route("/admin_dashboard")
def admin_dashboard():
    if "admin_logged_in" not in session:
        return redirect(url_for("admin_login"))
    try:
        # Fetch all adoption requests with related cat and user data in one call
        ar_res = supabase.table("adoption_requests").select(
            "id, status, created_at, living_situation, has_other_pets, experience_level, reason,"
            "user_id, cat_id,"
            "cats(name, breed),"
            "users(full_name, phone, address, valid_id_url, email)"
        ).order("created_at", desc=True).execute()

        requests_list = []
        for ar in (ar_res.data or []):
            cat  = ar.get("cats")  or {}
            user = ar.get("users") or {}
            requests_list.append((
                ar["id"],                        # r[0]
                cat.get("name"),                 # r[1]
                cat.get("breed"),                # r[2]
                user.get("full_name"),           # r[3]
                user.get("phone"),               # r[4]
                user.get("address"),             # r[5]
                ar["status"],                    # r[6]
                parse_dt(ar.get("created_at")),  # r[7]
                user.get("valid_id_url"),        # r[8]
                user.get("email"),               # r[9]
                ar.get("living_situation"),      # r[10]
                ar.get("has_other_pets"),        # r[11]
                ar.get("experience_level"),      # r[12]
                ar.get("reason"),                # r[13]
            ))

        total_cats    = len(supabase.table("cats").select("id", count="exact").execute().data or [])
        pending_count = len(supabase.table("adoption_requests").select("id").eq("status", "Pending").execute().data or [])
        total_users   = len(supabase.table("users").select("id", count="exact").execute().data or [])
    except Exception as e:
        log.error("admin_dashboard failed: %s", e)
        requests_list = []
        total_cats = pending_count = total_users = 0

    return render_template("admin_dashboard.html",
                           requests=requests_list,
                           total_cats=total_cats,
                           pending_count=pending_count,
                           total_users=total_users)


# ------------------------------------------------------------------ admin update status --

@app.route("/admin/update_status/<int:req_id>", methods=["POST"])
def update_status(req_id):
    if "admin_logged_in" not in session:
        return redirect(url_for("admin_login"))
    new_status = request.form.get("status")
    try:
        supabase.table("adoption_requests").update({"status": new_status}).eq("id", req_id).execute()
        flash(f"Request #{req_id} updated to {new_status}", "success")
    except Exception as e:
        log.error("update_status(%s) failed: %s", req_id, e)
        flash("Failed to update status. Please try again.", "error")
    return redirect(url_for("admin_dashboard"))


# ------------------------------------------------------------------ register --

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email    = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        fullname = request.form.get("fullname", "").strip()
        if not email or not password or not fullname:
            return render_template("register.html", error="Please fill all fields")
        try:
            existing = supabase.table("users").select("id").eq("email", email).execute()
            if existing.data:
                return render_template("register.html", error="Email already registered")
        except Exception as e:
            log.error("register — duplicate-check failed: %s", e)
            return render_template("register.html", error="Registration failed. Please try again.")

        try:
            supabase.table("users").insert({
                "email": email,
                "password": generate_password_hash(password),
                "full_name": fullname,
            }).execute()
        except Exception as e:
            # The real Supabase error (e.g. RLS violation, unique constraint)
            # is now visible in Vercel → Functions → Logs.
            log.error("register — insert failed for %s: %s", email, e)
            return render_template("register.html", error="Registration failed. Please try again.")

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
            q = q.ilike("name", f"%{search}%")
        if breed and breed != "All Breeds":
            q = q.eq("breed", breed)
        cats = [
            (c["id"], c["name"], c["breed"], c["age"], c["gender"],
             c.get("image", "cat1.jpg"), c["status"])
            for c in (q.execute().data or [])
        ]
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
    return render_template("dashboard.html", cats=cats, user=user, pending_count=pending_count)


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
            "status, created_at, cats(name, breed)"
        ).eq("user_id", session["user_id"]).order("created_at", desc=True).execute()

        requests = [
            (
                (ar.get("cats") or {}).get("name"),
                (ar.get("cats") or {}).get("breed"),
                ar["status"],
                parse_dt(ar.get("created_at")),
            )
            for ar in (ar_res.data or [])
        ]
    except Exception as e:
        log.error("history failed: %s", e)
        requests = []

    user = get_user_profile(session["user_id"])
    return render_template("history.html", requests=requests, user=user)


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

    return render_template("profile.html", user=user, recent=recent)


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


# app.run() is intentionally kept inside the __name__ guard.
# Vercel never calls this block — it imports the `app` object directly
# via api/index.py and calls it as a WSGI app.
if __name__ == "__main__":
    app.run(debug=True)
