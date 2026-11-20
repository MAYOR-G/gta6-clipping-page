#!/usr/bin/env python3
"""
Step 3 (Prompt 3): Clip Template Renderer (clip.py)

Renders a 1080x1920, 30 fps vertical clip from a small JSON spec using Python + ffmpeg + Pillow.

Supported features:
  - Stack layout (facecam crop on top 1080x720, gameplay crop below 1080x1200) or full layout
  - Payoff-first teaser segment with its own hook text
  - White flash + synthesized whoosh SFX at the teaser -> full-play cut
  - Keep ranges to trim dead setup
  - Slow-mo segments (with audio tempo preservation)
  - Freeze frames with optional burned-in freeze text
  - Punchline text at source timestamps (never stacked on teaser, never over facecam)
  - Burned-in SRT subtitles mapped from source seconds onto the output timeline
  - Credit line (always in gameplay area, never over facecam)
  - Volume boost + safety limiter
  - Strict NO-EMOJI enforcement in all burned-in text
  - Automatic full-res QA frames (hook, flash, payoff, end) and contact-sheet preview

All spec times (teaser, keep, slowmo, freezes, punchlines, SRT) are SOURCE seconds
and are mapped automatically onto the output timeline.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

OUT_W = 1080
OUT_H = 1920
FACE_H = 720
GAME_Y = 720
GAME_H = OUT_H - FACE_H  # 1200
FPS = 30
FLASH_DUR = 0.14

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

# Reject emoji / pictographs / dingbats / variation selectors in burned-in text
EMOJI_RE = re.compile(
    "["
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FAFF"
    "\u2600-\u26FF"          # misc symbols
    "\u2700-\u27BF"          # dingbats
    "\uFE0E\uFE0F"           # variation selectors
    "]+",
    flags=re.UNICODE,
)


def assert_no_emoji(text: str, field_name: str) -> str:
    if EMOJI_RE.search(text or ""):
        raise ValueError(f"Emoji detected in burned-in text field '{field_name}': {text!r}. Remove emoji.")
    return text


def find_font(size: int, prefer_impact: bool = True) -> ImageFont.FreeTypeFont:
    candidates = FONT_CANDIDATES if prefer_impact else FONT_CANDIDATES[1:] + FONT_CANDIDATES[:1]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.strip().split()
    if not words:
        return []
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        test = f"{cur} {w}"
        bbox = draw.textbbox((0, 0), test, font=font, stroke_width=6)
        if (bbox[2] - bbox[0]) <= max_width:
            cur = test
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def render_text_overlay(
    text: str,
    out_png: Path,
    y_center: int,
    font_size: int = 68,
    max_width: int = 940,
    fill: tuple[int, int, int, int] = (255, 255, 255, 255),
    stroke_fill: tuple[int, int, int, int] = (0, 0, 0, 255),
    stroke_width: int = 8,
    align: str = "center",
    prefer_impact: bool = True,
    box_bg: tuple[int, int, int, int] | None = None,
    min_y: int = GAME_Y + 14,
) -> Path:
    """Render crisp transparent 1080x1920 PNG overlay guaranteed to stay in the gameplay zone."""
    img = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = find_font(font_size, prefer_impact=prefer_impact)
    lines = wrap_text(draw, text, font, max_width)
    if not lines:
        img.save(out_png)
        return out_png

    line_metrics = []
    total_h = 0
    line_gap = int(font_size * 0.22)
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        line_metrics.append((line, w, h, bbox[0], bbox[1]))
        total_h += h
    total_h += line_gap * (len(lines) - 1)

    y_start = max(min_y, int(y_center - total_h / 2))
    if y_start + total_h > OUT_H - 40:
        y_start = OUT_H - 40 - total_h

    if box_bg is not None:
        max_line_w = max(m[1] for m in line_metrics)
        pad_x, pad_y = 26, 14
        bx0 = (OUT_W - max_line_w) // 2 - pad_x
        by0 = y_start - pad_y
        bx1 = (OUT_W + max_line_w) // 2 + pad_x
        by1 = y_start + total_h + pad_y
        draw.rounded_rectangle([bx0, by0, bx1, by1], radius=18, fill=box_bg)

    y = y_start
    for line, w, h, bx, by in line_metrics:
        if align == "right":
            x = OUT_W - 36 - w - bx
        elif align == "left":
            x = 36 - bx
        else:
            x = (OUT_W - w) // 2 - bx
        # Subtle drop shadow
        draw.text(
            (x + 4, y - by + 4),
            line,
            font=font,
            fill=(0, 0, 0, 175),
            stroke_width=stroke_width,
            stroke_fill=(0, 0, 0, 175),
        )
        draw.text(
            (x, y - by),
            line,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        y += h + line_gap

    img.save(out_png)
    return out_png


def parse_srt_timestamp(ts: str) -> float:
    m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", ts.strip())
    if not m:
        raise ValueError(f"Invalid SRT timestamp: {ts!r}")
    h, mn, s, ms = map(int, m.groups())
    return h * 3600 + mn * 60 + s + ms / 1000.0


def parse_srt(srt_path: Path) -> list[dict[str, Any]]:
    raw = srt_path.read_text(encoding="utf-8-sig")
    blocks = re.split(r"\r?\n\r?\n+", raw.strip())
    cues = []
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        ts_idx = 1 if "-->" in lines[1] else 0
        if "-->" not in lines[ts_idx]:
            continue
        parts = lines[ts_idx].split("-->")
        start = parse_srt_timestamp(parts[0])
        end = parse_srt_timestamp(parts[1].split()[0])
        text = " ".join(lines[ts_idx + 1 :]).strip()
        if text:
            assert_no_emoji(text, f"subtitle@{start:.2f}s")
            cues.append({"start": start, "end": end, "text": text})
    return cues


def atempo_chain(speed: float) -> str:
    """Build valid ffmpeg atempo filter chain (each stage must be in [0.5, 2.0])."""
    if speed <= 0:
        raise ValueError("speed must be > 0")
    stages = []
    rem = speed
    while rem < 0.5:
        stages.append("atempo=0.5")
        rem /= 0.5
    while rem > 2.0:
        stages.append("atempo=2.0")
        rem /= 2.0
    stages.append(f"atempo={rem:.5f}")
    return ",".join(stages)


@dataclass
class OutputPiece:
    kind: str  # "teaser" | "flash" | "play" | "slowmo" | "freeze"
    src_start: float | None
    src_end: float | None
    out_start: float
    out_end: float
    speed: float = 1.0
    text: str | None = None


def build_body_plan(
    keep_ranges: list[list[float]],
    slowmos: list[dict[str, Any]],
    freezes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Split source keep ranges into ordered atomic segments (play, slowmo, freeze)."""
    segments: list[dict[str, Any]] = []
    for k_start, k_end in keep_ranges:
        if k_end <= k_start:
            continue
        cuts = {float(k_start), float(k_end)}
        for sm in slowmos:
            s, e = float(sm["start"]), float(sm["end"])
            if e > k_start and s < k_end:
                cuts.add(max(k_start, s))
                cuts.add(min(k_end, e))
        freeze_points = []
        for fr in freezes:
            at = float(fr["at"])
            if k_start <= at <= k_end:
                cuts.add(at)
                freeze_points.append(fr)

        sorted_cuts = sorted(cuts)
        for i in range(len(sorted_cuts) - 1):
            a, b = sorted_cuts[i], sorted_cuts[i + 1]
            if b - a > 0.02:
                mid = (a + b) / 2.0
                speed = 1.0
                kind = "play"
                for sm in slowmos:
                    if float(sm["start"]) <= mid <= float(sm["end"]):
                        speed = float(sm.get("speed", 0.45))
                        kind = "slowmo"
                        break
                segments.append({"kind": kind, "start": a, "end": b, "speed": speed})
            # Insert freeze if a freeze sits right at `b`
            for fr in freeze_points:
                if abs(float(fr["at"]) - b) < 1e-4:
                    segments.append({
                        "kind": "freeze",
                        "at": b,
                        "duration": float(fr.get("duration", 0.8)),
                        "text": fr.get("text"),
                    })
    return segments


