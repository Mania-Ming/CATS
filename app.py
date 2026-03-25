from flask import Flask, render_template, request, redirect, url_for, session
import pymysql
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "secretkey")

def get_db():
    return pymysql.connect(
        host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
        user=os.environ.get("MYSQL_USER", "root"),
        password=os.environ.get("MYSQL_PASSWORD", ""),
        database=os.environ.get("MYSQL_DB", "cat_adoption"),
        port=int(os.environ.get("MYSQL_PORT", 3308)),
        cursorclass=pymysql.cursors.Cursor
    )

# ================= USER LOGIN =================
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        if not email or not password:
            return render_template("login.html", error="Please fill all fields")

        db = get_db()
        cur = db.cursor()
        cur.execute("SELECT * FROM users WHERE email=%s AND password=%s", (email, password))
        user = cur.fetchone()
        cur.close()
        db.close()

        if user:
            session.clear()
            session["user_id"] = user[0]
            session["email"] = user[1]
            session["role"] = "user"
            return redirect(url_for("dashboard"))
        else:
            return render_template("login.html", error="Invalid email or password")

    return render_template("login.html")


# ================= ADMIN LOGIN =================
@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if username == "admin" and password == "admin123":
            session.clear()
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))
        else:
            return render_template("admin_login.html", error="Invalid admin credentials")

    return render_template("admin_login.html")


# ================= ADMIN DASHBOARD =================
@app.route("/admin_dashboard")
def admin_dashboard():
    if "admin_logged_in" not in session:
        return redirect(url_for("admin_login"))

    return render_template("admin_dashboard.html")


# ================= REGISTER =================
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        db = get_db()
        cur = db.cursor()

        cur.execute("SELECT * FROM users WHERE email=%s", (email,))
        existing = cur.fetchone()

        if existing:
            cur.close()
            db.close()
            return render_template("register.html", error="Email already registered")

        cur.execute("INSERT INTO users (email, password) VALUES (%s, %s)", (email, password))
        db.commit()
        cur.close()
        db.close()

        return redirect(url_for("login"))

    return render_template("register.html")


# ================= USER DASHBOARD =================
@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    search = request.args.get("search")
    breed = request.args.get("breed")

    db = get_db()
    cur = db.cursor()

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
    cur.close()
    db.close()

    return render_template("dashboard.html", cats=cats)


# ================= ADOPT REQUEST =================
@app.route("/adopt_request", methods=["POST"])
def adopt_request():
    if "user_id" not in session:
        return redirect(url_for("login"))

    cat_id = request.form.get("cat_id")
    fullname = request.form.get("fullname")
    contact = request.form.get("contact")
    address = request.form.get("address")

    db = get_db()
    cur = db.cursor()

    cur.execute("""
        INSERT INTO adoption_requests
        (cat_id, fullname, contact, address, status, created_at)
        VALUES (%s, %s, %s, %s, 'Pending', NOW())
    """, (cat_id, fullname, contact, address))

    db.commit()
    cur.close()
    db.close()

    return redirect(url_for("dashboard"))


# ================= USER HISTORY =================
@app.route("/history")
def history():
    if "user_id" not in session:
        return redirect(url_for("login"))

    db = get_db()
    cur = db.cursor()

    cur.execute("""
        SELECT cats.name,
               cats.breed,
               adoption_requests.status,
               adoption_requests.created_at
        FROM adoption_requests
        JOIN cats ON adoption_requests.cat_id = cats.id
        WHERE adoption_requests.fullname IS NOT NULL
        ORDER BY adoption_requests.created_at DESC
    """)

    requests = cur.fetchall()
    cur.close()
    db.close()

    return render_template("history.html", requests=requests)


# ================= PROFILE =================
@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "user_id" not in session:
        return redirect(url_for("login"))

    db = get_db()
    cur = db.cursor()

    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        cur.execute("UPDATE users SET email=%s, password=%s WHERE id=%s",
                    (email, password, session["user_id"]))
        db.commit()
        session["email"] = email
        cur.close()
        db.close()
        return redirect(url_for("profile"))

    cur.execute("SELECT * FROM users WHERE id=%s", (session["user_id"],))
    user = cur.fetchone()
    cur.close()
    db.close()
    return render_template("profile.html", user=user)


# ================= DELETE ACCOUNT =================
@app.route("/delete_account", methods=["POST"])
def delete_account():
    if "user_id" not in session:
        return redirect(url_for("login"))

    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM users WHERE id=%s", (session["user_id"],))
    db.commit()
    cur.close()
    db.close()
    session.clear()
    return redirect(url_for("login"))


# ================= LOGOUT =================
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(debug=True)
