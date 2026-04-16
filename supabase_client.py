import os
import logging
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

SUPABASE_URL         = os.environ.get("SUPABASE_URL",         "").strip()
SUPABASE_KEY         = os.environ.get("SUPABASE_KEY",         "").strip()
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()

if not SUPABASE_URL or not SUPABASE_KEY:
    log.error("supabase_client: SUPABASE_URL and/or SUPABASE_KEY missing")
    supabase = None
else:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        log.warning("supabase_client: anon client ready (%s)", SUPABASE_URL)
    except Exception as e:
        log.error("supabase_client: create_client failed: %r", e)
        supabase = None

# Service-role client — bypasses RLS for all admin operations.
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
    try:
        supabase_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        log.warning("supabase_client: service-role (admin) client ready")
    except Exception as e:
        log.error("supabase_client: admin create_client failed: %r", e)
        supabase_admin = supabase
else:
    log.warning(
        "supabase_client: SUPABASE_SERVICE_KEY is NOT set. "
        "Admin pages will use the anon key and will be blocked by RLS. "
        "Add SUPABASE_SERVICE_KEY to your .env and Vercel environment variables."
    )
    supabase_admin = supabase
