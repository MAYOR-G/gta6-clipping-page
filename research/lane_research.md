# Step 1 — GTA 6 Funny-Moments Lane Research

**Date:** 2026-11-20 (Launch Week — Day 2 after Nov 19 global launch / Nov 18 NZ rolling launch)  
**Platforms:** Instagram Reels, TikTok, YouTube Shorts  
**Timezone:** WAT (UTC+1)

> **SAMPLE BIAS LABEL — WINNERS' SAMPLE:**  
> Most-liked and top-search posts are a **winners' sample**, not a random slice of all uploads. Use these numbers for **ratios, formats, and packaging patterns**, never as baseline view expectations for a brand-new account.  
> **REUSE RULE:** Reuse **nothing** from any post listed below. Research only.

---

## 1. Macro Lane Signal (Why GTA 6 Right Now)

- **Historic live supply:** *Grand Theft Auto VI* launched Nov 19, 2026 (Nov 18 rolling launch in NZ) and peaked at roughly **7.91M concurrent viewers** across Twitch, YouTube, and Kick (~3.3M+ concurrent viewers on Twitch alone; Streams Charts / Streamlabs / IGN).
- **Content supply:** Thousands of streamers are live around the clock, generating fresh gameplay chaos every hour.
- **Global audience:** Demand is worldwide (Americas, EU/UK, Africa, Asia/India), which supports around-the-clock posting from WAT (UTC+1).

---

## 2. TikTok & Instagram Benchmarks (Guide Baseline + Live API Status)

> **API Status:** Live 24-hour TikTok & Instagram scraping is **PENDING `SCRAPE_CREATORS_API_KEY`** (free tier gives 100 credits with no credit card). As soon as you add the key to `.env`, run:
> ```bash
> python3 scripts/lane_research.py --out research/lane_scan.json
> ```
> It queries 8 GTA 6 terms (`gta 6 funny`, `gta 6 streamer`, `gta 6 npc`, `gta 6 cops`, `gta 6 lucia`, `gta 6 fails`, `ishowspeed gta 6`, `ohnepixel gta 6`), respects a 40-credit cap, and never prints your key.

### Verified Baseline Ratios (from Guide's Launch-Window TikTok Pulls — Winners' Sample)
- **Pre-launch pull (235 TikToks):** `"gta 6 funny"` hit **1.28M median plays** at a **15-second median length** (vs. 38K median for long GTA RP clips and 57K for RDR2).
- **24-hour 8-term pull (189 TikToks, most-liked):**
  - Median **share rate:** **0.72%** (our target: **>= 1.0%** at 24h)
  - Median **like rate:** **7.2%**
  - Median **comment rate:** **0.05%**
- **Top share-rate themes (what viewers send to friends):**
  1. Relatable chaos / "this is so you" moments: **9.73%** share rate
  2. NPC dialogue / absurd Leonida AI: **5.69%** share rate
  3. NPCs or cops fighting back: **5.09%** share rate
- **Streamer-name vs. relatable trade-off:** Streamer-name clips earned higher raw search views, while relatable/NPC moments earned much higher share rates.
- **Caption style benchmark:** Winning captions were **1–6 words in a friend's voice** with a search keyword (game/character) and **no trailing question** (caption questions correlated with **0.17x views** in the guide's cited study).

---

## 3. Live YouTube Shorts Scan (Nov 18–20, 2026 — 20 Fresh Shorts via `yt-dlp`)

*Note: YouTube Shorts does not expose public share counts (`share_rate` = unavailable publicly). Ratios below use `views >= 10` to avoid 1-view distortion.*

### By Video Length
| Length Bucket | Count | Median Views | Median Like Rate | Median Comment Rate |
| :--- | :---: | :---: | :---: | :---: |
| `<= 20s` | 2 | 11 | 10.53% *(n=1)* | n/a |
| `21–40s` | 7 | 8 | n/a | 3.39% *(n=1)* |
| `41–65s` | 11 | 524 | 0.94% *(n=8)* | 0.30% *(n=7)* |

### By Title / Caption Style
| Caption Style | Count | Median Views | Median Like Rate | Median Comment Rate |
| :--- | :---: | :---: | :---: | :---: |
| Short (1–6 words) | 4 | 5 | 2.60% *(n=1)* | n/a |
| Medium (7–10 words) | 7 | 18 | 8.04% *(n=2)* | 3.39% *(n=1)* |
| Long (11+ words) | 8 | 2,415 | 0.65% *(n=5)* | 0.46% *(n=6)* |
| Question (`?`) | 1 | 196,719 | 1.12% *(n=1)* | 0.16% *(n=1)* |

### Top 10 Fresh YouTube Shorts (`>= 15` views, ranked by like rate then views)
*(Note: YouTube Shorts share rate is private; ranked by like rate as proxy.)*

