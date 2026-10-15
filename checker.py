"""Rendering (Playwright), noise filtering, hashing and diffing."""
import os, re, math, time, socket, hashlib, difflib, ipaddress, threading
from html import escape
from urllib.parse import urlparse, urlunparse
from urllib import robotparser
import requests
from PIL import Image, ImageChops, ImageDraw
from db import DATA

SHOTS = os.path.join(DATA, "shots")
os.makedirs(SHOTS, exist_ok=True)
ALLOW_PRIVATE = os.environ.get("ALLOW_PRIVATE") == "1"   # only for local testing
BOT = "ChangeDetectorBot"
UA = f"Mozilla/5.0 (compatible; {BOT}/1.0)"
TIMEOUT = int(os.environ.get("PAGE_TIMEOUT", 30))        # seconds per page load
HOST_GAP = float(os.environ.get("HOST_GAP", 10))         # min seconds between hits to one host
MAX_SHOT_H = 8000

class Blocked(Exception):
    """Site outage-free but unusable: CAPTCHA, bot wall, 403/429, robots.txt."""

def split(s):
    return [x.strip() for x in (s or "").splitlines() if x.strip()]

# ---------- 1. Safety: public pages only ----------
def validate_url(url):
    p = urlparse(url.strip())
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("Enter a full http:// or https:// URL")
    if p.username or p.password:
        raise ValueError("URLs with login details aren't allowed — public pages only")
    if not ALLOW_PRIVATE:
        try:
            infos = socket.getaddrinfo(p.hostname, None)
        except socket.gaierror:
            raise ValueError(f"Can't resolve {p.hostname}")
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise ValueError("Internal/private addresses aren't allowed")
    return urlunparse((p.scheme, p.netloc.lower(), p.path or "/", "", p.query, ""))

# ---------- 2. Politeness: robots.txt + per-host gap ----------
_robots, _rlock = {}, threading.Lock()
def robots_ok(url):
    p = urlparse(url); base = f"{p.scheme}://{p.netloc}"
    with _rlock:
        cached = _robots.get(base)
    if not cached or time.time() - cached[1] > 86400:
        rp = robotparser.RobotFileParser()
        try:
            r = requests.get(base + "/robots.txt", timeout=10, headers={"User-Agent": UA})
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except requests.RequestException:
            rp.parse([])
        cached = (rp, time.time())
        with _rlock:
            _robots[base] = cached
    return cached[0].can_fetch(BOT, url)

_host_last, _hlock = {}, threading.Lock()
def polite_wait(url):
    host = urlparse(url).hostname
    while True:
        with _hlock:
            wait = _host_last.get(host, 0) + HOST_GAP - time.time()
            if wait <= 0:
                _host_last[host] = time.time(); return
        time.sleep(min(wait, 5))

