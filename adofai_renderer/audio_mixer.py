"""Deterministic offline audio mixer using real ADOFAI AudioClip WAVs and level BGM."""

from __future__ import annotations

import json
import math
import os
import subprocess
import wave
from dataclasses import dataclass
from typing import Callable, Dict, Optional

import numpy as np


@dataclass
class AudioMixOptions:
    include_bgm: bool = True
    bgm_volume: float = 1.0
    include_hitsounds: bool = True
    hitsound_volume: float = 1.0
    include_sfx: bool = True
    sfx_volume: float = 1.0
    sample_rate: int = 48000


def _probe_sample_rate(ffmpeg_path: str, audio_path: str) -> int:
    ffprobe = os.path.join(os.path.dirname(ffmpeg_path), "ffprobe.exe")
    if not os.path.exists(ffprobe):
        ffprobe = "ffprobe"
    try:
        res = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=sample_rate",
                "-of", "default=noprint_wrappers=1:nokey=1",
                audio_path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        val = int(res.stdout.strip().splitlines()[0])
        if 8000 <= val <= 384000:
            return val
    except Exception:
        pass
    return 44100


def _decode_audio_f32_stereo(
    ffmpeg_path: str,
    audio_path: str,
    target_sr: int = 48000,
    pitch: float = 1.0,
) -> np.ndarray:
    """Decode any audio file to (N, 2) float32 stereo at target_sr with exact Unity-style pitch shift."""
    cmd = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-i", audio_path]

    if abs(pitch - 1.0) > 1e-4 and pitch > 0.05:
        src_sr = _probe_sample_rate(ffmpeg_path, audio_path)
        new_rate = max(4000.0, src_sr * pitch)
        cmd += ["-af", f"asetrate={new_rate:.4f},aresample={target_sr}"]
    else:
        cmd += ["-ar", str(target_sr)]

    cmd += ["-ac", "2", "-f", "f32le", "-"]

    res = subprocess.run(
        cmd,
        capture_output=True,
        timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if res.returncode != 0:
        err = res.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"FFmpeg audio decode failed for {audio_path}: {err}")

    raw = res.stdout
    if not raw:
        return np.zeros((0, 2), dtype=np.float32)

    usable = (len(raw) // 8) * 8
    arr = np.frombuffer(raw[:usable], dtype=np.float32).reshape(-1, 2).copy()
    return arr


def _write_wav_pcm16(path: str, stereo_f32: np.ndarray, sample_rate: int) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    clipped = np.clip(stereo_f32, -1.0, 1.0)
    pcm16 = np.round(clipped * 32767.0).astype("<i2")
    with wave.open(path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())


def _soft_limit_inplace(mix: np.ndarray) -> None:
    """Apply gentle soft-knee limiting above 0.92 to avoid harsh clipping during dense hitsound streams."""
    peak = float(np.max(np.abs(mix))) if mix.size else 0.0
    if peak <= 0.98:
        return
    knee = 0.92
    mask = np.abs(mix) > knee
    if np.any(mask):
        vals = mix[mask]
        sign = np.sign(vals)
        over = np.abs(vals) - knee
        # Smoothly map [knee, inf) -> [knee, 0.995)
        headroom = 0.075
        mix[mask] = sign * (knee + headroom * np.tanh(over / headroom))


def mix_audio_from_timeline(
    timeline_json_path: str,
    output_wav_path: str,
    ffmpeg_path: str = "ffmpeg",
    options: Optional[AudioMixOptions] = None,
    log_cb: Optional[Callable[[str], None]] = None,
) -> bool:
    """Mix BGM and real extracted ADOFAI hitsounds/SFX into a sample-accurate stereo WAV.

    Returns True if non-silent audio was written, False if audio is disabled/empty.
    """
    if options is None:
        options = AudioMixOptions()

    if not options.include_bgm and not options.include_hitsounds and not options.include_sfx:
        return False

    with open(timeline_json_path, "r", encoding="utf-8-sig") as f:
        tl = json.load(f)

    sr = options.sample_rate
    duration_sec = float(tl.get("videoDurationSec", 0.0))
    if duration_sec <= 0.0:
        fps = max(1, int(tl.get("fps", 60)))
        frames = int(tl.get("totalCapturedFrames", 0))
        duration_sec = frames / float(fps)

    total_samples = max(1, int(round(duration_sec * sr)))
    mix = np.zeros((total_samples, 2), dtype=np.float32)

    # 1. Mix BGM track at exact songStartInVideoSec
    song_path = tl.get("songFilePath", "")
    song_pitch = float(tl.get("songPitch", 1.0) or 1.0)
    song_vol = float(tl.get("songVolume", 1.0) or 1.0)
    song_start_sec = float(tl.get("songStartInVideoSec", 0.0))

    if options.include_bgm and options.bgm_volume > 0.0 and song_path and os.path.isfile(song_path):
        if log_cb:
            log_cb(f"BGM 디코딩 중: {os.path.basename(song_path)} (pitch={song_pitch:.3f}, offset={song_start_sec:.4f}s)")
        bgm = _decode_audio_f32_stereo(ffmpeg_path, song_path, target_sr=sr, pitch=song_pitch)
        gain = song_vol * options.bgm_volume
        start_idx = int(round(song_start_sec * sr))
        if start_idx >= 0:
            dst_start = start_idx
            src_start = 0
        else:
            dst_start = 0
            src_start = -start_idx

        avail_src = len(bgm) - src_start
        avail_dst = total_samples - dst_start
        copy_len = min(avail_src, avail_dst)
        if copy_len > 0:
            mix[dst_start : dst_start + copy_len] += bgm[src_start : src_start + copy_len] * gain

    # 2. Mix hitsounds, hold sounds, countdown ticks, and custom SFX using real exported WAVs
    events = tl.get("events", [])
    clip_cache: Dict[tuple, np.ndarray] = {}
    mixed_events = 0
    missing_clips = set()

    for ev in events:
        cat = ev.get("category", "hitsound")
        if cat in ("hitsound", "holdsound", "countdown"):
            if not options.include_hitsounds or options.hitsound_volume <= 0.0:
                continue
            cat_gain = options.hitsound_volume
        else:
            if not options.include_sfx or options.sfx_volume <= 0.0:
                continue
            cat_gain = options.sfx_volume

        ev_time = float(ev.get("timeSec", -9999.0))
        ev_end = float(ev.get("endTimeSec", -1.0))
        if ev_time > duration_sec + 0.1:
            continue
        if ev_time < -10.0 and ev_end < 0.0:
            continue

        wav_path = ev.get("wav", "")
        if not wav_path or not os.path.isfile(wav_path):
            missing_clips.add(ev.get("clip", "?"))
            continue

        ev_pitch = float(ev.get("pitch", 1.0) or 1.0)
        ev_vol = float(ev.get("volume", 1.0) or 1.0) * cat_gain
        if ev_vol <= 1e-5:
            continue

        cache_key = (wav_path, round(ev_pitch, 4))
        clip = clip_cache.get(cache_key)
        if clip is None:
            clip = _decode_audio_f32_stereo(ffmpeg_path, wav_path, target_sr=sr, pitch=ev_pitch)
            clip_cache[cache_key] = clip

        if len(clip) == 0:
            continue

        # Handle hold loop / bounded endTimeSec
        active_clip = clip
        if ev_end > ev_time + 0.005:
            req_len = int(round((ev_end - ev_time) * sr))
            if req_len <= len(clip):
                active_clip = clip[:req_len].copy()
            else:
                reps = int(math.ceil(req_len / float(len(clip))))
                active_clip = np.tile(clip, (reps, 1))[:req_len].copy()
            fade = min(int(0.005 * sr), len(active_clip))
            if fade > 1:
                ramp = np.linspace(1.0, 0.0, fade, dtype=np.float32)[:, None]
                active_clip[-fade:] *= ramp

        start_idx = int(round(ev_time * sr))
        if start_idx >= total_samples:
            continue
        if start_idx < 0:
            src_start = -start_idx
            dst_start = 0
        else:
            src_start = 0
            dst_start = start_idx

        copy_len = min(len(active_clip) - src_start, total_samples - dst_start)
        if copy_len > 0:
            mix[dst_start : dst_start + copy_len] += active_clip[src_start : src_start + copy_len] * ev_vol
            mixed_events += 1

    if missing_clips and log_cb:
        log_cb(f"참고: WAV를 찾지 못한 오디오 클립 {len(missing_clips)}개: {', '.join(sorted(missing_clips))}")

    _soft_limit_inplace(mix)
    _write_wav_pcm16(output_wav_path, mix, sr)
    if log_cb:
        log_cb(f"오디오 오프라인 믹싱 완료: {duration_sec:.2f}초, 타격음/효과음 {mixed_events}개 합성")
    return True
