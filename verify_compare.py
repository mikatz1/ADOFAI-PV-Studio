"""Align a rendered video with a reference in-game MP4 via audio cross-correlation and build visual comparison sheets."""

from __future__ import annotations

import argparse
import os
import subprocess
from typing import List

import cv2
import numpy as np

from adofai_renderer.gpu_detect import find_ffmpeg


def _extract_mono_pcm(ffmpeg: str, path: str, sr: int = 8000, max_sec: float = 180.0) -> np.ndarray:
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-i", path, "-t", str(max_sec),
        "-ac", "1", "-ar", str(sr), "-f", "f32le", "-",
    ]
    res = subprocess.run(cmd, capture_output=True, timeout=60)
    if res.returncode != 0 or not res.stdout:
        return np.zeros(0, dtype=np.float32)
    usable = (len(res.stdout) // 4) * 4
    return np.frombuffer(res.stdout[:usable], dtype=np.float32)


def find_audio_offset_sec(ffmpeg: str, ref_video: str, ren_video: str, sr: int = 8000) -> float:
    """Return offset `dt` such that `ref_time = ren_time + dt` aligns the two audio tracks."""
    ref_a = _extract_mono_pcm(ffmpeg, ref_video, sr=sr, max_sec=180.0)
    ren_a = _extract_mono_pcm(ffmpeg, ren_video, sr=sr, max_sec=180.0)
    if len(ref_a) < sr or len(ren_a) < sr:
        return 0.0

    # Use envelope / raw FFT cross-correlation
    n = 1
    total = len(ref_a) + len(ren_a)
    while n < total:
        n <<= 1

    f_ref = np.fft.rfft(ref_a - np.mean(ref_a), n=n)
    f_ren = np.fft.rfft(ren_a - np.mean(ren_a), n=n)
    cc = np.fft.irfft(f_ref * np.conj(f_ren), n=n)

    # Search lag within [-120s, +120s]
    max_lag = min(len(ref_a) - 1, int(120 * sr))
    pos = cc[:max_lag]
    neg = cc[-max_lag:]
    best_pos = int(np.argmax(pos))
    best_neg = int(np.argmax(neg))
    if pos[best_pos] >= neg[best_neg]:
        lag = best_pos
    else:
        lag = best_neg - max_lag
    return lag / float(sr)


def _grab_frame(ffmpeg: str, video_path: str, ts: float, out_png: str) -> np.ndarray | None:
    if ts < 0:
        return None
    subprocess.run(
        [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{ts:.4f}", "-i", video_path,
            "-frames:v", "1", out_png,
        ],
        capture_output=True,
        timeout=30,
    )
    if not os.path.isfile(out_png):
        return None
    return cv2.imread(out_png)


def compare_videos(
    ref_video: str,
    ren_video: str,
    out_dir: str,
    timestamps: List[float],
    manual_offset: float | None = None,
) -> None:
    ffmpeg = find_ffmpeg()
    os.makedirs(out_dir, exist_ok=True)

    dt = manual_offset if manual_offset is not None else find_audio_offset_sec(ffmpeg, ref_video, ren_video)
    print(f"Audio alignment offset: ref_time = ren_time + ({dt:+.4f}s)")

    for ts in timestamps:
        ref_ts = ts + dt
        ref_png = os.path.join(out_dir, f"_ref_{ts:.1f}s.png")
        ren_png = os.path.join(out_dir, f"_ren_{ts:.1f}s.png")
        img_ref = _grab_frame(ffmpeg, ref_video, ref_ts, ref_png)
        img_ren = _grab_frame(ffmpeg, ren_video, ts, ren_png)
        if img_ref is None or img_ren is None:
            print(f"  [skip] t={ts:.1f}s (ref_t={ref_ts:.2f}s) - frame unavailable")
            continue

        # Resize both to 1280x720 for direct side-by-side comparison & metric check
        w, h = 1280, 720
        r1 = cv2.resize(img_ref, (w, h), interpolation=cv2.INTER_AREA)
        r2 = cv2.resize(img_ren, (w, h), interpolation=cv2.INTER_AREA)

        diff = np.abs(r1.astype(np.float32) - r2.astype(np.float32))
        mae = float(np.mean(diff))
        corr = float(np.corrcoef(r1.ravel(), r2.ravel())[0, 1]) if np.std(r1) > 1e-3 and np.std(r2) > 1e-3 else 1.0

        # Add clean banners
        b1 = r1.copy()
        b2 = r2.copy()
        cv2.rectangle(b1, (0, 0), (480, 34), (0, 0, 0), -1)
        cv2.putText(b1, f"REFERENCE MP4  (t={ref_ts:.2f}s)", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 230, 255), 2)
        cv2.rectangle(b2, (0, 0), (540, 34), (0, 0, 0), -1)
        cv2.putText(b2, f"OUR RENDER     (t={ts:.2f}s | MAE={mae:.1f} r={corr:.3f})", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (80, 255, 120), 2)

        stacked = np.vstack([b1, b2])
        out_cmp = os.path.join(out_dir, f"compare_{ts:05.1f}s.png")
        cv2.imwrite(out_cmp, stacked)
        print(f"  t={ts:6.1f}s (ref={ref_ts:6.2f}s) -> MAE={mae:5.2f}, corr={corr:.4f} | {out_cmp}")


def main() -> int:
    p = argparse.ArgumentParser(description="Compare rendered video vs reference in-game MP4")
    p.add_argument("ref_video")
    p.add_argument("ren_video")
    p.add_argument("-o", "--out-dir", required=True)
    p.add_argument("-t", "--timestamps", nargs="+", type=float, default=[1.5, 10.0, 25.0, 60.0, 120.0])
    p.add_argument("--offset", type=float, default=None)
    args = p.parse_args()
    compare_videos(args.ref_video, args.ren_video, args.out_dir, args.timestamps, args.offset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
