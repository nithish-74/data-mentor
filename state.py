"""Progress store: one JSON file per person (atomic writes)."""
import copy
import json
import os
from datetime import date
from pathlib import Path

DEFAULT = {
    "lesson_index": 0,      # next lesson to teach (0-based)
    "lessons": {},          # {lesson index as str: lesson text} so the website can show past lessons
    "checked": {},          # progress checklist {lesson index as str: {by: mentor|you, date, note}}
    "mcq": None,            # open multiple-choice test incl. answers (never sent to the browser)
    "reviews": [],          # last reviews [{date, kind, topic, answer, feedback, score}]
    "ai_usage": {"date": None, "count": 0},
    "current": None,        # pending task awaiting /submit: {kind, topic, assignment, sent_on, week}
    "last_daily": None,     # ISO date of the last 7 AM run
    "assessed_weeks": [],   # weeks whose Saturday assessment/challenge was sent
    "scores": [],           # [{date, kind, topic, score}]
    "weak_areas": {},       # {area: count}
    "history": [],          # recent chat turns [{role: user|model, text}]
    "streak": 0,
    "last_submit": None,
}


class Store:
    """One person's progress. Saved as the document `name` (e.g. "users/<uid>.json") in the app's storage
    (database or files, see docstore.py). A plain Path still works: its folder becomes the storage."""

    def __init__(self, name, docs=None):
        if isinstance(name, Path):  # Store(Path(".../p.json")) - used by simple scripts and tests
            from docstore import Docs, FileDocs
            docs, name = Docs(FileDocs(name.parent)), name.name
        self.name, self.docs = name, docs
        self.data = copy.deepcopy(DEFAULT)
        self.data.update(self.docs.get(self.name) or {})

    def save(self):
        self.docs.put(self.name, self.data)

    def __getitem__(self, k):
        return self.data[k]

    def __setitem__(self, k, v):
        self.data[k] = v

    # ---- history -------------------------------------------------------
    def add_turn(self, role: str, text: str, keep: int = 12):
        self.data["history"].append({"role": role, "text": text[:2000]})
        self.data["history"] = self.data["history"][-keep:]

    # ---- scoring -------------------------------------------------------
    def record_score(self, kind: str, topic: str, score, weak: list, today: date):
        if isinstance(score, (int, float)):
            self.data["scores"].append(
                {"date": today.isoformat(), "kind": kind, "topic": topic, "score": int(score)}
            )
        for area in weak or []:
            area = str(area).strip().lower()[:60]
            if area:
                self.data["weak_areas"][area] = self.data["weak_areas"].get(area, 0) + 1
        # streak counts days with a submission
        last = self.data["last_submit"]
        if last != today.isoformat():
            if last and (today - date.fromisoformat(last)).days == 1:
                self.data["streak"] += 1
            else:
                self.data["streak"] = 1
            self.data["last_submit"] = today.isoformat()

    def use_ai(self, today: date, limit: int) -> bool:
        """Counts one AI call for today; False if this person's daily limit is reached."""
        u = self.data["ai_usage"]
        if u["date"] != today.isoformat():
            u["date"], u["count"] = today.isoformat(), 0
        if u["count"] >= limit:
            return False
        u["count"] += 1
        return True

    def top_weak(self, n: int = 5):
        items = sorted(self.data["weak_areas"].items(), key=lambda kv: -kv[1])
        return [k for k, _ in items[:n]]

    def summary(self) -> str:
        """Short progress context passed into prompts."""
        s = self.data["scores"]
        recent = [x["score"] for x in s[-5:]]
        avg = f"{sum(recent) / len(recent):.1f}/10" if recent else "no scores yet"
        weak = ", ".join(self.top_weak()) or "none identified yet"
        return (
            f"MY PROGRESS: {self.data['lesson_index']} lessons taught, recent average {avg}, "
            f"weak areas: {weak}. Adapt depth and examples to this."
        )