def map_source_interval_to_body(
    src_start: float,
    src_end: float,
    pieces: list[OutputPiece],
) -> list[tuple[float, float]]:
    """Map a source [start, end] interval onto output body timeline ranges (excluding teaser/flash)."""
    mapped: list[tuple[float, float]] = []
    for p in pieces:
        if p.kind not in ("play", "slowmo") or p.src_start is None or p.src_end is None:
            continue
        ov_s = max(src_start, p.src_start)
        ov_e = min(src_end, p.src_end)
        if ov_e <= ov_s:
            continue
        out_s = p.out_start + (ov_s - p.src_start) / p.speed
        out_e = p.out_start + (ov_e - p.src_start) / p.speed
        mapped.append((out_s, out_e))

    # Merge touching ranges
    if not mapped:
        return []
    mapped.sort()
    merged = [mapped[0]]
    for s, e in mapped[1:]:
        ps, pe = merged[-1]
        if s <= pe + 0.08:
            merged[-1] = (ps, max(pe, e))
        else:
            merged.append((s, e))
    return merged


def run_ffmpeg(cmd: list[str], quiet: bool = False) -> None:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE if quiet else None,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}):\n{proc.stderr[-2500:]}")


def build_base_video_filter(layout: str, facecam: list[int] | None, game: list[int] | None) -> str:
    """Build filtergraph converting input [0:v] into 1080x1920 @ 30fps base canvas [basev]."""
    if layout == "stack":
        if not facecam or not game:
            raise ValueError("stack layout requires both 'facecam' [x,y,w,h] and 'game' [x,y,w,h]")
        fx, fy, fw, fh = facecam
        gx, gy, gw, gh = game
        return (
            f"[0:v]fps={FPS},split=2[fraw][graw];"
            f"[fraw]crop={fw}:{fh}:{fx}:{fy},"
            f"scale={OUT_W}:{FACE_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{FACE_H},setsar=1[ftop];"
            f"[graw]crop={gw}:{gh}:{gx}:{gy},"
            f"scale={OUT_W}:{GAME_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{GAME_H},setsar=1[gbot];"
            f"[ftop][gbot]vstack=inputs=2,"
            f"drawbox=x=0:y={GAME_Y-2}:w={OUT_W}:h=4:color=white@0.85:t=fill[basev]"
        )
    elif layout == "full":
        if game:
            gx, gy, gw, gh = game
            crop_part = f"crop={gw}:{gh}:{gx}:{gy},"
        else:
            crop_part = ""
        return (
            f"[0:v]fps={FPS},{crop_part}"
            f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{OUT_H},setsar=1[basev]"
        )
    else:
        raise ValueError(f"Unsupported layout: {layout!r} (expected 'stack' or 'full')")


