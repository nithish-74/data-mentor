"""Where the mentor keeps its data: JSON documents by name ("users.json", "users/<uid>.json", ...).

- DATABASE_URL set (e.g. a free Neon Postgres) -> one table `docs`, so the app can run on a host without a disk
  (Render's free plan).
- Otherwise -> plain files under data/, as before.
"""
import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("mentor.docs")


class FileDocs:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, name):
        return self.root / name

    def read_text(self, name):
        p = self._p(name)
        return p.read_text(encoding="utf-8") if p.exists() else None

    def write_text(self, name, text):
        p = self._p(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)

    def delete(self, name):
        p = self._p(name)
        if p.exists():
            p.unlink()

    def names(self, prefix=""):
        return sorted(str(p.relative_to(self.root)).replace("\\", "/") for p in self.root.rglob("*")
                      if p.is_file() and str(p.relative_to(self.root)).replace("\\", "/").startswith(prefix))


class PostgresDocs:
    def __init__(self, url: str):
        import psycopg
        self.psycopg = psycopg
        self.url = url
        self.lock = threading.Lock()
        self.conn = None
        self._connect()
        with self.conn.cursor() as c:
            c.execute("CREATE TABLE IF NOT EXISTS docs (name TEXT PRIMARY KEY, body TEXT NOT NULL, "
                      "updated TIMESTAMPTZ NOT NULL DEFAULT now())")
        log.info("Using the database for storage")

    def _connect(self):
        self.conn = self.psycopg.connect(self.url, autocommit=True, connect_timeout=15)

    def _run(self, sql, args=(), fetch=None):
        with self.lock:  # one connection, used by one caller at a time
            for attempt in (1, 2):
                try:
                    with self.conn.cursor() as c:
                        c.execute(sql, args)
                        return c.fetchone() if fetch == "one" else c.fetchall() if fetch == "all" else None
                except self.psycopg.OperationalError:
                    if attempt == 2:
                        raise
                    log.warning("Database connection dropped; reconnecting")
                    self._connect()  # free databases close idle connections

    def read_text(self, name):
        row = self._run("SELECT body FROM docs WHERE name = %s", (name,), "one")
        return row[0] if row else None

    def write_text(self, name, text):
        self._run("INSERT INTO docs (name, body, updated) VALUES (%s, %s, now()) "
                  "ON CONFLICT (name) DO UPDATE SET body = EXCLUDED.body, updated = now()", (name, text))

    def delete(self, name):
        self._run("DELETE FROM docs WHERE name = %s", (name,))

    def names(self, prefix=""):
        return [r[0] for r in self._run("SELECT name FROM docs WHERE name LIKE %s ORDER BY name",
                                        (prefix.replace("%", r"\%") + "%",), "all")]


class Docs:
    """JSON helpers on top of either backend."""

    def __init__(self, backend):
        self.backend = backend

    def get(self, name, default=None):
        text = self.backend.read_text(name)
        return json.loads(text) if text else default

    def put(self, name, data):
        self.backend.write_text(name, json.dumps(data, indent=1, ensure_ascii=False))

    def get_text(self, name):
        return self.backend.read_text(name)

    def put_text(self, name, text):
        self.backend.write_text(name, text)

    def exists(self, name):
        return self.backend.read_text(name) is not None

    def delete(self, name):
        self.backend.delete(name)

    def names(self, prefix=""):
        return self.backend.names(prefix)


_docs = None


def for_dir(data_dir):
    """The app's storage for its data folder (database when configured); a plain folder for anything else
    (tests, throwaway copies)."""
    import config as cfg
    if Path(data_dir).resolve() == Path(cfg.DATA).resolve():
        return docs()
    return Docs(FileDocs(Path(data_dir)))


def docs():
    global _docs
    if _docs is None:
        import config as cfg
        url = os.getenv("DATABASE_URL", "").strip()
        _docs = Docs(PostgresDocs(url) if url else FileDocs(cfg.DATA))
    return _docs
