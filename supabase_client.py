import os
import logging
from supabase import create_client
from dotenv import load_dotenv

# load_dotenv() reads .env locally; on Vercel env vars are injected by the platform
load_dotenv()

log = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()

if not SUPABASE_URL and not SUPABASE_KEY:
    log.error("supabase_client: SUPABASE_URL and SUPABASE_KEY are both missing")
    supabase = None
elif not SUPABASE_URL:
    log.error("supabase_client: SUPABASE_URL is missing")
    supabase = None
elif not SUPABASE_KEY:
    log.error("supabase_client: SUPABASE_KEY is missing")
    supabase = None
else:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        log.warning("supabase_client: connected to %s", SUPABASE_URL)
    except Exception as e:
        log.error("supabase_client: create_client failed: %r", e)
        supabase = None
