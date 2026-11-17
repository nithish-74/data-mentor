"""Articles: pulls new posts from trusted data blogs (RSS), summarizes them with the AI and tags each
with the course week it relates to. Stored in data/articles.json. Learners read the original on the
source site; we keep only the title, link and our own short summary.
"""
import asyncio
import hashlib
import html
import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

import curriculum as cur
import prompts as P

log = logging.getLogger("mentor.articles")

DEFAULT_SOURCES = [
    ("Databricks", "https://www.databricks.com/feed"),
    ("Snowflake", "https://www.snowflake.com/feed/"),
    ("Microsoft Fabric", "https://blog.fabric.microsoft.com/en-us/blog/feed/"),
    ("Azure", "https://azure.microsoft.com/en-us/blog/feed/"),
    ("AWS Big Data", "https://aws.amazon.com/blogs/big-data/feed/"),
    ("Google Cloud", "https://cloudblog.withgoogle.com/products/data-analytics/rss/"),
    ("dbt Labs", "https://www.getdbt.com/blog/rss.xml"),
    ("Confluent", "https://www.confluent.io/rss.xml"),
    ("Martin Fowler", "https://martinfowler.com/feed.atom"),
]
UA = "Mozilla/5.0 (compatible; DataMentorFeedReader/1.0)"
REFRESH_EVERY = timedelta(hours=3)
MAX_AGE_DAYS = 30
PER_RUN = 12          # AI summaries per refresh
PER_DAY = 40          # AI summaries per day
KEEP = 400            # articles kept on disk


def now_utc():
    return datetime.now(timezone.utc)


def strip_html(s: str) -> str:
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def weeks_text():
    return "\n".join(f"{i}. {w['title']}: {'; '.join(t[:60] for t in w['lessons'])}"
                     for i, w in enumerate(cur.WEEKS, start=1))


