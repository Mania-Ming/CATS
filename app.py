import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_mysqldb import MySQL
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "cat_adoption_secret_2026"

app.config["MYSQL_HOST"] = "127.0.0.1"
app.config["MYSQL_USER"] = "root"
app.config["MYSQL_PASSWORD"] = ""
app.config["MYSQL_DB"] = "cat_adoption"
app.config["MYSQL_PORT"] = 3308

UPLOAD_FOLDER = os.path.join("static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "pdf"}
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

mysql = MySQL(app)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def ensure_columns():
    alterations = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS fullname VARCHAR(150) DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS contact VARCHAR(20) DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS address TEXT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS valid_id VARCHAR(255) DEFAULT NULL",
        "ALTER TABLE adoption_requests ADD COLUMN IF NOT EXISTS user_id INT DEFAULT NULL",
        "ALTER TABLE adoption_requests ADD COLUMN IF NOT EXISTS living_situation VARCHAR(100) DEFAULT NULL",
        "ALTER TABLE adoption_requests ADD COLUMN IF NOT EXISTS has_other_pets VARCHAR(100) DEFAULT NULL",
        "ALTER TABLE adoption_requests ADD COLUMN IF NOT EXISTS experience VARCHAR(100) DEFAULT NULL",
        "ALTER TABLE adoption_requests ADD COLUMN IF NOT EXISTS reason TEXT DEFAULT NULL",
    ]
    cur = mysql.connection.cursor()
    for sql in alterations:
        try:
            cur.execute(sql)
        except Exception:
            pass
    mysql.connection.commit()
    cur.close()


def get_user_profile(user_id):
    cur = mysql.connection.cursor()
    cur.execute(
        "SELECT id, email, password, fullname, contact, address, valid_id FROM users WHERE id=%s",
        (user_id,)
    )
    user = cur.fetchone()
    cur.close()
    return user


# ================= BROWSE (guest view) =================
@app.route("/browse")
def browse():
    ensure_columns()
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM cats")
    cats = cur.fetchall()
    cur.close()
    return render_template("browse.html", cats=cats, logged_in="user_id" in session)


# ================= USER LOGIN =================
@app.route("/", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    ensure_columns()
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        if not email or not password:
            return render_template("login.html", error="Please fill all fields")
        cur = mysql.connection.cursor()
        cur.execute("SELECT * FROM users WHERE email=%s AND password=%s", (email, password))
        user = cur.fetchone()
        cur.close()
        if user:
            session.clear()
            session["user_id"] = user[0]
            session["email"] = user[1]
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="Invalid email or password")
    return render_template("login.html")


# ================= ADMIN LOGIN =================
@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if "admin_logged_in" in session:
        return redirect(url_for("admin_dashboard"))
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username == "admin" and password == "admin123":
            session.clear()
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))
        return render_template("admin_login.html", error="Invalid admin credentials")
    return render_template("admin_login.html")


