# Clipping Campaigns, YouTube Recovery & Earnings Guide

Your complete step-by-step operating guide for:
1. Fixing the **0-view YouTube Shorts bottleneck**
2. Configuring **all required & optional environment variables / API keys**
3. Joining & earning on **all 11 clipping platforms** (`WHOP`, `CLIPUR`, `CLIPSTER`, `CLIPSTAKE`, `DAREBAY`, `VUES`, `CLIPPING`, `CLIPFY`, `CLIPERSHQ`, `CLIPPINGEXE`, `STARLET`)
4. Tracking **individual platform/campaign payouts + cumulative total earnings** on your live dashboard

---

## 1. Why YouTube Shorts Stalled (And What We Just Fixed)

From inspecting your live `@leonidachao` YouTube Shorts channel and `scoreboard.csv`:
- **GTA-001 (`@ohnePixel`) broke out to 9,261–9,857 views** because it was **13.7s**, had **burned-in SRT speech captions**, a **tight setup -> crash payoff**, and an **intriguing Shorts title** (`he lasted three seconds on that bridge 💀`).
- **Over 10 later Shorts got 0–6 views** because of four specific bugs that are now fixed in code:
  1. **Broken YouTube Titles:** The old Telegram delivery merged the entire IG/TikTok caption (including `Credit: @...` and 5 `#hashtags` glued without spaces) into the YouTube Shorts title (`this is aCredit: @esfandtv...`). YouTube Shorts now receives a **clean, standalone 40–62 char title** in its own 1-tap Telegram message.
  2. **Missing Speech Captions & Dead Air:** Cloud clips previously had zero spoken-word captions and arbitrary time slices instead of real setup-to-payoff framing. The new `scripts/stream_clipper_engine.py` transcribes words via **Groq Whisper / `faster-whisper` / `mlx-whisper`**, removes silent dead air (`silencedetect`), and burns **2–3 word kinetic captions** right at the facecam/gameplay seam.
  3. **Low Stranger-Comprehension Moments:** Clips are now scored 0–10 on the `stream-clipper` rule: *"Would a total stranger understand what's happening within the first 2 seconds?"* Anything below 7 is dropped automatically.
  4. **Daily Upload Spam & Sync Collision:** Default `DAILY_CLIP_COUNT` is reduced from `5` to `3` higher-quality clips/day, and `scripts/sync_stats.py` now prevents two posts (`GTA-006` & `GTA-009`) from accidentally claiming the same video ID.

> **Action for You on YouTube Studio:** When posting each clip from Telegram, paste **Message 5 (YouTube Shorts Title ONLY)** into the Title field and **Message 6** into the Description — never paste the IG/TikTok caption into the YouTube Title.

---

## 2. Environment Variables & API Keys (`.env` + GitHub Actions Secrets)

Add private keys to **local `.env`** AND **GitHub Repo -> Settings -> Secrets and variables -> Actions -> Secrets**. Add public settings under **Actions -> Variables**.

| Variable | Cost | Required? | Where to Get It & Why It Matters |
| :--- | :---: | :---: | :--- |
| `TELEGRAM_BOT_TOKEN` | \$0 | **Yes** | `@BotFather` on Telegram (`/newbot`) — sends your daily clips |
| `TELEGRAM_CHAT_ID` | \$0 | **Yes** | `https://api.telegram.org/bot<TOKEN>/getUpdates` after messaging your bot |
| `TWITCH_CLIENT_ID` | \$0 | **Yes** | `https://dev.twitch.tv/console/apps` — scouts high-view Twitch clips & VOD clips |
| `TWITCH_CLIENT_SECRET` | \$0 | **Yes** | Same Twitch Developer app -> **New Secret** |
| `OPENROUTER_API_KEY` | \$0 | **Strongly Recommended** | `https://openrouter.ai` — powers free vision facecam detection, stranger scoring, and clean YouTube titles |
| `GROQ_API_KEY` | \$0 | **Recommended** | `https://console.groq.com` — ultra-fast (<1s) free-tier `whisper-large-v3-turbo` word timestamps for kinetic captions (falls back to free local `faster-whisper` / `mlx-whisper` if unset) |
| `SCRAPE_CREATORS_API_KEY` | \$0 (100 free cr) | Optional | `https://scrapecreators.com` — auto-syncs TikTok & Instagram views/followers |
| `WHISPER_BACKEND` | \$0 | Optional (Var) | `auto` (default), `groq`, `mlx`, `faster_whisper`, `whisper_cli`, or `none` |
| `CONTENT_NICHE` | \$0 | Optional (Var) | `gta6` (default) or `gaming` (unlocks broader gaming clipping campaigns) |
| `ACTIVE_CAMPAIGN_ID` | \$0 | Optional (Var) | Campaign ID from `data/campaigns.json` to auto-check rules & inject required tags |
| `DAILY_CLIP_COUNT` | \$0 | Optional (Var) | `3` (recommended for new YouTube Shorts channel quality) |

