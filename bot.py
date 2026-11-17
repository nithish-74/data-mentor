"""Telegram channel for the mentor. Started by app.py when TELEGRAM_BOT_TOKEN is set."""
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import config as cfg
import curriculum as cur
from ai import MentorError
from mentor import Mentor, today
from tgformat import split_message, to_html
from users import Users

log = logging.getLogger("mentor.telegram")

HELP = """Data Architect Mentor - commands

/lesson - today's lesson now (if none is pending)
/submit <answer> - submit your assignment / test answers for review
/skip - drop the pending task and move on
/test - weekly test now
/quiz - quick 3-question quiz on recent topics
/news - recent data architecture developments
/progress - scores, streak, weak areas
/checklist - all 60 topics with ✅ ticks
/articles - latest articles from data blogs, summarized
/tick <n> or /untick <n> - tick a topic yourself (numbers from /checklist)
/roadmap - the 12-week plan

Your settings: /profile /setname /settime HH:MM /setbackground <text> /pause /resume

Website (same progress + auto-graded tests): {url}
Set your website username with /username <name>, then sign in there with it.

Or just type any question - I answer as your mentor."""

ADMIN_HELP = """Admin - managing people (also on the website's People page)

/invite <name> - one-time invite (7 days) with website + Telegram join links
/invites - open invites;  /revoke <CODE>
/users - everyone with a number
/edituser <n> name|time|background|role <value>   or   /edituser <n> pause|resume
/removeuser <n> - remove (asks to confirm; progress archived, not deleted)"""


