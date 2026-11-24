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


def _clean_hitsound_clip(clip: np.ndarray, sample_rate: int, hpf_hz: float = 40.0) -> np.ndarray:
    """Remove DC offset, de-click hard non-zero starts, and strip sub-sonic rumble (<hpf_hz) from a hitsound clip.

    Because filtering is linear, applying the zero-phase 2nd-order Butterworth HPF once per unique cached clip
    is mathematically identical to filtering the summed hitsound bus while using negligible CPU/RAM.
    """
    if clip.size == 0:
        return clip
    c = clip.astype(np.float32, copy=True)
    c -= np.mean(c, axis=0, keepdims=True)

    # Micro fade-in (0.5 ms) only if sample 0 starts with a sharp DC step (e.g. sndFireTile ~0.69)
    if float(np.max(np.abs(c[0]))) > 0.05:
        fi = min(int(round(0.0005 * sample_rate)), len(c))
        if fi > 1:
            c[:fi] *= np.linspace(0.0, 1.0, fi, dtype=np.float32)[:, None]

    if hpf_hz > 0.0 and len(c) >= 64:
        pad = 1024
        n_fft = len(c) + pad
        spec = np.fft.rfft(c, n=n_fft, axis=0)
        freqs = np.fft.rfftfreq(n_fft, 1.0 / float(sample_rate))
        h_mag = 1.0 / np.sqrt(1.0 + (hpf_hz / np.maximum(freqs, 1e-6)) ** 4)
        h_mag[0] = 0.0
        c = np.fft.irfft(spec * h_mag[:, None], n=n_fft, axis=0)[: len(c)].astype(np.float32)

    return c


def _lookahead_limit_inplace(
    mix: np.ndarray,
    sample_rate: int = 48000,
    ceiling: float = 0.92,
    lookahead_ms: float = 2.5,
    release_ms: float = 45.0,
) -> None:
    """Clean look-ahead envelope peak limiter (replaces memoryless tanh clipping).

    Prevents low-frequency intermodulation distortion (IMD), DC shift, and lossy AAC true-peak explosions
    when dense hitsounds overlap with full-scale BGM.
    """
    if mix.size == 0:
        return
    peak = float(np.max(np.abs(mix)))
    if peak <= ceiling:
        return

    from numpy.lib.stride_tricks import sliding_window_view

    hop = 16  # 0.333 ms envelope resolution at 48 kHz
    peak_env = np.max(np.abs(mix), axis=1)
    pad = (-len(peak_env)) % hop
    p_pad = np.pad(peak_env, (0, pad)) if pad else peak_env
    blk = p_pad.reshape(-1, hop).max(axis=1)

    la_blk = max(1, int(round((lookahead_ms * 0.001 * sample_rate) / hop)))
    b_pad = np.pad(blk, (la_blk, la_blk), mode="edge")
    win_max = sliding_window_view(b_pad, 2 * la_blk + 1).max(axis=1)
    target_g = np.where(win_max > ceiling, ceiling / np.maximum(win_max, 1e-9), 1.0).astype(np.float32)

    atk_a = float(math.exp(-hop / max(1.0, lookahead_ms * 0.001 * sample_rate * 0.45)))
    rel_a = float(math.exp(-hop / max(1.0, release_ms * 0.001 * sample_rate)))
    g_list = target_g.tolist()
    cur = 1.0
    for i, tg in enumerate(g_list):
        if tg < cur:
            cur = tg + atk_a * (cur - tg)
        else:
            cur = tg + rel_a * (cur - tg)
        g_list[i] = cur

    g_blk = np.asarray(g_list, dtype=np.float32)
    xp = np.arange(len(g_blk), dtype=np.float32) * hop + (hop * 0.5)
    gain = np.interp(np.arange(len(mix), dtype=np.float32), xp, g_blk).astype(np.float32)
    mix *= gain[:, None]

    # Gentle safety guard only for rare sub-block micro-transients above ceiling + 0.02
    guard = min(0.95, ceiling + 0.02)
    mask = np.abs(mix) > guard
    if np.any(mask):
        headroom = max(0.01, 0.975 - guard)
        vals = mix[mask]
        over = np.abs(vals) - guard
        mix[mask] = np.sign(vals) * (guard + headroom * np.tanh(over / headroom))


