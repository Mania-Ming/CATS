from flask import Flask, render_template, request

app = Flask(__name__)

# ===== HOME PAGE =====
@app.route("/")
def home():
    return render_template("index.html")


# ===== SHOP PAGE =====
@app.route("/shop")
def shop():
    category = request.args.get("category")

    # Sample products (you can replace with database later)
    products = [
        {"name": "Laptop", "image": "images/laptop.jpg", "category": "laptop"},
        {"name": "Phone", "image": "images/phone.jpg", "category": "phone"},
        {"name": "Shoes", "image": "images/shoes.jpg", "category": "shoes"},
        {"name": "Watch", "image": "images/watch.jpg", "category": "watch"},
    ]

    # Filter by category
    if category:
        products = [p for p in products if p["category"] == category]

    return render_template("shop.html", products=products)


# ===== CONTACT PAGE =====
@app.route("/contact")
def contact():
    return render_template("contact.html")


# ===== PROFILE PAGE =====
@app.route("/profile")
def profile():
    return "<h2>User Profile Page</h2>"


# ===== CART PAGE =====
@app.route("/cart")
def cart():
    return "<h2>Your Cart is empty 🛒</h2>"


# ===== LOGIN PAGE =====
@app.route("/login")
def login():
    return "<h2>Login Page</h2>"


# ===== LOGOUT =====
@app.route("/logout")
def logout():
    return "<h2>You have been logged out</h2>"


# ===== RUN APP =====
if __name__ == "__main__":
    app.run(debug=True)