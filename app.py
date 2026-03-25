from flask import Flask, render_template

app = Flask(__name__)

@app.route("/")
def home():
    return "Hello from Vercel!"

# VERY IMPORTANT FOR VERCEL
def handler(request):
    return app(request.environ, lambda *args: None)