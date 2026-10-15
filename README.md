# Website Change Detector

Monitors public web pages and alerts you to **meaningful** changes while ignoring noise
(timestamps, counters, ads, rotating banners, random tokens).

Built with **Python · Flask · Playwright (headless Chromium) · SQLite · Pillow**.

## Features (mapped to the requirements)

| # | Requirement | How it's done |
|---|---|---|
| 1 | URL, name, interval 15 min–24 h, headless browser | Dashboard form; pages rendered in Playwright Chromium |
| 2 | Scheduler + job queue, timeouts, retries, public only | `scheduler.py`: scheduler thread → `queue.Queue` → worker threads; 30 s page timeout, 2 retries with backoff; URLs with credentials or private/internal IPs are rejected |
| 3 | Text + screenshot, new snapshot only on hash change | SHA-256 of the normalised text; snapshot saved only if the hash differs |
| 4 | Noise filtering + sensitivity | Regex masks for times, dates, "x mins ago", counters, tokens; ads/carousels hidden before extraction; High / Medium / Low sensitivity |
| 5 | Visual picker | "Pick regions" loads the page in a sandboxed preview; click elements to save watch/ignore CSS selectors |
| 6 | Text diff, highlighted screenshot diff, side-by-side | Compare page for any two versions |
| 7 | Email + Telegram/Discord, no duplicates, daily digest | `alerts.py`; `UNIQUE(page_id, hash)` stops duplicate alerts; optional daily digest |
| 8 | Timeline + dashboard | Per-page version timeline; dashboard shows last checked, last changed, status (ok / error / blocked) |

**Bonus done:** keyword-only alerts, per-page RSS feed (`/rss/<id>`).

## Design questions

**1. Is a site outage or CAPTCHA a change?**
No. Timeouts and HTTP 4xx/5xx set the status to **error**; CAPTCHA pages, bot walls, HTTP 403/429 and
robots.txt blocks set it to **blocked**. In both cases no snapshot is saved and no alert is sent, so the
next successful check is compared with the last *real* version.

**2. If 200 users watch the same URL, is it fetched 200 times?**
No. The scheduler groups due pages **by URL** and creates one job per URL. The worker loads the page
**once** and extracts text/screenshots for every user's selectors from that single load. Pages on the same
URL that are due within the next 5 minutes are piggy-backed onto the same load.

**3. How to avoid endless alerts on pages that differ on every load?**
Three layers: (a) regex masking of timestamps, counters and tokens, plus hiding ads/carousels;
(b) when a change is suspected, the page is loaded a **second time** and only lines that appear in **both**
loads are kept — anything that changes on every load is dropped automatically; (c) alerts are unique per
content hash, so a page flipping A → B → A → B never alerts twice for the same content. Sensitivity and
ignore-regions give the user extra control.

**4. How to avoid hammering a site?**
Minimum interval of 15 min, a per-host gap (default 10 s) between requests, a small worker pool,
random jitter, exponential backoff (up to 24 h) after errors/blocks, robots.txt is respected, and the
URL de-duplication above.

## Run on Windows without typing commands

Install Python from python.org (tick **Add Python to PATH**), then double-click **start.bat**.
The first run installs everything (a few minutes); after that it starts in seconds and opens the dashboard.
Use **test-mode.bat** instead when testing with the `/demo` page.

## Run locally (terminal)

```bash
git clone https://github.com/<your-username>/website-change-detector.git
cd website-change-detector
python -m venv venv
# Windows: venv\Scripts\activate     Mac/Linux: source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python app.py
```
Open http://localhost:5000

Optional: copy `.env.example` to `.env` to set up email or change settings.

Docker: `docker build -t change-detector . && docker run -p 5000:5000 change-detector`

## How to test

**Unit tests** (no browser needed):
```bash
python -m pytest -v
```

**End-to-end with the built-in noisy demo page.** `/demo` changes its time, view count, session token,
ad and banner on *every* load — only the price is "real" content.

1. Allow localhost for testing: in `.env` put `ALLOW_PRIVATE=1` (Windows PowerShell: `$env:ALLOW_PRIVATE="1"`), then `python app.py`.
2. Add `http://127.0.0.1:5000/demo` on the dashboard. Within ~15 s it shows **ok** with 1 version (baseline).
3. Click **Check now** a few times → still 1 version, no alerts. *Noise is ignored.*
4. Open `http://127.0.0.1:5000/demo/set?price=₹749`, then **Check now** → version 2 appears with
   "New: Price: ₹749" and an alert is recorded. Open **Compare** to see the text diff, red-box screenshot diff and side-by-side view.
5. Set the price back and forth (999 → 749 → 999 → 749): the second 749 does **not** send another alert.
6. Add the demo URL a second time under another name → the server log shows a single `GET /demo` serving both.
7. **Pick regions** → click the price → Save. Only that element is watched from now on.
8. Alerts: create a Discord channel webhook (Channel settings → Integrations → Webhooks) and paste the URL,
   or for Telegram use `https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<CHAT_ID>`.
9. Real sites to try: `https://time.is` (changes every second → should stay quiet),
   `https://news.ycombinator.com` (real changes), a site with a CAPTCHA (→ **blocked**),
   `https://this-domain-does-not-exist-123.com` (→ rejected).

## Project structure

```
app.py         Flask routes: dashboard, page timeline, compare, picker, RSS, demo
scheduler.py   scheduler loop, job queue, workers, retries, backoff, URL de-duplication
checker.py     Playwright rendering, noise filtering, hashing, diffs, URL safety, robots.txt
alerts.py      email / Telegram / Discord, de-duplication, daily digest
db.py          SQLite helper
static/picker.js   visual region picker
templates/     HTML pages
tests/         pytest unit tests
```