# ================= ADMIN DASHBOARD =================
@app.route("/admin_dashboard")
def admin_dashboard():
    if "admin_logged_in" not in session:
        return redirect(url_for("admin_login"))
    ensure_columns()
    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT ar.id, c.name, c.breed, ar.fullname, ar.contact, ar.address,
               ar.status, ar.created_at, u.valid_id, u.email,
               ar.living_situation, ar.has_other_pets, ar.experience, ar.reason
        FROM adoption_requests ar
        JOIN cats c ON ar.cat_id = c.id
        JOIN users u ON ar.user_id = u.id
        ORDER BY ar.created_at DESC
    """)
    requests_list = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM cats")
    total_cats = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM adoption_requests WHERE status='Pending'")
    pending_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]
    cur.close()
    return render_template("admin_dashboard.html",
                           requests=requests_list,
                           total_cats=total_cats,
                           pending_count=pending_count,
                           total_users=total_users)


# ================= ADMIN UPDATE STATUS =================
@app.route("/admin/update_status/<int:req_id>", methods=["POST"])
def update_status(req_id):
    if "admin_logged_in" not in session:
        return redirect(url_for("admin_login"))
    new_status = request.form.get("status")
    cur = mysql.connection.cursor()
    cur.execute("UPDATE adoption_requests SET status=%s WHERE id=%s", (new_status, req_id))
    mysql.connection.commit()
    cur.close()
    flash(f"Request #{req_id} updated to {new_status}", "success")
    return redirect(url_for("admin_dashboard"))


# ================= REGISTER =================
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        fullname = request.form.get("fullname", "").strip()
        if not email or not password or not fullname:
            return render_template("register.html", error="Please fill all fields")
        cur = mysql.connection.cursor()
        cur.execute("SELECT id FROM users WHERE email=%s", (email,))
        if cur.fetchone():
            cur.close()
            return render_template("register.html", error="Email already registered")
        cur.execute(
            "INSERT INTO users (email, password, fullname) VALUES (%s, %s, %s)",
            (email, password, fullname)
        )
        mysql.connection.commit()
        cur.close()
        flash("Account created! Please login.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")


# ================= USER DASHBOARD =================
@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))
    ensure_columns()
    search = request.args.get("search", "")
    breed = request.args.get("breed", "")
    cur = mysql.connection.cursor()
    query = "SELECT * FROM cats WHERE 1=1"
    values = []
    if search:
        query += " AND name LIKE %s"
        values.append(f"%{search}%")
    if breed and breed != "All Breeds":
        query += " AND breed = %s"
        values.append(breed)
    cur.execute(query, values)
    cats = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM adoption_requests WHERE user_id=%s AND status='Pending'",
                (session["user_id"],))
    pending_count = cur.fetchone()[0]
    cur.close()
    user = get_user_profile(session["user_id"])
    return render_template("dashboard.html", cats=cats, user=user, pending_count=pending_count)


# ================= ADOPT REQUEST =================
@app.route("/adopt_request", methods=["POST"])
def adopt_request():
    if "user_id" not in session:
        return redirect(url_for("login"))
    ensure_columns()
    user = get_user_profile(session["user_id"])
    if not user[3] or not user[4] or not user[5]:
        flash("Please complete your profile (name, contact, address) before adopting.", "error")
        return redirect(url_for("profile"))
    cat_id = request.form.get("cat_id")
    living_situation = request.form.get("living_situation", "").strip()
    has_other_pets = request.form.get("has_other_pets", "").strip()
    experience = request.form.get("experience", "").strip()
    reason = request.form.get("reason", "").strip()
    if not living_situation or not has_other_pets or not reason:
        flash("Please answer all required questions.", "error")
        return redirect(url_for("dashboard"))
    cur = mysql.connection.cursor()
    cur.execute("""
        INSERT INTO adoption_requests
            (user_id, cat_id, fullname, contact, address, living_situation, has_other_pets, experience, reason, status, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'Pending', NOW())
    """, (session["user_id"], cat_id, user[3], user[4], user[5],
          living_situation, has_other_pets, experience, reason))
    mysql.connection.commit()
    cur.close()
    flash("Adoption request submitted! We will review it shortly.", "success")
    return redirect(url_for("dashboard"))


# ================= USER HISTORY =================
@app.route("/history")
def history():
    if "user_id" not in session:
        return redirect(url_for("login"))
    ensure_columns()
    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT c.name, c.breed, ar.status, ar.created_at
        FROM adoption_requests ar
        JOIN cats c ON ar.cat_id = c.id
        WHERE ar.user_id = %s
        ORDER BY ar.created_at DESC
    """, (session["user_id"],))
    requests = cur.fetchall()
    cur.close()
    user = get_user_profile(session["user_id"])
    return render_template("history.html", requests=requests, user=user)


# ================= PROFILE =================
@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "user_id" not in session:
        return redirect(url_for("login"))
    ensure_columns()
    if request.method == "POST":
        fullname = request.form.get("fullname", "").strip()
        contact = request.form.get("contact", "").strip()
        address = request.form.get("address", "").strip()
        valid_id_filename = None
        if "valid_id" in request.files:
            file = request.files["valid_id"]
            if file and file.filename and allowed_file(file.filename):
                filename = secure_filename(f"uid_{session['user_id']}_{file.filename}")
                file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
                valid_id_filename = filename
        cur = mysql.connection.cursor()
        if valid_id_filename:
            cur.execute(
                "UPDATE users SET fullname=%s, contact=%s, address=%s, valid_id=%s WHERE id=%s",
                (fullname, contact, address, valid_id_filename, session["user_id"])
            )
        else:
            cur.execute(
                "UPDATE users SET fullname=%s, contact=%s, address=%s WHERE id=%s",
                (fullname, contact, address, session["user_id"])
            )
        mysql.connection.commit()
        cur.close()
        flash("Profile updated successfully!", "success")
        return redirect(url_for("profile"))
    user = get_user_profile(session["user_id"])
    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT ar.id, c.name, ar.status, ar.created_at
        FROM adoption_requests ar
        JOIN cats c ON ar.cat_id = c.id
        WHERE ar.user_id=%s ORDER BY ar.created_at DESC LIMIT 5
    """, (session["user_id"],))
    recent = cur.fetchall()
    cur.close()
    return render_template("profile.html", user=user, recent=recent)


# ================= DELETE ACCOUNT =================
@app.route("/delete_account", methods=["POST"])
def delete_account():
    if "user_id" not in session:
        return redirect(url_for("login"))
    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM users WHERE id=%s", (session["user_id"],))
    mysql.connection.commit()
    cur.close()
    session.clear()
    return redirect(url_for("login"))


# ================= LOGOUT =================
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(debug=True)
