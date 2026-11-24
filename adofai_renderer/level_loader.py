"""Level import utilities: Open File, Open Folder, Open ZIP, exact duration probe, and audio waveform peaks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import zipfile
from typing import List, Optional, Tuple

import numpy as np

from .level_info import AdofaiLevelInfo, parse_adofai_info
from .paths import get_app_root, get_data_root

PROJECT_ROOT = get_app_root()
ZIP_CACHE_ROOT = os.path.join(get_data_root(), "cache", "extracted_zips")


def find_adofai_in_directory(folder: str) -> List[str]:
    """Return candidate .adofai files inside folder (root first, then up to 2 sublevels), preferring non-backup files."""
    if not folder or not os.path.isdir(folder):
        return []

    primary: List[str] = []
    backups: List[str] = []
    base_depth = folder.rstrip("\\/").count(os.sep)

    for root, dirs, files in os.walk(folder):
        depth = root.rstrip("\\/").count(os.sep) - base_depth
        if depth > 2:
            dirs[:] = []
            continue
        dirs[:] = [d for d in sorted(dirs) if not d.startswith(".") and d != "__MACOSX"]
        for fn in sorted(files):
            if not fn.lower().endswith(".adofai"):
                continue
            full = os.path.join(root, fn)
            low = fn.lower()
            if low == "backup.adofai" or low.startswith("backup"):
                backups.append(full)
            else:
                primary.append(full)

    # Prefer shallower depth, then larger file size
    primary.sort(key=lambda p: (p.count(os.sep), -os.path.getsize(p)))
    backups.sort(key=lambda p: (p.count(os.sep), -os.path.getsize(p)))
    return primary + backups


def _decode_zip_name(info: zipfile.ZipInfo) -> str:
    """Safely decode ZIP member names including Korean CP949/EUC-KR archives created on Windows."""
    raw_name = info.filename
    if info.flag_bits & 0x800:
        return raw_name
    try:
        raw_bytes = raw_name.encode("cp437")
    except Exception:
        return raw_name
    for enc in ("utf-8", "cp949", "euc-kr", "shift_jis"):
        try:
            return raw_bytes.decode(enc)
        except Exception:
            continue
    return raw_name


def extract_zip_level(zip_path: str) -> Tuple[str, List[str]]:
    """Extract a .zip archive containing an ADOFAI level into cache and return (extract_dir, adofai_candidates)."""
    abs_zip = os.path.abspath(zip_path)
    if not os.path.isfile(abs_zip):
        raise FileNotFoundError(f"ZIP 파일을 찾을 수 없습니다: {abs_zip}")

    st = os.stat(abs_zip)
    sig = f"{abs_zip}|{st.st_size}|{int(st.st_mtime)}".encode("utf-8", errors="ignore")
    h = hashlib.sha1(sig).hexdigest()[:12]
    stem = re.sub(r"[^A-Za-z0-9가-힣_\- ]+", "_", os.path.splitext(os.path.basename(abs_zip))[0]).strip()[:36] or "level"
    dest_dir = os.path.join(ZIP_CACHE_ROOT, f"{stem}_{h}")
    marker = os.path.join(dest_dir, ".extracted.ok")

    if not os.path.isfile(marker):
        os.makedirs(dest_dir, exist_ok=True)
        real_dest = os.path.realpath(dest_dir)
        with zipfile.ZipFile(abs_zip, "r") as zf:
            for member in zf.infolist():
                decoded = _decode_zip_name(member).replace("\\", "/")
                if not decoded or decoded.startswith("__MACOSX/") or "/__MACOSX/" in decoded:
                    continue
                if decoded.endswith("/"):
                    continue
                rel = os.path.normpath(decoded).lstrip("\\/")
                if rel.startswith("..") or os.path.isabs(rel):
                    continue
                target_path = os.path.realpath(os.path.join(dest_dir, rel))
                if not target_path.startswith(real_dest + os.sep) and target_path != real_dest:
                    continue
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                with zf.open(member, "r") as src, open(target_path, "wb") as dst:
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        dst.write(chunk)
        with open(marker, "w", encoding="utf-8") as mf:
            mf.write(abs_zip)

    candidates = find_adofai_in_directory(dest_dir)
    if not candidates:
        raise ValueError(f"압축 파일 내부에서 .adofai 레벨 파일을 찾지 못했습니다: {os.path.basename(abs_zip)}")
    return dest_dir, candidates


def resolve_level_input(path: str) -> str:
    """Accept a .adofai file, folder containing .adofai, or .zip archive and return the resolved .adofai path."""
    p = os.path.abspath(path)
    if os.path.isdir(p):
        cands = find_adofai_in_directory(p)
        if not cands:
            raise ValueError(f"폴더에서 .adofai 레벨 파일을 찾을 수 없습니다: {p}")
        return cands[0]
    if os.path.isfile(p) and p.lower().endswith(".zip"):
        _dir, cands = extract_zip_level(p)
        return cands[0]
    if os.path.isfile(p) and p.lower().endswith(".adofai"):
        return p
    raise FileNotFoundError(f"지원하지 않거나 존재하지 않는 레벨 경로입니다: {p}")


def probe_media_duration(media_path: str, ffprobe_path: str = "ffprobe") -> float:
    """Return duration in seconds of an audio/video file via ffprobe, or 0.0 on failure."""
    if not media_path or not os.path.isfile(media_path):
        return 0.0
    try:
        res = subprocess.run(
            [
                ffprobe_path, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json",
                media_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if res.returncode == 0:
            data = json.loads(res.stdout)
            return max(0.0, float(data.get("format", {}).get("duration", 0.0) or 0.0))
    except Exception:
        pass
    return 0.0


def estimate_level_duration_sec(info: AdofaiLevelInfo, ffprobe_path: str = "ffprobe") -> float:
    """Estimate total playable duration of an ADOFAI level (accounting for song duration & pitch)."""
    song_dur = 0.0
    if info.song_path and os.path.isfile(info.song_path):
        raw_dur = probe_media_duration(info.song_path, ffprobe_path)
        pitch_factor = max(0.25, (info.pitch or 100.0) / 100.0)
        song_dur = raw_dur / pitch_factor

    if song_dur > 1.0:
        return song_dur

    # Fallback heuristic from tile count & initial BPM when no audio file is present
    bpm = max(20.0, info.bpm or 100.0)
    tiles = max(1, info.tile_count)
    return max(5.0, (tiles * 60.0 / bpm) * 0.75)


def extract_audio_waveform(
    audio_path: str,
    ffmpeg_path: str = "ffmpeg",
    bins: int = 1000,
) -> Optional[List[float]]:
    """Extract normalized high-resolution peak/RMS envelope bins (0.0..1.0) from level BGM for the timeline waveform."""
    if not audio_path or not os.path.isfile(audio_path):
        return None
    try:
        res = subprocess.run(
            [
                ffmpeg_path,
                "-hide_banner", "-loglevel", "error",
                "-i", audio_path,
                "-ac", "1",
                "-ar", "4000",
                "-f", "f32le",
                "-",
            ],
            capture_output=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if res.returncode != 0 or len(res.stdout) < 16:
            return None
        samples = np.frombuffer(res.stdout, dtype=np.float32)
        n = len(samples)
        if n < bins:
            return [float(min(1.0, abs(x))) for x in samples]
        step = n // bins
        trimmed = samples[: step * bins].reshape(bins, step)
        peak_env = np.max(np.abs(trimmed), axis=1)
        rms_env = np.sqrt(np.mean(trimmed * trimmed, axis=1))
        peaks = 0.65 * peak_env + 0.35 * rms_env
        max_p = float(np.max(peaks))
        if max_p > 1e-6:
            peaks = np.clip(peaks / max_p, 0.0, 1.0)
        return [float(x) for x in peaks]
    except Exception:
        return None