def _coalesce_simultaneous_hits(hit_events: list) -> list:
    """Coalesce near-simultaneous identical hitsounds (<4 ms, e.g. multi-tile chords/pseudos)
    using sqrt-energy scaling (capped at 1.30x) instead of linear phase-doubling spikes."""
    if len(hit_events) <= 1:
        return hit_events
    hit_events.sort(key=lambda e: (e[0], e[1], e[2]))  # (wav_path, pitch_key, timeSec, vol, cat)
    merged = []
    i = 0
    n = len(hit_events)
    while i < n:
        wav_path, pitch_key, t0, vol0, cat0 = hit_events[i]
        sq_sum = vol0 * vol0
        max_v = vol0
        j = i + 1
        while j < n and hit_events[j][0] == wav_path and hit_events[j][1] == pitch_key and (hit_events[j][2] - t0) < 0.004:
            vj = hit_events[j][3]
            sq_sum += vj * vj
            if vj > max_v:
                max_v = vj
            j += 1
        if j > i + 1:
            eff_vol = min(max_v * 1.30, math.sqrt(sq_sum))
        else:
            eff_vol = vol0
        merged.append((wav_path, pitch_key, t0, eff_vol, cat0))
        i = j
    return merged


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
    bgm_bus = np.zeros((total_samples, 2), dtype=np.float32)
    hit_bus = np.zeros((total_samples, 2), dtype=np.float32)
    sfx_bus = np.zeros((total_samples, 2), dtype=np.float32)

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
            bgm_bus[dst_start : dst_start + copy_len] += bgm[src_start : src_start + copy_len] * gain

    # 2. Separate percussive hitsounds (managed for clean overlap) from holds & general SFX
    events = tl.get("events", [])
    clip_cache: Dict[tuple, np.ndarray] = {}
    mixed_events = 0
    missing_clips = set()
    percussive_hits = []   # (wav_path, pitch_key, timeSec, ev_vol, cat)
    other_events = []      # (wav_path, pitch_key, timeSec, ev_end, ev_vol, is_hit_bus)

    for ev in events:
        cat = ev.get("category", "hitsound")
        is_hit_cat = cat in ("hitsound", "holdsound", "countdown")
        if is_hit_cat:
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

        pitch_key = round(ev_pitch, 4)
        if cat in ("hitsound", "countdown") and ev_end <= ev_time + 0.005:
            percussive_hits.append((wav_path, pitch_key, ev_time, ev_vol, cat))
        else:
            other_events.append((wav_path, pitch_key, ev_time, ev_end, ev_vol, is_hit_cat))

    def _get_clip(wav_path: str, pitch_key: float, is_hitsound: bool) -> np.ndarray:
        ck = (wav_path, pitch_key, is_hitsound)
        clip = clip_cache.get(ck)
        if clip is None:
            raw_clip = _decode_audio_f32_stereo(ffmpeg_path, wav_path, target_sr=sr, pitch=pitch_key)
            clip = _clean_hitsound_clip(raw_clip, sr, hpf_hz=40.0 if is_hitsound else 20.0)
            clip_cache[ck] = clip
        return clip

    # 2a. Mix percussive hitsounds onto hit_bus with chord coalescing & smooth same-clip tail choke
    #     so dense high-BPM streams never accumulate muddy sub-bass tails.
    percussive_hits = _coalesce_simultaneous_hits(percussive_hits)
    n_hits = len(percussive_hits)
    for idx_h, (wav_path, pitch_key, ev_time, ev_vol, _cat) in enumerate(percussive_hits):
        clip = _get_clip(wav_path, pitch_key, is_hitsound=True)
        if len(clip) == 0:
            continue

        clip_sec = len(clip) / float(sr)
        # Short percussive hits (<= 1.0s, e.g. sndKick/sndChuck/sndFireTile) smoothly choke on the next
        # same-clip retrigger; long reverb hits (> 1.0s, e.g. sndReverbClack) keep up to 4 overlapping tails.
        look_ahead_idx = idx_h + (1 if clip_sec <= 1.0 else 4)
        active_clip = clip
        if look_ahead_idx < n_hits:
            nxt_wav, nxt_pitch, nxt_time, _, _ = percussive_hits[look_ahead_idx]
            if nxt_wav == wav_path and nxt_pitch == pitch_key:
                dt_next = nxt_time - ev_time
                if 0.0 < dt_next < clip_sec:
                    if clip_sec <= 1.0:
                        fade_ms = min(20.0, max(8.0, dt_next * 1000.0 * 0.55))
                    else:
                        fade_ms = 40.0
                    fade_n = int(round(fade_ms * 0.001 * sr))
                    keep_n = int(round(dt_next * sr)) + fade_n
                    if 2 < keep_n < len(clip):
                        active_clip = clip[:keep_n].copy()
                        fn = min(fade_n, len(active_clip))
                        if fn > 1:
                            # Smooth half-cosine fade (C1-continuous, click-free)
                            u = np.linspace(0.0, math.pi, fn, dtype=np.float32)
                            ramp = (0.5 * (1.0 + np.cos(u)))[:, None]
                            active_clip[-fn:] *= ramp

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
            hit_bus[dst_start : dst_start + copy_len] += active_clip[src_start : src_start + copy_len] * ev_vol
            mixed_events += 1

    # 2b. Mix hold sounds and general SFX
    for wav_path, pitch_key, ev_time, ev_end, ev_vol, is_hit_bus in other_events:
        clip = _get_clip(wav_path, pitch_key, is_hitsound=is_hit_bus)
        if len(clip) == 0:
            continue

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
                u = np.linspace(0.0, math.pi, fade, dtype=np.float32)
                ramp = (0.5 * (1.0 + np.cos(u)))[:, None]
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
            bus = hit_bus if is_hit_bus else sfx_bus
            bus[dst_start : dst_start + copy_len] += active_clip[src_start : src_start + copy_len] * ev_vol
            mixed_events += 1

    if missing_clips and log_cb:
        log_cb(f"참고: WAV를 찾지 못한 오디오 클립 {len(missing_clips)}개: {', '.join(sorted(missing_clips))}")

    # 3. Clean bus limiting + true-peak-safe master look-ahead limiter
    _lookahead_limit_inplace(hit_bus, sample_rate=sr, ceiling=0.78, lookahead_ms=2.0, release_ms=40.0)
    mix = bgm_bus
    mix += hit_bus
    mix += sfx_bus
    _lookahead_limit_inplace(mix, sample_rate=sr, ceiling=0.92, lookahead_ms=2.5, release_ms=45.0)

    _write_wav_pcm16(output_wav_path, mix, sr)
    if log_cb:
        log_cb(f"오디오 오프라인 믹싱 완료: {duration_sec:.2f}초, 타격음/효과음 {mixed_events}개 합성")
    return True
