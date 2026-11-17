"""The learning engine shared by the website and the Telegram bot.

Every function works on a person id (uid) and returns plain text / dicts; channels decide how to show them.
"""
import logging
import random
import re
from datetime import date, datetime

import config as cfg
import curriculum as cur
import prompts as P
from ai import AI, MentorError
from users import Users

log = logging.getLogger("mentor")


def today() -> date:
    return datetime.now(cfg.TZ).date()


def extract_assignment(lesson: str) -> str:
    m = re.search(r"(?is)\*{0,2}\s*(?:\d+\.\s*)?assignment\s*\*{0,2}\s*[:\-]?(.*)$", lesson)
    return (m.group(1) if m else lesson[-1200:]).strip()[:2500]


class Mentor:
    def __init__(self, ai: AI, users: Users):
        self.ai = ai
        self.users = users

    # ---- plumbing ----------------------------------------------------------
    def system(self, uid):
        u = self.users.get(uid)
        return P.MENTOR_SYSTEM.format(name=u["name"], background=u["background"])

    def _budget(self, uid):
        d = today().isoformat()
        g = self.__dict__.setdefault("_global", {"date": d, "count": 0})
        if g["date"] != d:
            g["date"], g["count"] = d, 0
        if g["count"] >= cfg.GLOBAL_DAILY_AI_LIMIT:
            raise MentorError("The mentor has reached today's limit for the whole site. Please come back tomorrow.")
        g["count"] += 1
        s = self.users.store(uid)
        if not s.use_ai(today(), cfg.DAILY_AI_LIMIT):
            raise MentorError(f"You've reached today's limit of {cfg.DAILY_AI_LIMIT} AI requests. "
                              "It resets tomorrow - great work today!")
        s.save()

    async def ask(self, uid, prompt, **kw):
        self._budget(uid)
        return await self.ai.ask(prompt, self.system(uid), **kw)

    async def ask_json(self, uid, prompt, **kw):
        self._budget(uid)
        return await self.ai.ask_json(prompt, self.system(uid), **kw)

    def set_current(self, uid, kind, topic, assignment, week=None, index=None):
        s = self.users.store(uid)
        s["current"] = {"kind": kind, "topic": topic, "assignment": assignment[:3000],
                        "sent_on": today().isoformat(), "week": week, "index": index}
        s.save()

    def remember(self, uid, prompt_summary, answer):
        s = self.users.store(uid)
        s.add_turn("user", prompt_summary)
        s.add_turn("model", answer)
        s.save()

    def recent_topics(self, uid, n=5):
        i = self.users.store(uid)["lesson_index"]
        return [cur.lesson_at(k)[3] for k in range(max(0, i - n), i)]

    # ---- progress checklist ------------------------------------------------------
    PASS_ASSIGNMENT = 6   # mentor ticks a topic when its assignment scores at least this /10
    PASS_WEEK_TEST = 7    # ...or when the week's multiple-choice test scores at least this /10

    def set_check(self, uid, index: int, checked: bool, by: str = "you", note: str = ""):
        """Tick / untick one topic (lesson index 0-59). by = 'you' or 'mentor'."""
        if not cur.lesson_at(index):
            raise MentorError("No such topic.")
        s = self.users.store(uid)
        if checked:
            s["checked"][str(index)] = {"by": by, "date": today().isoformat(), "note": note[:80]}
        else:
            s["checked"].pop(str(index), None)
        s.save()

    def checked_count(self, uid):
        return len(self.users.store(uid)["checked"])

    # ---- learning ------------------------------------------------------------
    async def lesson(self, uid):
        """Teach the next lesson. Returns None when the course is finished."""
        s = self.users.store(uid)
        found = cur.lesson_at(s["lesson_index"])
        if not found:
            return None
        week, day, title, topic = found
        text = await self.ask(uid, P.LESSON_TASK.format(week=week, week_title=title, day=day, topic=topic,
                                                        progress=s.summary()), search=cfg.SEARCH_IN_LESSONS)
        index = s["lesson_index"]
        s["lessons"][str(index)] = text
        s["lesson_index"] += 1
        s.save()
        self.set_current(uid, "lesson", topic, extract_assignment(text), week, index=index)
        self.remember(uid, f"Teach lesson: {topic}", text)
        return {"title": f"📘 Week {week} · Lesson {day}/5 · {topic}", "text": text}

    async def weekly_test(self, uid, force=False):
        """Weekly assessment, or architecture challenge in weeks 4/8/12. None if nothing to test."""
        s = self.users.store(uid)
        if s["lesson_index"] == 0:
            return None
        week = cur.week_of(s["lesson_index"] - 1)
        if week in s["assessed_weeks"] and not force:
            return None
        if week in cur.CHALLENGE_WEEKS:
            first = week - 3
            topics = "\n".join(f"- {t}" for w in cur.WEEKS[first - 1:week] for t in w["lessons"])
            text = await self.ask(uid, P.CHALLENGE.format(first=first, last=week, topics=topics,
                                                          progress=s.summary()), for_grading=True)
            title, kind, topic = (f"🏗️ Architecture challenge · weeks {first}-{week}", "challenge",
                                  f"Architecture challenge weeks {first}-{week}")
        else:
            w = cur.WEEKS[week - 1]
            topics = "\n".join(f"- {t}" for t in w["lessons"])
            text = await self.ask(uid, P.WEEKLY_ASSESSMENT.format(week=week, week_title=w["title"], topics=topics,
                                                                  progress=s.summary()))
            title, kind, topic = (f"📝 Weekly assessment · Week {week}", "assessment",
                                  f"Week {week} assessment: {w['title']}")
        self.set_current(uid, kind, topic, text, week)
        if week not in s["assessed_weeks"]:
            s["assessed_weeks"].append(week)
        s.save()
        self.remember(uid, f"Weekly test for week {week}", text)
        return {"title": title, "text": text}

    async def submit(self, uid, answer: str):
        s = self.users.store(uid)
        c = s["current"] or {"kind": "free", "topic": "general", "assignment": "(none - review my design)"}
        prompt = P.REVIEW_TASK.format(topic=c["topic"], assignment=c["assignment"], answer=answer[:8000])
        r = await self.ask_json(uid, prompt, for_grading=True)
        score = r.get("score")
        score = int(score) if isinstance(score, (int, float)) or str(score).isdigit() else None
        feedback = str(r.get("feedback") or r.get("_raw") or "").strip() or "(no feedback returned)"
        weak = [str(w) for w in (r.get("weak_areas") or []) if w][:5]
        s.record_score(c["kind"], c["topic"], score, weak, today())
        s["reviews"] = (s["reviews"] + [{"date": today().isoformat(), "kind": c["kind"], "topic": c["topic"],
                                         "answer": answer[:4000], "feedback": feedback, "score": score}])[-30:]
        s["current"] = None
        s.save()
        ticked = []
        if c["kind"] == "lesson" and c.get("index") is not None and score is not None \
                and score >= self.PASS_ASSIGNMENT:
            self.set_check(uid, c["index"], True, by="mentor", note=f"assignment {score}/10")
            ticked.append(c["topic"])
        self.remember(uid, f"My answer to {c['topic']}: {answer[:1500]}", feedback)
        return {"score": score, "feedback": feedback, "weak_areas": weak, "topic": c["topic"],
                "streak": s["streak"], "ticked": ticked, "pass_mark": self.PASS_ASSIGNMENT,
                "kind": c["kind"]}

    def skip(self, uid):
        s = self.users.store(uid)
        c, s["current"] = s["current"], None
        s.save()
        return c

    # ---- multiple-choice tests (auto-graded) ---------------------------------
    async def mcq(self, uid, scope="recent", n=5):
        s = self.users.store(uid)
        week = None
        if scope == "week" and s["lesson_index"]:
            week = cur.week_of(s["lesson_index"] - 1)
            topics = cur.WEEKS[week - 1]["lessons"]
            title = f"Week {week} test"
        else:
            topics = self.recent_topics(uid) or [cur.WEEKS[0]["lessons"][0]]
            title = "Quick check"
        r = await self.ask_json(uid, P.MCQ_TASK.format(n=n, topics="; ".join(topics),
                                                       weak=", ".join(s.top_weak()) or "none yet"))
        qs = []
        for q in r.get("questions") or []:
            opts = q.get("options") or []
            ans = q.get("answer")
            if isinstance(q.get("q"), str) and len(opts) == 4 and isinstance(ans, int) and 0 <= ans < 4:
                qs.append({"q": q["q"], "options": [str(o) for o in opts], "answer": ans,
                           "explain": str(q.get("explain", ""))})
        if len(qs) < 3:
            raise MentorError("Couldn't build a good test this time - please try again.")
        test_id = f"{random.randrange(16**6):06x}"
        s["mcq"] = {"id": test_id, "title": title, "topics": topics, "questions": qs[:n],
                    "created": today().isoformat(), "week": week}
        s.save()
        return self.mcq_public(uid)

    def mcq_public(self, uid):
        """The open test without answers (safe to send to the browser)."""
        m = self.users.store(uid)["mcq"]
        if not m:
            return None
        return {"id": m["id"], "title": m["title"],
                "questions": [{"q": q["q"], "options": q["options"]} for q in m["questions"]]}

    def grade_mcq(self, uid, test_id, answers: list):
        s = self.users.store(uid)
        m = s["mcq"]
        if not m or m["id"] != test_id:
            raise MentorError("This test has expired. Start a new one.")
        results, correct = [], 0
        for i, q in enumerate(m["questions"]):
            given = answers[i] if i < len(answers) else None
            ok = given == q["answer"]
            correct += ok
            results.append({"q": q["q"], "options": q["options"], "given": given, "answer": q["answer"],
                            "correct": ok, "explain": q["explain"]})
        total = len(m["questions"])
        score10 = round(10 * correct / total)
        weak = [m["topics"][0][:60]] if score10 < 6 and m["topics"] else []
        s.record_score("mcq", m["title"], score10, weak, today())
        s["mcq"] = None
        s.save()
        ticked = []
        if m.get("week") and score10 >= self.PASS_WEEK_TEST:  # passing a week test ticks that week's taught topics
            first = sum(len(w["lessons"]) for w in cur.WEEKS[:m["week"] - 1])
            for i in range(first, min(first + len(cur.WEEKS[m["week"] - 1]["lessons"]), s["lesson_index"])):
                if str(i) not in s["checked"]:
                    self.set_check(uid, i, True, by="mentor", note=f"week test {score10}/10")
                    ticked.append(cur.lesson_at(i)[3])
        return {"correct": correct, "total": total, "score": score10, "results": results, "streak": s["streak"],
                "ticked": ticked}

    # ---- other ---------------------------------------------------------------
    async def revision(self, uid):
        recent = self.recent_topics(uid)
        if not recent:
            return None
        weak = ", ".join(self.users.store(uid).top_weak()) or "none recorded yet - pick from recent lessons"
        text = await self.ask(uid, P.REVISION.format(weak=weak, recent="; ".join(recent)))
        self.remember(uid, "Revision", text)
        return {"title": "🔁 Revision", "text": text}

    async def nudge(self, uid):
        c = self.users.store(uid)["current"]
        days = max(1, (today() - date.fromisoformat(c["sent_on"])).days)
        text = await self.ask(uid, P.NUDGE.format(topic=c["topic"], days=days))
        return {"title": "⏰ Good morning!", "text": text}

    async def quiz_text(self, uid):
        topics = "; ".join(self.recent_topics(uid)) or "data modelling fundamentals"
        text = await self.ask(uid, f"Give me a quick 3-question quiz on: {topics}. Weak areas: "
                                   f"{', '.join(self.users.store(uid).top_weak()) or 'none'}. Put answers at the "
                                   f"end under **Answers** after a line of dashes.")
        return {"title": "🧠 Quick quiz", "text": text}

    async def news(self, uid):
        text = await self.ask(uid, P.NEWS_TASK, search=True)
        self.remember(uid, "Latest data architecture news", text)
        return {"title": "📰 What's new", "text": text}

    async def chat(self, uid, msg: str):
        s = self.users.store(uid)
        c = s["current"]
        ctx = (f"\n\n(Context: I have a pending task '{c['topic']}'. If this message looks like my answer to it, "
               f"review it briefly and remind me to submit it so it gets scored.)") if c else ""
        text = await self.ask(uid, msg[:6000] + ctx, history=s["history"], search=cfg.SEARCH_IN_LESSONS)
        self.remember(uid, msg[:2000], text)
        return text

    # ---- saved chats (Ask page) ---------------------------------------------------------
    MAX_CHATS, MAX_MESSAGES, CONTEXT_MESSAGES = 60, 200, 12

    def _chats(self, uid):
        return self.users.store(uid).data.setdefault("chats", [])

    def list_chats(self, uid):
        chats = sorted(self._chats(uid), key=lambda c: c["updated"], reverse=True)
        return [{"id": c["id"], "title": c["title"], "updated": c["updated"], "count": len(c["messages"])}
                for c in chats]

    def get_chat(self, uid, cid):
        return next((c for c in self._chats(uid) if c["id"] == cid), None)

    def new_chat(self, uid):
        now = datetime.now(cfg.TZ).isoformat(timespec="microseconds")
        chat = {"id": f"{random.randrange(16**8):08x}", "title": "New chat", "created": now, "updated": now,
                "messages": []}
        chats = self._chats(uid)
        chats.append(chat)
        if len(chats) > self.MAX_CHATS:  # keep the most recent ones
            chats.sort(key=lambda c: c["updated"])
            del chats[: len(chats) - self.MAX_CHATS]
        self.users.store(uid).save()
        return chat

    def delete_chat(self, uid, cid):
        s = self.users.store(uid)
        before = len(self._chats(uid))
        s.data["chats"] = [c for c in self._chats(uid) if c["id"] != cid]
        s.save()
        return len(s.data["chats"]) != before

    async def chat_stream(self, uid, cid, msg: str):
        """Answer in a saved chat, yielding the reply piece by piece; the exchange is saved when it completes."""
        chat = self.get_chat(uid, cid) or self.new_chat(uid)
        self._budget(uid)
        s = self.users.store(uid)
        c = s["current"]
        ctx = (f"\n\n(Context: I have a pending task '{c['topic']}'. If this message looks like my answer to it, "
               f"review it briefly and remind me to submit it so it gets scored.)") if c else ""
        history = [{"role": m["role"], "text": m["text"][:1500]} for m in chat["messages"][-self.CONTEXT_MESSAGES:]]
        reply = ""
        async for piece in self.ai.stream(msg[:6000] + ctx, self.system(uid), history=history):
            reply += piece
            yield piece
        now = datetime.now(cfg.TZ).isoformat(timespec="microseconds")
        if not chat["messages"]:
            chat["title"] = (msg.strip().splitlines() or ["Chat"])[0][:70]
        chat["messages"] += [{"role": "user", "text": msg[:6000], "at": now},
                             {"role": "model", "text": reply, "at": now}]
        chat["messages"] = chat["messages"][-self.MAX_MESSAGES:]
        chat["updated"] = now
        s.save()

    async def daily(self, uid):
        """The morning routine; returns messages to deliver (at most once per day per person)."""
        s = self.users.store(uid)
        d = today()
        if s["last_daily"] == d.isoformat():
            return []
        s["last_daily"] = d.isoformat()
        s.save()
        log.info("Daily run for %s on %s (weekday %d)", uid, d, d.weekday())
        try:
            wd = d.weekday()  # Mon=0 ... Sun=6
            if wd == 5:
                msg = await self.weekly_test(uid)
                if not msg and s["current"]:
                    msg = await self.nudge(uid)
                return [msg] if msg else []
            if wd == 6:
                msg = await self.revision(uid)
                return [msg] if msg else []
            c = s["current"]
            challenge_open = c and c["kind"] == "challenge" and (d - date.fromisoformat(c["sent_on"])).days <= 2
            if c and not challenge_open:
                return [await self.nudge(uid)]
            if c:
                return [{"title": "⏰ Morning!", "text": "Your architecture challenge is still open - keep going, "
                                                        "then submit your design."}]
            msg = await self.lesson(uid)
            return [msg] if msg else []
        except MentorError as e:
            return [{"title": "⚠️ Couldn't prepare today's session", "text": f"{e}\nTry again later."}]

    # ---- progress view -----------------------------------------------------------
    def progress(self, uid):
        s = self.users.store(uid)
        sc = s["scores"]
        modules = []
        for w, week in enumerate(cur.WEEKS, start=1):
            first = sum(len(x["lessons"]) for x in cur.WEEKS[:w - 1])
            lessons = [{"index": first + i, "topic": t, "done": first + i < s["lesson_index"],
                        "checked": s["checked"].get(str(first + i))}
                       for i, t in enumerate(week["lessons"])]
            week_scores = [x["score"] for x in sc if x["topic"].startswith((f"Week {w} ", f"Week {w}:"))]
            modules.append({"week": w, "title": week["title"], "lessons": lessons,
                            "challenge": w in cur.CHALLENGE_WEEKS, "tested": w in s["assessed_weeks"],
                            "test_score": week_scores[-1] if week_scores else None})
        return {
            "lesson_index": s["lesson_index"], "total": cur.TOTAL_LESSONS, "streak": s["streak"],
            "checked": len(s["checked"]), "pass_assignment": self.PASS_ASSIGNMENT, "pass_week": self.PASS_WEEK_TEST,
            "activity": sorted({x["date"] for x in sc} | {c["date"] for c in s["checked"].values()})[-60:],
            "today": today().isoformat(),
            "average": round(sum(x["score"] for x in sc) / len(sc), 1) if sc else None,
            "scores": sc[-20:], "weak_areas": s.top_weak(8), "current": s["current"], "modules": modules,
            "ai_left": max(0, cfg.DAILY_AI_LIMIT - (s["ai_usage"]["count"] if s["ai_usage"]["date"]
                                                     == today().isoformat() else 0)),
        }
