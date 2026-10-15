"""Tiny SQLite helper (thread-safe)."""
import os, sqlite3, threading

DATA = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
DB = os.path.join(DATA, "monitor.db")
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages(
  id INTEGER PRIMARY KEY, name TEXT, url TEXT, interval_min INTEGER,
  watch_selectors TEXT DEFAULT '', ignore_selectors TEXT DEFAULT '',
  sensitivity TEXT DEFAULT 'medium', keywords TEXT DEFAULT '',
  email TEXT DEFAULT '', webhook TEXT DEFAULT '', digest INTEGER DEFAULT 0,
  status TEXT DEFAULT 'pending', last_error TEXT DEFAULT '',
  last_checked TEXT, last_changed TEXT, next_run REAL DEFAULT 0,
  fail_count INTEGER DEFAULT 0, rebaseline INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS snapshots(
  id INTEGER PRIMARY KEY, page_id INTEGER, hash TEXT, text TEXT, screenshot TEXT,
  summary TEXT DEFAULT '', created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY, page_id INTEGER, snapshot_id INTEGER, hash TEXT, summary TEXT,
  sent INTEGER DEFAULT 0, digested INTEGER DEFAULT 0, created TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(page_id, hash));
"""

def _run(sql, args=(), fetch=True, script=False):
    with _lock:
        c = sqlite3.connect(DB, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            if script:
                c.executescript(sql); return None
            cur = c.execute(sql, args)
            rows = cur.fetchall() if fetch else None
            c.commit()
            return rows if fetch else (cur.lastrowid if cur.rowcount > 0 else None)
        finally:
            c.close()

def init():
    os.makedirs(DATA, exist_ok=True)
    _run(SCHEMA, script=True)

def q(sql, args=(), one=False):
    rows = _run(sql, args)
    return (rows[0] if rows else None) if one else rows

def ex(sql, args=()):
    """Execute a write. Returns new row id, or None if nothing was inserted/changed."""
    return _run(sql, args, fetch=False)
