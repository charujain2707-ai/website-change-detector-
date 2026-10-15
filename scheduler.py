"""Scheduler + job queue + workers (timeouts, retries, backoff, URL de-duplication)."""
import os, time, queue, random, threading, traceback
from datetime import datetime, timezone
import db, checker, alerts

WORKERS = int(os.environ.get("WORKERS", 2))
RETRIES = int(os.environ.get("RETRIES", 2))
COALESCE = 300                      # piggy-back pages on the same URL due within 5 min
DIGEST_HOUR = int(os.environ.get("DIGEST_HOUR", 9))

jobs = queue.Queue()
inflight, _lock = set(), threading.Lock()

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def finish(ids, status, err=""):
    for pid in ids:
        p = db.q("SELECT * FROM pages WHERE id=?", (pid,), one=True)
        if not p: continue
        fails = 0 if status == "ok" else p["fail_count"] + 1
        delay = min(p["interval_min"] * 60 * 2 ** min(fails, 4), 86400)   # exponential backoff on failure
        db.ex("UPDATE pages SET status=?, last_error=?, last_checked=?, fail_count=?, next_run=? WHERE id=?",
              (status, err[:300], now_iso(), fails, time.time() + delay + random.uniform(0, 30), pid))

def process(url, ids):
    pages = [p for p in (db.q("SELECT * FROM pages WHERE id=?", (i,), one=True) for i in ids) if p]
    if not pages: return
    if not checker.robots_ok(url):
        raise checker.Blocked("Disallowed by the site's robots.txt")
    checker.polite_wait(url)
    first = checker.render(url, pages)               # ONE browser load serves every user of this URL
    suspects = []
    for p in pages:
        text, shot = first[p["id"]]
        norm = checker.normalize(text, p["sensitivity"])
        last = None if p["rebaseline"] else db.q(
            "SELECT * FROM snapshots WHERE page_id=? ORDER BY id DESC LIMIT 1", (p["id"],), one=True)
        if last and checker.sha(norm) == last["hash"]:
            checker.remove_shot(shot)                 # nothing changed -> no new snapshot
        else:
            suspects.append((p, norm, shot, last))
    if suspects:                                      # confirm with a 2nd load to strip per-load noise
        time.sleep(3); checker.polite_wait(url)
        second = checker.render(url, [s[0] for s in suspects], screenshots=False)
        for p, norm, shot, last in suspects:
            text = checker.stable(norm, checker.normalize(second[p["id"]][0], p["sensitivity"]))
            h = checker.sha(text)
            if last and h == last["hash"]:
                checker.remove_shot(shot); continue   # only noise moved
            added, removed = checker.changes(last["text"], text) if last else ([], [])
            summary = checker.summarize(added, removed) if last else "Baseline snapshot"
            sid = db.ex("INSERT INTO snapshots(page_id, hash, text, screenshot, summary) VALUES(?,?,?,?,?)",
                        (p["id"], h, text, shot, summary))
            db.ex("UPDATE pages SET rebaseline=0 WHERE id=?", (p["id"],))
            if not last: continue
            db.ex("UPDATE pages SET last_changed=? WHERE id=?", (now_iso(), p["id"]))
            if len(added) + len(removed) >= checker.min_lines(p["sensitivity"]) and checker.keyword_hit(p["keywords"], added + removed):
                alerts.alert(p, sid, h, summary)
    finish(ids, "ok")

def worker():
    while True:
        url, ids = jobs.get()
        try:
            for attempt in range(RETRIES + 1):
                try:
                    process(url, ids); break
                except checker.Blocked as e:          # CAPTCHA/bot wall is a status, NOT a change
                    finish(ids, "blocked", str(e)); break
                except Exception as e:                # timeout / outage -> retry, then mark error
                    if attempt == RETRIES:
                        finish(ids, "error", f"{type(e).__name__}: {e}"); traceback.print_exc()
                    else:
                        time.sleep(5 * 2 ** attempt)
        finally:
            with _lock: inflight.discard(url)
            jobs.task_done()

def loop():
    last_digest = None
    while True:
        try:
            t = time.time()
            for row in db.q("SELECT DISTINCT url FROM pages WHERE next_run<=?", (t,)):
                url = row["url"]
                with _lock:
                    if url in inflight: continue
                    inflight.add(url)
                ids = [r["id"] for r in db.q("SELECT id FROM pages WHERE url=? AND next_run<=?", (url, t + COALESCE))]
                jobs.put((url, ids))
            today = datetime.now()
            if today.hour == DIGEST_HOUR and last_digest != today.date():
                alerts.daily_digest(); last_digest = today.date()
        except Exception:
            traceback.print_exc()
        time.sleep(15)

def start():
    for _ in range(WORKERS):
        threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()