class Articles:
    def __init__(self, data_dir, ai):
        from docstore import for_dir
        self.docs = for_dir(data_dir)
        self.ai = ai
        self.lock = asyncio.Lock()
        self.data = {"sources": [], "items": {}, "last_fetch": None, "ai_day": {"date": None, "count": 0}}
        self.data.update(self.docs.get("articles.json") or {})
        if not self.data["sources"]:
            self.data["sources"] = [{"id": secrets.token_hex(3), "name": n, "url": u, "on": True}
                                    for n, u in DEFAULT_SOURCES]
            self.save()

    def save(self):
        self.docs.put("articles.json", self.data)

    # ---- reading ----------------------------------------------------------------
    def visible(self):
        items = [a for a in self.data["items"].values() if a.get("relevant")]
        return sorted(items, key=lambda a: (a["published"], a["added"]), reverse=True)

    def list(self, week=None, source=None, limit=80):
        items = self.visible()
        if week:
            items = [a for a in items if a.get("week") == week]
        if source:
            items = [a for a in items if a["source"] == source]
        return items[:limit]

    def new_since(self, iso_ts):
        if not iso_ts:
            return len(self.visible())
        return sum(1 for a in self.visible() if a["added"] > iso_ts)

    # ---- fetching -----------------------------------------------------------------
    async def fetch_source(self, http, src):
        try:
            r = await http.get(src["url"])
            r.raise_for_status()
        except Exception as e:
            log.warning("Feed %s failed: %s", src["name"], type(e).__name__)
            return []
        feed = feedparser.parse(r.content)
        out, cutoff = [], now_utc() - timedelta(days=MAX_AGE_DAYS)
        for e in feed.entries[:30]:
            link = e.get("link")
            title = strip_html(e.get("title", ""))
            when = e.get("published_parsed") or e.get("updated_parsed")
            if not link or not title or not when:
                continue
            published = datetime(*when[:6], tzinfo=timezone.utc)
            if published < cutoff:
                continue
            excerpt = strip_html(e.get("summary") or e.get("description") or "")
            if not excerpt and e.get("content"):
                excerpt = strip_html(e["content"][0].get("value", ""))
            out.append({"id": hashlib.sha1(link.encode()).hexdigest()[:16], "title": title[:300], "link": link,
                        "source": src["name"], "published": published.date().isoformat(),
                        "excerpt": excerpt[:1500]})
        return out

    async def summarize(self, a):
        r = await self.ai.ask_json(P.ARTICLE_TASK.format(source=a["source"], title=a["title"],
                                                         excerpt=a["excerpt"] or "(no excerpt)", weeks=weeks_text()),
                                   P.ARTICLE_SYSTEM)
        week = r.get("week")
        week = int(week) if isinstance(week, (int, float)) and 1 <= int(week) <= 12 else None
        label = r.get("label") if r.get("label") in ("established", "modern", "emerging") else "modern"
        return {"relevant": bool(r.get("relevant")) and bool(r.get("summary")),
                "summary": str(r.get("summary") or "").strip()[:700], "label": label, "week": week,
                "tags": [str(t)[:30] for t in (r.get("tags") or [])][:4]}

    def _ai_budget_left(self):
        day = now_utc().date().isoformat()
        d = self.data["ai_day"]
        if d["date"] != day:
            d["date"], d["count"] = day, 0
        return PER_DAY - d["count"]

    async def refresh(self, force=False) -> int:
        """Fetch new articles (at most every 3 hours unless forced). Returns how many were added."""
        if self.lock.locked():
            return 0
        async with self.lock:
            last = self.data["last_fetch"]
            if not force and last and now_utc() - datetime.fromisoformat(last) < REFRESH_EVERY:
                return 0
            sources = [s for s in self.data["sources"] if s.get("on")]
            async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": UA}) as http:
                batches = await asyncio.gather(*[self.fetch_source(http, s) for s in sources])
            # take sources in turn (each one's newest first) so one busy blog doesn't fill the page
            queues = [sorted([a for a in b if a["id"] not in self.data["items"]],
                             key=lambda a: a["published"], reverse=True) for b in batches]
            fresh = []
            while any(queues):
                for q in queues:
                    if q:
                        fresh.append(q.pop(0))
            added = 0
            for a in fresh[:min(PER_RUN, max(0, self._ai_budget_left()))]:
                try:
                    meta = await self.summarize(a)
                except Exception as e:  # AI busy / quota: try again next refresh
                    log.warning("Summary failed for an article: %s", type(e).__name__)
                    break
                self.data["ai_day"]["count"] += 1
                a.pop("excerpt", None)
                a.update(meta, added=now_utc().isoformat(timespec="seconds"))
                self.data["items"][a["id"]] = a
                added += a["relevant"]
            self.data["last_fetch"] = now_utc().isoformat(timespec="seconds")
            if len(self.data["items"]) > KEEP:
                keep = sorted(self.data["items"].values(), key=lambda a: a["added"], reverse=True)[:KEEP]
                self.data["items"] = {a["id"]: a for a in keep}
            self.save()
            log.info("Articles refresh: %d new (from %d candidates)", added, len(fresh))
            return added

    # ---- sources (admin) --------------------------------------------------------------
    async def add_source(self, name, url):
        if not re.match(r"^https://\S+$", url or ""):
            raise ValueError("Use a full https:// feed address.")
        if any(s["url"] == url for s in self.data["sources"]):
            raise ValueError("That feed is already in the list.")
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": UA}) as http:
            try:
                r = await http.get(url)
                r.raise_for_status()
            except Exception:
                raise ValueError("Couldn't open that address. Check it's an RSS or Atom feed.")
        feed = feedparser.parse(r.content)
        if not feed.entries:
            raise ValueError("That address isn't an RSS or Atom feed (no articles found in it).")
        name = (name or feed.feed.get("title") or "Feed").strip()[:40]
        self.data["sources"].append({"id": secrets.token_hex(3), "name": name, "url": url, "on": True})
        self.save()
        return name

    def set_source(self, sid, on):
        for s in self.data["sources"]:
            if s["id"] == sid:
                s["on"] = bool(on)
                self.save()
                return True
        return False

    def remove_source(self, sid):
        before = len(self.data["sources"])
        self.data["sources"] = [s for s in self.data["sources"] if s["id"] != sid]
        self.save()
        return len(self.data["sources"]) != before
