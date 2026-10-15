"""Website Change Detector — Flask web app (dashboard, timeline, diffs, picker, RSS)."""
import os, html, random, secrets
from email.utils import format_datetime
from datetime import datetime, timezone
try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, abort, Response
from markupsafe import Markup
import db, checker, scheduler, alerts

app = Flask(__name__)
SENS = ["high", "medium", "low"]
INTERVALS = [(15, "15 min"), (30, "30 min"), (60, "1 hour"), (180, "3 hours"), (360, "6 hours"), (720, "12 hours"), (1440, "24 hours")]

def parse_ts(ts):
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

@app.template_filter("ago")
def ago(ts):
    if not ts: return "never"
    s = (datetime.now(timezone.utc) - parse_ts(ts)).total_seconds()
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n: return f"{int(s // n)}{unit} ago"
    return "just now"

def get_page(pid):
    p = db.q("SELECT * FROM pages WHERE id=?", (pid,), one=True)
    return p or abort(404)

def page_fields(f):
    url = checker.validate_url(f.get("url", ""))
    iv = int(f.get("interval_min") or 60)
    if not 15 <= iv <= 1440:
        raise ValueError("Check interval must be between 15 minutes and 24 hours")
    sens = f.get("sensitivity") if f.get("sensitivity") in SENS else "medium"
    return dict(name=(f.get("name") or "").strip() or url, url=url, interval_min=iv, sensitivity=sens,
                keywords=f.get("keywords", "").strip(), email=f.get("email", "").strip(),
                webhook=f.get("webhook", "").strip(), digest=1 if f.get("digest") else 0,
                watch_selectors=f.get("watch_selectors", "").strip(), ignore_selectors=f.get("ignore_selectors", "").strip())

@app.get("/")
def index():
    pages = db.q("SELECT p.*, (SELECT COUNT(*) FROM snapshots s WHERE s.page_id=p.id) AS versions FROM pages p ORDER BY p.id DESC")
    return render_template("index.html", pages=pages, error=request.args.get("error"), sens=SENS, intervals=INTERVALS)

@app.post("/add")
def add():
    try:
        f = page_fields(request.form)
    except ValueError as e:
        return redirect(url_for("index", error=str(e)))
    db.ex(f"INSERT INTO pages({','.join(f)}) VALUES({','.join('?' * len(f))})", tuple(f.values()))
    return redirect(url_for("index"))

@app.get("/page/<int:pid>")
def detail(pid):
    p = get_page(pid)
    snaps = db.q("SELECT id, screenshot, summary, created FROM snapshots WHERE page_id=? ORDER BY id DESC", (pid,))
    sent = db.q("SELECT * FROM alerts WHERE page_id=? ORDER BY id DESC LIMIT 20", (pid,))
    return render_template("page.html", p=p, snaps=snaps, alerts=sent, sens=SENS, intervals=INTERVALS,
                           error=request.args.get("error"), base=alerts.BASE_URL)

@app.post("/page/<int:pid>/settings")
def settings(pid):
    old = get_page(pid)
    try:
        f = page_fields(request.form)
    except ValueError as e:
        return redirect(url_for("detail", pid=pid, error=str(e)))
    # changing what we watch starts a fresh baseline instead of firing a giant false alert
    reb = int(any(f[k] != old[k] for k in ("url", "watch_selectors", "ignore_selectors", "sensitivity")))
    db.ex(f"UPDATE pages SET {', '.join(k + '=?' for k in f)}, rebaseline=MAX(rebaseline, ?), next_run=0 WHERE id=?",
          (*f.values(), reb, pid))
    return redirect(url_for("detail", pid=pid))

@app.post("/page/<int:pid>/selectors")
def save_selectors(pid):
    get_page(pid)
    db.ex("UPDATE pages SET watch_selectors=?, ignore_selectors=?, rebaseline=1, next_run=0 WHERE id=?",
          (request.form.get("watch_selectors", ""), request.form.get("ignore_selectors", ""), pid))
    return redirect(url_for("detail", pid=pid))

@app.post("/page/<int:pid>/check")
def check_now(pid):
    get_page(pid)
    db.ex("UPDATE pages SET next_run=0 WHERE id=?", (pid,))
    return redirect(request.referrer or url_for("index"))

@app.post("/page/<int:pid>/delete")
def delete(pid):
    get_page(pid)
    for s in db.q("SELECT screenshot FROM snapshots WHERE page_id=?", (pid,)):
        checker.remove_shot(s["screenshot"])
    for sql in ("DELETE FROM snapshots WHERE page_id=?", "DELETE FROM alerts WHERE page_id=?", "DELETE FROM pages WHERE id=?"):
        db.ex(sql, (pid,))
    return redirect(url_for("index"))

