"""Web push notifications (works for the installed app and in Chrome/Edge/Firefox/Safari).

Keys are created once in data/ (vapid_private.pem + vapid_public.txt). Each person can have a few
devices; their subscriptions live in their progress file under "push_subs".
"""
import asyncio
import base64
import json
import logging

from starlette.concurrency import run_in_threadpool

import config as cfg

log = logging.getLogger("mentor.push")
DEFAULT_PREFS = {"daily": True, "nudge": True, "articles": True, "sound": True, "vibrate": True}
MAX_REMINDERS = 6
EVENING = "20:00"


def prefs(user):
    return {**DEFAULT_PREFS, **(user.get("notify") or {})}


def reminder_times(user):
    """The learner's study reminder times (HH:MM, sorted). Default: their reminder time + an evening one."""
    times = user.get("reminders") or [user.get("reminder", "07:00"), EVENING]
    return sorted(set(times))[:MAX_REMINDERS]


def set_reminder_times(user, times):
    import re
    clean = []
    for t in times or []:
        m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", str(t))
        if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
            clean.append(f"{int(m.group(1)):02d}:{m.group(2)}")
    clean = sorted(set(clean))[:MAX_REMINDERS]
    if not clean:
        raise ValueError("Keep at least one reminder time.")
    user["reminders"] = clean
    user["reminder"] = clean[0]  # Telegram's morning message uses the first one
    return clean


def due_slots(users, uid, channel, now):
    """Reminder times that have passed today and haven't been handled yet on this channel."""
    s = users.store(uid)
    key = f"{channel}_slots"
    rec = s.data.get(key) or {}
    if rec.get("date") != now.date().isoformat():
        rec = {"date": now.date().isoformat(), "done": []}
    hhmm = now.strftime("%H:%M")
    due = [t for t in reminder_times(users.get(uid)) if t <= hhmm and t not in rec["done"]]
    return due, rec, key


def mark_slots(users, uid, key, rec, slots):
    s = users.store(uid)
    rec["done"] = sorted(set(rec["done"]) | set(slots))
    s[key] = rec
    s.save()


def slot_messages(users, articles, uid, channel, today, slot):
    """What a reminder at `slot` says. The first time of the day carries the daily message (lesson, nudge,
    weekend test, new articles); later ones are a short 'time to learn' that fits what the learner has done."""
    import curriculum as cur
    u, s = users.get(uid), users.store(uid)
    times = reminder_times(u)
    if slot == times[0] or slot not in times:
        msgs = daily_messages(users, articles, uid, channel, today)
        if msgs:
            return msgs
    p = prefs(u)
    if not (p["daily"] or p["nudge"]):
        return []
    c = s["current"]
    today_iso = today.isoformat()
    studied = any(x["date"] == today_iso for x in s["scores"]) or \
        any(v.get("date") == today_iso for v in s["checked"].values())
    if c and p["nudge"]:
        return [{"title": "Time to learn", "body": f"Your answer on {c['topic'][:80]} is still waiting.",
                 "url": "/#home", "tag": "study"}]
    if studied:
        return [{"title": "Nice work today", "body": "Two minutes: a quick test keeps it fresh.",
                 "url": "/#tests", "tag": "study"}]
    nxt = cur.lesson_at(s["lesson_index"])
    if nxt:
        return [{"title": "Time to learn", "body": f"20 minutes now: {nxt[3][:100]}", "url": "/#learn/new",
                 "tag": "study"}]
    return [{"title": "Time to learn", "body": "A quick test or an article keeps you sharp.", "url": "/#tests",
             "tag": "study"}]


