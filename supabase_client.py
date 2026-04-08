import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()

if not SUPABASE_URL or not SUPABASE_KEY:
    raise EnvironmentError(
        "Missing required environment variables: SUPABASE_URL and/or SUPABASE_KEY. "
        "Copy .env.example to .env and fill in your Supabase project credentials."
    )

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
