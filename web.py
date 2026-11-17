"""Website: JSON API + static single-page app. People sign in with just a username."""
import hmac
import logging
import re
import time

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import config as cfg
import curriculum as cur
from ai import MentorError
from mentor import Mentor, today
from users import Users

log = logging.getLogger("mentor.web")
STATIC = cfg.HERE / "static"


def parse_hhmm(s):
    m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", str(s or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def create_app(mentor: Mentor, users: Users, telegram=None, articles=None, push=None) -> FastAPI:
    """telegram: object with .bot_username (or None)."""
    app = FastAPI(title="Data Architect Mentor", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SessionMiddleware, secret_key=cfg.session_secret(), session_cookie="mentor_session",
                       max_age=60 * 60 * 24 * 60, same_site="lax", https_only=cfg.PUBLIC_URL.startswith("https"))
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.exception_handler(MentorError)
    async def mentor_error(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=429 if "limit" in str(exc) or "quota" in str(exc)
                            else 503)

    # ---- auth helpers -------------------------------------------------------
    def csrf(request: Request):
        if request.method != "GET" and request.headers.get("x-mentor") != "1":
            raise HTTPException(403, "Bad request")

    def current(request: Request) -> str:
        uid = request.session.get("uid")
        if not uid or not users.get(uid):
            request.session.clear()
            raise HTTPException(401, "Not signed in")
        return uid

    def admin_uid(uid: str = Depends(current)) -> str:
        if not users.is_admin(uid):
            raise HTTPException(403, "Admins only")
        return uid

    def body_str(body, key, limit):
        v = body.get(key)
        return str(v).strip()[:limit] if v is not None else ""

    def join(username, invite):
        """Create a new person for this username if allowed; returns uid or raises 403 need_invite."""
        if not users.people or (cfg.ADMIN_USERNAME and username == cfg.ADMIN_USERNAME):
            uid = users.add(username, role="admin", username=username)
        elif invite and (inv := users.use_invite(invite)):
            uid = users.add(inv["name"] or username, username=username)
        elif not cfg.REQUIRE_INVITE:
            uid = users.add(username, username=username)
        else:
            raise HTTPException(403, "need_invite")
        s = users.store(uid)
        s["last_daily"] = today().isoformat()
        s.save()
        log.info("New person joined via website: %s", uid)
        return uid

    # ---- pages / config -------------------------------------------------------
    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    # The app files must live at the site root so the app (and its notifications) cover every page.
    @app.get("/sw.js")
    async def service_worker():
        return FileResponse(STATIC / "sw.js", media_type="text/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    @app.get("/manifest.webmanifest")
    async def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    # Digital Asset Links: lets the Android app open the site full-screen (no address bar).
    @app.get("/.well-known/assetlinks.json")
    async def assetlinks():
        from docstore import docs
        text = docs().get_text("assetlinks.json")
        if not text:
            raise HTTPException(404)
        from fastapi.responses import Response
        return Response(text, media_type="application/json")

    @app.get("/download/android")
    async def download_android():
        apk = cfg.HERE / "dist" / "DataArchitectMentor.apk"
        if not apk.exists():
            raise HTTPException(404, "The Android app hasn't been built yet")
        return FileResponse(apk, media_type="application/vnd.android.package-archive",
                            filename="DataArchitectMentor.apk", headers={"Cache-Control": "no-cache"})

    @app.get("/favicon.ico")
    async def favicon():
        return FileResponse(STATIC / "icons" / "favicon-32.png", media_type="image/png")

    # ---- notifications -----------------------------------------------------------
    def need_push():
        if not push:
            raise HTTPException(404, "Notifications are not set up")

    @app.get("/api/push/key")
    async def push_key(uid: str = Depends(current)):
        need_push()
        return {"key": push.public_key}

    @app.post("/api/push/subscribe", dependencies=[Depends(csrf)])
    async def push_subscribe(request: Request, uid: str = Depends(current)):
        need_push()
        first = not push.subs(uid)
        try:
            push.subscribe(uid, (await request.json()).get("subscription"))
        except ValueError as e:
            raise HTTPException(400, str(e))
        if first:  # start fresh: no reminder for a time that already passed today, no article backlog
            from datetime import datetime
            s, now = users.store(uid), datetime.now(cfg.TZ)
            if now.strftime("%H:%M") >= users.get(uid)["reminder"]:
                s["push_daily"] = now.date().isoformat()
            if articles and articles.visible():
                s["push_articles_at"] = max(a["added"] for a in articles.visible())
            s.save()
        return {"devices": len(push.subs(uid))}

    @app.post("/api/push/unsubscribe", dependencies=[Depends(csrf)])
    async def push_unsubscribe(request: Request, uid: str = Depends(current)):
        need_push()
        push.unsubscribe(uid, body_str(await request.json(), "endpoint", 1000))
        return {"devices": len(push.subs(uid))}

    @app.post("/api/push/test", dependencies=[Depends(csrf)])
    async def push_test(uid: str = Depends(current)):
        need_push()
        n = await push.send(uid, "Notifications are on", f"You'll get your daily reminder at "
                            f"{users.get(uid)['reminder']}. Tap to open the app.", "/#home", "test")
        if not n:
            raise HTTPException(400, "No device accepted it. Turn notifications off and on again on this device.")
        return {"sent": n}

    def schedule_for(u):
        from push import prefs, reminder_times
        p = prefs(u)
        return {"times": reminder_times(u), "sound": p["sound"], "vibrate": p["vibrate"], "timezone": cfg.TZ.key,
                "enabled": not u.get("paused") and (p["daily"] or p["nudge"])}

    @app.get("/api/app/digest")
    async def app_digest(slot: str = "", test: int = 0, uid: str = Depends(current)):
        """Called by the Android app when one of the learner's alarms goes off; returns what the alarm says."""
        from datetime import datetime
        from push import slot_messages
        u = users.get(uid)
        if test:
            t = ", ".join(schedule_for(u)["times"])
            items = [{"title": "Time to learn", "body": f"This is how your reminders will look. They ring at {t}.",
                      "url": "/#home", "tag": "test"}]
        elif u.get("paused"):
            items = []
        else:
            items = slot_messages(users, articles, uid, "app", datetime.now(cfg.TZ).date(), slot)
        return {"schedule": schedule_for(u), "notifications": items}

    @app.post("/api/notify-prefs", dependencies=[Depends(csrf)])
    async def notify_prefs(request: Request, uid: str = Depends(current)):
        body, u = await request.json(), users.get(uid)
        from push import prefs, set_reminder_times
        if "times" in body:
            try:
                set_reminder_times(u, body["times"])
            except ValueError as e:
                raise HTTPException(400, str(e))
        u["notify"] = {k: bool(body.get(k, v)) for k, v in prefs(u).items()}
        users.save()
        return {"notify": u["notify"], "schedule": schedule_for(u)}

    @app.get("/api/config")
    async def config():
        return {"require_invite": cfg.REQUIRE_INVITE,
                "telegram_bot": getattr(telegram, "bot_username", None), "total_lessons": cur.TOTAL_LESSONS,
                "timezone": cfg.TZ.key, "server_url": cfg.RENDER_URL or None}

    failed, signups = {}, {}  # ip -> [timestamps]   (in memory; resets on restart)

    def client_ip(request: Request) -> str:
        # Cloudflare Tunnel passes the visitor's address in this header; direct visits are local
        return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")

    def recent(bucket, ip, window):
        now = time.time()
        bucket[ip] = [t for t in bucket.get(ip, []) if now - t < window]
        return bucket[ip]

    @app.post("/api/login", dependencies=[Depends(csrf)])
    async def login(request: Request):
        body = await request.json()
        ip = client_ip(request)
        username = users.valid_username(body_str(body, "username", 30))
        if not username:
            raise HTTPException(400, "Usernames are 3–30 characters: letters, numbers, dots, dashes or underscores.")
        uid = users.find(username=username)
        becomes_admin = not uid and (not users.people or (cfg.ADMIN_USERNAME and username == cfg.ADMIN_USERNAME))
        if cfg.ADMIN_PASSWORD and (becomes_admin or (uid and users.is_admin(uid))):
            if len(recent(failed, ip, 900)) >= 5:
                raise HTTPException(429, "Too many wrong passwords. Try again in 15 minutes.")
            given = body_str(body, "password", 200)
            if not given:
                raise HTTPException(401, "need_password")
            if not hmac.compare_digest(given.encode(), cfg.ADMIN_PASSWORD.encode()):
                failed[ip].append(time.time())
                log.warning("Wrong admin password for %s", username)
                raise HTTPException(401, "wrong_password")
        if not uid:
            if len(recent(signups, ip, 86400)) >= cfg.NEW_USERS_PER_IP_PER_DAY:
                raise HTTPException(429, "Too many new usernames from this connection today. Sign in with yours.")
            uid = join(username, body_str(body, "invite", 20))
            signups[ip].append(time.time())
        request.session["uid"] = uid
        return {"ok": True}

    @app.post("/api/logout", dependencies=[Depends(csrf)])
    async def logout(request: Request):
        request.session.clear()
        return {"ok": True}

    # ---- learner ---------------------------------------------------------------
    @app.get("/api/me")
    async def me(uid: str = Depends(current)):
        u = users.get(uid)
        return {"uid": uid, "name": u["name"], "username": u.get("username"), "role": u["role"],
                "reminder": u["reminder"], "background": u["background"], "paused": u.get("paused", False),
                "telegram": bool(u.get("telegram_id")), "progress": mentor.progress(uid),
                "mcq": mentor.mcq_public(uid),
                "articles_new": articles.new_since(users.store(uid).data.get("articles_seen")) if articles else 0,
                "notify": __import__("push").prefs(u), "schedule": schedule_for(u),
                "push_devices": len(users.store(uid).data.get("push_subs") or []), "push_ready": bool(push)}

    # ---- articles ----------------------------------------------------------------
    def need_articles():
        if not articles:
            raise HTTPException(404, "Articles are not set up")

    @app.get("/api/articles")
    async def list_articles(week: str = "", source: str = "", uid: str = Depends(current)):
        need_articles()
        week = int(week) if week.isdigit() else 0
        seen = users.store(uid).data.get("articles_seen") or ""
        items = [{k: a[k] for k in ("id", "title", "link", "source", "published", "summary", "label", "week", "tags")}
                 | {"new": a["added"] > seen} for a in articles.list(week or None, source or None)]
        return {"items": items, "sources": sorted({a["source"] for a in articles.visible()}),
                "last_fetch": articles.data["last_fetch"], "current_week": cur.week_of(max(0, users.store(uid)["lesson_index"] - 1))}

    @app.post("/api/articles/seen", dependencies=[Depends(csrf)])
    async def articles_seen(uid: str = Depends(current)):
        need_articles()
        from datetime import datetime, timezone
        s = users.store(uid)
        s["articles_seen"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        s.save()
        return {"ok": True}

    @app.post("/api/admin/articles/refresh", dependencies=[Depends(csrf)])
    async def articles_refresh(uid: str = Depends(admin_uid)):
        need_articles()
        if articles.lock.locked():
            raise HTTPException(409, "Already checking for new articles. Give it a minute.")
        return {"added": await articles.refresh(force=True)}

    @app.get("/api/admin/articles/sources")
    async def article_sources(uid: str = Depends(admin_uid)):
        need_articles()
        counts = {}
        for a in articles.visible():
            counts[a["source"]] = counts.get(a["source"], 0) + 1
        return {"sources": [s | {"count": counts.get(s["name"], 0)} for s in articles.data["sources"]]}

    @app.post("/api/admin/articles/sources", dependencies=[Depends(csrf)])
    async def article_source_add(request: Request, uid: str = Depends(admin_uid)):
        need_articles()
        body = await request.json()
        try:
            name = await articles.add_source(body_str(body, "name", 40), body_str(body, "url", 500))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"name": name}

    @app.post("/api/admin/articles/sources/{sid}", dependencies=[Depends(csrf)])
    async def article_source_toggle(sid: str, request: Request, uid: str = Depends(admin_uid)):
        need_articles()
        if not articles.set_source(sid, (await request.json()).get("on")):
            raise HTTPException(404, "No such source")
        return {"ok": True}

    @app.delete("/api/admin/articles/sources/{sid}", dependencies=[Depends(csrf)])
    async def article_source_remove(sid: str, uid: str = Depends(admin_uid)):
        need_articles()
        if not articles.remove_source(sid):
            raise HTTPException(404, "No such source")
        return {"ok": True}

    @app.get("/api/lessons/{index}")
    async def get_lesson(index: int, uid: str = Depends(current)):
        text = users.store(uid)["lessons"].get(str(index))
        found = cur.lesson_at(index)
        if not text or not found:
            raise HTTPException(404, "Lesson not taught yet")
        return {"index": index, "title": f"Week {found[0]} · Lesson {found[1]}/5 · {found[3]}", "text": text}

    @app.post("/api/lesson", dependencies=[Depends(csrf)])
    async def next_lesson(uid: str = Depends(current)):
        c = users.store(uid)["current"]
        if c and c["kind"] == "lesson":
            raise HTTPException(409, "Finish or skip your open assignment first")
        msg = await mentor.lesson(uid)
        if not msg:
            return {"done": True}
        return {"index": users.store(uid)["lesson_index"] - 1, **msg}

    @app.post("/api/check", dependencies=[Depends(csrf)])
    async def check(request: Request, uid: str = Depends(current)):
        body = await request.json()
        try:
            index = int(body.get("index"))
        except (TypeError, ValueError):
            raise HTTPException(400, "Bad topic")
        mentor.set_check(uid, index, bool(body.get("checked")), by="you")
        return {"checked": mentor.checked_count(uid)}

    @app.post("/api/test/weekly", dependencies=[Depends(csrf)])
    async def weekly(uid: str = Depends(current)):
        msg = await mentor.weekly_test(uid, force=True)
        if not msg:
            raise HTTPException(409, "Do your first lesson before taking a test")
        return msg

    @app.post("/api/submit", dependencies=[Depends(csrf)])
    async def submit(request: Request, uid: str = Depends(current)):
        answer = body_str(await request.json(), "answer", 10000)
        if len(answer) < 3:
            raise HTTPException(400, "Write your answer first")
        return await mentor.submit(uid, answer)

    @app.post("/api/skip", dependencies=[Depends(csrf)])
    async def skip(uid: str = Depends(current)):
        return {"skipped": mentor.skip(uid)}

    @app.post("/api/mcq", dependencies=[Depends(csrf)])
    async def new_mcq(request: Request, uid: str = Depends(current)):
        body = await request.json()
        scope = "week" if body.get("scope") == "week" else "recent"
        n = max(3, min(10, int(body.get("n") or 5)))
        return await mentor.mcq(uid, scope, n)

    @app.post("/api/mcq/grade", dependencies=[Depends(csrf)])
    async def grade_mcq(request: Request, uid: str = Depends(current)):
        body = await request.json()
        answers = [a if isinstance(a, int) else None for a in (body.get("answers") or [])][:20]
        return mentor.grade_mcq(uid, str(body.get("id", "")), answers)

    @app.post("/api/revision", dependencies=[Depends(csrf)])
    async def revision(uid: str = Depends(current)):
        msg = await mentor.revision(uid)
        if not msg:
            raise HTTPException(409, "Do a few lessons first - then there's something to revise")
        return msg

    @app.post("/api/news", dependencies=[Depends(csrf)])
    async def news(uid: str = Depends(current)):
        return await mentor.news(uid)

    @app.get("/api/history")
    async def history(uid: str = Depends(current)):
        return {"history": users.store(uid)["history"], "reviews": users.store(uid)["reviews"][-10:]}

    # ---- saved chats (Ask page) ---------------------------------------------------------
    @app.get("/api/chats")
    async def chats_list(uid: str = Depends(current)):
        return {"chats": mentor.list_chats(uid)}

    @app.post("/api/chats", dependencies=[Depends(csrf)])
    async def chats_new(uid: str = Depends(current)):
        c = mentor.new_chat(uid)
        return {"id": c["id"], "title": c["title"], "messages": []}

    @app.get("/api/chats/{cid}")
    async def chats_get(cid: str, uid: str = Depends(current)):
        c = mentor.get_chat(uid, cid)
        if not c:
            raise HTTPException(404, "That chat no longer exists")
        return {"id": c["id"], "title": c["title"], "updated": c["updated"], "messages": c["messages"]}

    @app.delete("/api/chats/{cid}", dependencies=[Depends(csrf)])
    async def chats_delete(cid: str, uid: str = Depends(current)):
        if not mentor.delete_chat(uid, cid):
            raise HTTPException(404, "That chat no longer exists")
        return {"ok": True}

    @app.post("/api/chats/{cid}/message", dependencies=[Depends(csrf)])
    async def chats_message(cid: str, request: Request, uid: str = Depends(current)):
        """Streams the answer as plain text while it's written. A failure is sent as a final line starting with
        the marker below, so the page can show it as an error."""
        from fastapi.responses import StreamingResponse
        msg = body_str(await request.json(), "message", 6000)
        if not msg:
            raise HTTPException(400, "Type a question first")

        async def gen():
            try:
                async for piece in mentor.chat_stream(uid, cid, msg):
                    yield piece
            except MentorError as e:
                yield f"\n\u0000ERROR:{e}"
            except Exception:
                log.exception("Chat stream failed")
                yield "\n\u0000ERROR:Something went wrong. Please ask again."

        return StreamingResponse(gen(), media_type="text/plain; charset=utf-8",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/chat", dependencies=[Depends(csrf)])
    async def chat(request: Request, uid: str = Depends(current)):
        msg = body_str(await request.json(), "message", 6000)
        if not msg:
            raise HTTPException(400, "Empty message")
        return {"reply": await mentor.chat(uid, msg)}

    @app.post("/api/profile", dependencies=[Depends(csrf)])
    async def profile(request: Request, uid: str = Depends(current)):
        body, u = await request.json(), users.get(uid)
        apply_edits(u, body, allow_role=False)
        users.save()
        return {"ok": True}

    @app.post("/api/link-telegram", dependencies=[Depends(csrf)])
    async def link_telegram(uid: str = Depends(current)):
        bot = getattr(telegram, "bot_username", None)
        if not bot:
            raise HTTPException(400, "Telegram isn't set up on this server")
        return {"url": f"https://t.me/{bot}?start={users.new_link(uid)}"}

    # ---- admin --------------------------------------------------------------------
    def apply_edits(u, body, allow_role):
        if "name" in body and body_str(body, "name", 40):
            u["name"] = body_str(body, "name", 40)
        if "reminder" in body:
            t = parse_hhmm(body.get("reminder"))
            if not t:
                raise HTTPException(400, "Time must look like 07:30")
            if t != u["reminder"]:
                from push import reminder_times, set_reminder_times
                times = reminder_times(u)
                set_reminder_times(u, [t] + [x for x in times if x != u["reminder"]])
            u["reminder"] = t
        if "background" in body and body_str(body, "background", 600):
            u["background"] = body_str(body, "background", 600)
        if "paused" in body:
            u["paused"] = bool(body["paused"])
        if allow_role and body.get("role") in ("admin", "member"):
            if body["role"] == "member" and u["role"] == "admin" and len(users.admins()) == 1:
                raise HTTPException(400, "They're the only admin - make someone else admin first")
            u["role"] = body["role"]

    @app.get("/api/admin/users")
    async def admin_users(uid: str = Depends(admin_uid)):
        out = []
        for n, pid, u in users.ordered():
            p = mentor.progress(pid)
            out.append({"uid": pid, "n": n, "name": u["name"], "username": u.get("username"), "role": u["role"],
                        "reminder": u["reminder"], "paused": u.get("paused", False), "background": u["background"],
                        "telegram": bool(u.get("telegram_id")), "joined": u["joined"],
                        "lessons": p["lesson_index"], "average": p["average"], "streak": p["streak"],
                        "weak_areas": p["weak_areas"][:3]})
        return {"users": out, "invites": [{"code": c, **i} for c, i in users.data["invites"].items()],
                "public_url": cfg.PUBLIC_URL if cfg.PUBLIC_URL.startswith("https") else None}

    @app.post("/api/admin/invite", dependencies=[Depends(csrf)])
    async def admin_invite(request: Request, uid: str = Depends(admin_uid)):
        name = body_str(await request.json(), "name", 40)
        code = users.new_invite(name)
        bot = getattr(telegram, "bot_username", None)
        return {"code": code, "web": f"{cfg.PUBLIC_URL}/?invite={code}",
                "telegram": f"https://t.me/{bot}?start={code}" if bot else None}

    @app.delete("/api/admin/invites/{code}", dependencies=[Depends(csrf)])
    async def admin_revoke(code: str, uid: str = Depends(admin_uid)):
        users.data["invites"].pop(code.upper(), None)
        users.save()
        return {"ok": True}

    @app.post("/api/admin/users/{pid}", dependencies=[Depends(csrf)])
    async def admin_edit(pid: str, request: Request, uid: str = Depends(admin_uid)):
        u = users.get(pid)
        if not u:
            raise HTTPException(404, "No such person")
        apply_edits(u, await request.json(), allow_role=True)
        users.save()
        return {"ok": True}

    @app.delete("/api/admin/users/{pid}", dependencies=[Depends(csrf)])
    async def admin_remove(pid: str, uid: str = Depends(admin_uid)):
        u = users.get(pid)
        if not u:
            raise HTTPException(404, "No such person")
        if u["role"] == "admin" and len(users.admins()) == 1:
            raise HTTPException(400, "That's the only admin - make someone else admin first")
        users.remove(pid)
        return {"ok": True}

    return app