---

## 3. Multi-Window Telegram Fallback Schedule

Your daily Telegram drop now runs across **4 staggered UTC+1 (WAT) windows**:
1. **09:37 WAT (Primary)** (`37 8 * * *` UTC)
2. **12:17 WAT (Backup 1)** (`17 11 * * *` UTC)
3. **15:43 WAT (Backup 2)** (`43 14 * * *` UTC)
4. **19:19 WAT (Backup 3)** (`19 18 * * *` UTC)

**How idempotency works:**
- The state file `data/daily_drops.json` records every day's WAT date (`YYYY-MM-DD`) and delivered `post_id`s.
- If **09:37 WAT** succeeds, **12:17 / 15:43 / 19:19 WAT** automatically skip sending anything (`"Daily drop already completed for today"`).
- If an earlier window fails or only delivers 1 of 3 clips, the next backup window automatically delivers **only the missing clips** to finish your daily drop.

---

## 4. Step-by-Step: Joining & Earning on All 11 Clipping Platforms

> **No clipping platform needs an API key or password in GitHub Secrets.** You connect your social handles inside each platform's own web dashboard (usually via a temporary bio verification code), while our repo tracks your accounts and earnings in two ways:
> 1. **Social account views & followers (GitHub Variables):** `YT_CHANNEL_URL`, `TIKTOK_HANDLE`, and `IG_HANDLE` support **comma-separated multiple accounts** (e.g. `https://youtube.com/@leonidachao,https://youtube.com/@secondpage`) synced automatically every 6 hours.
> 2. **Per-campaign USD earnings (`scripts/campaigns.py`):** When you submit a posted URL to any campaign, log it once with `log-submission`. Every 6 hours, `campaigns.py sync-views` pulls verified views from `scoreboard.csv` (and directly via `yt-dlp` for any YouTube Short URL on secondary accounts) to recalculate your Pending and Paid USD.

> **YouTube Studio Tip:** Always set your upload **Category = Gaming** (not *People & Blogs*), and paste **only** the `YOUTUBE SHORTS TITLE ONLY` message from Telegram into the YouTube Title box.

### Universal 5-Step Setup Before Applying Anywhere
1. **Profile Hygiene:** Ensure `@leonidachao` (YouTube), `@leonida.chaos4` (TikTok), and `@leonida_chaos4` (Instagram) have clean fan-page bios, no Rockstar official logos, and **at least 10–15 clean Shorts/Reels posted**.
2. **Payout Account Ready:** Set up at least one international payout method accepted across these platforms:
   - **Wise / Payoneer USD account** (for Stripe/ACH bank payouts)
   - **USDC / USDT wallet** (Solana / Base / Polygon — fastest zero-border option supported by Whop & most clipping hubs)
3. **One Account Per Person:** Never create duplicate accounts on any clipping network — platforms run automated fraud checks and forfeit funds.
4. **Keep Your Best 2–3 Clips Ready:** Platforms that vet editors (like Clipur & Clippers HQ) ask for 1–2 portfolio links. Send your `GTA-001` (`@ohnePixel`) video + another clean 40/60 facecam kinetic-caption edit.
5. **Track Every Platform & Campaign in `data/campaigns.json`:** Log platforms you join and campaigns you enter so your dashboard calculates **individual + cumulative USD earnings** automatically.

### Platform-by-Platform Directory & Onboarding Steps