# ---------- 3. Noise filtering ----------
MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
BASE_RULES = [
    (rf"\b{MON}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b", "<DATE>"),
    (rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{MON},?\s+\d{{4}}\b", "<DATE>"),
    (r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?", "<DATE>"),
    (r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b", "<DATE>"),
    (r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\s*[ap]\.?m\.?)?", "<TIME>"),
    (r"\b(?:just now|\d+\s*(?:s|secs?|seconds?|m|mins?|minutes?|h|hrs?|hours?|d|days?|w|weeks?)\s+ago)\b", "<AGO>"),
    (r"\b(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{20,}\b", "<TOKEN>"),
]
COUNTERS = [(r"\b\d[\d,.]*\s*[km]?\+?\s+(?:views?|likes?|comments?|followers?|visitors?|online|reads?|shares?|reviews?|ratings?|watching|people)\b", "<COUNT>")]
NUMBERS = [(r"\d[\d,.]*", "<N>")]
# sensitivity -> (rules, min changed lines to alert)
SENSITIVITY = {"high": (BASE_RULES, 1), "medium": (BASE_RULES + COUNTERS, 1), "low": (BASE_RULES + COUNTERS + NUMBERS, 3)}
_RX = {k: ([(re.compile(p, re.I), r) for p, r in rules], n) for k, (rules, n) in SENSITIVITY.items()}

def normalize(text, sensitivity="medium"):
    rules = _RX.get(sensitivity, _RX["medium"])[0]
    out = []
    for line in (text or "").splitlines():
        line = " ".join(line.split())
        if line:
            for rx, rep in rules:
                line = rx.sub(rep, line)
            out.append(line)
    return "\n".join(out)

def min_lines(sensitivity):
    return _RX.get(sensitivity, _RX["medium"])[1]

def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()

def stable(a, b):
    """Keep only lines present in BOTH back-to-back loads -> drops content that changes on every load."""
    keep = set(b.splitlines())
    return "\n".join(l for l in a.splitlines() if l in keep)

def changes(old, new):
    o, n = old.splitlines(), new.splitlines()
    so, sn = set(o), set(n)
    return [l for l in n if l not in so], [l for l in o if l not in sn]

def summarize(added, removed):
    s = f"{len(added)} line(s) added, {len(removed)} removed."
    if added: s += f' New: "{added[0][:120]}"'
    elif removed: s += f' Removed: "{removed[0][:120]}"'
    return s

def keyword_hit(keywords, lines):
    kws = [k.strip().lower() for k in (keywords or "").split(",") if k.strip()]
    blob = "\n".join(lines).lower()
    return not kws or any(k in blob for k in kws)

# ---------- 4. Rendering ----------
AD_SELECTORS = ['ins.adsbygoogle', 'iframe[src*="doubleclick"]', 'iframe[src*="googlesyndication"]',
    '[id^="google_ads"]', '[id^="ad-"]', '[id^="ad_"]', '[class^="ad-"]', '[class~="ad"]', '[class~="ads"]',
    '[class*="advert"]', '[class*="sponsor"]', '[class*="carousel"]', '[class*="swiper"]', '[class*="slick-"]',
    '[aria-roledescription="carousel"]', '[class*="cookie-banner"]', '[id*="cookie-banner"]']
BLOCK_WORDS = ["captcha", "verify you are human", "are you a robot", "access denied", "attention required",
    "unusual traffic", "just a moment...", "checking your browser"]

EXTRACT_JS = """([watch, ignore]) => {
  const marked = [];
  for (const s of ignore) { try { document.querySelectorAll(s).forEach(e => {
      if (e === document.body || e === document.documentElement) return;
      marked.push([e, e.style.visibility]); e.style.visibility = 'hidden'; }); } catch (err) {} }
  let text = '';
  if (watch.length) { for (const s of watch) { try { document.querySelectorAll(s).forEach(e => text += e.innerText + '\\n'); } catch (err) {} } }
  else text = document.body ? document.body.innerText : '';
  window.__cdRestore = () => marked.forEach(([e, v]) => e.style.visibility = v);
  return text;
}"""

def _open(p, url):
    from playwright.sync_api import TimeoutError as PWTimeout
    browser = p.chromium.launch(args=["--no-sandbox"])
    tab = browser.new_page(user_agent=UA, viewport={"width": 1280, "height": 900})
    resp = tab.goto(url, timeout=TIMEOUT * 1000, wait_until="domcontentloaded")
    try: tab.wait_for_load_state("networkidle", timeout=8000)
    except PWTimeout: pass
    status = resp.status if resp else 0
    body = tab.evaluate("() => document.body ? document.body.innerText : ''")
    probe = (tab.title() + " " + (body if len(body) < 3000 else "")).lower()
    if status in (403, 429) or any(w in probe for w in BLOCK_WORDS):
        browser.close(); raise Blocked(f"Bot wall / CAPTCHA detected (HTTP {status})")
    if status >= 400:
        browser.close(); raise RuntimeError(f"Site returned HTTP {status}")
    return browser, tab

def render(url, pages, screenshots=True):
    """Load the URL ONCE and extract text (+screenshot) for every page config watching it."""
    from playwright.sync_api import sync_playwright
    out = {}
    with sync_playwright() as p:
        browser, tab = _open(p, url)
        try:
            for pg in pages:
                text = tab.evaluate(EXTRACT_JS, [split(pg["watch_selectors"]), AD_SELECTORS + split(pg["ignore_selectors"])])
                shot = None
                if screenshots:
                    shot = f"{pg['id']}_{int(time.time() * 1000)}.png"
                    h = min(tab.evaluate("document.documentElement.scrollHeight") or 900, MAX_SHOT_H)
                    tab.screenshot(path=os.path.join(SHOTS, shot), full_page=True,
                                   clip={"x": 0, "y": 0, "width": 1280, "height": h})
                tab.evaluate("() => window.__cdRestore && window.__cdRestore()")
                out[pg["id"]] = (text, shot)
        finally:
            browser.close()
    return out

def render_html(url):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser, tab = _open(p, url)
        try: return tab.content()
        finally: browser.close()

def remove_shot(name):
    if name:
        try: os.remove(os.path.join(SHOTS, name))
        except OSError: pass

# ---------- 5. Visual picker page ----------
_PICKER = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "picker.js")).read()
def picker_html(url, html):
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S | re.I)
    html = re.sub(r"<meta[^>]+http-equiv=[\"']?(?:refresh|content-security-policy)[^>]*>", "", html, flags=re.I)
    base = f'<base href="{escape(url)}">'
    if re.search(r"<head[^>]*>", html, re.I):
        html = re.sub(r"<head[^>]*>", lambda m: m.group(0) + base, html, count=1, flags=re.I)
    else:
        html = base + html
    return html + f"<script>{_PICKER}</script>"

# ---------- 6. Diffs ----------
def text_diff_html(a, b, label_a, label_b):
    return difflib.HtmlDiff(wrapcolumn=60).make_table(a.splitlines(), b.splitlines(), label_a, label_b, context=True, numlines=2)

def shot_diff(path_a, path_b, out, cell=16):
    """Red boxes on version B wherever pixels differ from version A. Returns number of changed cells."""
    A, B = Image.open(path_a).convert("RGB"), Image.open(path_b).convert("RGB")
    w, h = max(A.width, B.width), max(A.height, B.height)
    pa, pb = Image.new("RGB", (w, h), "white"), Image.new("RGB", (w, h), "white")
    pa.paste(A); pb.paste(B)
    mask = ImageChops.difference(pa, pb).convert("L").point(lambda v: 255 if v > 30 else 0)
    gw, gh = math.ceil(w / cell), math.ceil(h / cell)
    grid = mask.resize((gw, gh), Image.Resampling.BOX).load()
    draw, n = ImageDraw.Draw(pb, "RGBA"), 0
    for y in range(gh):
        for x in range(gw):
            if grid[x, y] > 0:
                n += 1
                draw.rectangle([x * cell, y * cell, x * cell + cell - 1, y * cell + cell - 1], fill=(255, 0, 0, 80))
    pb.save(out)
    return n
