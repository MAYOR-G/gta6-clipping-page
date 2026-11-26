#!/usr/bin/env python3
"""
Stream Clipper Engine (stream_clipper_engine.py)

Implements the `stream-clipper` workflow as both an importable library for
`scripts/daily_cloud_pipeline.py` and a standalone CLI for long Twitch/YouTube VODs:

1. Multi-Signal Candidate Detection:
   - Twitch VOD clips (Helix /clips filtered by video_id, clustered within 30s)
   - YouTube Most Replayed heatmap peaks (`yt-dlp -J` -> `heatmap`)
   - Chat hype/laugh reactions (W/WWW/LOL/LMAO/KEKW/OMEGALUL/LUL/😂/💀/clip it)
   - Audio energy peaks fallback (ffmpeg PCM RMS windowing)
   - Multi-signal merge (within 45s; 2-3 signals outrank single signals)
2. Word-Level Speech Transcription (`transcribe_with_words`):
   - Priority: Groq Whisper API (free tier, <1s) -> whisper-cli -> mlx_whisper -> faster_whisper
   - Outputs 2-3 word kinetic SRT (`words_to_kinetic_srt`)
3. Dead-Air Removal (`detect_silences`, `build_dead_air_free_keep_ranges`):
   - Runs `ffmpeg -af silencedetect=noise=-35dB:d=0.6`, cuts >0.6s silences while
     protecting visual payoff reaction windows and starting <=1s before setup.
4. Stranger-Comprehension & Setup/Payoff Scoring (`score_moment_for_strangers`):
   - Scores 0-10 on: "Would someone who has never seen this streamer get what is
     happening within the first 2 seconds?" + generates clean YT Shorts title
     (40-62 chars, zero Credit spam) separate from IG/TikTok caption.
5. Campaign Rules Compliance (`check_campaign_rules`, `apply_campaign_caption_fixes`)
   & Markdown/JSON Report Generation.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent

HYPE_TOKENS_RE = re.compile(
    r"^(w+|lol+|lmao+|lmfao+|kekw|omegalul|lul+|lulw|icant|no\s*way|clip\s*it|dead|💀+|😂+|🤣+|😭+)$",
    flags=re.IGNORECASE,
)
HYPE_SUBSTR_RE = re.compile(
    r"\b(w{2,}|lol+|lmao+|kekw|omegalul|lulw|clip\s*it)\b|[💀😂🤣😭]",
    flags=re.IGNORECASE,
)

EMOJI_STRIP_RE = re.compile(
    "["
    "\U0001F1E0-\U0001F1FF"
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "\uFE0E\uFE0F"
    "]+",
    flags=re.UNICODE,
)


def _strip_emoji_and_clean(text: str) -> str:
    cleaned = EMOJI_STRIP_RE.sub("", text or "")
    cleaned = cleaned.replace("—", " ").replace("--", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _format_srt_ts(sec: float) -> str:
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - math.floor(sec)) * 1000))
    if ms >= 1000:
        ms -= 1000
        s += 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ---------------------------------------------------------------------------
# 1. Viewer Signals (YouTube Heatmap, Twitch VOD Clips, Chat Hype, Audio)
# ---------------------------------------------------------------------------


def fetch_youtube_heatmap_signals(url: str, min_value: float = 0.45) -> list[dict[str, Any]]:
    """Run `yt-dlp -J <url>` and extract local peaks from YouTube's Most replayed `heatmap`."""
    try:
        out = subprocess.check_output(
            ["yt-dlp", "-J", "--no-warnings", url],
            text=True,
            timeout=45,
        )
        info = json.loads(out)
    except Exception:
        return []

    heatmap = info.get("heatmap") or []
    if not heatmap:
        return []

    vals = [float(b.get("value") or 0.0) for b in heatmap]
    max_val = max(vals) if vals else 1.0
    if max_val <= 0:
        return []

    peaks: list[dict[str, Any]] = []
    for i, bucket in enumerate(heatmap):
        val = float(bucket.get("value") or 0.0)
        prev_v = float(heatmap[i - 1].get("value") or 0.0) if i > 0 else 0.0
        next_v = float(heatmap[i + 1].get("value") or 0.0) if i + 1 < len(heatmap) else 0.0
        is_local_peak = val >= prev_v and val >= next_v
        norm = val / max_val
        if is_local_peak and norm >= min_value:
            s_t = float(bucket.get("start_time") or 0.0)
            e_t = float(bucket.get("end_time") or (s_t + 10.0))
            peaks.append({
                "signal": "youtube_heatmap",
                "timestamp": round((s_t + e_t) / 2.0, 2),
                "start_sec": round(s_t, 2),
                "end_sec": round(e_t, 2),
                "raw_value": round(val, 4),
                "score": round(min(1.0, norm), 4),
            })
    peaks.sort(key=lambda x: x["score"], reverse=True)
    return peaks


