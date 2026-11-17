"""People registry (data/users.json) + one progress file per person (data/users/<uid>.json).

A person can use the website (Google account), Telegram, or both (linked). Admins invite people.
"""
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

from state import Store

DEFAULT_BACKGROUND = ("Data engineer who wants to become a strong Senior Data Engineer and cloud-independent "
                      "Data Architect. (No detailed background given yet.)")


class Users:
    def __init__(self, data_dir: Path, default_reminder: str):
        from docstore import for_dir
        self.dir = data_dir
        self.docs = for_dir(data_dir)   # database when DATABASE_URL is set, else files in data_dir
        self.default_reminder = default_reminder
        self.data = {"users": {}, "invites": {}, "links": {}}
        self.data.update(self.docs.get("users.json") or {})
        self._stores = {}

    def save(self):
        self.docs.put("users.json", self.data)

    # ---- people ----------------------------------------------------------
    @property
    def people(self) -> dict:
        return self.data["users"]

    def get(self, uid):
        return self.people.get(str(uid)) if uid else None

    def is_admin(self, uid) -> bool:
        u = self.get(uid)
        return bool(u and u["role"] == "admin")

    def find(self, **kw):
        """find(telegram_id=...) / find(google_sub=...) / find(email=...) -> uid or None"""
        (field, value), = kw.items()
        value = str(value).lower() if field in ("email", "username") else str(value)
        for uid, u in self.people.items():
            if u.get(field) is not None and str(u.get(field)) == value:
                return uid
        return None

    def add(self, name: str, role: str = "member", background: str = DEFAULT_BACKGROUND, **ids) -> str:
        uid = secrets.token_hex(6)
        self.people[uid] = {
            "name": (name or "Learner")[:40],
            "role": role,
            "reminder": self.default_reminder,
            "background": background,
            "paused": False,
            "joined": date.today().isoformat(),
            "joined_at": datetime.now().isoformat(timespec="microseconds"),
            "username": (ids.get("username") or "").lower() or None,
            "email": (ids.get("email") or "").lower() or None,
            "google_sub": ids.get("google_sub"),
            "telegram_id": str(ids["telegram_id"]) if ids.get("telegram_id") else None,
        }
        self.save()
        return uid

    def ordered(self):
        """[(number, uid, user)] in join order; numbers are what admins type in commands."""
        items = sorted(self.people.items(), key=lambda kv: kv[1].get("joined_at") or kv[1]["joined"])
        return [(i, uid, u) for i, (uid, u) in enumerate(items, start=1)]

    def by_number(self, n: int):
        for i, uid, u in self.ordered():
            if i == n:
                return uid, u
        return None, None

    def remove(self, uid):
        """Removes the person; their progress is archived under removed/ (not deleted)."""
        uid = str(uid)
        self.people.pop(uid, None)
        self._stores.pop(uid, None)
        text = self.docs.get_text(f"users/{uid}.json")
        if text is not None:
            self.docs.put_text(f"removed/{uid}-{datetime.now():%Y%m%d%H%M%S}.json", text)
            self.docs.delete(f"users/{uid}.json")
        self.save()

    def admins(self):
        return [uid for uid, u in self.people.items() if u["role"] == "admin"]

    USERNAME_RE = r"[a-z0-9][a-z0-9_.-]{2,29}"

    def valid_username(self, name: str):
        import re
        name = (name or "").strip().lower()
        return name if re.fullmatch(self.USERNAME_RE, name) else None

    # ---- progress --------------------------------------------------------
    def store(self, uid) -> Store:
        uid = str(uid)
        if uid not in self._stores:
            self._stores[uid] = Store(f"users/{uid}.json", self.docs)
        return self._stores[uid]

    # ---- invites ---------------------------------------------------------
    def new_invite(self, name: str = "", days: int = 7) -> str:
        code = secrets.token_hex(4).upper()
        self.data["invites"][code] = {"name": name[:40], "expires": (date.today() + timedelta(days=days)).isoformat()}
        self.save()
        return code

    def check_invite(self, code: str):
        inv = self.data["invites"].get((code or "").strip().upper())
        if inv and date.fromisoformat(inv["expires"]) >= date.today():
            return inv
        return None

    def use_invite(self, code: str):
        """Returns the invite (and consumes it) if valid, else None."""
        inv = self.check_invite(code)
        if inv:
            self.data["invites"].pop(code.strip().upper(), None)
            self.save()
        return inv

    def purge_expired(self):
        today = date.today()
        changed = False
        for key in ("invites", "links"):
            keep = {c: i for c, i in self.data[key].items() if date.fromisoformat(i["expires"]) >= today}
            if len(keep) != len(self.data[key]):
                self.data[key], changed = keep, True
        if changed:
            self.save()

    # ---- linking a website account to Telegram -----------------------------
    def new_link(self, uid) -> str:
        code = "LINK" + secrets.token_hex(5).upper()
        self.data["links"][code] = {"uid": uid, "expires": (date.today() + timedelta(days=1)).isoformat()}
        self.save()
        return code

    def use_link(self, code: str, telegram_id) -> str:
        link = self.data["links"].pop((code or "").upper(), None)
        if not link or date.fromisoformat(link["expires"]) < date.today() or not self.get(link["uid"]):
            self.save()
            return None
        other = self.find(telegram_id=telegram_id)
        if other and other != link["uid"]:
            self.people[other]["telegram_id"] = None  # move the Telegram chat to the website account
        self.people[link["uid"]]["telegram_id"] = str(telegram_id)
        self.save()
        return link["uid"]
