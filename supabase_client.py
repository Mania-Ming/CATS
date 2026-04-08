import os
from supabase import create_client
from dotenv import load_dotenv

# load_dotenv() is a no-op on Vercel (env vars are injected by the platform)
# but it keeps local development working with a .env file
load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()

if not SUPABASE_URL or not SUPABASE_KEY:
    # Don't raise here — raising at import time crashes the Vercel cold start
    # before the platform has a chance to inject environment variables.
    # The app will still boot; any route that calls supabase will get an
    # exception which is caught by the try/except blocks in app.py.
    supabase = None
else:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