def generate_whoosh_wav(out_wav: Path, duration: float = 0.28) -> None:
    """Synthesize a clean whoosh transition sound effect using ffmpeg aevalsrc."""
    # Band-swept noise burst with smooth envelope
    expr = (
        f"sin(PI*t/{duration})*sin(PI*t/{duration})*"
        f"(0.55*random(0)+0.35*sin(2*PI*(180+900*t/{duration})*t))"
    )
    run_ffmpeg([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"aevalsrc='{expr}':s=48000:d={duration:.3f}",
        "-af", "highpass=f=180,lowpass=f=3600,volume=0.9",
        "-ac", "2", "-ar", "48000", str(out_wav),
    ], quiet=True)


def make_contact_sheet(video_path: Path, out_png: Path, interval: float = 2.0, cols: int = 4) -> Path:
    """Build a crisp timestamped contact sheet (1 frame every `interval` seconds) using ffmpeg + Pillow."""
    with tempfile.TemporaryDirectory(prefix="sheet_") as td:
        td_path = Path(td)
        run_ffmpeg([
            "ffmpeg", "-y", "-i", str(video_path),
            "-vf", f"fps=1/{interval},scale=360:-2",
            str(td_path / "f_%04d.png"),
        ], quiet=True)
        frames = sorted(td_path.glob("f_*.png"))
        if not frames:
            raise RuntimeError(f"No frames extracted for contact sheet from {video_path}")

        sample = Image.open(frames[0])
        tw, th = sample.size
        rows = math.ceil(len(frames) / cols)
        sheet = Image.new("RGB", (cols * tw, rows * th), (15, 15, 18))
        font = find_font(26, prefer_impact=False)

        for idx, fp in enumerate(frames):
            im = Image.open(fp).convert("RGB")
            draw = ImageDraw.Draw(im)
            t_sec = idx * interval
            label = f"{int(t_sec // 60):02d}:{t_sec % 60:04.1f}"
            draw.rectangle([8, 8, 128, 44], fill=(0, 0, 0))
            draw.text((16, 12), label, font=font, fill=(255, 255, 0))
            r, c = divmod(idx, cols)
            sheet.paste(im, (c * tw, r * th))

        out_png.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(out_png)
        return out_png