| # | Platform | URL | Typical CPM | Min Payout & Fee | Exact Steps to Join & Earn |
| :-: | :--- | :--- | :---: | :---: | :--- |
| 1 | **Whop Content Rewards** | https://whop.com | \$1.00–\$3.00 | \$10 min · ~10% fee | Create Whop account → open **Content Rewards** → filter *Gaming / Streamers* → read brief → post clip → submit live URL within brief window (auto-approves ~48h, tracks 7d, settles after ~3d hold). |
| 2 | **Clipur** | https://www.clipur.com | \$1.25–\$2.50 | \$15 min · ~10% fee | Apply on `clipur.com` / Whop Clipur hub → submit sample GTA 6 stacked edit → link your IG/TT/YT channels → pick creator briefs & submit live links. |
| 3 | **Clipster** | https://www.clipster.com | \$1.20–\$2.25 | \$10 min · ~10% fee | Register on `clipster.com` → verify social pages via bio code → join active Twitch/YouTube streamer pools → upload & submit live clip URLs. |
| 4 | **ClipStake** | https://www.clipstake.com | \$1.50–\$3.00 | \$20 min · ~10% fee | Sign up on `app.clipstake.com` → connect Shorts/TikTok/Reels analytics → join open CPM campaigns (watch per-clip cap & min-view threshold) → submit live post URLs. |
| 5 | **DareBay** | https://darebay.com | \$1.25–\$2.50 | \$10 min · ~10% fee | Create wallet-enabled account on `darebay.com` → link posting handles → claim open stream bounties/contests → post & submit URL before pool closes. |
| 6 | **Vues** | https://vues.app | \$1.00–\$2.00 | \$10 min · ~9% fee | Sign up at `vues.app` → verify social handles → enroll in self-serve CPM campaigns → paste post URLs immediately after publishing so verified tracking starts on view #1. |
| 7 | **Clipping.io** | https://www.clipping.io | \$1.25–\$2.50 | \$15 min · ~10% fee | Register at `clipping.io` → complete account verification → read brief rules (geo, tags, length) → submit posted links into the campaign tracker. |
| 8 | **Clipfy** | https://clipfy.app | \$1.00–\$2.00 | \$10 min · ~10% fee | Create creator profile on `clipfy.app` → connect YouTube/TikTok/IG → join gaming or creator campaigns → submit URLs after posting. |
| 9 | **Clippers HQ** | https://clipershq.com | \$1.25–\$2.50 | \$15 min · ~9.5% fee | Sign up on `clipershq.com` & join Discord → verify your single account + social pages → pick streamer briefs → obey strict no-bot & tag rules. |
| 10 | **Clipping.exe** | https://clip.tech | \$1.40–\$3.00 | \$15 min · ~10% fee | Apply via `clip.tech` → connect clean vertical clipping pages → join vetted gaming/creator campaigns → submit URLs for 30-day verified tracking. |
| 11 | **Starlet** | https://starlet.gg | \$1.25–\$2.50 | \$10 min · ~10% fee | Sign up on `starlet.gg` (gaming-native) → link YouTube/TikTok/IG → grab gaming VODs → cut with `stream-clipper`, post, and submit links. |

---

## 5. Logging Campaigns & Tracking Earnings on Your Dashboard

Use `scripts/campaigns.py` (or the interactive calculator in the **Campaigns & Earnings** tab on your dashboard):

```bash
# 1. Mark a platform as joined / active
python3 scripts/campaigns.py join-platform whop --status active --handle "@leonidachao"

# 2. Register a campaign you joined (with its exact brief rules!)
python3 scripts/campaigns.py add-campaign \
  --id WHOP-GAMING-1 \
  --platform whop \
  --name "Streamer Chaos CPM Pool" \
  --creator "@Streamer" \
  --cpm 1.50 --cap 150 --min-views 1000 --max-sec 30 --niche gaming \
  --required-tags "#gaming" "#clips"

# 3. Cut clips checked against that campaign's rules
python3 scripts/daily_cloud_pipeline.py --niche gaming --campaign WHOP-GAMING-1 --clip-count 3

# 4. Log a posted URL to track individual & total earnings
python3 scripts/campaigns.py log-submission \
  --campaign WHOP-GAMING-1 --post-id GTA-021 --social youtube \
  --url "https://youtube.com/shorts/YOUR_ID" --views 15000 --status approved

# 5. Mark paid once you withdraw, and rebuild the dashboard
python3 scripts/campaigns.py mark-paid SUB-001
python3 scripts/dashboard_data.py
```

---

## 6. Clipping Any Long VOD On-Demand (`stream-clipper`)

To clip any campaign's full Twitch or YouTube VOD using viewer signals (YouTube *Most replayed* heatmap, Twitch clip clusters, chat hype):

```bash
python3 scripts/stream_clipper_engine.py --vod-url "https://www.youtube.com/watch?v=VIDEO_ID"
```

Or in chat, paste the VOD link + campaign rules and say: **"use stream-clipper on this VOD"**.
