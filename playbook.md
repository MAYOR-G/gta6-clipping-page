# Playbook — GTA 6 Faceless Clipping Page

Read this file at the start of every run. It is the single source of truth for rules, guardrails, format standards, scheduling, and experiments.

---

## 1. Identity & Scope
- **Niche:** Funny *Grand Theft Auto VI (GTA 6)* moments from real streamers.
- **Platforms:** Instagram Professional (Reels), TikTok, and YouTube Shorts.
- **Time Zone:** WAT (UTC+1 / `Africa/Lagos`).
- **Publishing Stack (100% Free):** `scripts/safe_publish.py` (`queue` local ready-to-post bundles by default; optional free self-hosted `postiz` REST API).
- **Target Creator Pool:**
  - *Primary watchlist (real facecam + chaos, dedupe strictly `<3` pages):* `@ohnepixel` (Twitch), `@IShowSpeed`, `@KaiCenatLive`, `@Caylus` (verify active YT/Twitch handle), `@Wynnsanity` (check live streams), plus **mid-size Twitch streamers with real facecams** (highest yield, lowest competition).
  - *Hold / Needs explicit call:* `@TheBurntPeanut` (animated peanut avatar — requires explicit exception to real-facecam rule).
  - *Skip for primary format:* `@MKIceAndFire` and `@UG_Z` (no-commentary / no-facecam walkthroughs with high launch-week story-spoiler risk).
- **Branding Rules (when setting up profiles — never edit without human approval):**
  - Searchable display name & handle (e.g., *Vice City Chaos*).
  - Bio must include: `"Fan page. Not affiliated with Rockstar."` Never imply official status.
  - Non-official profile photo readable at 40 px (no Rockstar logos, key art, or official character renders).
  - TikTok posts marked **not AI-generated** (`is_ai_generated: false`); turn off any profile AI label.
  - **Profile quarantine:** Make all profile edits in one sitting, run `python3 scripts/unpause.py --record-profile-edit`, and wait **>= 2 hours** before publishing anything.

---

## 2. Hard Guardrails (Non-Negotiable)
- **Never** pay for, sign up for, or upgrade any tool or service without explicit human approval.
- **Never** DM, comment, or message anyone without explicit human approval.
- **Never** change profile names, bios, handles, photos, or account settings without explicit human approval.
- **Never** print, log, or commit API keys or secrets (env vars only).
- **Same-day takedowns:** If any creator or rights holder requests removal, remove the clip from all 3 platforms the same day, log it in `takedowns.md`, and alert the human immediately.
- **Content bans:** No sexual content, no animal cruelty, no slurs, no story spoilers during launch week, no leaks/datamines/piracy/early-play tricks/exploits, no AI-generated gameplay, no unverified rumors (rumors require human yes + `"RUMOR:"` label).
- **Approval ladder:**
  1. Human approves the **first 3 clips individually** (exact rendered MP4, QA frames, and caption).
  2. Switch to routine autopilot only after those 3 are approved.
  3. Anything involving money, messages, profile edits, rumors, or rule changes ALWAYS requires human approval.

---

## 3. Verification & Scouting Pipeline
1. **Scout wide:** Pull fresh GTA 6 clips (last 3–24h, English) from Twitch Helix API / YouTube live streams + trending TikTok/Shorts terms. Never trust titles—build a contact sheet (`scripts/sheet.sh`) first. Expect to reject 90%+.
2. **Real GTA 6 verification (all 3 required):**
   - **Fresh:** Created within ~24 hours (and post-launch).
   - **Visual proof:** Save a proof frame (`proofs/<post_id>_proof.png`) showing a GTA 6-only cue (Vice City skyline, Lucia/Jason, new HUD, new police livery — NOT GTA V!). Cite it in `log.md`.
   - **Context:** Launch stream title/overlay corroborates it.
3. **Market dedupe:** Search X, TikTok, and YouTube Shorts for the same moment **before cutting**. If **3+ pages** already posted it, skip it or find a clearly different angle.
4. **The Send Test:** Write down **who** a viewer would send this clip to and **what they would type** (e.g., *"bro this is literally you driving"*). No obvious friend = no post.

---

## 4. The Edit Formula (`scripts/clip.py`)
The edit is the product. Subtitles + a credit line alone do NOT count as original on Instagram, TikTok, or YouTube.

```text
[TEASER 2–4s] -> [WHITE FLASH + WHOOSH] -> [SETUP 3–6s] -> [ESCALATION]
-> [PAYOFF w/ 1 slow-mo OR freeze] -> [REACTION HOLD 0.5–1.5s + PUNCHLINE] -> hard cut loop
```