| # | Date | Len | Views | Like % | Cmt % | Channel | Title | Link |
| :-: | :--- | :-: | --: | --: | --: | :--- | :--- | :--- |
| 1 | 2026-11-19 | 20s | 19 | 10.53% | n/a | Tae Gaming | GTA 6 First Person to fail taking a CAR! | https://www.youtube.com/shorts/4ZzTambFhP0 |
| 2 | 2026-11-19 | 61s | 18 | 5.56% | n/a | G6 GAMING YT | I can throw her \|\| GTA 6 | https://www.youtube.com/shorts/HkP1ZjVjHIM |
| 3 | 2026-11-19 | 49s | 524 | 3.63% | 1.34% | *(unlisted)* | GTA 6 NPCs wish Rockstar delayed the game for another year | https://www.youtube.com/shorts/PW0FAAmxqmA |
| 4 | 2026-11-19 | 57s | 77 | 2.60% | n/a | G6 GAMING YT | Biker Gang in GTA 6 #gta6 | https://www.youtube.com/shorts/Gwft5PjDFwc |
| 5 | 2026-11-18 | 61s | 196,719 | 1.12% | 0.16% | xQc Clips | Literally WHAT is GOING ON in GTA 6?? \| xQc GTA VI | https://www.youtube.com/shorts/wMsR4TJe1T8 |
| 6 | 2026-11-18 | 51s | 61,191 | 0.77% | 0.07% | xQc Clips | xQc gets into a FATAL CAR CRASH IN GTA 6 \| xQc GTA VI | https://www.youtube.com/shorts/LphAOQ6wMm4 |
| 7 | 2026-11-18 | 52s | 46,118 | 0.65% | 0.13% | xQc Clips | xQc causes ROAD RAGE for the first time in GTA 6! \| xQc | https://www.youtube.com/shorts/PbSvLZld-iU |
| 8 | 2026-11-18 | 60s | 72,350 | 0.54% | 0.63% | MajesticDrama | NickEh30 calls GTA VI "boring," claiming it failed to live up to the hype | https://www.youtube.com/shorts/0DFtmbwU-aQ |
| 9 | 2026-11-20 | 63s | 4,306 | 0.51% | 0.30% | xQc Clips | XQC hits an INSANE DOUBLE BACKFLIP on a bike in GTA 6 | https://www.youtube.com/shorts/IV1034pNuEQ |
| 10 | 2026-11-18 | 21s | 148 | n/a | n/a | Opus ART | What Happens After You Break Into the GTA 6 Cruise Ship... (Part 2) | https://www.youtube.com/shorts/GRzDNKVEfxo |

### What the First Frames & Packaging Look Like (Key Gap We Can Exploit)
1. **Most launch-week Shorts are lazy 50–63s raw stream rips** (e.g., `xQc Clips`) with slow build-ups, raw UI clutter, and **0.5%–1.1% like rates**. They win purely on channel authority and streamer search terms.
2. **Controversy/debate clips (`0DFtmbwU-aQ`) drive 4–8x higher comment rates** (0.63% vs. 0.07%–0.16%), matching the guide's insight that debate drives comments while relatable/NPC absurdity drives shares.
3. **Our opening:** Tight **12–16s payoff-first vertical stacks** (tight facecam top + clean gameplay bottom, white flash + whoosh, original ALL-CAPS hook text in frame 1 with `"GTA 6"` / `"LUCIA"` / `"JASON"` / `"NPC"`, proofread subtitles, one slow-mo/freeze punchline) stand out immediately against 60-second unedited Twitch dumps across all three platforms.

---

## 4. Audit of Your 8 Target Channels (Live Check on 2026-11-20)

| Channel | Active GTA 6 Today? | Facecam Fit | Guide Alignment & Recommendation |
| :--- | :---: | :---: | :--- |
| **@IShowSpeed** | Yes (3.7M trailer + streams) | Real facecam, huge reactions | **Tier 1 (Watch dedupe):** Massive competition—only clip unique moments `<3` pages posted, or pivot angles fast. |
| **@KaiCenatLive** | Yes (Marathon + First Time VOD) | Real facecam, huge reactions | **Tier 1 (Watch dedupe):** Same as Speed—high view ceiling, high race risk. Dedupe on X/TikTok before cutting. |
| **@ohnepixel** | Yes (primarily on Twitch) | Real facecam, chaotic comedy | **Tier 1 (Prime):** Proven format fit; scout via Twitch Helix API rather than YouTube `/videos`. |
| **@Caylus** | Participating in YT launch streams | Real facecam | **Tier 1/2:** `@Caylus/videos` returned an error on YT; verify active handle/channel URL before scraping. |
| **@Wynnsanity** | Recent `/videos` are CODM/PUBGM; check `/streams` | Real facecam | **Tier 2:** Check live streams tab for GTA 6 sessions. |
| **@TheBurntPeanut** | Yes (3 GTA 6 uploads: 275K–394K views) | **Animated peanut avatar** | **Needs your call:** Guide rule says *no VTubers/PNGtubers*. His expressive peanut filter is genuinely comedic—keep as a deliberate exception or skip? |
| **@UG_Z** | Yes (Full Game Walkthrough Pt 1–4) | **No facecam** | **Skip for primary format:** Long walkthroughs with no facecam reaction + high launch-week **story spoiler** risk. |
| **@MKIceAndFire** | Yes (Walkthrough Pt 4–6, No Commentary) | **No facecam / no commentary** | **Skip for primary format:** Explicitly no-commentary walkthroughs—conflicts with both facecam reaction and no-spoiler rules. |
| **Mid-size Twitch streamers** | Thousands live now | Real facecams | **Highest-yield scouting pool:** Less competition than Speed/Kai, fresher moments, much easier to pass the `<3 pages` market dedupe check. |

---

## 5. Immediate Takeaways Going Into Step 2

1. **Keep the primary format strict:** 12–16s stacked vertical clips with **real facecams**, payoff-first teaser, white flash + whoosh, original hook + punchline text, and proofread subtitles.
2. **Prioritize mid-size Twitch streamers + ohnePixel/Speed/Kai (deduped)** over no-commentary walkthrough channels (`MKIceAndFire`, `UG_Z`).
3. **Re-run `scripts/lane_research.py`** as soon as you grab a free Scrape Creators key (100 free credits) to fill in live TikTok & Instagram share rates.