def daily_messages(users, articles, uid, channel, today, force=False):
    """The day's notifications for one person, at most once a day per channel ("push" = web push,
    "app" = the Android app, which asks the server at the reminder time). Returns [{title, body, url, tag}]."""
    from datetime import date
    import curriculum as cur
    u, s = users.get(uid), users.store(uid)
    day_key, art_key = f"{channel}_daily", f"{channel}_articles_at"
    if s.data.get(day_key) == today.isoformat() and not force:
        return []
    s[day_key] = today.isoformat()
    p, c, wd, out = prefs(u), s["current"], today.weekday(), []
    if c and p["nudge"]:
        days = (today - date.fromisoformat(c["sent_on"])).days
        out.append({"title": "Your answer is waiting",
                    "body": f"{c['topic'][:90]}. {'Sent ' + str(days) + ' days ago. ' if days > 1 else ''}"
                            f"Ten minutes is enough to send it.", "url": "/#home", "tag": "daily"})
    elif p["daily"]:
        nxt = cur.lesson_at(s["lesson_index"])
        if wd == 5 and s["lesson_index"]:
            out.append({"title": "Saturday test", "body": "Five minutes: check what stuck from this week.",
                        "url": "/#tests", "tag": "daily"})
        elif wd == 6 and s["lesson_index"]:
            out.append({"title": "Sunday revision", "body": "A short look back at the topics that need another pass.",
                        "url": "/#home", "tag": "daily"})
        elif nxt:
            out.append({"title": "Today's lesson is ready", "body": f"Week {nxt[0]}: {nxt[3][:110]}",
                        "url": "/#learn/new", "tag": "daily"})
    if p["articles"] and articles:
        since = s.data.get(art_key) or ""
        new = [a for a in articles.visible() if a["added"] > since]
        if new:
            s[art_key] = max(a["added"] for a in new)
            names = ", ".join(sorted({a["source"] for a in new[:6]}))
            out.append({"title": f"{len(new)} new article{'s' if len(new) > 1 else ''} to read",
                        "body": f"{new[0]['title'][:90]} · from {names}", "url": "/#articles", "tag": "articles"})
    s.save()
    return out


class Push:
    def __init__(self, data_dir, users):
        """The push keys live in the app's storage (database or files) so every device stays subscribed after a
        move or restart; a local copy of the private key is written for the sending library."""
        from docstore import for_dir
        docs = for_dir(data_dir)
        self.users = users
        self.pem = data_dir / "vapid_private.pem"
        if not docs.exists("vapid_private.pem"):
            from cryptography.hazmat.primitives import serialization
            from py_vapid import Vapid
            v = Vapid()
            v.generate_keys()
            v.save_key(str(self.pem))
            raw = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
            docs.put_text("vapid_private.pem", self.pem.read_text(encoding="ascii"))
            docs.put_text("vapid_public.txt", base64.urlsafe_b64encode(raw).decode().rstrip("="))
            log.info("Created push keys")
        data_dir.mkdir(parents=True, exist_ok=True)
        self.pem.write_text(docs.get_text("vapid_private.pem"), encoding="ascii")
        self.public_key = docs.get_text("vapid_public.txt").strip()

    # ---- subscriptions ---------------------------------------------------------
    def subs(self, uid):
        return self.users.store(uid).data.setdefault("push_subs", [])

    def subscribe(self, uid, sub: dict):
        if not isinstance(sub, dict) or not str(sub.get("endpoint", "")).startswith("https://") \
                or not isinstance(sub.get("keys"), dict):
            raise ValueError("That browser sent an incomplete subscription. Try again.")
        s = self.users.store(uid)
        subs = [x for x in self.subs(uid) if x["endpoint"] != sub["endpoint"]]
        subs.append({"endpoint": sub["endpoint"], "keys": {k: str(v) for k, v in sub["keys"].items()}})
        s["push_subs"] = subs[-5:]  # keep the 5 most recent devices
        s.save()

    def unsubscribe(self, uid, endpoint):
        s = self.users.store(uid)
        s["push_subs"] = [x for x in self.subs(uid) if x["endpoint"] != endpoint]
        s.save()

    # ---- sending -----------------------------------------------------------------
    def _send_one(self, sub, payload):
        from pywebpush import WebPushException, webpush
        contact = cfg.PUBLIC_URL if cfg.PUBLIC_URL.startswith("https") else "mailto:mentor@example.com"
        try:
            webpush(sub, data=payload, vapid_private_key=str(self.pem), vapid_claims={"sub": contact}, ttl=12 * 3600)
            return "ok"
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            return "gone" if status in (404, 410) else f"error {status}"
        except Exception as e:
            return f"error {type(e).__name__}"

    async def send(self, uid, title, body, url="/#home", tag="mentor") -> int:
        """Send to every device this person has. Returns how many devices accepted it."""
        payload = json.dumps({"title": title, "body": body[:300], "url": url, "tag": tag})
        subs = list(self.subs(uid))
        if not subs:
            return 0
        results = await asyncio.gather(*[run_in_threadpool(self._send_one, s, payload) for s in subs])
        gone = {s["endpoint"] for s, r in zip(subs, results) if r == "gone"}
        if gone:
            s = self.users.store(uid)
            s["push_subs"] = [x for x in self.subs(uid) if x["endpoint"] not in gone]
            s.save()
        bad = [r for r in results if r.startswith("error")]
        if bad:
            log.warning("Push to %s: %d device(s) failed (%s)", uid, len(bad), bad[0])
        return sum(r == "ok" for r in results)