- **Specs:** `1080x1920`, `30 fps`, **12 to 35 seconds** (**~30s target**, **5 clips/day** until crossing monetization milestones, then test 61–75s mini-compilations for TikTok Creator Rewards).
- **Layout:** Stack (`facecam` cropped tight inside its border on top `1080x720`; clean `gameplay` below `1080x1200` with chat, watermarks, and foreign captions cropped out).
- **Teaser:** Tease the *end* (2–4s, finishing the word/action), then start full play from the setup with **8+ seconds** of fresh footage before the payoff returns.
- **Text rules:**
  - ALL CAPS white with black outline, placed strictly in the **gameplay area** (never over the facecam).
  - **Adds context, never describes** (BANNED: `"STREAMER DOES X"`).
  - Include a search keyword in frame 1 (`GTA 6`, `LUCIA`, `JASON`, `NPC`, `COPS`, or streamer name).
  - Never stack punchlines inside the teaser window.
  - **No emoji** in burned-in text.
- **Subtitles:** Burned-in, proofread, never guessed. Prefer burned-in Twitch captions (shifted `-0.2s`) or hand-timing. Read in-game captions first so an NPC line is never attributed to the streamer.
- **Comedic edit:** At most **1** slow-mo (`0.35–0.5x`) or freeze (`0.5–1.0s`) on the funniest frame. Never use whole-frame zoom on a stack layout.
- **Audio:** Boost quiet mics near `-20 dB` mean; keep the streamer's real laugh audible with zero music covering it.
- **Visual QA before scheduling:** Inspect `hook.png`, `flash.png`, `payoff.png`, and `end.png` at full resolution.

---

## 5. Captions, Cadence & WAT (UTC+1) Slots
- **Caption template:**
  ```text
  <1 to 6 word joke in a friend's voice with game/character keyword> <at most 1 emoji>

  Credit: @creator (Twitch/YouTube)

  #gta6 #<streamer> #<specific> #gaming #funnymoments
  ```
- **Caption rules:** Sentence case, 3–5 hashtags max, never end with `?`, no em dashes, never repeat within 7 days, no banned words (`official`, `leak`, `early access`, `free copy`, `giveaway`). Put one honest discussion question in the **Instagram first comment**.
- **Cadence:** Space posts **>= 2.5 hours apart** per platform, around the clock, filling overnight gaps first.
- **WAT (UTC+1) Global Posting Slots (target up to 8/day once autopilot is live):**
  - `07:30 WAT` *(UK/EU morning + India noon)*
  - `10:30 WAT` *(EU mid-morning + US 4:30 AM ET breakout window)*
  - `13:30 WAT` *(EU/UK midday + US East morning)*
  - `16:30 WAT` *(US East midday + EU afternoon)*
  - `19:30 WAT` *(US afternoon + EU evening)*
  - `22:30 WAT` *(US East prime time)*
  - `01:30 WAT` *(US West prime time + APAC morning)*
  - `04:30 WAT` *(APAC afternoon — test & monitor)*
  - **Avoid `05:30–07:00 WAT`** (`22:30–00:00 CT` weak historical TikTok window).

---

## 6. Metrics, Experiments & Anti-Stuck Protocol
- **Priority metrics:** (1) **Share / send rate >= 1.0% at 24h**, (2) Like rate, (3) Comment rate, (4) Follows per 1,000 views, (5) Watch time / YT "Stayed to watch" %, (6) Raw plays at 2h and 24h.
- **Judge ratios, not raw views:** Never judge a format in `< 24 hours`; never judge a time slot in `< 14 days`.
- **Momentum rule:** If a post beats **3x** the account median, queue a same-streamer or same-theme follow-up within 24 hours.
- **Anti-stuck rules (per platform):**
  - `< 20` plays after **1 hour** = **STUCK**.
  - Check IG Account Status / TikTok Studio before any repost.
  - Delete stuck original first, then repost **once** as a **fresh cut** (new teaser, trim, hook, caption — never re-upload the exact file).
  - **2 stuck in a row on one platform** → pause that platform 24h (`scripts/unpause.py --pause <platform>`) and keep others running; flag human.
  - **All platforms stuck at once** → `scripts/unpause.py --pause-all` and alert human immediately (even overnight).
  - **4+ IG Reels stalling in the same play band (e.g. 220–240)** → audit Account Status & trigger `EXP-R6` (Originality Ladder).

---

## 7. Disk Hygiene & Monetization Gate
- **Disk hygiene:** Delete raw downloads once cut and queued/scheduled, and delete rejected downloads immediately. Keep **no raw files older than 24 hours**. Check for stuck `ffmpeg` processes if a render exceeds 5 minutes.
- **Monetization Phase 1 (Days 1–14):** **Audience only** — no paid posts, affiliate links, or clipping campaigns for the first 2 weeks. Review monetization triggers (Content Rewards on Whop, affiliates, sponsors) only after 14 days of real scoreboard data.