def extract_qa_frames(video_path: Path, qa_dir: Path, flash_time: float | None, total_dur: float) -> dict[str, str]:
    """Extract full-resolution hook, flash, payoff (~78%), and end frames for visual QA."""
    qa_dir.mkdir(parents=True, exist_ok=True)
    targets = {
        "hook": 0.25,
        "payoff": max(0.5, total_dur * 0.78),
        "end": max(0.1, total_dur - 0.15),
    }
    if flash_time is not None:
        targets["flash"] = flash_time + FLASH_DUR * 0.45

    saved = {}
    for name, ts in targets.items():
        out_img = qa_dir / f"{video_path.stem}_{name}.png"
        run_ffmpeg([
            "ffmpeg", "-y", "-ss", f"{ts:.3f}", "-i", str(video_path),
            "-frames:v", "1", str(out_img),
        ], quiet=True)
        saved[name] = str(out_img)
    return saved


def probe_duration(video_path: Path) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
    ], text=True).strip()
    return float(out)


def render_clip(spec_path: Path, make_qa: bool = True, make_sheet: bool = True) -> dict[str, Any]:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    base_dir = spec_path.parent

    src = Path(spec["src"])
    if not src.is_absolute():
        src = (base_dir / src).resolve()
    if not src.exists():
        raise FileNotFoundError(f"Source video not found: {src}")

    out_path = Path(spec["out"])
    if not out_path.is_absolute():
        out_path = (base_dir / out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    credit = assert_no_emoji(spec.get("credit", ""), "credit").strip()
    if not credit:
        raise ValueError("Spec must include a non-empty 'credit' line (e.g. '@streamer (Twitch)').")

    teaser = spec.get("teaser")
    teaser_text = assert_no_emoji(spec.get("teaser_text", ""), "teaser_text").strip()
    layout = spec.get("layout", "stack")
    facecam = spec.get("facecam")
    game = spec.get("game")
    keep = spec.get("keep") or []
    slowmos = spec.get("slowmo") or []
    freezes = spec.get("freezes") or []
    punchlines = spec.get("punchlines") or []
    volume = float(spec.get("volume", 1.0))

    for idx, p in enumerate(punchlines):
        assert_no_emoji(p.get("text", ""), f"punchlines[{idx}].text")
    for idx, fr in enumerate(freezes):
        if fr.get("text"):
            assert_no_emoji(fr["text"], f"freezes[{idx}].text")

    subtitles_cues: list[dict[str, Any]] = []
    if spec.get("subtitles"):
        srt_path = Path(spec["subtitles"])
        if not srt_path.is_absolute():
            srt_path = (base_dir / srt_path).resolve()
        subtitles_cues = parse_srt(srt_path)

    if not keep:
        src_dur = probe_duration(src)
        keep = [[0.0, src_dur]]

    with tempfile.TemporaryDirectory(prefix="clip_render_") as td:
        td_path = Path(td)
        # 1. Pre-render clean stacked/cropped base master so all segments share exact 1080x1920 30fps geometry
        base_mp4 = td_path / "base_1080x1920.mp4"
        base_vf = build_base_video_filter(layout, facecam, game)
        run_ffmpeg([
            "ffmpeg", "-y", "-i", str(src),
            "-filter_complex", base_vf,
            "-map", "[basev]", "-map", "0:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
            str(base_mp4),
        ], quiet=True)

        # 2. Build ordered timeline pieces (teaser -> flash -> body)
        seg_files: list[Path] = []
        pieces: list[OutputPiece] = []
        cursor = 0.0
        flash_time: float | None = None

        if teaser:
            ts_s, ts_e = float(teaser["start"]), float(teaser["end"])
            ts_dur = ts_e - ts_s
            if ts_dur <= 0:
                raise ValueError("teaser end must be > teaser start")
            seg_fp = td_path / f"seg_{len(seg_files):03d}_teaser.mkv"
            run_ffmpeg([
                "ffmpeg", "-y", "-ss", f"{ts_s:.4f}", "-t", f"{ts_dur:.4f}", "-i", str(base_mp4),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-r", str(FPS),
                "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
                str(seg_fp),
            ], quiet=True)
            seg_files.append(seg_fp)
            pieces.append(OutputPiece("teaser", ts_s, ts_e, cursor, cursor + ts_dur, 1.0, teaser_text or None))
            cursor += ts_dur

            # White flash + whoosh transition
            flash_time = cursor
            whoosh_wav = td_path / "whoosh.wav"
            generate_whoosh_wav(whoosh_wav, duration=FLASH_DUR)
            flash_fp = td_path / f"seg_{len(seg_files):03d}_flash.mkv"
            run_ffmpeg([
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", f"color=c=white:s={OUT_W}x{OUT_H}:r={FPS}:d={FLASH_DUR:.3f}",
                "-i", str(whoosh_wav),
                "-shortest",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p",
                "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
                str(flash_fp),
            ], quiet=True)
            seg_files.append(flash_fp)
            pieces.append(OutputPiece("flash", None, None, cursor, cursor + FLASH_DUR))
            cursor += FLASH_DUR

        body_plan = build_body_plan(keep, slowmos, freezes)
        for seg in body_plan:
            seg_fp = td_path / f"seg_{len(seg_files):03d}_{seg['kind']}.mkv"
            if seg["kind"] == "play":
                dur = seg["end"] - seg["start"]
                run_ffmpeg([
                    "ffmpeg", "-y", "-ss", f"{seg['start']:.4f}", "-t", f"{dur:.4f}", "-i", str(base_mp4),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-r", str(FPS),
                    "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
                    str(seg_fp),
                ], quiet=True)
                pieces.append(OutputPiece("play", seg["start"], seg["end"], cursor, cursor + dur, 1.0))
                cursor += dur
            elif seg["kind"] == "slowmo":
                dur = seg["end"] - seg["start"]
                spd = seg["speed"]
                out_dur = dur / spd
                run_ffmpeg([
                    "ffmpeg", "-y", "-ss", f"{seg['start']:.4f}", "-t", f"{dur:.4f}", "-i", str(base_mp4),
                    "-vf", f"setpts=PTS/{spd:.5f},fps={FPS}",
                    "-af", atempo_chain(spd),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
                    "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
                    str(seg_fp),
                ], quiet=True)
                pieces.append(OutputPiece("slowmo", seg["start"], seg["end"], cursor, cursor + out_dur, spd))
                cursor += out_dur
            elif seg["kind"] == "freeze":
                fdur = seg["duration"]
                frame_png = td_path / f"freeze_{len(seg_files):03d}.png"
                run_ffmpeg([
                    "ffmpeg", "-y", "-ss", f"{seg['at']:.4f}", "-i", str(base_mp4),
                    "-frames:v", "1", str(frame_png),
                ], quiet=True)
                run_ffmpeg([
                    "ffmpeg", "-y",
                    "-loop", "1", "-t", f"{fdur:.3f}", "-i", str(frame_png),
                    "-f", "lavfi", "-t", f"{fdur:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-r", str(FPS), "-pix_fmt", "yuv420p",
                    "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
                    str(seg_fp),
                ], quiet=True)
                pieces.append(OutputPiece("freeze", seg["at"], seg["at"], cursor, cursor + fdur, 0.0, seg.get("text")))
                cursor += fdur
            seg_files.append(seg_fp)

        # 3. Concatenate all timeline pieces cleanly
        concat_txt = td_path / "concat.txt"
        concat_txt.write_text(
            "\n".join(f"file '{p.as_posix()}'" for p in seg_files) + "\n",
            encoding="utf-8",
        )
        timeline_mp4 = td_path / "timeline.mkv"
        run_ffmpeg([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-r", str(FPS), "-pix_fmt", "yuv420p",
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
            str(timeline_mp4),
        ], quiet=True)

        # 4. Generate all transparent PNG overlays (NEVER covering facecam y < 720)
        overlays: list[tuple[Path, float | None, float | None]] = []

        # Permanent credit badge (top-right inside gameplay panel)
        credit_png = td_path / "ov_credit.png"
        render_text_overlay(
            f"Credit: {credit}",
            credit_png,
            y_center=GAME_Y + 34,
            font_size=28,
            stroke_width=4,
            align="right",
            prefer_impact=False,
            fill=(255, 255, 255, 225),
        )
        overlays.append((credit_png, None, None))

        # Teaser hook text (strictly during teaser window)
        for p in pieces:
            if p.kind == "teaser" and p.text:
                ov = td_path / "ov_teaser.png"
                render_text_overlay(
                    p.text.upper(),
                    ov,
                    y_center=GAME_Y + 150,
                    font_size=72,
                    stroke_width=9,
                    prefer_impact=True,
                )
                overlays.append((ov, p.out_start, max(p.out_start, p.out_end - 0.03)))
            elif p.kind == "freeze" and p.text:
                ov = td_path / f"ov_freeze_{len(overlays)}.png"
                render_text_overlay(
                    p.text.upper(),
                    ov,
                    y_center=GAME_Y + 180,
                    font_size=70,
                    stroke_width=9,
                    prefer_impact=True,
                )
                overlays.append((ov, p.out_start, p.out_end))

        # Punchline overlays mapped strictly onto body timeline (NEVER in teaser window)
        for idx, pl in enumerate(punchlines):
            ranges = map_source_interval_to_body(float(pl["start"]), float(pl["end"]), pieces)
            if not ranges:
                continue
            ov = td_path / f"ov_punch_{idx}.png"
            render_text_overlay(
                pl["text"].upper(),
                ov,
                y_center=GAME_Y + 160,
                font_size=66,
                stroke_width=8,
                prefer_impact=True,
            )
            for s, e in ranges:
                overlays.append((ov, s, e))

        # Burned-in SRT subtitles mapped onto body timeline (lower gameplay zone, safe above bottom UI)
        for idx, cue in enumerate(subtitles_cues):
            ranges = map_source_interval_to_body(cue["start"], cue["end"], pieces)
            if not ranges:
                continue
            ov = td_path / f"ov_sub_{idx}.png"
            render_text_overlay(
                cue["text"],
                ov,
                y_center=GAME_Y + 760,  # y=1480, safely above bottom ~320px UI chrome
                font_size=50,
                max_width=900,
                stroke_width=7,
                prefer_impact=False,
                box_bg=(0, 0, 0, 125),
            )
            for s, e in ranges:
                overlays.append((ov, s, e))

        # 5. Composite all overlays + audio boost/limiter into final 1080x1920 30fps MP4
        cmd = ["ffmpeg", "-y", "-i", str(timeline_mp4)]
        for ov_path, _, _ in overlays:
            cmd.extend(["-i", str(ov_path)])

        filter_parts = []
        last_v = "[0:v]"
        for i, (_, s, e) in enumerate(overlays, start=1):
            next_v = f"[v{i}]"
            if s is None or e is None:
                filter_parts.append(f"{last_v}[{i}:v]overlay=0:0:eof_action=repeat{next_v}")
            else:
                filter_parts.append(
                    f"{last_v}[{i}:v]overlay=0:0:eof_action=repeat:enable='between(t,{s:.3f},{e:.3f})'{next_v}"
                )
            last_v = next_v

        af_chain = f"volume={volume:.3f},alimiter=limit=0.95"
        if spec.get("loudnorm", False):
            af_chain = f"volume={volume:.3f},loudnorm=I=-16:TP=-1.5:LRA=11"

        cmd.extend([
            "-filter_complex", ";".join(filter_parts),
            "-map", last_v, "-map", "0:a:0",
            "-af", af_chain,
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-r", str(FPS), "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            str(out_path),
        ])
        run_ffmpeg(cmd, quiet=True)

    final_dur = probe_duration(out_path)
    qa_dir = out_path.parent / "qa"
    qa_frames = extract_qa_frames(out_path, qa_dir, flash_time, final_dur) if make_qa else {}
    sheet_png = None
    if make_sheet:
        sheet_png = str(make_contact_sheet(out_path, qa_dir / f"{out_path.stem}_sheet.png", interval=2.0))

    warnings = []
    if final_dur < 12.0 or final_dur > 22.0:
        warnings.append(f"Duration {final_dur:.2f}s is outside target 12-22s window (~20s target).")

    return {
        "out": str(out_path),
        "duration_sec": round(final_dur, 2),
        "resolution": f"{OUT_W}x{OUT_H}",
        "fps": FPS,
        "flash_at_sec": round(flash_time, 2) if flash_time is not None else None,
        "qa_frames": qa_frames,
        "contact_sheet": sheet_png,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Render 1080x1920 30fps vertical clip from JSON spec.")
    parser.add_argument("spec", type=Path, help="Path to JSON clip spec")
    parser.add_argument("--no-qa", action="store_true", help="Skip QA frame extraction")
    parser.add_argument("--no-sheet", action="store_true", help="Skip contact-sheet generation")
    args = parser.parse_args()

    result = render_clip(args.spec, make_qa=not args.no_qa, make_sheet=not args.no_sheet)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
