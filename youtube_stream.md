# YouTube-Only Streamer Clip Drop Prompt (`youtube_stream.md`)

Because YouTube blocks video downloads from cloud datacenter IPs (GitHub Actions), **GitHub Actions runs Twitch streamers automatically every day at 10:00 AM WAT**, while **YouTube-exclusive / YouTube-primary streamers** (like IShowSpeed, TypicalGamer, DarkViperAU, Valkyrae, Ludwig, TimTheTatman, CouRage, Caylus, Wynnsanity, LazarBeam, Berleezy, RDCworldGaming, Slogo, DrDisrespect) are generated **locally on your Mac** using this reusable prompt.

---

## Copy & Paste Prompt (Works With Antigravity or Any AI Coding Agent)

> Copy everything inside the box below and paste it into chat whenever you want **2 fresh ~30s GTA 6 clips from YouTube-only streamers**.
> To target specific creators, replace `random` with names like `IShowSpeed, TypicalGamer`.

```text
Run a local YouTube-only GTA 6 streamer clip drop for my Leonida Chaos clipping page.

Project folder: /Users/mayorg/Documents/MAYOR G PROJECT/gta6-clipping-page
Target clip count: 2
Target clip duration: 18 seconds each
Target YouTube-only streamers: random
(Optional: replace "random" above with specific names, e.g. IShowSpeed, TypicalGamer, DarkViperAU, Valkyrae, Ludwig, TimTheTatman, CouRage, Caylus, Wynnsanity, LazarBeam, Berleezy, RDCworldGaming, Slogo, DrDisrespect)

First read youtube_stream.md and playbook.md in the project folder so you follow every rule and guardrail.
Then run the local YouTube streamer pipeline from the project folder, verify the clips are real GTA 6 gameplay via our free OpenRouter vision check, render ~30s 1080x1920 clips via scripts/clip.py, validate via scripts/safe_publish.py, deliver all 5 Telegram parts (Header+Slot -> MP4 -> Cover -> Caption -> IG First Comment + YT Title) to my Telegram bot, log all 3 platforms (instagram, tiktok, youtube) in scoreboard.csv + log.md, refresh dashboard/index.html, commit locally, and give me the exact `git push origin main` command.

Hard rules:
- Focus strictly on YouTube-primary / YouTube-exclusive streamers (max 1 clip per streamer per run).
- Never print or expose any API key or token from .env.
- Never pay for, sign up for, or upgrade any tool, never message anyone, never change social profiles, and do NOT push to remote git without my confirmation.
```

---

## Agent Execution Guide (Full System Context)

If you are an AI agent executing the prompt above, here is the complete architecture of the **Leonida Chaos** repository at `/Users/mayorg/Documents/MAYOR G PROJECT/gta6-clipping-page`:

### 1. Brand & Live Accounts
- **Page Name:** Leonida Chaos
- **Timezone:** WAT (`UTC+1` / `Africa/Lagos`)
- **YouTube:** `https://www.youtube.com/@leonidachao`
- **TikTok:** `@leonida.chaos4`
- **Instagram:** `@leonida_chaos4`
- **Telegram Delivery Bot:** configured in local `.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) — **NEVER print `.env` values**.
- **Live Dashboard:** `https://mayor-g.github.io/gta6-clipping-page/` (rebuilt locally by `scripts/dashboard_data.py` into `dashboard/index.html` and deployed by GitHub Pages on push).

### 2. One-Command Local Execution
First sync with `origin/main` so you never conflict with GitHub Actions' auto-committed `scoreboard.csv` / `dashboard/index.html`:

```bash
cd "/Users/mayorg/Documents/MAYOR G PROJECT/gta6-clipping-page"
git pull --rebase --autostash origin main
```

Then run the built-in YouTube-only streamer mode in `scripts/daily_cloud_pipeline.py` (it automatically loads `.env` locally, scouts YouTube-only streamers round-robin, enforces 1 clip per streamer, runs the free OpenRouter 4-frame vision check to reject non-GTA-6 footage and write visually grounded copy, renders ~30s `1080x1920` 30fps MP4s via `scripts/clip.py`, runs `scripts/safe_publish.py`, delivers 5-part bundles to Telegram via `scripts/telegram_deliver.py`, appends rows for **all 3 platforms** `instagram, tiktok, youtube` into `scoreboard.csv`, logs proof frames in `proofs/` + `log.md`, and rebuilds `dashboard/index.html`):

- **Random 2 YouTube-only streamers:**
  ```bash
  python3 scripts/daily_cloud_pipeline.py --source youtube --clip-count 2 --target-sec 18
  ```
- **Tagged specific YouTube-only streamers (e.g., IShowSpeed + TypicalGamer):**
  ```bash
  python3 scripts/daily_cloud_pipeline.py --source youtube --clip-count 2 --target-sec 18 --streamers "IShowSpeed,TypicalGamer"
  ```
- **Dry run (preview without sending to Telegram or mutating tracking files):**
  ```bash
  python3 scripts/daily_cloud_pipeline.py --source youtube --clip-count 2 --target-sec 18 --dry-run
  ```

### 3. Curated YouTube-Only / YouTube-Primary GTA 6 Streamer Roster
These creators stream primarily or exclusively on YouTube (so they rarely/never appear in GitHub Actions' Twitch drop):

| Creator | Tag / Handle | Style |
| :--- | :--- | :--- |
| **IShowSpeed** | `@IShowSpeed` | Loud chaotic energy, huge viral reactions |
| **Typical Gamer** | `@TypicalGamer` | Massive GTA YouTube livestreamer, clean facecam |
| **DarkViperAU** | `@DarkViperAU` | Deep GTA community favorite, hilarious rage/physics fails |
| **Valkyrae** | `@Valkyrae` | High-energy reactions, relatable moments |
| **Ludwig** | `@ludwig` | Dry humor, crisp storytelling & comedic timing |
| **TimTheTatman** | `@TimTheTatman` | Loud laughs, instant fail moments |
| **CouRage** | `@CouRage` | Expressive reactions, comedic GTA chaos |
| **Caylus** | `@Caylus` | Fast-paced GTA stunt/chaos clips |
| **Wynnsanity** | `@Wynnsanity` | High-energy gaming reactions |
| **LazarBeam** | `@LazarBeam` | Pure comedy & absurd sandbox experiments |
| **Berleezy** | `@Berleezy` | Iconic commentary & relatable disbelief |
| **RDCworldGaming** | `@RDCworldGaming` | Group chaos & comedy skits/gameplay |
| **Slogo** | `@Slogo` | GTA multiplayer chaos & trolling |
| **DrDisrespect** | `@DrDisrespect` | Over-the-top character reactions |

### 4. Finish & Sync Checklist (For the Agent)
After the pipeline finishes:
1. Verify `produced` clips count, streamer diversity, vision check summary, and Telegram delivery status.
2. Confirm `scoreboard.csv` added rows for **all 3 platforms** (`instagram`, `tiktok`, `youtube`) and `dashboard/index.html` refreshed cleanly.
3. Stage and commit updated tracking files locally (`scoreboard.csv`, `log.md`, `dashboard/index.html`, `data/`, `proofs/`):
   ```bash
   git add scoreboard.csv log.md dashboard/index.html data/ proofs/
   git commit -m "chore: local YouTube-only streamer clip drop & dashboard refresh"
   ```
4. Report the produced clips, streamers, hooks, and recommended WAT posting slots to the user, and provide `git push origin main` so they can publish the updated dashboard to GitHub Pages.
