# GTA 6 Faceless Clipping Page — Complete System & GitHub Cloud Guide

An end-to-end, **100% free (\$0/month)** faceless GTA 6 clipping pipeline for **Instagram Reels**, **TikTok**, and **YouTube Shorts** running automatically on **GitHub Actions every day at 10:00 AM WAT (UTC+1)**, delivering structured ready-to-post packs straight to your **Telegram**, and publishing your live analytics dashboard on **GitHub Pages**.

---

## 1. How the Whole System Works (Big Picture)

Every morning at **10:00 AM WAT (`09:00 UTC`)**, GitHub Actions wakes up automatically (even when your Mac is turned off!) and runs `scripts/daily_cloud_pipeline.py`:

1. **Syncs your live account stats (`scripts/sync_stats.py`)** — pulls your YouTube Shorts views/likes/comments/subscribers (free via `yt-dlp`) and Instagram/TikTok stats (via free Scrape Creators credits) into `scoreboard.csv`.
2. **Checks for stuck posts (`scripts/stuck_check.py`)** — flags any post under 20 plays after 1 hour and enforces the 2-in-a-row auto-pause rule.
3. **Scouts fresh `<24h` GTA 6 clips via free Twitch Helix API** — English only, skipping already-seen clips and unsafe titles.
4. **Writes original hooks, punchlines & captions** — uses a free OpenRouter model (`OPENROUTER_API_KEY`) if provided, or a clean guardrail-checked fallback.
5. **Cuts `~20-second` vertical clips (`scripts/clip.py`)** — `1080×1920`, `30 fps`, payoff-first teaser + white flash + whoosh + slow-mo beat + punchlines + credit line, using verified per-streamer facecam crops (`data/streamer_crops.json`) and clean fallback for new streamers.
6. **Validates & queues (`scripts/safe_publish.py`)** — enforces every caption rule, SHA-256 anti-double-post ledger, TikTok `not AI-generated` flag, cover-frame extraction, and spaced WAT posting slots (`13:30`, `16:30`, `19:30`, `22:30`, `01:30 WAT` — always 3h apart).
7. **Delivers to your Telegram in 5 clean tap-to-copy messages per clip (`scripts/telegram_deliver.py`):**
   1. **Slot Header** (`CLIP 1 OF 3 — GTA-004 | Recommended Post Time: 13:30 WAT`)
   2. **MP4 Video** (`1080×1920`)
   3. **Cover Image** (`cover.png`)
   4. **Caption** *(standalone message — 1 tap to copy)*
   5. **Instagram First Comment** *(standalone message — 1 tap to copy)*
8. **Updates your Live Dashboard (`dashboard/index.html`)** — commits updated `scoreboard.csv`, `log.md`, and `dashboard/index.html`, deletes raw/rendered MP4s to keep the repo small, and auto-deploys your dashboard to **GitHub Pages** so you can check views, followers, and experiments from your phone!

---

## 2. Step-by-Step: Deploy to GitHub & Set Up Secrets (Your `.env` File)

> **CRITICAL SECURITY RULE:** **NEVER** push your `.env` file to GitHub! Our `.gitignore` already blocks `.env` automatically so your keys stay private. Instead, you store those exact keys inside **GitHub Repository Secrets**, where GitHub Actions reads them securely as environment variables without ever printing them.

### Step A — Create Your Free Keys (10 minutes total, \$0, no card)
1. **Telegram Bot (Free — required to receive clips on your phone):**
   - Open Telegram → search **`@BotFather`** → send `/newbot` → choose a name & username → copy the **Bot Token** (`TELEGRAM_BOT_TOKEN`).
   - Open a chat with your new bot and tap **Start** (send `"hi"`).
   - Open this URL in your browser (replace `<TOKEN>`):
     `https://api.telegram.org/bot<TOKEN>/getUpdates`
   - Find `"chat":{"id": 123456789}` → copy that number (`TELEGRAM_CHAT_ID`).
2. **Twitch Developer App (100% Free — required for automatic cloud scouting):**
   - Go to `https://dev.twitch.tv/console/apps` → sign in → **Register Your Application**.
   - Name: `gta6-clip-scout`, OAuth Redirect URL: `http://localhost`, Category: `Application Integration`.
   - Click **Manage** → copy **Client ID** (`TWITCH_CLIENT_ID`) and click **New Secret** → copy **Client Secret** (`TWITCH_CLIENT_SECRET`).
3. **Optional Free Keys (add anytime):**
   - **OpenRouter (Free tier):** Sign up at `https://openrouter.ai` → create an API key (`OPENROUTER_API_KEY`) for custom AI hooks/captions in cloud runs.
   - **Scrape Creators (100 free credits, no card):** Sign up at `https://scrapecreators.com` → copy key (`SCRAPE_CREATORS_API_KEY`) for automatic TikTok & Instagram follower/view tracking.

### Step B — Push This Folder to a New GitHub Repository
Create a new **Private** (or Public — GitHub Pages is free on Public repos; Private repos need GitHub Pro for Pages) repo on GitHub named `gta6-clipping-page` (leave it empty—no README), then run in your terminal:

```bash
cd "/Users/mayorg/Documents/MAYOR G PROJECT/gta6-clipping-page"
git init
git branch -M main
git add .
git commit -m "Initial GTA 6 faceless clipping pipeline"
git remote add origin https://github.com/<YOUR_GITHUB_USERNAME>/gta6-clipping-page.git
git push -u origin main
```

*(Note: `.env`, `raw/`, and `renders/*.mp4` are automatically ignored by `.gitignore` so no secrets or giant videos are ever pushed!)*

### Step C — Add Your `.env` Keys to GitHub Secrets
On GitHub, open your repository → **Settings** → **Secrets and variables** → **Actions**:

1. Under the **Secrets** tab, click **New repository secret** and add:
   | Secret Name | Required? | Where You Got It |
   | :--- | :---: | :--- |
   | `TELEGRAM_BOT_TOKEN` | **Yes** | `@BotFather` |
   | `TELEGRAM_CHAT_ID` | **Yes** | `/getUpdates` numeric chat ID |
   | `TWITCH_CLIENT_ID` | **Yes** | `dev.twitch.tv/console/apps` |
   | `TWITCH_CLIENT_SECRET` | **Yes** | `dev.twitch.tv/console/apps` |
   | `OPENROUTER_API_KEY` | Optional (Free) | `openrouter.ai` |
   | `SCRAPE_CREATORS_API_KEY` | Optional (Free 100 cr) | `scrapecreators.com` |

2. Under the **Variables** tab (same page!), click **New repository variable** and add:
   | Variable Name | Example Value | Purpose |
   | :--- | :--- | :--- |
   | `DAILY_CLIP_COUNT` | `3` *(or `5` / `8`)* | How many clips arrive at 10:00 AM WAT daily |
   | `TARGET_CLIP_SEC` | `20` | Target clip duration in seconds |
   | `YT_CHANNEL_URL` | `https://www.youtube.com/@YourHandle` | Free automatic YouTube Shorts view & sub sync |
   | `TIKTOK_HANDLE` | `yourhandle` *(no @)* | Automatic TikTok stats sync (with Scrape Creators) |
   | `IG_HANDLE` | `yourhandle` *(no @)* | Automatic Instagram stats sync (with Scrape Creators) |

### Step D — Turn On Your Free Live Dashboard (GitHub Pages) & Test Run!
1. In your GitHub repo → **Settings** → **Pages** → under **Build and deployment → Source**, select **GitHub Actions**.
2. Go to the **Actions** tab → click **Daily 10 AM WAT GTA 6 Clip Drop & Dashboard** → click **Run workflow** (top-right).
   - You can choose how many clips (`3`, `5`, `8`) and test with `dry_run = true` first, then run live with `dry_run = false`!
3. Within ~2 minutes:
   - Your Telegram receives each clip in structured 5-part format (**Slot Header → Video → Cover → Caption → IG First Comment**).
   - Your live dashboard goes online at `https://<YOUR_GITHUB_USERNAME>.github.io/gta6-clipping-page/` — bookmark it on your phone!
4. Every day after that, it runs automatically at **10:00 AM WAT** with zero manual triggers.

---

## 3. Send Your First 3 Already-Cut Clips (`GTA-001`, `GTA-002`, `GTA-003`) to Telegram Now

Before waiting for tomorrow's 10 AM cloud drop, you can deliver the 3 clips already rendered locally (`renders/GTA-001.mp4`–`GTA-003.mp4`) straight to your phone:

```bash
cd "/Users/mayorg/Documents/MAYOR G PROJECT/gta6-clipping-page"
cp .env.example .env
# Edit .env and paste TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
set -a; source .env; set +a
python3 scripts/telegram_deliver.py --all-queued
```

---

## 4. Folder & File Map

```text
gta6-clipping-page/
├── .github/workflows/daily_10am_wat.yml   # Free daily 10 AM WAT cloud cron + GitHub Pages deploy
├── README.md                              # Complete setup & usage guide
├── requirements.txt                       # Pillow + yt-dlp for GitHub Actions
├── playbook.md                            # Operating rules & guardrails
├── scoreboard.csv                         # 19-column analytics tracker
├── log.md                                 # Post audit trail + scout rejects
├── learning.md                            # Dated lessons + experiments (EXP-R1..R6)
├── takedowns.md                           # Same-day takedown log + Do-Not-Clip list
├── dashboard/index.html                   # Live offline-safe frontend dashboard
├── data/
│   ├── streamer_crops.json                # Verified facecam/gameplay crop presets per streamer
│   ├── publish_ledger.json                # Anti-double-post SHA-256 ledger
│   ├── pause_state.json                   # Emergency kill switch & 2h profile quarantine
│   └── telegram_sent.json                 # Prevents duplicate Telegram deliveries
└── scripts/
    ├── daily_cloud_pipeline.py            # Main automated 10 AM WAT cloud runner (~20s clips)
    ├── telegram_deliver.py                # 5-part structured Telegram drop (--all-queued)
    ├── sync_stats.py                      # Auto-pulls YT/TT/IG views & followers -> scoreboard.csv
    ├── clip.py                            # 1080x1920 30fps vertical clip renderer
    ├── sheet.sh                           # Timestamped contact-sheet generator
    ├── safe_publish.py                    # Guardrailed publisher & ledger
    ├── stuck_check.py                     # Stuck-post detector (<20 plays @ 1h)
    ├── unpause.py                         # One-command pause/unpause & quarantine manager
    ├── dashboard_data.py                  # Rebuilds dashboard/index.html data block
    └── lane_research.py                   # Re-runnable lane benchmark scanner
```

---

## 5. Scaling Clip Length & Monetization Later

- **Now (Launch & Growth):** `TARGET_CLIP_SEC=20` (~18–21 s) balances strong completion rate with rich comedy payoff across IG Reels, TikTok, and YouTube Shorts.
- **Days 1–14:** Stay audience-only (no ads/affiliates/paid campaigns).
- **Fastest income after Day 14 (works on 20s clips):** Whop **Content Rewards** (\$1.00–\$1.50/1K views, no follower minimum) → Bio Affiliates → Sponsors (\$8.50–\$25/1K median views at 10K+ followers).
- **TikTok Creator Rewards (`> 60s`):** Once approaching 5K–10K TikTok followers, increase `TARGET_CLIP_SEC` or run 62–75s multi-moment compilations (`keep: [[...], [...], [...]]`).