@app.get("/compare/<int:pid>")
def compare(pid):
    p = get_page(pid)
    snaps = db.q("SELECT * FROM snapshots WHERE page_id=? ORDER BY id DESC", (pid,))
    if len(snaps) < 2:
        return render_template("compare.html", p=p, snaps=snaps, a=None, b=None)
    b = next((s for s in snaps if s["id"] == request.args.get("b", type=int)), snaps[0])
    older = next((s for s in snaps if s["id"] < b["id"]), snaps[-1])
    a = next((s for s in snaps if s["id"] == request.args.get("a", type=int)), older)
    table = checker.text_diff_html(a["text"], b["text"], f"Version {a['id']} ({a['created']})", f"Version {b['id']} ({b['created']})")
    return render_template("compare.html", p=p, snaps=snaps, a=a, b=b, table=Markup(table))

@app.get("/shot/<name>")
def shot(name):
    return send_from_directory(checker.SHOTS, name)

@app.get("/shotdiff/<int:a>/<int:b>.png")
def shotdiff(a, b):
    out = f"diff_{a}_{b}.png"
    if not os.path.exists(os.path.join(checker.SHOTS, out)):
        ra, rb = (db.q("SELECT screenshot FROM snapshots WHERE id=?", (i,), one=True) for i in (a, b))
        if not (ra and rb and ra["screenshot"] and rb["screenshot"]): abort(404)
        checker.shot_diff(os.path.join(checker.SHOTS, ra["screenshot"]), os.path.join(checker.SHOTS, rb["screenshot"]),
                          os.path.join(checker.SHOTS, out))
    return send_from_directory(checker.SHOTS, out)

@app.get("/picker/<int:pid>")
def picker(pid):
    return render_template("picker.html", p=get_page(pid))

@app.get("/proxy/<int:pid>")
def proxy(pid):
    p = get_page(pid)
    try:
        checker.polite_wait(p["url"])
        page_html = checker.picker_html(p["url"], checker.render_html(p["url"]))
    except Exception as e:
        page_html = f"<p style='font-family:sans-serif;padding:20px'>Couldn't load the page: {html.escape(str(e))}</p>"
    # sandbox: the third-party page runs in an opaque origin and can't touch this app
    return Response(page_html, headers={"Content-Security-Policy": "sandbox allow-scripts"})

@app.get("/rss/<int:pid>")
def rss(pid):
    p, e = get_page(pid), html.escape
    items = "".join(
        f"<item><title>{e(p['name'])}: {e(s['summary'])}</title>"
        f"<link>{alerts.BASE_URL}/compare/{pid}?b={s['id']}</link><guid>{alerts.BASE_URL}/compare/{pid}?b={s['id']}</guid>"
        f"<pubDate>{format_datetime(parse_ts(s['created']))}</pubDate></item>"
        for s in db.q("SELECT id, summary, created FROM snapshots WHERE page_id=? ORDER BY id DESC LIMIT 30", (pid,)))
    xml = (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{e(p["name"])} changes</title>'
           f"<link>{e(p['url'])}</link><description>Changes detected on {e(p['url'])}</description>{items}</channel></rss>")
    return Response(xml, mimetype="application/rss+xml")

# ---------- Demo page for testing (noisy on purpose) ----------
DEMO = {"price": "₹999"}

@app.get("/demo")
def demo():
    now = datetime.now()
    return f"""<!doctype html><html><head><title>Demo Shop</title></head><body style="font-family:sans-serif;max-width:600px;margin:40px auto">
<h1>Demo Shop</h1>
<p>Page generated at {now:%H:%M:%S} on {now:%Y-%m-%d}</p>
<p>{random.randint(100, 99999)} views</p>
<p>Session id: {secrets.token_hex(16)}</p>
<div class="ad-banner" style="background:#ffe066;padding:12px">AD: Buy product #{random.randint(1, 999)} today!</div>
<div class="carousel"><p>Featured: {random.choice(['Shoes', 'Bags', 'Watches', 'Phones'])}</p></div>
<h2 id="price">Price: {html.escape(DEMO['price'])}</h2>
<p>Stock: available</p></body></html>"""

@app.get("/demo/set")
def demo_set():
    DEMO["price"] = request.args.get("price", DEMO["price"])
    return f"Price set to {html.escape(DEMO['price'])}. <a href='/demo'>View demo page</a>"

if __name__ == "__main__":
    db.init()
    scheduler.start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False, threaded=True)