def fetch_twitch_vod_clip_signals(
    broadcaster_id: str,
    vod_id: str,
    started_at: str,
    ended_at: str,
    client_id: str,
    token: str,
) -> list[dict[str, Any]]:
    """Call Helix GET /clips for a VOD window, keep matching video_id, cluster within 30s."""
    qs = urllib.parse.urlencode({
        "broadcaster_id": broadcaster_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "first": "100",
    })
    req = urllib.request.Request(
        f"https://api.twitch.tv/helix/clips?{qs}",
        headers={"Client-Id": client_id, "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8")).get("data") or []
    except Exception:
        return []

    matching = [c for c in data if str(c.get("video_id") or "") == str(vod_id) and c.get("vod_offset") is not None]
    if not matching:
        return []

    matching.sort(key=lambda c: float(c.get("vod_offset") or 0.0))
    clusters: list[list[dict[str, Any]]] = []
    for c in matching:
        off = float(c["vod_offset"])
        if clusters and abs(off - float(clusters[-1][-1]["vod_offset"])) <= 30.0:
            clusters[-1].append(c)
        else:
            clusters.append([c])

    scored: list[dict[str, Any]] = []
    max_views = max((sum(int(x.get("view_count") or 0) for x in cl) for cl in clusters), default=1)
    max_views = max(1, max_views)
    for cl in clusters:
        total_v = sum(int(x.get("view_count") or 0) for x in cl)
        start_off = min(float(x["vod_offset"]) for x in cl)
        end_off = max(float(x["vod_offset"]) + float(x.get("duration") or 20.0) for x in cl)
        score = min(1.0, (total_v / max_views) * 0.7 + min(1.0, len(cl) / 3.0) * 0.3)
        scored.append({
            "signal": "twitch_clips",
            "timestamp": round((start_off + end_off) / 2.0, 2),
            "start_sec": round(start_off, 2),
            "end_sec": round(end_off, 2),
            "clip_count": len(cl),
            "view_count": total_v,
            "score": round(score, 4),
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def analyze_chat_hype_signals(chat_json_path: Path, window_sec: float = 10.0) -> list[dict[str, Any]]:
    """
    Parse TwitchDownloaderCLI or chat_downloader JSON into 10s hype-weighted windows.
    Weights W/WWW/LOL/LMAO/KEKW/OMEGALUL/LUL/😂/💀/clip it over raw message volume.
    """
    if not chat_json_path.exists():
        return []
    try:
        raw = json.loads(chat_json_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    messages: list[tuple[float, str]] = []
    if isinstance(raw, dict) and isinstance(raw.get("comments"), list):
        # TwitchDownloaderCLI format
        for c in raw["comments"]:
            off = float(c.get("content_offset_seconds") or 0.0)
            body = ((c.get("message") or {}).get("body") or "").strip()
            if body:
                messages.append((off, body))
    elif isinstance(raw, list):
        # chat_downloader format
        for c in raw:
            off = float(c.get("time_in_seconds") or 0.0)
            body = (c.get("message") or "").strip()
            if off >= 0 and body:
                messages.append((off, body))

    if not messages:
        return []

    buckets: dict[int, dict[str, float]] = {}
    for off, body in messages:
        b_idx = int(max(0.0, off) // window_sec)
        b = buckets.setdefault(b_idx, {"total": 0.0, "hype": 0.0, "weighted": 0.0})
        b["total"] += 1.0
        clean_b = body.strip()
        if HYPE_TOKENS_RE.match(clean_b):
            b["hype"] += 1.0
            b["weighted"] += 2.5
        elif HYPE_SUBSTR_RE.search(clean_b):
            b["hype"] += 1.0
            b["weighted"] += 1.4
        else:
            b["weighted"] += 0.25

    if not buckets:
        return []

    # Score windows where hype density is high AND weighted volume stands out
    raw_scores: list[tuple[int, float]] = []
    for idx, b in buckets.items():
        if b["total"] < 3:
            continue
        hype_ratio = b["hype"] / max(1.0, b["total"])
        score_raw = b["weighted"] * (0.5 + hype_ratio)
        raw_scores.append((idx, score_raw))

    if not raw_scores:
        return []
    max_s = max(s for _, s in raw_scores) or 1.0
    signals: list[dict[str, Any]] = []
    for idx, s in raw_scores:
        norm = s / max_s
        if norm >= 0.40:
            start_s = idx * window_sec
            signals.append({
                "signal": "chat_hype",
                "timestamp": round(start_s + window_sec * 0.5, 2),
                "start_sec": round(start_s, 2),
                "end_sec": round(start_s + window_sec, 2),
                "score": round(min(1.0, norm), 4),
            })
    signals.sort(key=lambda x: x["score"], reverse=True)
    return signals


def detect_audio_peak_signals(video_path: Path, window_sec: float = 1.0) -> list[dict[str, Any]]:
    """Fast PCM RMS audio-energy peak finder via ffmpeg for clips without VOD heatmap/chat."""
    if not video_path.exists():
        return []
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(video_path),
                "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        pcm = proc.stdout
    except Exception:
        return []
    if not pcm:
        return []

    samples_per_win = max(1, int(8000 * window_sec))
    bytes_per_win = samples_per_win * 2
    rms_vals: list[tuple[float, float]] = []
    for i in range(0, len(pcm) - bytes_per_win + 1, bytes_per_win):
        chunk = pcm[i : i + bytes_per_win]
        n = len(chunk) // 2
        if n == 0:
            continue
        vals = struct.unpack(f"<{n}h", chunk)
        rms = math.sqrt(sum(v * v for v in vals) / n)
        t_start = (i // 2) / 8000.0
        rms_vals.append((t_start, rms))

    if not rms_vals:
        return []
    max_rms = max(r for _, r in rms_vals) or 1.0
    signals: list[dict[str, Any]] = []
    for t_start, rms in rms_vals:
        norm = rms / max_rms
        if norm >= 0.55:
            signals.append({
                "signal": "audio_peak",
                "timestamp": round(t_start + window_sec * 0.5, 2),
                "start_sec": round(t_start, 2),
                "end_sec": round(t_start + window_sec, 2),
                "score": round(min(1.0, norm), 4),
            })
    signals.sort(key=lambda x: x["score"], reverse=True)
    return signals[:10]


def merge_signals(
    signals: list[dict[str, Any]],
    merge_window_sec: float = 45.0,
    top_k: int = 15,
) -> list[dict[str, Any]]:
    """
    Merge signals within `merge_window_sec`.
    A moment found by 2 or 3 distinct signals strictly outranks any single-signal moment.
    """
    if not signals:
        return []
    ordered = sorted(signals, key=lambda x: float(x.get("timestamp") or 0.0))
    clusters: list[list[dict[str, Any]]] = []
    for sig in ordered:
        ts = float(sig.get("timestamp") or 0.0)
        if clusters and abs(ts - float(clusters[-1][-1]["timestamp"])) <= merge_window_sec:
            clusters[-1].append(sig)
        else:
            clusters.append([sig])

    merged: list[dict[str, Any]] = []
    for cl in clusters:
        sig_names = sorted({str(x.get("signal") or "signal") for x in cl})
        # Best score per signal type averaged, then boosted by distinct signal count
        best_per_sig: dict[str, float] = {}
        for x in cl:
            sn = str(x.get("signal") or "signal")
            best_per_sig[sn] = max(best_per_sig.get(sn, 0.0), float(x.get("score") or 0.0))
        avg_score = sum(best_per_sig.values()) / max(1, len(best_per_sig))
        start_s = min(float(x.get("start_sec") or x.get("timestamp") or 0.0) for x in cl)
        end_s = max(float(x.get("end_sec") or (float(x.get("timestamp") or 0.0) + 15.0)) for x in cl)
        center_ts = sum(float(x.get("timestamp") or 0.0) for x in cl) / len(cl)
        merged.append({
            "timestamp": round(center_ts, 2),
            "start_sec": round(start_s, 2),
            "end_sec": round(end_s, 2),
            "signals_found": sig_names,
            "signal_count": len(sig_names),
            "score": round(avg_score, 4),
            "rank_key": (len(sig_names), round(avg_score, 4)),
        })

    merged.sort(key=lambda x: x["rank_key"], reverse=True)
    return merged[:top_k]


# ---------------------------------------------------------------------------
# 2. Word-Level Transcription + Kinetic 2-3 Word Captions
# ---------------------------------------------------------------------------


def _transcribe_groq_whisper(wav_path: Path, api_key: str) -> list[dict[str, Any]]:
    boundary = "----GroqWhisperBoundary771234"
    wav_bytes = wav_path.read_bytes()
    fields = {
        "model": "whisper-large-v3-turbo",
        "response_format": "verbose_json",
        "language": "en",
    }
    parts: list[bytes] = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"timestamp_granularities[]\"\r\n\r\nword\r\n".encode("utf-8")
    )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{wav_path.name}"\r\n'
            f"Content-Type: audio/wav\r\n\r\n"
        ).encode("utf-8")
        + wav_bytes
        + f"\r\n--{boundary}--\r\n".encode("utf-8")
    )
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        data=b"".join(parts),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(req, timeout=35) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    words: list[dict[str, Any]] = []
    for w in data.get("words") or []:
        txt = _strip_emoji_and_clean(w.get("word") or "")
        if txt:
            words.append({"word": txt, "start": float(w.get("start") or 0.0), "end": float(w.get("end") or 0.0)})
    if not words and data.get("segments"):
        for seg in data["segments"]:
            s_t, e_t = float(seg.get("start") or 0.0), float(seg.get("end") or 0.0)
            toks = [t for t in _strip_emoji_and_clean(seg.get("text") or "").split() if t]
            if toks:
                step = max(0.08, (e_t - s_t) / len(toks))
                for i, tok in enumerate(toks):
                    words.append({"word": tok, "start": s_t + i * step, "end": s_t + (i + 1) * step})
    return words


def _transcribe_whisper_cli(wav_path: Path, model_path: str, out_dir: Path) -> list[dict[str, Any]]:
    out_prefix = out_dir / "whisper_out"
    subprocess.run(
        ["whisper-cli", "-m", model_path, "-f", str(wav_path), "-ml", "1", "-sow", "-oj", "-of", str(out_prefix)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=90,
    )
    json_fp = Path(f"{out_prefix}.json")
    if not json_fp.exists():
        return []
    data = json.loads(json_fp.read_text(encoding="utf-8"))
    words: list[dict[str, Any]] = []
    for seg in data.get("transcription") or []:
        for tok in seg.get("tokens") or []:
            txt = _strip_emoji_and_clean(tok.get("text") or "")
            if not txt or txt.startswith("["):
                continue
            off = tok.get("offsets") or {}
            s_ms = float(off.get("from") or 0.0) / 1000.0
            e_ms = float(off.get("to") or (s_ms * 1000.0 + 150.0)) / 1000.0
            words.append({"word": txt, "start": s_ms, "end": max(s_ms + 0.05, e_ms)})
    return words


def _transcribe_mlx_whisper(wav_path: Path) -> list[dict[str, Any]]:
    import mlx_whisper  # type: ignore

    model_repo = os.environ.get("MLX_WHISPER_MODEL", "mlx-community/whisper-tiny.en-mlx")
    res = mlx_whisper.transcribe(str(wav_path), path_or_hf_repo=model_repo, word_timestamps=True)
    words: list[dict[str, Any]] = []
    for seg in res.get("segments") or []:
        for w in seg.get("words") or []:
            txt = _strip_emoji_and_clean(w.get("word") or "")
            if txt:
                words.append({"word": txt, "start": float(w.get("start") or 0.0), "end": float(w.get("end") or 0.0)})
    return words


_FASTER_WHISPER_MODEL: Any = None


def _transcribe_faster_whisper(wav_path: Path) -> list[dict[str, Any]]:
    global _FASTER_WHISPER_MODEL
    from faster_whisper import WhisperModel  # type: ignore

    if _FASTER_WHISPER_MODEL is None:
        model_name = os.environ.get("FASTER_WHISPER_MODEL", "tiny.en")
        _FASTER_WHISPER_MODEL = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _ = _FASTER_WHISPER_MODEL.transcribe(str(wav_path), language="en", word_timestamps=True, vad_filter=True)
    words: list[dict[str, Any]] = []
    for seg in segments:
        for w in getattr(seg, "words", None) or []:
            txt = _strip_emoji_and_clean(w.word or "")
            if txt:
                words.append({"word": txt, "start": float(w.start), "end": float(w.end)})
    return words


def transcribe_with_words(video_path: Path) -> dict[str, Any]:
    """
    Extract 16kHz mono WAV and transcribe with word timestamps.
    Never fails the pipeline if no Whisper backend is available.
    """
    backend_pref = os.environ.get("WHISPER_BACKEND", "auto").strip().lower()
    if backend_pref == "none" or not video_path.exists():
        return {"backend": "none", "text": "", "words": []}

    with tempfile.TemporaryDirectory(prefix="whisper_") as td:
        td_path = Path(td)
        wav_path = td_path / "audio.wav"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(video_path), "-ar", "16000", "-ac", "1", str(wav_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=45,
            )
        except Exception:
            return {"backend": "none", "text": "", "words": []}

        groq_key = os.environ.get("GROQ_API_KEY", "").strip()
        w_cli = shutil.which("whisper-cli")
        w_model = os.environ.get("WHISPER_MODEL_PATH", "").strip()

        order = (
            [backend_pref]
            if backend_pref in ("groq", "whisper_cli", "mlx", "faster_whisper")
            else ["groq", "whisper_cli", "mlx", "faster_whisper"]
        )

        for b in order:
            try:
                words: list[dict[str, Any]] = []
                if b == "groq" and groq_key:
                    words = _transcribe_groq_whisper(wav_path, groq_key)
                elif b == "whisper_cli" and w_cli and w_model and Path(w_model).exists():
                    words = _transcribe_whisper_cli(wav_path, w_model, td_path)
                elif b == "mlx":
                    words = _transcribe_mlx_whisper(wav_path)
                elif b == "faster_whisper":
                    words = _transcribe_faster_whisper(wav_path)
                else:
                    continue
                if words:
                    full_text = " ".join(w["word"] for w in words).strip()
                    return {"backend": b, "text": full_text, "words": words}
            except Exception:
                continue

    return {"backend": "none", "text": "", "words": []}


def words_to_kinetic_srt(
    words: list[dict[str, Any]],
    out_srt: Path,
    max_words: int = 3,
    max_dur: float = 1.4,
    uppercase: bool = True,
) -> Path | None:
    """Group word timestamps into crisp 2-3 word kinetic SRT cues."""
    if not words:
        return None
    chunks: list[dict[str, Any]] = []
    cur: list[dict[str, Any]] = []

    for w in words:
        txt = _strip_emoji_and_clean(w.get("word") or "")
        if not txt:
            continue
        item = {"word": txt, "start": float(w["start"]), "end": float(w["end"])}
        if not cur:
            cur.append(item)
            continue

        gap = item["start"] - cur[-1]["end"]
        span = item["end"] - cur[0]["start"]
        prev_ends_punct = cur[-1]["word"].endswith((".", "!", "?", ","))
        if len(cur) >= max_words or span > max_dur or gap > 0.45 or prev_ends_punct:
            chunks.append({
                "start": cur[0]["start"],
                "end": max(cur[0]["start"] + 0.18, cur[-1]["end"]),
                "text": " ".join(c["word"] for c in cur),
            })
            cur = [item]
        else:
            cur.append(item)

    if cur:
        chunks.append({
            "start": cur[0]["start"],
            "end": max(cur[0]["start"] + 0.18, cur[-1]["end"]),
            "text": " ".join(c["word"] for c in cur),
        })

    if not chunks:
        return None

    lines: list[str] = []
    for idx, ch in enumerate(chunks, start=1):
        txt = ch["text"].upper() if uppercase else ch["text"]
        lines.append(f"{idx}\n{_format_srt_ts(ch['start'])} --> {_format_srt_ts(ch['end'])}\n{txt}\n")
    out_srt.parent.mkdir(parents=True, exist_ok=True)
    out_srt.write_text("\n".join(lines), encoding="utf-8")
    return out_srt


# ---------------------------------------------------------------------------
# 3. Dead-Air Removal (`silencedetect=noise=-35dB:d=0.6`)
# ---------------------------------------------------------------------------


def detect_silences(video_path: Path, noise_db: float = -35.0, min_dur: float = 0.6) -> list[tuple[float, float]]:
    """Run ffmpeg silencedetect and return list of (silence_start, silence_end) pairs."""
    if not video_path.exists():
        return []
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-i", str(video_path),
                "-af", f"silencedetect=noise={noise_db:.0f}dB:d={min_dur:.2f}",
                "-f", "null", "-",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        stderr = proc.stderr or ""
    except Exception:
        return []

    silences: list[tuple[float, float]] = []
    cur_start: float | None = None
    for line in stderr.splitlines():
        m_s = re.search(r"silence_start:\s*([0-9.]+)", line)
        if m_s:
            cur_start = float(m_s.group(1))
            continue
        m_e = re.search(r"silence_end:\s*([0-9.]+)", line)
        if m_e and cur_start is not None:
            silences.append((cur_start, float(m_e.group(1))))
            cur_start = None
    return silences


def build_dead_air_free_keep_ranges(
    window_start: float,
    window_end: float,
    silences: list[tuple[float, float]],
    words: list[dict[str, Any]] | None = None,
    protected_window: tuple[float, float] | None = None,
    min_cut_silence: float = 0.65,
    pad_edge: float = 0.18,
) -> list[list[float]]:
    """
    Trim dead air at the start (begin <= 1.0s before first speech) and cut internal
    silent stretches > `min_cut_silence` unless inside `protected_window` (visual payoff).
    """
    s0 = max(0.0, float(window_start))
    e0 = max(s0 + 1.0, float(window_end))

    # Start at most 1.0s before the first spoken word inside this window
    if words:
        first_spoken = next((float(w["start"]) for w in words if float(w["end"]) > s0 and float(w["start"]) < e0), None)
        if first_spoken is not None and first_spoken - s0 > 1.0:
            s0 = max(s0, first_spoken - 0.85)

    # Trim leading/trailing silences outside speech
    cuts: list[tuple[float, float]] = []
    for sil_s, sil_e in silences:
        ov_s = max(s0, sil_s + pad_edge)
        ov_e = min(e0, sil_e - pad_edge)
        if ov_e - ov_s < min_cut_silence:
            continue
        if protected_window:
            ps, pe = protected_window
            # Do not cut silence during the visual payoff reaction
            if ov_e > ps and ov_s < pe:
                continue
        cuts.append((ov_s, ov_e))

    if not cuts:
        return [[round(s0, 2), round(e0, 2)]]

    cuts.sort()
    keep: list[list[float]] = []
    cursor = s0
    for cs, ce in cuts:
        if cs - cursor >= 0.45:
            keep.append([round(cursor, 2), round(cs, 2)])
        cursor = max(cursor, ce)
    if e0 - cursor >= 0.45:
        keep.append([round(cursor, 2), round(e0, 2)])

    return keep or [[round(s0, 2), round(e0, 2)]]


# ---------------------------------------------------------------------------
# 4. Stranger-Comprehension & Setup/Payoff Scoring + Copy Generation
# ---------------------------------------------------------------------------


def _format_transcript_for_prompt(words: list[dict[str, Any]], max_chars: int = 1200) -> str:
    if not words:
        return "(no spoken transcript detected)"
    parts = []
    for w in words[:160]:
        parts.append(f"[{w['start']:.1f}s]{w['word']}")
    joined = " ".join(parts)
    return joined[:max_chars]


def _make_clean_yt_title(raw_title: str, streamer: str, game_detected: str, is_gta6: bool) -> str:
    """Produce a clean 38-62 char YouTube Shorts title with NO Credit: or hashtag spam."""
    t = _strip_emoji_and_clean(raw_title)
    # Strip accidental Credit: or hashtags if an LLM leaked them
    t = re.sub(r"Credit:\s*@\S+.*$", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"#\S+", "", t).strip()
    if len(t) < 12:
        prefix = "GTA 6" if is_gta6 else game_detected
        t = f"{streamer} Lost It in {prefix}"
    if len(t) > 65:
        t = t[:62].rsplit(" ", 1)[0].strip()
    return t


def score_moment_for_strangers(
    streamer: str,
    clip_title: str,
    src_dur: float,
    strip_b64: str | None = None,
    words: list[dict[str, Any]] | None = None,
    require_gta6: bool = True,
    source_platform: str = "Twitch",
    strategy: dict[str, Any] | None = None,
    campaign: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Score a candidate clip on stranger comprehension (0-10) + virality (1-10) using
    both the 4-frame visual grid AND the timestamped word transcript.
    """
    handle_clean = re.sub(r"[^A-Za-z0-9_]", "", streamer.lower()) or "streamer"
    default_setup = max(0.0, min(src_dur - 14.0, 2.0))
    default_payoff = min(src_dur - 0.2, default_setup + 18.0)

    fallback_hook = "GTA 6 CHAOS HIT INSTANTLY" if require_gta6 else "THIS STREAM MOMENT WENT WILD"
    fallback_yt_title = _make_clean_yt_title(
        f"@{streamer} couldn't survive 5 seconds in GTA 6" if require_gta6 else f"{streamer} didn't see this coming",
        streamer,
        "GTA 6" if require_gta6 else "Gaming",
        require_gta6,
    )
    fallback_tag = "#gta6" if require_gta6 else "#gaming"
    fallback = {
        "is_gta6": True,
        "stranger_score": 7,
        "virality_score": 7,
        "scene_energy": "high_action",
        "reject_reason": "",
        "game_detected": "GTA 6" if require_gta6 else "Gaming",
        "visual_summary": f"Clip from @{streamer}: {clip_title}",
        "setup_start_sec": round(default_setup, 2),
        "payoff_end_sec": round(default_payoff, 2),
        "hook": fallback_hook,
        "punchline": "ZERO CHILL ON STREAM",
        "yt_title": fallback_yt_title,
        "caption_line": f"{'gta 6' if require_gta6 else handle_clean} did not let him breathe 💀",
        "caption": f"{'gta 6' if require_gta6 else handle_clean} did not let him breathe 💀\n\nCredit: @{streamer} ({source_platform})\n\n{fallback_tag} #{handle_clean} #shorts #gaming #funnymoments",
        "ig_first_comment": "Be honest, how would you have reacted right there?",
        "send_test": "Send to your gaming friend: 'bro watch his face at the end'",
        "campaign_safe": True,
        "model_used": "deterministic-fallback",
    }

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return fallback

    primary_model = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3.5-super-vl:free").strip()
    vision_models: list[str] = []
    for m in (
        primary_model,
        "nvidia/nemotron-3.5-super-vl:free",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "openrouter/free",
        "google/gemma-4-31b-it:free",
    ):
        if m and m not in vision_models:
            vision_models.append(m)
    text_fallbacks = ["nvidia/nemotron-3.5-lightning:free", "openrouter/free"]

    strat = strategy or {}
    win_str = "; ".join(strat.get("winning_patterns") or ["instant-karma crashes, chases, sudden fails, shocked reactions"])
    avoid_str = "; ".join(strat.get("avoid_patterns") or ["opening phone/menus, slow walking, idle talking with no payoff"])
    hook_guide = strat.get("hook_style_guidance") or "Lead with a punchy tension/irony hook that makes viewers stop scrolling."
    transcript_str = _format_transcript_for_prompt(words or [])
    campaign_block = ""
    if campaign:
        campaign_block = (
            f"\nACTIVE CAMPAIGN RULES ({campaign.get('name', 'Campaign')}):\n"
            f"- Max length: {campaign.get('max_length_sec', 60)}s | Banned terms: {campaign.get('banned_terms', [])}\n"
            f"- Required tags/text: {campaign.get('required_tags', [])} / {campaign.get('required_caption_text', '')}\n"
        )

    prompt = (
        f"You are the Lead Shorts Producer for a viral gaming clipping channel (source duration: {src_dur:.1f}s).\n"
        f"Streamer: @{streamer} ({source_platform}). Original clip title: {clip_title!r}.\n"
        f"WORD TRANSCRIPT (with timestamps):\n{transcript_str}\n\n"
        f"ANALYTICS STRATEGY:\n"
        f"- DOUBLE DOWN ON: {win_str}\n"
        f"- REJECT: {avoid_str}\n"
        f"- HOOK STYLE: {hook_guide}{campaign_block}\n\n"
        "EVALUATE THIS CLIP STRICTLY ON ONE CORE QUESTION:\n"
        "\"Would someone who has NEVER seen this streamer understand what is happening and why it is funny/wild within the FIRST 2 SECONDS?\"\n"
        "Rules:\n"
        "1. Describe ONLY what is literally visible in the 4 frames or spoken in the transcript. NEVER hallucinate events.\n"
        f"2. is_gta6=true ONLY if frames show Grand Theft Auto open-world gameplay (Vice City/Leonida streets, GTA characters/HUD/cars/police). Set false for shooters/FIFA/Minecraft/IRL-only/menus. (Note: require_gta6={require_gta6}).\n"
        "3. Score stranger_score (0-10) and virality_score (1-10):\n"
        "   - 8-10 ('high_action' or 'funny_reaction'): Immediate conflict, crash, fail, clutch, or hilarious outburst with clear setup & payoff.\n"
        "   - 1-6 ('needs_context', 'slow_menu', or 'boring_idle'): Inside jokes, slow menus/phones, aimless walking, or needs earlier VOD context.\n"
        "4. Pick exact setup_start_sec (float within 0..duration) right where the setup starts (<=1s before setup line, never dead air) and payoff_end_sec (float) right after the reaction lands (ideally 14-22s span).\n"
        "5. Write yt_title specifically for YouTube Shorts search/browse: 38-60 chars, intriguing human story/curiosity (NO hashtags, NO 'Credit:', NO all-caps spam).\n"
        "6. Write hook (4-7 words ALL CAPS, NO emoji) for the 2-second on-screen overlay, punchline (2-4 words ALL CAPS, NO emoji), and caption_line (3-7 words lowercase friend voice for IG/TikTok, NO question mark, NO em dash).\n\n"
        "Return STRICT JSON ONLY with keys: is_gta6, stranger_score, virality_score, scene_energy, reject_reason, "
        "game_detected, visual_summary, setup_start_sec, payoff_end_sec, hook, punchline, yt_title, caption_line, "
        "ig_first_comment, send_test, campaign_safe."
    )

    attempts: list[tuple[str, bool]] = [(m, True) for m in vision_models if strip_b64] + [(m, False) for m in text_fallbacks]
    for model, use_vision in attempts:
        content: Any = (
            [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{strip_b64}"}},
            ]
            if (use_vision and strip_b64)
            else prompt
        )
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.3,
            "reasoning": {"effort": "none"},
        }
        try:
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                method="POST",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/MAYOR-G/gta6-clipping-page",
                    "X-Title": "Leonida Chaos Stream Clipper Engine",
                },
            )
            with urllib.request.urlopen(req, timeout=22) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            raw = body["choices"][0]["message"].get("content") or ""
            if not raw.strip():
                continue
            m_json = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            parsed = json.loads(m_json.group(0) if m_json else raw)

            game_det = str(parsed.get("game_detected") or "Gaming").strip()
            if use_vision:
                is_gta6 = bool(parsed.get("is_gta6", False)) or ("gta" in game_det.lower()) or ("grand theft auto" in game_det.lower())
            else:
                is_gta6 = require_gta6

            stranger_s = max(0, min(10, int(parsed.get("stranger_score") or parsed.get("virality_score") or 7)))
            viral_s = max(1, min(10, int(parsed.get("virality_score") or stranger_s or 7)))
            scene_energy = str(parsed.get("scene_energy") or "high_action").strip().lower()
            reject_reason = str(parsed.get("reject_reason") or "").strip()

            s_start = float(parsed.get("setup_start_sec") if parsed.get("setup_start_sec") is not None else default_setup)
            p_end = float(parsed.get("payoff_end_sec") if parsed.get("payoff_end_sec") is not None else default_payoff)
            s_start = max(0.0, min(max(0.0, src_dur - 12.0), s_start))
            p_end = max(s_start + 12.0, min(src_dur - 0.15, p_end))

            hook_raw = _strip_emoji_and_clean(str(parsed.get("hook") or ""))
            hook_clean = re.sub(r"[^A-Za-z0-9 ':!.,?-]+", "", hook_raw).strip()[:54].upper() or fallback_hook
            punch_raw = _strip_emoji_and_clean(str(parsed.get("punchline") or ""))
            punch_clean = re.sub(r"[^A-Za-z0-9 ':!.,?-]+", "", punch_raw).strip()[:40].upper() or "ZERO CHILL ON STREAM"

            yt_title = _make_clean_yt_title(str(parsed.get("yt_title") or ""), streamer, game_det, is_gta6)

            cap_line = str(parsed.get("caption_line") or "bro did not expect that 💀").strip()
            cap_line = cap_line.replace("—", " ").replace("--", " ").rstrip("?").strip()
            if re.search(r"\b(official|leak|early|giveaway|free copy)\b", cap_line, re.I):
                cap_line = "bro did not expect that 💀"

            tag1 = "#gta6" if is_gta6 else f"#{re.sub(r'[^a-z0-9]', '', game_det.lower()) or 'gaming'}"
            tag3 = "#vicecity" if is_gta6 else ("#youtubeclips" if source_platform.lower() == "youtube" else "#twitchclips")
            caption = f"{cap_line}\n\nCredit: @{streamer} ({source_platform})\n\n{tag1} #{handle_clean} {tag3} #gaming #funnymoments"

            return {
                "is_gta6": is_gta6,
                "stranger_score": stranger_s,
                "virality_score": viral_s,
                "scene_energy": scene_energy,
                "reject_reason": reject_reason,
                "game_detected": "GTA 6" if is_gta6 else game_det,
                "visual_summary": str(parsed.get("visual_summary") or clip_title).strip(),
                "setup_start_sec": round(s_start, 2),
                "payoff_end_sec": round(p_end, 2),
                "hook": hook_clean,
                "punchline": punch_clean,
                "yt_title": yt_title,
                "caption_line": cap_line,
                "caption": caption,
                "ig_first_comment": str(parsed.get("ig_first_comment") or fallback["ig_first_comment"]).strip(),
                "send_test": str(parsed.get("send_test") or fallback["send_test"]).strip(),
                "campaign_safe": bool(parsed.get("campaign_safe", True)),
                "model_used": model,
            }
        except Exception:
            continue
    return fallback


# ---------------------------------------------------------------------------
# 5. Campaign Compliance Checker & Report Builder
# ---------------------------------------------------------------------------


def check_campaign_rules(clip_meta: dict[str, Any], campaign: dict[str, Any] | None) -> dict[str, Any]:
    """Check a clip against a clipping campaign's rules and return PASS/FIX diagnostics."""
    if not campaign:
        return {"passed": True, "campaign_id": None, "checks": [], "auto_fixes": []}

    checks: list[dict[str, str]] = []
    auto_fixes: list[str] = []

    dur = float(clip_meta.get("duration_sec") or 0.0)
    max_len = float(campaign.get("max_length_sec") or 60.0)
    min_len = float(campaign.get("min_length_sec") or 5.0)
    if min_len <= dur <= max_len:
        checks.append({"rule": "duration_limit", "status": "PASS", "detail": f"{dur:.1f}s within {min_len:.0f}-{max_len:.0f}s"})
    else:
        checks.append({"rule": "duration_limit", "status": "FIX", "detail": f"{dur:.1f}s outside {min_len:.0f}-{max_len:.0f}s"})

    caption = str(clip_meta.get("caption") or "")
    yt_title = str(clip_meta.get("yt_title") or "")
    combined = f"{caption}\n{yt_title}".lower()

    req_tags = [str(t).strip() for t in (campaign.get("required_tags") or []) if str(t).strip()]
    missing_tags = [t for t in req_tags if t.lower() not in caption.lower()]
    if not missing_tags:
        checks.append({"rule": "required_tags", "status": "PASS", "detail": "All required tags present"})
    else:
        checks.append({"rule": "required_tags", "status": "FIX", "detail": f"Missing: {', '.join(missing_tags)}"})
        auto_fixes.append(f"add_tags:{','.join(missing_tags)}")

    req_text = str(campaign.get("required_caption_text") or "").strip()
    if not req_text or req_text.lower() in caption.lower():
        checks.append({"rule": "required_caption_text", "status": "PASS", "detail": "Required text present"})
    else:
        checks.append({"rule": "required_caption_text", "status": "FIX", "detail": f"Missing: {req_text}"})
        auto_fixes.append(f"add_text:{req_text}")

    banned = [str(b).strip() for b in (campaign.get("banned_terms") or []) if str(b).strip()]
    hit_banned = [b for b in banned if re.search(rf"\b{re.escape(b.lower())}\b", combined)]
    if not hit_banned:
        checks.append({"rule": "banned_content", "status": "PASS", "detail": "No banned terms detected"})
    else:
        checks.append({"rule": "banned_content", "status": "FIX", "detail": f"Banned term hit: {', '.join(hit_banned)}"})

    allowed_plats = [p.lower() for p in (campaign.get("allowed_platforms") or ["youtube", "tiktok", "instagram"])]
    target_plats = [p.lower() for p in (clip_meta.get("platforms") or allowed_plats)]
    disallowed = [p for p in target_plats if p not in allowed_plats]
    if not disallowed:
        checks.append({"rule": "allowed_platforms", "status": "PASS", "detail": f"Platforms allowed: {', '.join(target_plats)}"})
    else:
        checks.append({"rule": "allowed_platforms", "status": "FIX", "detail": f"Not allowed by brief: {', '.join(disallowed)}"})

    passed = all(c["status"] == "PASS" for c in checks)
    return {
        "passed": passed,
        "campaign_id": campaign.get("id"),
        "checks": checks,
        "auto_fixes": auto_fixes,
    }


def apply_campaign_caption_fixes(caption: str, campaign: dict[str, Any] | None) -> str:
    """Inject required campaign caption text/tags while keeping total hashtags <= 5."""
    if not campaign:
        return caption
    lines = caption.strip().splitlines()
    body = lines[0] if lines else ""
    rest = "\n".join(lines[1:]) if len(lines) > 1 else ""

    req_text = str(campaign.get("required_caption_text") or "").strip()
    if req_text and req_text.lower() not in caption.lower():
        body = f"{body} {req_text}".strip()

    existing_tags = re.findall(r"#[A-Za-z0-9_]+", caption)
    req_tags = [str(t).strip() for t in (campaign.get("required_tags") or []) if str(t).strip()]
    for rt in req_tags:
        rt_norm = rt if rt.startswith("#") else f"#{rt}"
        if rt_norm.lower() not in [e.lower() for e in existing_tags]:
            existing_tags.insert(0, rt_norm)
    existing_tags = existing_tags[:5]

    non_tag_rest = [ln for ln in rest.splitlines() if not ln.strip().startswith("#")]
    out_parts = [body]
    if non_tag_rest:
        out_parts.append("\n".join(non_tag_rest))
    if existing_tags:
        out_parts.append(" ".join(existing_tags))
    return "\n\n".join(p.strip() for p in out_parts if p.strip())


def write_clips_report_md(items: list[dict[str, Any]], report_path: Path) -> Path:
    """Write `clips/report.md` summarizing every clip's signals, stranger score, captions & rules."""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Stream Clipper Report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "| Clip | Length | Timestamp / Source | Signals | Stranger Score | Captions | Hook Title | YouTube Title | Campaign Rules |",
        "| :--- | :---: | :--- | :--- | :---: | :---: | :--- | :--- | :---: |",
    ]
    for it in items:
        sigs = ", ".join(it.get("signals") or ["viewer_clip"])
        camp = it.get("campaign_check") or {}
        camp_str = "PASS" if camp.get("passed", True) else "FIX"
        lines.append(
            f"| `{it.get('post_id', it.get('file', ''))}` "
            f"| {float(it.get('duration_sec') or 0):.1f}s "
            f"| `{it.get('timestamp', '-')}` "
            f"| {sigs} "
            f"| {it.get('stranger_score', '-')}/10 "
            f"| {'Yes' if it.get('has_captions') else 'No'} "
            f"| {it.get('hook', '')} "
            f"| {it.get('yt_title', '')} "
            f"| {camp_str} |"
        )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze a long stream VOD using stream-clipper signals & scoring.")
    parser.add_argument("--vod-url", required=True, help="YouTube or Twitch VOD URL")
    parser.add_argument("--out-dir", type=Path, default=ROOT_DIR / "raw" / "vod_analysis", help="Output folder for signals JSON")
    parser.add_argument("--chat-json", type=Path, default=None, help="Optional pre-downloaded Twitch/YT chat JSON")
    parser.add_argument("--top-k", type=int, default=15, help="Top candidates to keep (default: 15)")
    args = parser.parse_args()

    signals: list[dict[str, Any]] = []
    if "youtube.com" in args.vod_url or "youtu.be" in args.vod_url:
        signals.extend(fetch_youtube_heatmap_signals(args.vod_url))
    if args.chat_json:
        signals.extend(analyze_chat_hype_signals(args.chat_json))

    merged = merge_signals(signals, top_k=args.top_k)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_json = args.out_dir / "candidates.json"
    out_json.write_text(json.dumps({"vod_url": args.vod_url, "candidates": merged}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"signals_raw": len(signals), "candidates_merged": len(merged), "out_json": str(out_json)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
