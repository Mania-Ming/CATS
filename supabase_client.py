import os
import logging
from supabase import create_client
from dotenv import load_dotenv

# On Vercel, env vars are already in os.environ before this runs.
# override=False ensures Vercel's values are never overwritten by a local .env file.
# No dotenv_path — let python-dotenv search upward from cwd, which works locally.
load_dotenv(override=False)

log = logging.getLogger(__name__)

SUPABASE_URL         = (os.environ.get("SUPABASE_URL")         or "").strip()
SUPABASE_KEY         = (os.environ.get("SUPABASE_KEY")         or "").strip()
SUPABASE_SERVICE_KEY = (os.environ.get("SUPABASE_SERVICE_KEY") or "").strip()

# Always log at WARNING so this appears in Vercel function logs.
log.warning(
    "supabase_client boot — URL=%s | ANON=%s | SERVICE=%s",
    SUPABASE_URL or "MISSING",
    (SUPABASE_KEY[:16]         + "...") if SUPABASE_KEY         else "MISSING",
    (SUPABASE_SERVICE_KEY[:16] + "...") if SUPABASE_SERVICE_KEY else "MISSING",
)

# ── Anon client (used for public-facing routes) ──────────────────────────────
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        log.warning("supabase_client: anon client ready")
    except Exception as e:
        log.error("supabase_client: anon create_client failed: %r", e)
        supabase = None
else:
    log.error("supabase_client: SUPABASE_URL or SUPABASE_KEY missing — anon client unavailable")
    supabase = None

# ── Service-role client (used for all admin routes — bypasses RLS) ───────────
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
    try:
        supabase_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        log.warning("supabase_client: service-role client ready")
    except Exception as e:
        log.error("supabase_client: service-role create_client failed: %r", e)
        supabase_admin = supabase  # fallback — admin pages will be RLS-blocked
else:
    log.error(
        "supabase_client: SUPABASE_SERVICE_KEY missing — "
        "admin routes will fall back to anon key and be blocked by RLS. "
        "Set SUPABASE_SERVICE_KEY in Vercel → Project Settings → Environment Variables."
    )
    supabase_admin = supabase  # fallback
