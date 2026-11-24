# Learning & Experiments Log

Dated lessons from every research pass and 2-hour cycle, plus the active experiment tracker.

---

## Active Experiments Tracker

| ID | Status | Variable / Hypothesis | Sample Size | Primary Metric | Kill / Keep Condition |
| :--- | :---: | :--- | :---: | :--- | :--- |
| **EXP-R1** | Queued | Keyword-first Frame 1 (`GTA 6` / `LUCIA` / `JASON` / `NPC` in hook + caption) vs. generic hook | 6/arm | 24h median plays + share rate | Keep if >=25% lift on 24h plays without hurting share rate |
| **EXP-R2** | Queued | Provocative claim hook vs. curiosity-gap hook | 6/arm | Share rate & YT "Stayed to watch" % | Kill losing style after 12 posts if >=20% behind |
| **EXP-R3** | Queued | Character/NPC chaos pillar vs. big-streamer-name pillar | 6/arm | Share rate (target >=1.0%) | Allocate >=60% mix to winning share-rate pillar |
| **EXP-R4** | Queued | IG first-comment honest question vs. no first comment | 8/arm | Comment rate (comments/plays) | Keep if >=2x baseline comment rate without hurting sends |
| **EXP-R5** | Queued | Overnight WAT global slots (`01:30`, `04:30`, `07:30 WAT`) vs. daytime WAT median | 4/slot (14d min) | 24h plays vs. daytime median | Kill any slot >=30% below daytime median after 2 weeks |
| **EXP-R6** | Planned | IG Originality Ladder (heavier facecam framing/pacing remix) | 5 posts | Break 220–240 play band + green IG status | Trigger only if 4 consecutive IG Reels stall in same band |

---

## Dated Lessons

### 2026-11-20 — Launch-Week Lane Baseline (Step 1)
- **Question:** What does the live GTA 6 funny-moments clip lane look like across TikTok, Instagram, and YouTube Shorts on Day 2 of launch, and which of our 8 target channels fit the format?
- **Sources:** Live `yt-dlp` scan of 20 fresh Nov 18–20, 2026 YouTube Shorts + live channel audit of 8 target creators + guide's launch-window 189-post & 235-post TikTok winners' samples (`research/lane_research.md`).
- **Findings:**
  - GTA 6 peaked at ~7.91M concurrent viewers (~3.3M+ on Twitch) — massive 24/7 raw clip supply.
  - Most launch-week YouTube Shorts in search are raw 50–63s Twitch dumps earning only 0.5%–1.1% like rates; tight 12–16s payoff-first stacked edits have a wide quality gap to win.
  - `@MKIceAndFire` and `@UG_Z` are no-facecam walkthrough channels (spoiler risk, no reaction) — excluded from primary scouting. `@TheBurntPeanut` uses an animated peanut avatar (held pending explicit exception). `@ohnepixel`, `@IShowSpeed`, `@KaiCenatLive`, and mid-size Twitch streamers are the prime pool.
- **Action / Change:** Prioritize mid-size Twitch streamers + deduped Tier-1 streamers (`<3` pages on X/TikTok). Run `scripts/lane_research.py` for live TikTok/IG share ratios once free `SCRAPE_CREATORS_API_KEY` is added.

### 2026-11-20 — 2026 Platform Algorithms & Humor Mechanics (Step 2)
- **Question:** What ranking signals and originality rules actually control distribution for a brand-new clipping page on Instagram Reels, TikTok, and YouTube Shorts in 2026?
- **Sources:** Primary platform sources (Instagram/Mosseri Reels ranking & April 30, 2026 Originality Update; TikTok Newsroom *How TikTok recommends videos* & Unoriginal Content Guidelines; YouTube Creator Insider Todd Sherman/Rene Ritchie & Google Support *Reused Content* policy; McGraw & Warren 2010; Berger & Milkman 2012; NYT Psychology of Sharing) (`research/algorithm_research.md`).
- **Findings:**
  - Across all 3 apps, the two levers we control are **completion/looping** and **sends/shares per reach**.
  - Subtitles + credit alone fail originality checks on IG, TikTok, and YouTube; context-adding on-screen hook/punchline text + payoff-first recut + clean facecam/gameplay crop are mandatory.
  - YouTube's *Reused Content* policy is stricter for YPP ad monetization than IG/TikTok distribution — treat YouTube Shorts as a free reach/brand funnel first.
- **Action / Change:** Encoded 6 standing production rules in `playbook.md`, automated caption/ledger/pause/quarantine guardrails in `scripts/safe_publish.py`, and queued `EXP-R1` through `EXP-R6`.

### AI Self-Improvement Review (2026-11-23)
- **Model:** `nvidia/nemotron-3.5-super-vl:free` | **Virality Gate:** `>= 8/10`
- **Brief:** The ohnepixel crash clip was the biggest win, driving nearly 10k views on YouTube Shorts alone. The biggest leak is the 30-second generic marathon clip that received only 1 view. Tomorrow, prioritize 13-15 second physics glitch clips from high-energy streamers and post them first to YouTube Shorts.
- **Priority Creators:** ohnepixel, xqc, caseoh, jynxzi, kaicenat, fanum
- **Double Down:** Short 13-15 second clips with all-caps streamer-name hooks dominate, as shown by ohnepixel's 10,867 views versus the 346 median.; Physics-breaking and glitch moments generate the highest engagement, evidenced by the top two clips focusing on crashing and broken physics.; YouTube Shorts is the primary traffic driver for breakout hits, contributing 9,857 of 10,867 views for the top clip while TikTok and Instagram remain secondary.
- **Reject / Avoid:** Reject marathon finale or long-form event recaps that lack a specific in-game glitch or reaction, as seen in the 1-view Agent 00 clip.; Avoid clips longer than 20 seconds without a rapid payoff, since the 30-second Jynxzi clip underperformed compared to 13-15 second winners.; Do not post generic streamer reactions without a clear GTA 6 visual hook or conflict in the first 3 seconds.

### AI Self-Improvement Review (2026-11-24)
- **Model:** `nvidia/nemotron-3.5-super-vl:free` | **Virality Gate:** `>= 8/10`
- **Brief:** The biggest win was GTA-001 with 10910 views from a 13.7 second ohnepixel clip about crashing promises. The biggest leak is posting 30-second generic scenes that get 1 to 4 views. Tomorrow, cut all clips to under 16 seconds and lead with a streamer-named physics failure.
- **Priority Creators:** ohnepixel, xqc, caseoh, jynxzi, kaicenat, typicalgamer
- **Double Down:** Short 13-15 second clips with all-caps hooks referencing a specific streamer promise or failure, like GTA-001, outperformed because they delivered immediate conflict and resolved within the first 15 seconds.; Physics-breaking moments and game glitches, such as GTA-002 and GTA-006, generated high TikTok views because they are visually chaotic and require no context to understand.; Clips under 22 seconds had a median of 531.5 views versus 267.0 for 23-35 second clips, proving fast pacing and quick punchlines drive higher retention on Shorts and Reels.
- **Reject / Avoid:** Reject strip club or adult venue scenes like GTA-012 which received 1 view because they lack gameplay action and trigger algorithmic suppression.; Reject generic beach or vehicle chaos titles without a streamer reaction, like GTA-018 which received 4 views, because they fail to create personality-driven curiosity.; Reject 30-second clips with slow builds and no immediate hook in the first 3 seconds, as all bottom flops were 30.04 seconds with under 10 views.