async def send(bot, chat_id, md: str):
    for chunk in split_message(md):
        try:
            await bot.send_message(chat_id, to_html(chunk), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except BadRequest:
            await bot.send_message(chat_id, chunk, disable_web_page_preview=True)


def article_lines(items):
    return "\n\n".join(f"**{a['title']}**\n{a['source']} · {a['published']}"
                       f"{' · week ' + str(a['week']) if a.get('week') else ''}\n{a['summary']}\n{a['link']}"
                       for a in items)


def build(mentor: Mentor, users: Users, articles=None) -> Application:
    import re

    def parse_hhmm(s):
        m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", s or "")
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            return None
        return f"{int(m.group(1)):02d}:{m.group(2)}"

    def arg_text(update):
        return (update.message.text or "").partition(" ")[2].strip() if update.message else ""

    async def out(update, msg):
        if msg:
            await send(update.get_bot(), update.effective_chat.id, f"{msg['title']}\n\n{msg['text']}")

    def help_for(uid):
        t = HELP.format(url=cfg.PUBLIC_URL)
        return t + ("\n\n" + ADMIN_HELP if users.is_admin(uid) else "")

    # ---- joining ----------------------------------------------------------
    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        tg_name = (update.effective_user.first_name if update.effective_user else "") or "Learner"
        arg = context.args[0] if context.args else ""
        uid, is_new = users.find(telegram_id=chat.id), False

        if arg.upper().startswith("LINK"):
            linked = users.use_link(arg, chat.id)
            if linked:
                await send(context.bot, chat.id, f"✅ Telegram is now linked to your website account, "
                                                 f"{users.get(linked)['name']}. Same progress in both places.")
                return
            await update.message.reply_text("That link has expired. Open your Profile on the website for a new one.")
            return
        if uid:
            pass
        elif not users.people and (not cfg.ALLOWED_CHAT_ID or str(chat.id) == cfg.ALLOWED_CHAT_ID):
            import users as U
            uid = users.add(tg_name, role="admin", background=U.DEFAULT_BACKGROUND, telegram_id=chat.id)
            is_new = True
        elif (inv := (users.use_invite(arg) if arg else None)) or not cfg.REQUIRE_INVITE:
            uid = users.add((inv or {}).get("name") or tg_name, telegram_id=chat.id)
            is_new = True
            for a in users.admins():
                if users.get(a).get("telegram_id") and a != uid:
                    try:
                        await context.bot.send_message(int(users.get(a)["telegram_id"]),
                                                       f"👋 {users.get(uid)['name']} joined the mentor.")
                    except Exception:
                        pass
        else:
            await update.message.reply_text(
                "This is a private mentor bot. Ask the admin for an invite code, then send: /start CODE")
            return

        s = users.store(uid)
        if is_new:
            s["last_daily"] = today().isoformat()  # first scheduled message is tomorrow morning
            s.save()
        await send(context.bot, chat.id, f"👋 Hi {users.get(uid)['name']}! I'm your Data Architect Mentor.\n\n"
                                         + cur.roadmap_text() + "\n\n" + help_for(uid))
        if is_new:
            await send(context.bot, chat.id, "Tip: tell me about yourself so I can pitch lessons right, e.g.\n"
                                             "/setbackground 3 years SQL + Power BI, learning Databricks\n\n"
                                             "Ready? Send /lesson for lesson 1.")

    # ---- wrappers -----------------------------------------------------------
    def member(fn):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            chat = update.effective_chat
            uid = users.find(telegram_id=chat.id) if chat else None
            if not uid:
                if update.message:
                    await update.message.reply_text("Send /start CODE with your invite code to join.")
                return
            try:
                await context.bot.send_chat_action(chat.id, "typing")
                await fn(update, context, uid)
            except MentorError as e:
                await send(context.bot, chat.id, f"⚠️ {e}")
            except Exception:
                log.exception("Handler %s failed", fn.__name__)
                await send(context.bot, chat.id, "⚠️ Something went wrong. Details are in data/mentor.log.")
        return wrapper

    def admin(fn):
        @member
        async def wrapper(update, context, uid):
            if not users.is_admin(uid):
                await send(context.bot, update.effective_chat.id, "Only an admin can do that.")
                return
            await fn(update, context, uid)
        return wrapper

    async def say(update, md):
        await send(update.get_bot(), update.effective_chat.id, md)

    # ---- learner commands ---------------------------------------------------
    @member
    async def cmd_help(update, context, uid):
        await say(update, help_for(uid))

    @member
    async def cmd_roadmap(update, context, uid):
        nxt = cur.lesson_at(users.store(uid)["lesson_index"])
        where = f"\n\nNext up: Week {nxt[0]}, lesson {nxt[1]} - {nxt[3]}" if nxt else "\n\nAll lessons done 🎓"
        await say(update, cur.roadmap_text() + where)

    @member
    async def cmd_lesson(update, context, uid):
        c = users.store(uid)["current"]
        if c and c["kind"] == "lesson":
            await say(update, f"You still have an open assignment on **{c['topic']}**.\n\n{c['assignment']}\n\n"
                              f"Send /submit <answer>, or /skip to move on.")
            return
        msg = await mentor.lesson(uid)
        await out(update, msg or {"title": "🎓 Course complete", "text": "You've finished all 60 lessons!"})

    @member
    async def cmd_test(update, context, uid):
        msg = await mentor.weekly_test(uid, force=True)
        await out(update, msg or {"title": "No test yet", "text": "Do your first lesson with /lesson first."})

    @member
    async def cmd_articles(update, context, uid):
        if not articles or not articles.visible():
            return await say(update, "No articles yet. New ones are collected every few hours.")
        await say(update, "📰 Latest articles\n\n" + article_lines(articles.list(limit=5)) +
                          f"\n\nMore, with filters by week and source: {cfg.PUBLIC_URL}/#articles")

    @member
    async def cmd_checklist(update, context, uid):
        p = mentor.progress(uid)
        lines = [f"☑️ Progress checklist: {p['checked']}/{p['total']} topics done",
                 f"I tick a topic when you pass its assignment ({p['pass_assignment']}+/10) or the week test "
                 f"({p['pass_week']}+/10). You can tick topics yourself with /tick <n>.", ""]
        for m in p["modules"]:
            done = sum(1 for l in m["lessons"] if l["checked"])
            lines.append(f"**Week {m['week']} · {m['title']}** ({done}/5)")
            for l in m["lessons"]:
                c = l["checked"]
                mark = "✅" if c else "⬜"
                who = f" (by {'mentor' if c['by'] == 'mentor' else 'you'})" if c else ""
                lines.append(f"{mark} {l['index'] + 1}. {l['topic'][:70]}{who}")
        await say(update, "\n".join(lines))

    async def _tick(update, uid, checked):
        try:
            n = int(arg_text(update))
        except ValueError:
            return await say(update, f"Usage: /{'tick' if checked else 'untick'} 7   (topic numbers 1-60 from /checklist)")
        mentor.set_check(uid, n - 1, checked, by="you")
        found = cur.lesson_at(n - 1)
        await say(update, f"{'✅ Ticked' if checked else '⬜ Unticked'}: {found[3]}\n"
                          f"{mentor.checked_count(uid)}/{cur.TOTAL_LESSONS} topics done.")

    @member
    async def cmd_tick(update, context, uid):
        await _tick(update, uid, True)

    @member
    async def cmd_untick(update, context, uid):
        await _tick(update, uid, False)

    @member
    async def cmd_submit(update, context, uid):
        answer = arg_text(update)
        if not answer and update.message.reply_to_message and update.message.reply_to_message.text:
            answer = update.message.reply_to_message.text.strip()
        if not answer:
            await say(update, "Send /submit followed by your answer (in the same message - multiple lines are fine).")
            return
        r = await mentor.submit(uid, answer)
        head = f"✅ Score: {r['score']}/10 · streak {r['streak']} day(s)\n\n" if r["score"] is not None else ""
        tail = ("\n\nWeak areas noted: " + ", ".join(r["weak_areas"])) if r["weak_areas"] else ""
        if r["ticked"]:
            tail += f"\n\n✅ I ticked this topic on your checklist ({mentor.checked_count(uid)}/{cur.TOTAL_LESSONS})."
        elif r["kind"] == "lesson" and r["score"] is not None:
            tail += (f"\n\n⬜ Not ticked yet - {r['pass_mark']}/10 needed. Revise and try the quiz, "
                     f"or tick it yourself with /tick when you're confident.")
        await say(update, head + r["feedback"] + tail +
                  f"\n\nNext lesson arrives tomorrow at {users.get(uid)['reminder']} - or send /lesson now.")

    @member
    async def cmd_skip(update, context, uid):
        c = mentor.skip(uid)
        await say(update, f"Skipped: {c['topic']}. Send /lesson for the next one." if c else "Nothing pending.")

    @member
    async def cmd_progress(update, context, uid):
        p = mentor.progress(uid)
        lines = [f"☑️ Topics done: {p['checked']}/{p['total']} (see /checklist)",
                 f"📊 Lessons taught: {p['lesson_index']}/{p['total']}", f"🔥 Streak: {p['streak']} day(s)",
                 f"Average score: {p['average']}/10" if p["average"] is not None else "No scores yet",
                 "Weak areas: " + (", ".join(p["weak_areas"]) or "none yet"),
                 f"Pending: {p['current']['topic']}" if p["current"] else "Pending: nothing",
                 f"AI requests left today: {p['ai_left']}"]
        await say(update, "\n".join(lines))

    @member
    async def cmd_quiz(update, context, uid):
        await out(update, await mentor.quiz_text(uid))

    @member
    async def cmd_news(update, context, uid):
        await out(update, await mentor.news(uid))

    @member
    async def on_text(update, context, uid):
        await say(update, await mentor.chat(uid, update.message.text or ""))

    # ---- self-service settings ------------------------------------------------
    def profile_text(u):
        return (f"👤 {u['name']} ({u['role']})\nReminder: {u['reminder']} {cfg.TZ.key}"
                f"{' - PAUSED' if u.get('paused') else ''}\nBackground: {u['background']}\n"
                f"Website username: {u.get('username') or 'not set (use /username)'}\nJoined: {u['joined']}")

    @member
    async def cmd_profile(update, context, uid):
        await say(update, profile_text(users.get(uid)))

    @member
    async def cmd_setname(update, context, uid):
        name = arg_text(update)[:40]
        if not name:
            return await say(update, "Usage: /setname Your Name")
        users.get(uid)["name"] = name
        users.save()
        await say(update, f"Done - I'll call you {name}.")

    @member
    async def cmd_settime(update, context, uid):
        t = parse_hhmm(arg_text(update))
        if not t:
            return await say(update, "Usage: /settime 07:30  (24-hour clock)")
        from push import reminder_times, set_reminder_times
        u = users.get(uid)
        set_reminder_times(u, [t] + [x for x in reminder_times(u) if x != u["reminder"]])
        u["reminder"] = t
        users.save()
        await say(update, f"Done - daily messages at {t} ({cfg.TZ.key}).")

    @member
    async def cmd_setbackground(update, context, uid):
        bg = arg_text(update)[:600]
        if not bg:
            return await say(update, "Usage: /setbackground 3 years SQL and ADF, learning Databricks")
        users.get(uid)["background"] = bg
        users.save()
        await say(update, "Saved. Lessons and reviews will be pitched to that.")

    @member
    async def cmd_username(update, context, uid):
        u = users.get(uid)
        name = users.valid_username(arg_text(update))
        if not name:
            cur_name = f"Your website username is {u['username']}. " if u.get("username") else ""
            return await say(update, cur_name + "Usage: /username ravi_k  (3-30 letters, numbers, . - _)\n"
                                                "Then sign in on the website with it to see the same progress.")
        other = users.find(username=name)
        if other and other != uid:
            return await say(update, "That username is taken - try another.")
        u["username"] = name
        users.save()
        await say(update, f"✅ Website username set: {name}\nSign in at {cfg.PUBLIC_URL} with it - same progress there.")

    @member
    async def cmd_pause(update, context, uid):
        users.get(uid)["paused"] = True
        users.save()
        await say(update, "Paused - no daily messages until you send /resume.")

    @member
    async def cmd_resume(update, context, uid):
        users.get(uid)["paused"] = False
        users.save()
        await say(update, f"Resumed - see you at {users.get(uid)['reminder']}.")

    # ---- admin --------------------------------------------------------------------
    @admin
    async def cmd_invite(update, context, uid):
        name = arg_text(update)
        code = users.new_invite(name)
        await say(update, f"Invite{' for ' + name if name else ''} created (one use, valid 7 days).\n\n"
                          f"Website: {cfg.PUBLIC_URL}/?invite={code}\n"
                          f"Telegram: https://t.me/{context.bot.username}?start={code}\n\nCode: {code}")

    @admin
    async def cmd_invites(update, context, uid):
        inv = users.data["invites"]
        await say(update, "Open invites:\n" + "\n".join(f"{c} - {i['name'] or '(no name)'} - expires {i['expires']}"
                                                        for c, i in inv.items()) if inv else "No open invites.")

    @admin
    async def cmd_revoke(update, context, uid):
        code = arg_text(update).upper()
        if users.data["invites"].pop(code, None):
            users.save()
            return await say(update, f"Invite {code} cancelled.")
        await say(update, "No such invite. See /invites.")

    @admin
    async def cmd_users(update, context, uid):
        lines = ["People (use the number with /edituser and /removeuser):"]
        for n, pid, u in users.ordered():
            p = mentor.progress(pid)
            lines.append(f"{n}. {u['name']} ({u['role']}){' PAUSED' if u.get('paused') else ''} - lesson "
                         f"{p['lesson_index']}/{p['total']}, avg {p['average'] if p['average'] is not None else '-'}, "
                         f"streak {p['streak']}, {'@' + u['username'] if u.get('username') else ''}"
                         f"{' + ' if u.get('username') and u.get('telegram_id') else ''}"
                         f"{'telegram' if u.get('telegram_id') else ''}")
        await say(update, "\n".join(lines))

    def find_person(arg):
        try:
            return users.by_number(int(arg))
        except ValueError:
            return None, None

    @admin
    async def cmd_edituser(update, context, uid):
        parts = arg_text(update).split(None, 2)
        usage = "Usage: /edituser <n> name|time|background|role <value>, or /edituser <n> pause|resume (see /users)"
        if len(parts) < 2:
            return await say(update, usage)
        pid, u = find_person(parts[0])
        if not u:
            return await say(update, "No person with that number. See /users.")
        field, value = parts[1].lower(), (parts[2].strip() if len(parts) > 2 else "")
        if field == "name" and value:
            u["name"] = value[:40]
        elif field == "time" and parse_hhmm(value):
            u["reminder"] = parse_hhmm(value)
        elif field == "background" and value:
            u["background"] = value[:600]
        elif field == "role" and value in ("admin", "member"):
            if value == "member" and u["role"] == "admin" and len(users.admins()) == 1:
                return await say(update, "They're the only admin - make someone else admin first.")
            u["role"] = value
        elif field in ("pause", "resume"):
            u["paused"] = field == "pause"
        else:
            return await say(update, usage)
        users.save()
        await say(update, "Updated:\n" + profile_text(u))

    @admin
    async def cmd_removeuser(update, context, uid):
        parts = arg_text(update).split()
        if not parts:
            return await say(update, "Usage: /removeuser <n>   (see numbers with /users)")
        pid, u = find_person(parts[0])
        if not u:
            return await say(update, "No person with that number. See /users.")
        if u["role"] == "admin" and len(users.admins()) == 1:
            return await say(update, "That's the only admin - make someone else admin first.")
        if len(parts) < 2 or parts[1].lower() != "yes":
            return await say(update, f"Remove {u['name']}? They lose access and can only rejoin with a new invite. "
                                     f"Progress is archived in data/removed/.\n\nTo confirm: /removeuser {parts[0]} yes")
        name, tg = u["name"], u.get("telegram_id")
        users.remove(pid)
        await say(update, f"Removed {name}.")
        if tg and tg != str(update.effective_chat.id):
            try:
                await context.bot.send_message(int(tg), "You've been removed from the Data Architect Mentor.")
            except Exception:
                pass

    app = Application.builder().token(cfg.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    commands = {
        "help": cmd_help, "roadmap": cmd_roadmap, "lesson": cmd_lesson, "submit": cmd_submit, "skip": cmd_skip,
        "test": cmd_test, "progress": cmd_progress, "quiz": cmd_quiz, "news": cmd_news,
        "checklist": cmd_checklist, "tick": cmd_tick, "untick": cmd_untick, "articles": cmd_articles,
        "profile": cmd_profile, "setname": cmd_setname, "settime": cmd_settime, "setbackground": cmd_setbackground,
        "username": cmd_username, "pause": cmd_pause, "resume": cmd_resume, "invite": cmd_invite, "invites": cmd_invites,
        "revoke": cmd_revoke, "users": cmd_users, "edituser": cmd_edituser, "removeuser": cmd_removeuser,
    }
    for name, fn in commands.items():
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.bot_data["handlers"] = {"start": cmd_start, **commands, "text": on_text}  # used by tests
    return app


async def deliver_daily(app: Application, mentor: Mentor, users: Users, uid, articles=None):
    """Send one person's morning messages to Telegram. Pauses them if they blocked the bot."""
    u = users.get(uid)
    msgs = await mentor.daily(uid)
    if msgs and articles:  # once a day, with the morning message: up to 3 articles they haven't had yet
        from datetime import datetime, timezone
        s = users.store(uid)
        since = s.data.get("articles_digest_at") or ""
        new = [a for a in articles.visible() if a["added"] > since][:3]
        s["articles_digest_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        s.save()
        if new:
            msgs.append({"title": "📰 New articles worth a read", "text": article_lines(new)})
    for m in msgs:
        try:
            await send(app.bot, int(u["telegram_id"]), f"{m['title']}\n\n{m['text']}")
        except Forbidden:
            u["paused"] = True
            users.save()
            log.info("%s blocked the bot; paused", uid)
            return
