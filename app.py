"""Starts everything: the website, the Telegram bot (if configured) and the daily scheduler.

Run:  python app.py      then open http://localhost:8000
"""
import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

# Started hidden (pythonw / autostart) there is no console: send any stray output nowhere instead of crashing.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
from datetime import date, datetime, time
from logging.handlers import RotatingFileHandler

import uvicorn

import config as cfg
from ai import build_ai
from articles import Articles
from mentor import Mentor
from push import Push
from users import Users

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[RotatingFileHandler(cfg.DATA / "mentor.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"),
              logging.StreamHandler(sys.stdout)],
)
for noisy in ("httpx", "apscheduler", "uvicorn.access"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("mentor")

users = Users(cfg.DATA, cfg.REMINDER_TIME)
mentor = Mentor(build_ai(cfg), users)
articles = Articles(cfg.DATA, mentor.ai)
push = Push(cfg.DATA, users)


async def push_daily(uid):
    """At each of the learner's reminder times: web push a study reminder (the first one carries the daily message)."""
    from push import due_slots, mark_slots, reminder_times, slot_messages
    now = datetime.now(cfg.TZ)
    due, rec, key = due_slots(users, uid, "push", now)
    if not due:
        return
    mark_slots(users, uid, key, rec, due)
    first = reminder_times(users.get(uid))[0]
    slot = first if first in due else due[-1]  # several missed while the PC was off: one reminder, not a burst
    for m in slot_messages(users, articles, uid, "push", now.date(), slot):
        await push.send(uid, m["title"], m["body"], m["url"], m["tag"])


async def article_refresher():
    """Checks the blogs every 3 hours (first check a minute after start)."""
    await asyncio.sleep(60)
    while True:
        try:
            await articles.refresh()
        except Exception:
            log.exception("Article refresh failed")
        await asyncio.sleep(900)


class TelegramChannel:
    def __init__(self):
        self.app = None
        self.bot_username = None


telegram = TelegramChannel()


async def scheduler():
    """Every 5 minutes: send each Telegram learner their morning routine once their reminder time has passed."""
    from bot import deliver_daily
    while True:
        try:
            now = datetime.now(cfg.TZ).time()
            for uid, u in list(users.people.items()):
                if u.get("paused") or now < time.fromisoformat(u["reminder"]):
                    continue
                if telegram.app and u.get("telegram_id"):
                    try:
                        await deliver_daily(telegram.app, mentor, users, uid, articles)
                    except Exception:
                        log.exception("Daily run failed for %s", uid)
                if users.store(uid).data.get("push_subs"):
                    try:
                        await push_daily(uid)
                    except Exception:
                        log.exception("Push reminder failed for %s", uid)
            users.purge_expired()
        except Exception:
            log.exception("Scheduler tick failed")
        await asyncio.sleep(300)


class Tunnel:
    """Runs a Cloudflare quick tunnel to this server and picks up its public https URL."""

    def __init__(self):
        self.proc = None
        self.stopping = False
        self.generation = 0  # bumps each time a new public URL appears

    def start(self):
        import re
        import subprocess
        import threading
        import time as _time
        from pathlib import Path
        if cfg.USE_NGROK:
            if cfg.NGROK:
                return self.start_ngrok()
            log.error("NGROK_AUTHTOKEN/NGROK_DOMAIN are set but ngrok isn't installed; using the Cloudflare tunnel")
        if not Path(cfg.CLOUDFLARED).exists():
            log.error("TUNNEL=true but cloudflared not found at %s", cfg.CLOUDFLARED)
            return
        cfg.PUBLIC_URL = f"http://localhost:{cfg.WEB_PORT}"
        self.proc = subprocess.Popen(
            [cfg.CLOUDFLARED, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{cfg.WEB_PORT}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

        def read():
            for line in self.proc.stderr:
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if m and not cfg.PUBLIC_URL.endswith("trycloudflare.com"):
                    cfg.PUBLIC_URL = m.group(0)
                    (cfg.DATA / "public_url.txt").write_text(cfg.PUBLIC_URL, encoding="utf-8")
                    self.generation += 1
                    log.info("PUBLIC LINK: %s", cfg.PUBLIC_URL)
            if not self.stopping:  # cloudflared exited (network drop etc.): start a fresh tunnel
                log.warning("Tunnel stopped; restarting in 10 seconds")
                _time.sleep(10)
                if not self.stopping:
                    self.start()
        threading.Thread(target=read, daemon=True).start()

    def start_ngrok(self):
        """Permanent address: https://<NGROK_DOMAIN>. The token goes in the environment, not the command line."""
        import subprocess
        import threading
        import time as _time
        env = {**os.environ, "NGROK_AUTHTOKEN": cfg.NGROK_AUTHTOKEN}
        self.proc = subprocess.Popen(
            [cfg.NGROK, "http", f"127.0.0.1:{cfg.WEB_PORT}", f"--url=https://{cfg.NGROK_DOMAIN}",
             "--log=stdout", "--log-format=logfmt"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

        def read():
            started = False
            for line in self.proc.stdout:
                if not started and ("authentication failed" in line or "ERR_NGROK_" in line):
                    # bad token / domain: don't leave the site offline, use the Cloudflare link instead
                    log.error("ngrok refused to start (check NGROK_AUTHTOKEN / NGROK_DOMAIN in .env). "
                              "Using the Cloudflare tunnel for now.")
                    self.stopping_ngrok = True
                    self.proc.terminate()
                    cfg.USE_NGROK = False
                    return self.start()
                if "started tunnel" in line:
                    started = True
                    cfg.PUBLIC_URL = f"https://{cfg.NGROK_DOMAIN}"
                    (cfg.DATA / "public_url.txt").write_text(cfg.PUBLIC_URL, encoding="utf-8")
                    if not self.generation:
                        self.generation = 1
                    log.info("PUBLIC LINK (permanent): %s", cfg.PUBLIC_URL)
                elif "lvl=eror" in line or "lvl=crit" in line or "ERR_NGROK" in line:
                    msg = re.search(r'err="?([^"]+)', line)
                    log.error("ngrok: %s", (msg.group(1) if msg else line.strip())[:200])
            if not self.stopping:
                log.warning("ngrok stopped; restarting in 30 seconds")
                _time.sleep(30)
                if not self.stopping:
                    self.start_ngrok()
        import re
        threading.Thread(target=read, daemon=True).start()

    def stop(self):
        self.stopping = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


tunnel = Tunnel()


async def announce_links():
    """The quick-tunnel address changes on every restart: message it to admins who use Telegram."""
    seen = 0
    while True:
        if tunnel.generation != seen and telegram.app:
            seen = tunnel.generation
            for uid in users.admins():
                tg = users.get(uid).get("telegram_id")
                if tg:
                    try:
                        await telegram.app.bot.send_message(
                            int(tg), f"Mentor website is up. Today's public link:\n{cfg.PUBLIC_URL}\n\n"
                                     f"Share it with learners. It changes when the PC restarts.",
                            disable_web_page_preview=True)
                    except Exception:
                        log.warning("Could not send the public link to admin %s", uid)
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(_app):
    if cfg.TUNNEL:
        tunnel.start()
    if cfg.TELEGRAM_BOT_TOKEN:
        from bot import build
        telegram.app = build(mentor, users, articles)
        await telegram.app.initialize()
        await telegram.app.start()
        await telegram.app.updater.start_polling()
        telegram.bot_username = telegram.app.bot.username
        log.info("Telegram bot @%s running", telegram.bot_username)
    task = asyncio.create_task(scheduler())
    refresher = asyncio.create_task(article_refresher())
    announcer = asyncio.create_task(announce_links()) if cfg.TUNNEL else None
    log.info("Website: http://localhost:%d  (people: %d, AI: %s)", cfg.WEB_PORT, len(users.people),
             ", ".join(f"{p.name}:{p.model}" for p in mentor.ai.providers))
    try:
        yield
    finally:
        task.cancel()
        refresher.cancel()
        if announcer:
            announcer.cancel()
        tunnel.stop()
        if telegram.app:
            await telegram.app.updater.stop()
            await telegram.app.stop()
            await telegram.app.shutdown()


def create():
    from web import create_app
    app = create_app(mentor, users, telegram, articles, push)
    app.router.lifespan_context = lifespan
    return app


app = create()

def already_running() -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", cfg.WEB_PORT)) == 0


if __name__ == "__main__":
    if not cfg.ON_RENDER and already_running():
        print(f"The mentor is already running (http://localhost:{cfg.WEB_PORT}). Nothing to do.")
        sys.exit(0)
    if sys.platform == "win32":
        # Ask Windows not to sleep while the mentor is running (the screen may still turn off). This is the same
        # request video players make; it lasts only while this process runs and changes no settings.
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_AWAYMODE_REQUIRED = 0x80000000, 0x00000001, 0x00000040
        if not ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED):
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        log.info("Asked Windows to stay awake while the mentor runs")
    # on Render, trust its proxy's X-Forwarded-For so each visitor's own address is seen (sign-up limits)
    uvicorn.run(app, host=cfg.WEB_HOST, port=cfg.WEB_PORT, log_level="warning",
                proxy_headers=True, forwarded_allow_ips="*" if cfg.ON_RENDER else "127.0.0.1")
