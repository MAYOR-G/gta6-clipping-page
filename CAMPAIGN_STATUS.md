# Clipping Campaigns Status & Action Plan

**Last Updated:** 2026-11-27  
**Primary Signup Email:** `mayowawork16@gmail.com`  
**Connected Social Channels:**
- **YouTube:** `@leonidachao` (`https://www.youtube.com/@leonidachao`)
- **TikTok:** `@leonida.chaos4` (`https://www.tiktok.com/@leonida.chaos4`)
- **Instagram:** `@leonida_chaos4` (`https://www.instagram.com/@leonida_chaos4`)

---

## 1. What Syncs Automatically vs. What Cannot Be Auto-Pulled

### What Syncs Automatically (Every 6 Hours via GitHub Actions)
- **YouTube, TikTok & Instagram Metrics:** `scripts/sync_stats.py` automatically pulls views, likes, shares, comments, and followers for `@leonidachao`, `@leonida.chaos4`, and `@leonida_chaos4` into `scoreboard.csv` and rebuilds `dashboard/index.html`.
- **Submitted Clip Views & USD Calculations:** Whenever you submit a posted clip URL to a campaign and log it once with `scripts/campaigns.py log-submission`, `scripts/campaigns.py sync-views` automatically updates its `verified_views` and recalculates Pending/Paid USD every 6 hours.

### What Cannot Be Auto-Pulled by Username
- **Clipping Platform Dashboards (Clipster, Vues, Whop, ClipStake, DareBay, Clipur, Starlet, Clippers HQ, Clipping.exe):** None of these platforms expose a public API or public profile URL (for example, `clipster.com/@leonidachaos4` returns `404`, and `whop.com/@mayowawork` is behind Cloudflare and does not expose private Content Rewards data).
- Because your joined campaigns, approval statuses, and payout balances live behind private Google/Discord OAuth sessions, GitHub Actions cannot read them automatically using just a username or email.
- **How to keep the dashboard accurate:** Whenever you join a new campaign, submit a new clip URL, or withdraw a payout, log it once using `scripts/campaigns.py` (or ask me in chat), and `sync-views` will keep the views and USD earnings synced automatically from then on.

---

## 2. All 11 Clipping Platforms — Current Status

| # | Platform | URL | Status | Account / Handle | Socials Linked? | Notes |
| :-: | :--- | :--- | :--- | :--- | :---: | :--- |
| 1 | **Clipster** | https://www.clipster.com | **Active** | `@leonidachaos4` (Google) | **Yes (YT, TT, IG)** | 149 open campaigns; 4 clean campaigns joined/saved |
| 2 | **Vues** | https://vues.app | **Active** | `@leonidachaos4` (Google) | **Yes (YT, TT, IG)** | Joined `Vues by Vues`; applied to `Pubs by InClips Media` |
| 3 | **Whop** | https://whop.com | **Active** | `@mayowawork` (Google) | Not yet | `Content Rewards` + `Whop AI` joined; connect socials in Whop settings |
| 4 | **ClipStake** | https://app.clipstake.com | **Active** | `@leonidachao` (Google) | Auto on submit | Only casino/logo campaigns active right now (skipped) |
| 5 | **DareBay** | https://darebay.com | **Active** | `@user359345651` (Google) | Per task | 2 non-gaming tasks live right now (Facebook Reels / Threads) |
| 6 | **Clipur** | https://www.clipur.com | **Waitlist** | `mayowawork16@gmail.com` | Pending approval | Email verified; waiting on `/waitlist` approval |
| 7 | **Starlet** | https://starlet.gg | **Waitlist** | `mayowawork16@gmail.com` | Via Discord | Joined `starlet.gg/launch` waitlist; active pools in Discord |
| 8 | **Clippers HQ** | https://clipershq.com | **Needs Discord** | Not signed in yet | Pending Discord | Login is Discord OAuth only |
| 9 | **Clipping.exe** | https://clip.tech | **Needs Discord** | Not signed in yet | Pending Discord | Runs via Clip Money Discord (`Kai Cenat` `$2/1K` live) |
| 10 | **Clipping.io** | https://www.clipping.io | **Unavailable** | — | — | No self-serve creator portal (`app.clipping.io` unresolved) |
| 11 | **Clipfy** | https://clipfy.app | **Unavailable** | — | — | Redirects to `cutdat.com` (paid link-in-bio SaaS) |

