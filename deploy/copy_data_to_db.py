"""One-time copy of everything in data/ (people, progress, chats, articles, keys...) into the database.

Usage (from the project folder, with the PC copy of the mentor stopped):
    set DATABASE_URL=postgresql://...      (from Neon)
    python deploy\\copy_data_to_db.py
Safe to run again: documents are overwritten with the PC's version.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if not os.getenv("DATABASE_URL"):
    sys.exit("Set DATABASE_URL first (the connection string from Neon).")

from docstore import PostgresDocs  # noqa: E402

SKIP = {"mentor.log", "public_url.txt"}
data = ROOT / "data"
db = PostgresDocs(os.environ["DATABASE_URL"])
count = 0
for p in sorted(data.rglob("*")):
    rel = str(p.relative_to(data)).replace("\\", "/")
    if not p.is_file() or rel in SKIP or rel.startswith("mentor.log") or rel.endswith(".tmp"):
        continue
    db.write_text(rel, p.read_text(encoding="utf-8"))
    count += 1
print(f"Copied {count} documents. In the database now: {len(db.names())}.")
print("People:", len(db.names("users/")), "| archived:", len(db.names("removed/")))
