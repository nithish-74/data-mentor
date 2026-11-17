"""All settings, read once from .env next to this file."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")


def _get(name, default=""):
    return os.getenv(name, default).strip()


DATA = Path(_get("DATA_DIR") or HERE / "data")
DATA.mkdir(parents=True, exist_ok=True)

# AI
GEMINI_API_KEY = _get("GEMINI_API_KEY")
GEMINI_MODEL = _get("GEMINI_MODEL", "gemini-3.8-flash")
OPENROUTER_API_KEY = _get("OPENROUTER_API_KEY")
OPENROUTER_MODEL = _get("OPENROUTER_MODEL")
OPENROUTER_GRADER_MODEL = _get("OPENROUTER_GRADER_MODEL")
SEARCH_IN_LESSONS = _get("SEARCH_IN_LESSONS", "false").lower() == "true"

# Schedule
TZ = ZoneInfo(_get("TIMEZONE", "Asia/Kolkata"))
REMINDER_TIME = _get("REMINDER_TIME", "07:00")

# Telegram (optional)
TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = _get("ALLOWED_CHAT_ID")

# Website
ON_RENDER = bool(_get("RENDER"))  # Render sets RENDER=true, PORT and RENDER_EXTERNAL_URL for every service
WEB_PORT = int(_get("PORT") or _get("WEB_PORT", "8000"))
WEB_HOST = "0.0.0.0" if ON_RENDER else _get("WEB_HOST", "127.0.0.1")
PUBLIC_URL = (_get("RENDER_EXTERNAL_URL") or _get("PUBLIC_URL", f"http://localhost:{WEB_PORT}")).rstrip("/")
ADMIN_USERNAME = _get("ADMIN_USERNAME").lower()  # this username is always admin (else: first person to join)
REQUIRE_INVITE = _get("REQUIRE_INVITE", "false").lower() == "true"  # true = new usernames need an invite code
DAILY_AI_LIMIT = int(_get("DAILY_AI_LIMIT", "40"))  # per person per day, protects the shared free quota
GLOBAL_DAILY_AI_LIMIT = int(_get("GLOBAL_DAILY_AI_LIMIT", "500"))  # whole site per day
NEW_USERS_PER_IP_PER_DAY = int(_get("NEW_USERS_PER_IP_PER_DAY", "5"))
ADMIN_PASSWORD = _get("ADMIN_PASSWORD")  # admins (only) must enter this when signing in on the website

# Public link via Cloudflare quick tunnel (free, no account). URL is printed at start and saved to data/public_url.txt
TUNNEL = _get("TUNNEL", "false").lower() == "true"
RUNTIME = Path(os.getenv("USERPROFILE", str(Path.home()))) / ".data-mentor"  # outside AppData so Task Scheduler sees it
CLOUDFLARED = _get("CLOUDFLARED") or str(RUNTIME / "tools" / "cloudflared.exe")

# Permanent link (needed for the installable app + notifications): free ngrok account with a static domain.
# When both are set, ngrok is used instead of the Cloudflare quick tunnel.
NGROK_AUTHTOKEN = _get("NGROK_AUTHTOKEN")
NGROK_DOMAIN = _get("NGROK_DOMAIN").replace("https://", "").strip("/")
def _find_ngrok():
    import shutil
    local = Path(os.getenv("LOCALAPPDATA", ""))
    for p in (_get("NGROK"), str(RUNTIME / "tools" / "ngrok.exe"), shutil.which("ngrok"),
              str(local / "Microsoft" / "WinGet" / "Links" / "ngrok.exe"),
              str(local / "Microsoft" / "WindowsApps" / "ngrok.exe")):  # Microsoft Store install (app alias)
        if p and os.path.lexists(p):  # lexists: Store app aliases are links that plain exists() rejects
            return p
    return None


NGROK = _find_ngrok()
USE_NGROK = bool(NGROK_AUTHTOKEN and NGROK_DOMAIN)


def session_secret() -> str:
    """Random secret for signing login cookies, created once and kept in the app's storage, so people stay
    signed in across restarts and moves."""
    import secrets
    from docstore import docs
    d = docs()
    if not d.exists("session_secret"):
        d.put_text("session_secret", secrets.token_hex(32))
    return d.get_text("session_secret").strip()
