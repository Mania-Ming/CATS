from flask import Flask, render_template

app = Flask(__name__)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/shop")
def shop():
    return "<h1>Shop Page</h1>"

@app.route("/contact")
def contact():
    return "<h1>Contact Page</h1>"

if __name__ == "__main__":
    app.run(debug=True)