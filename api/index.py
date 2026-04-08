import sys
import os

# Make the project root importable so app.py and supabase_client.py can be found
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app

# Vercel looks for a variable named "app" in this file
# No app.run() — Vercel calls the WSGI app directly
