"""Email + Telegram/Discord alerts, de-duplication and daily digest."""
import os, smtplib, requests
from email.message import EmailMessage
import db

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")

def _send(page, subject, body):
    ok = True
    hook = (page["webhook"] or "").strip()
    if hook:
        try:
            if "discord.com/api/webhooks" in hook:
                r = requests.post(hook, json={"content": body[:1900]}, timeout=15)
            elif "api.telegram.org" in hook:   # https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<ID>
                r = requests.post(hook, data={"text": body[:4000]}, timeout=15)
            else:
                r = requests.post(hook, json={"text": body, "content": body}, timeout=15)
            r.raise_for_status()
        except requests.RequestException as e:
            print("Webhook failed:", e); ok = False
    if (page["email"] or "").strip() and os.environ.get("SMTP_HOST"):
        try:
            m = EmailMessage()
            m["Subject"], m["To"] = subject, page["email"]
            m["From"] = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER", "")
            m.set_content(body)
            with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", 587)), timeout=20) as s:
                s.starttls()
                if os.environ.get("SMTP_USER"):
                    s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
                s.send_message(m)
        except Exception as e:
            print("Email failed:", e); ok = False
    return ok

def alert(page, snap_id, content_hash, summary):
    # UNIQUE(page_id, hash): the same content never alerts twice (stops A->B->A->B ping-pong spam)
    aid = db.ex("INSERT OR IGNORE INTO alerts(page_id, snapshot_id, hash, summary) VALUES(?,?,?,?)",
                (page["id"], snap_id, content_hash, summary))
    if not aid or page["digest"]:
        return   # duplicate, or waiting for the daily digest
    body = f"🔔 {page['name']} changed\n{summary}\nSee the diff: {BASE_URL}/compare/{page['id']}?b={snap_id}\n{page['url']}"
    if _send(page, f"[Change] {page['name']}", body):
        db.ex("UPDATE alerts SET sent=1 WHERE id=?", (aid,))

def daily_digest():
    rows = db.q("SELECT a.* FROM alerts a JOIN pages p ON p.id=a.page_id WHERE p.digest=1 AND a.digested=0 ORDER BY a.id")
    by_page = {}
    for r in rows:
        by_page.setdefault(r["page_id"], []).append(r)
    for pid, items in by_page.items():
        page = db.q("SELECT * FROM pages WHERE id=?", (pid,), one=True)
        if not page: continue
        lines = [f"• {r['created']} — {r['summary']}\n  {BASE_URL}/compare/{pid}?b={r['snapshot_id']}" for r in items]
        if _send(page, f"[Daily digest] {page['name']}: {len(items)} change(s)", f"{page['name']} ({page['url']})\n\n" + "\n".join(lines)):
            for r in items:
                db.ex("UPDATE alerts SET digested=1, sent=1 WHERE id=?", (r["id"],))