---

## 3. Campaigns Joined / Saved So Far

### On Clipster (`@leonidachaos4`)
1. **`Lord Kebun [CLIPPING]`** — *Saved / Ready to Submit*
   - **Payout:** `$0.75 / 1K views` (`$750` cap/post, `$2,500` cap/profile)
   - **Requirements:** `1,000` min views, `8s+` duration, `0.3%` min engagement, `0` min followers
   - **Brief Rules:** Original self-edited Lord Kebun GTA clips (`kick.com/lordkebun`) with `@lordkebun` watermark added to every video.
2. **`Furi [CLIPPING]`** — *Applied / Saved*
   - **Payout:** `$0.20 / 1K views` (`$400` cap/post, `$1,500` cap/profile)
   - **Requirements:** `1,069` min views, `0.2%` min engagement, `0` min followers
   - **Brief Rules:** Original self-edited Furi clips with `@FURI` watermark added to every video.
3. **`Money For Nothing [GTA 6 EDITS]`** — *Saved / Ready to Submit*
   - **Payout:** `$1.00 / 1K views` (`$800` cap/post, `$1,000` cap/profile)
   - **Requirements:** `2,000` min views, `3%` min engagement, `0` min followers
   - **Brief Rules:** Beat-synced GTA 6 edit using *Money For Nothing - Dire Straits* audio; comment song name and like your own comment.
4. **`ISSUES [GTA EDITS]`** — *Saved for Later*
   - **Payout:** `$2.00 / 1K views` (`$200` cap)
   - **Requirements:** `1,000` min views, `10s+` duration, **requires `1,000+` followers** (save until your channels cross 1K followers).

### On Vues (`@leonidachaos4`)
1. **`Vues by Vues`** — Joined (currently 0 active campaigns).
2. **`Pubs by InClips Media`** — Application pending review.

> **Why no existing `GTA-001`–`GTA-030` links were submitted yet:** Those clips were cut before joining Clipster and do not contain the required `@lordkebun` / `@FURI` watermarks or *Money For Nothing* audio track, so submitting them would fail brief checks.

---

## 4. What Is Left For You To Do Later

1. **Log into Discord in Chrome** so we can finish the remaining 3 Discord-based platforms:
   - **Clippers HQ:** Open `https://clipershq.com/login` and click **Continue with Discord**.
   - **Clipping.exe (`clip.tech`):** Join `https://discord.gg/FnCMF4KMAT` (has **Kai Cenat subathon** clipping at `$2.00/1K`, `$25K` budget).
   - **Starlet:** Join `https://discord.gg/tiktokclips`.
2. **Connect Socials on Whop:** Open `https://whop.com/@me/settings/external-accounts/` and link YouTube, TikTok, and Instagram.
3. **Watch Gmail (`mayowawork16@gmail.com`):** Check for approval emails from **Clipur** (`clipur.com/waitlist`) and **Starlet** (`starlet.gg/launch`), plus **Pubs by InClips Media** on Vues.
4. **Set Up Payout Method:** Add your **Wise / Payoneer USD** or **USDC / USDT** wallet inside Clipster, Vues, and Whop settings so payouts aren't delayed once you hit `$10–$15`.
5. **Cut & Submit Compliant Clips:**
   - For **Lord Kebun (`$0.75/1K`)** or **Furi (`$0.20/1K`)**: download the `@lordkebun` / `@FURI` watermark from their Clipster brief, cut a clip, post to YT/TT/IG, submit the URL in Clipster, and log it in `campaigns.py`:
     ```bash
     python3 scripts/campaigns.py log-submission --campaign CLIPSTER-KEBUN-1 --post-id GTA-035 --social tiktok --url "https://www.tiktok.com/@leonida.chaos4/video/..." --status pending
     ```
   - For **Money For Nothing (`$1.00/1K`)**: edit a beat-synced GTA 6 clip using *Money For Nothing - Dire Straits*, post, comment the song name, submit in Clipster, and log it with `--campaign CLIPSTER-MFN-1`.
