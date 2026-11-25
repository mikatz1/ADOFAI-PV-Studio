"""End-to-end render orchestration: low-spec safe fallback chain, planet colors, rough preview, ETA calibration."""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from .audio_mixer import AudioMixOptions, mix_audio_from_timeline
from .gpu_detect import (
    HardwareCapabilities,
    build_ffmpeg_preview_args,
    build_ffmpeg_video_args,
    detect_hardware,
    find_adofai_dir,
    resolve_encoder_fallback_chain,
    test_encoder_usable,
)
from .paths import get_agent_dll_path, get_app_root, get_bundle_root, get_data_root

PROJECT_ROOT = get_app_root()
AGENT_DLL_PATH = get_agent_dll_path()
AGENT_BUILD_SCRIPT = os.path.join(get_bundle_root(), "agent", "build.ps1")
CACHE_ROOT = os.path.join(get_data_root(), "cache")
PREVIEW_CACHE_DIR = os.path.join(CACHE_ROOT, "previews")
PERF_PROFILE_PATH = os.path.join(CACHE_ROOT, "perf_profile.json")
PROXY_SETTINGS_PATH = os.path.join(CACHE_ROOT, "proxy_settings.json")

PROXY_RES_PRESETS: List[Tuple[str, int, int]] = [
    ("426 × 240  (240p)", 426, 240),
    ("480 × 270  (270p)", 480, 270),
    ("640 × 360  (360p)", 640, 360),
    ("854 × 480  (480p)", 854, 480),
    ("1280 × 720 (720p HD)", 1280, 720),
]

DEFAULT_PROXY_WIDTH = 480
DEFAULT_PROXY_HEIGHT = 270
DEFAULT_PROXY_FPS = 12
PROXY_RENDER_MODES: Tuple[str, ...] = ("chart_only", "no_heavy_filters", "all")
DEFAULT_PROXY_MODE = "no_heavy_filters"


def normalize_proxy_mode(mode: Optional[str]) -> str:
    m = str(mode or "").strip().lower()
    if m in PROXY_RENDER_MODES:
        return m
    return DEFAULT_PROXY_MODE


def clamp_proxy_spec(width: int, height: int, fps: int) -> Tuple[int, int, int]:
    """Clamp proxy dimensions to even 240p..720p bounds and FPS to 5..60."""
    w = max(426, min(1280, int(width or DEFAULT_PROXY_WIDTH)))
    h = max(240, min(720, int(height or DEFAULT_PROXY_HEIGHT)))
    w = w - (w % 2)
    h = h - (h % 2)
    f = max(5, min(60, int(fps or DEFAULT_PROXY_FPS)))
    return w, h, f


def load_proxy_settings() -> dict:
    try:
        if os.path.isfile(PROXY_SETTINGS_PATH):
            with open(PROXY_SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            w, h, fps = clamp_proxy_spec(
                data.get("width", DEFAULT_PROXY_WIDTH),
                data.get("height", DEFAULT_PROXY_HEIGHT),
                data.get("fps", DEFAULT_PROXY_FPS),
            )
            mode = normalize_proxy_mode(data.get("mode", DEFAULT_PROXY_MODE))
            return {"width": w, "height": h, "fps": fps, "mode": mode}
    except Exception:
        pass
    return {
        "width": DEFAULT_PROXY_WIDTH,
        "height": DEFAULT_PROXY_HEIGHT,
        "fps": DEFAULT_PROXY_FPS,
        "mode": DEFAULT_PROXY_MODE,
    }


def save_proxy_settings(
    width: int,
    height: int,
    fps: int,
    mode: str = DEFAULT_PROXY_MODE,
) -> dict:
    w, h, f = clamp_proxy_spec(width, height, fps)
    m = normalize_proxy_mode(mode)
    state = {"width": w, "height": h, "fps": f, "mode": m}
    try:
        os.makedirs(CACHE_ROOT, exist_ok=True)
        with open(PROXY_SETTINGS_PATH, "w", encoding="utf-8") as fp:
            json.dump(state, fp, indent=2)
    except Exception:
        pass
    return state


def compute_preview_cache_key(
    level_path: str,
    red_color: str,
    red_opacity: float,
    blue_color: str,
    blue_opacity: float,
    proxy_width: int = DEFAULT_PROXY_WIDTH,
    proxy_height: int = DEFAULT_PROXY_HEIGHT,
    proxy_fps: int = DEFAULT_PROXY_FPS,
    proxy_mode: str = DEFAULT_PROXY_MODE,
) -> str:
    abs_lvl = os.path.abspath(level_path)
    st = os.stat(abs_lvl)
    pw, ph, pf = clamp_proxy_spec(proxy_width, proxy_height, proxy_fps)
    pm = normalize_proxy_mode(proxy_mode)
    key_src = (
        f"{abs_lvl}|{st.st_size}|{int(st.st_mtime)}|"
        f"{red_color}|{red_opacity:.2f}|{blue_color}|{blue_opacity:.2f}|"
        f"{pw}x{ph}@{pf}|{pm}|v8"
    )
    return hashlib.sha1(key_src.encode("utf-8", errors="ignore")).hexdigest()[:14]


def _fix_timeline_output_fps(timeline_path: str, out_fps: float) -> None:
    """The agent stamps the *simulation* fps into audio_timeline.json; with captureStride>1 the real video runs at
    sim_fps/stride, so rewrite fps/videoDurationSec to the output rate (otherwise audio is too short and -shortest
    truncates the video)."""
    try:
        if not os.path.isfile(timeline_path):
            return
        with open(timeline_path, "r", encoding="utf-8-sig") as f:
            tl = json.load(f)
        frames = int(tl.get("totalCapturedFrames", 0))
        if frames <= 0 or out_fps <= 0:
            return
        tl["fps"] = int(round(out_fps))
        tl["videoDurationSec"] = frames / float(out_fps)
        with open(timeline_path, "w", encoding="utf-8") as f:
            json.dump(tl, f, ensure_ascii=False)
    except Exception:
        pass


def _peak_bpm(level_path: str) -> float:
    """Highest BPM value found in a level file (SetSpeed events + initial BPM)."""
    import re

    try:
        with open(level_path, "r", encoding="utf-8-sig", errors="replace") as f:
            txt = f.read()
    except Exception:
        return 120.0
    vals = [float(x) for x in re.findall(r'"(?:beatsPerMinute|bpm)"\s*:\s*([0-9]+(?:\.[0-9]+)?)', txt)]
    vals = [v for v in vals if 20.0 <= v <= 2000.0]
    return max(vals) if vals else 120.0


def resolve_substeps(settings: "RenderSettings", level_path: str) -> int:
    """Sub-steps per output frame. Auto = 1: planet tails are re-emitted along the true orbit arc by the agent
    (PlanetTailArc), so extra simulation ticks are no longer needed for continuous trails. Manual 1..8 still
    available (--substeps) for extra-smooth particles/effects at the cost of render time."""
    n = int(getattr(settings, "substeps", 0) or 0)
    if n > 0:
        return max(1, min(8, n))
    return 1


@dataclass
class RenderSettings:
    level_path: str
    output_path: str
    adofai_dir: str = ""
    width: int = 1920
    height: int = 1080
    fps: int = 60
    gpu_mode: str = "auto"      # "auto", "nvenc", "igpu", "cpu"
    gpu_limit_percent: int = 90 # 10..100 (default in-app: 90%)
    ram_limit_percent: int = 80 # 30..95  (default in-app: 80%)
    max_cpu_cores: int = 0      # 0 = auto, >0 = max CPU threads for encoder/muxer
    codec: str = "h264"         # "h264", "hevc"
    quality: str = "high"       # "ultra", "high", "balanced"
    include_bgm: bool = True
    bgm_volume: float = 1.0
    include_hitsounds: bool = True
    hitsound_volume: float = 1.0
    include_sfx: bool = True
    sfx_volume: float = 1.0
    hide_countdown: bool = True
    hide_hit_text: bool = True
    start_sec: float = 0.0
    end_sec: float = 0.0
    end_padding_sec: float = 2.0
    audio_bitrate: str = "320k"
    keep_temp: bool = False
    # Planet color & opacity (defaults: pure red #FF0000 & pure blue #0000FF at 100%)
    red_planet_color: str = "#FF0000"
    red_planet_opacity: float = 1.0
    blue_planet_color: str = "#0000FF"
    blue_planet_opacity: float = 1.0
    # Low-spec safety mode ("auto", "force", "off") & experimental decoration optimizer
    low_spec_mode: str = "auto"
    experimental_optimize_render: bool = False
    # Temporal sub-stepping: simulate `substeps` game frames per output frame and capture only the last one,
    # so planet orbits/trails/particles evolve smoothly between captured frames (1 = legacy frame-by-frame).
    substeps: int = 0  # 0 = auto (derived from the level's peak BPM so orbit steps stay small at any output fps)


@dataclass
class RenderProgress:
    stage: str
    frames: int = 0
    video_sec: float = 0.0
    song_sec: float = 0.0
    est_total_sec: float = 0.0
    floor: int = 0
    total_floors: int = 0
    render_fps: float = 0.0
    wall_sec: float = 0.0
    percent: float = 0.0
    eta_sec: float = 0.0
    message: str = ""


@dataclass
class RenderResult:
    success: bool
    output_path: str = ""
    frames: int = 0
    video_sec: float = 0.0
    wall_sec: float = 0.0
    encoder_used: str = ""
    fallback_note: str = ""
    audio_timeline_path: str = ""
    log_path: str = ""
    error: str = ""


@dataclass
class PreviewResult:
    success: bool
    video_path: str = ""
    filmstrip_path: str = ""
    duration_sec: float = 0.0
    cached: bool = False
    error: str = ""


def _load_perf_profile() -> dict:
    try:
        if os.path.isfile(PERF_PROFILE_PATH):
            with open(PERF_PROFILE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_perf_sample(encoder: str, width: int, height: int, gpu_cap: int, measured_fps: float) -> None:
    if measured_fps <= 0.2:
        return
    try:
        os.makedirs(CACHE_ROOT, exist_ok=True)
        data = _load_perf_profile()
        # Normalize measured FPS to 1080p @ 100% GPU cap reference speed
        px_ratio = (width * height) / float(1920 * 1080)
        cap_ratio = max(0.10, min(1.0, gpu_cap / 100.0))
        norm_fps_1080p_100 = measured_fps * (px_ratio ** 0.72) / (cap_ratio ** 0.78)
        key = f"norm_{encoder}"
        prev = float(data.get(key, 0.0) or 0.0)
        data[key] = (prev * 0.4 + norm_fps_1080p_100 * 0.6) if prev > 0.1 else norm_fps_1080p_100
        with open(PERF_PROFILE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def _save_bitrate_sample(
    codec: str,
    quality: str,
    width: int,
    height: int,
    fps: int,
    file_bytes: int,
    video_sec: float,
) -> None:
    if video_sec < 8.0 or file_bytes <= 0:
        return
    try:
        os.makedirs(CACHE_ROOT, exist_ok=True)
        data = _load_perf_profile()
        measured_mbps = (file_bytes * 8.0) / (video_sec * 1_000_000.0)
        px_ratio = max(0.2, (width * height) / float(1920 * 1080))
        fps_ratio = max(0.2, fps / 60.0)
        q_mult = {"ultra": 1.65, "high": 1.0, "balanced": 0.52}.get(quality, 1.0)
        codec_mult = 0.72 if codec == "hevc" else 1.0
        denom = (px_ratio ** 1.12) * (fps_ratio ** 0.78) * q_mult * codec_mult
        norm_1080p60 = max(10.0, min(36.0, max(0.5, measured_mbps - 0.32) / max(0.05, denom)))
        key = "norm_mbps_1080p60"
        prev = float(data.get(key, 0.0) or 0.0)
        data[key] = (prev * 0.55 + norm_1080p60 * 0.45) if prev > 1.0 else norm_1080p60
        with open(PERF_PROFILE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def estimate_output_size_mb(
    settings: RenderSettings,
    level_info: object = None,
    clip_dur_sec: float = 60.0,
) -> float:
    """Estimate output file size in MiB calibrated against real ADOFAI CQ/VBR renders (720p..4K)."""
    dur = max(0.5, float(clip_dur_sec or 0.0))
    px_ratio = (settings.width * settings.height) / float(1920 * 1080)
    fps_ratio = settings.fps / 60.0
    q_mult = {"ultra": 1.65, "high": 1.0, "balanced": 0.52}.get(settings.quality, 1.0)
    codec_mult = 0.72 if settings.codec == "hevc" else 1.0

    # Empirically calibrated baseline: 18.0 Mbps at 1080p60 H.264 CQ19 (~85 Mbps base at 4K60)
    # because crisp high-contrast vector tiles, text, and particle edges scale super-linearly in 4K CQ.
    prof = _load_perf_profile()
    learned = float(prof.get("norm_mbps_1080p60", 0.0) or 0.0)
    base_1080p60 = (18.0 * 0.5 + max(12.0, min(32.0, learned)) * 0.5) if learned > 1.0 else 18.0

    # Mild chart visual-complexity multiplier (0.85 .. 1.20)
    complexity = 1.0
    if level_info is not None:
        deco = int(getattr(level_info, "decoration_count", 0) or 0)
        filts = len(getattr(level_info, "filters", ()) or ())
        acts = int(getattr(level_info, "action_count", 0) or 0)
        deco_s = min(1.0, deco / 2500.0)
        filt_s = min(1.0, filts / 12.0)
        act_s = min(1.0, acts / 8000.0)
        complexity = 0.85 + 0.13 * deco_s + 0.12 * filt_s + 0.10 * act_s

    audio_mbps = 0.0
    if settings.include_bgm or settings.include_hitsounds:
        try:
            audio_mbps = float(str(settings.audio_bitrate).lower().rstrip("k")) / 1000.0
        except Exception:
            audio_mbps = 0.32

    video_mbps = base_1080p60 * (px_ratio ** 1.12) * (fps_ratio ** 0.78) * q_mult * codec_mult * complexity
    total_bytes = ((video_mbps + audio_mbps) * 1_000_000.0 / 8.0) * dur
    return total_bytes / (1024.0 * 1024.0)


def estimate_render_time_sec(
    settings: RenderSettings,
    caps: Optional[HardwareCapabilities],
    level_duration_sec: float,
) -> Tuple[float, int, float]:
    """Estimate (wall_seconds, target_frames, clip_duration_sec) prior to starting a render."""
    total_dur = max(1.0, float(level_duration_sec or 60.0))
    start = max(0.0, float(settings.start_sec or 0.0))
    end = float(settings.end_sec or 0.0)
    if end > start:
        clip_dur = min(end - start, max(0.5, total_dur + settings.end_padding_sec - start))
    else:
        clip_dur = max(1.0, total_dur - start + settings.end_padding_sec)

    target_frames = max(1, int(round(clip_dur * settings.fps)))
    seek_frames = int(round(start * settings.fps))

    if caps is None:
        caps = detect_hardware(verify_hw=False)
    chain = resolve_encoder_fallback_chain(caps, settings.gpu_mode, settings.codec)
    enc = chain[0][0]

    # Calibrated baseline 1080p @ 100% cap throughput
    prof = _load_perf_profile()
    norm_fps = float(prof.get(f"norm_{enc}", 0.0) or 0.0)
    if norm_fps <= 0.1:
        if enc.endswith("_nvenc"):
            norm_fps = 95.0
        elif enc.endswith("_qsv") or enc.endswith("_amf") or enc.endswith("_d3d12va"):
            norm_fps = 55.0
        else:
            norm_fps = 28.0

    px_ratio = (settings.width * settings.height) / float(1920 * 1080)
    cap_ratio = max(0.10, min(1.0, settings.gpu_limit_percent / 100.0))
    safe_penalty = 0.72 if (settings.low_spec_mode == "force" or (settings.low_spec_mode == "auto" and caps.low_spec_recommended)) else 1.0

    est_fps = max(1.5, norm_fps * (cap_ratio ** 0.78) / (px_ratio ** 0.72) * safe_penalty)
    # Seek frames simulate faster (no FFmpeg encode / readback), roughly 2.2x est_fps
    seek_sec = seek_frames / max(5.0, est_fps * 2.2)
    render_sec = target_frames / est_fps
    overhead_sec = 7.5 + (clip_dur * 0.03)  # Unity launch + audio mixing + muxing
    return (overhead_sec + seek_sec + render_sec, target_frames, clip_dur)


def _kill_process_tree(proc: Optional[subprocess.Popen]) -> None:
    """Force-terminate a subprocess and all of its child processes (e.g. Unity + FFmpeg pipe)."""
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
    except Exception:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass
    try:
        if proc.poll() is None:
            proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=2.0)
    except Exception:
        pass


class WarmSessionManager:
    """Keeps one parked Unity ADOFAI process alive in IdleWarm between proxy & final renders of the same level.

    Eliminates redundant Unity cold boot + .adofai parse + decoration texture/sprite/audio decoding
    while guaranteeing zero orphan processes (parent PID watchdog + 5-min idle timeout + atexit/closeEvent).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._session_dir: str = ""
        self._level_path: str = ""
        self._level_mtime: float = 0.0
        self._adofai_dir: str = ""
        self._agent_dll: str = ""
        self._agent_dll_mtime: float = 0.0
        self._in_use: bool = False

    @staticmethod
    def _system_ram_gb() -> float:
        if os.name == "nt":
            try:
                import ctypes

                class _MemStat(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]

                ms = _MemStat()
                ms.dwLength = ctypes.sizeof(_MemStat)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
                    return float(ms.ullTotalPhys) / (1024.0 ** 3)
            except Exception:
                pass
        return 16.0

    @classmethod
    def is_eligible(cls, caps: Optional[HardwareCapabilities]) -> bool:
        if os.environ.get("ADOFAI_WARM_SESSION", "1") == "0":
            return False
        if caps is not None and caps.low_spec_recommended:
            return False
        ram_gb = cls._system_ram_gb()
        if ram_gb > 0.1 and ram_gb < 10.0:
            return False
        return True

    @staticmethod
    def _norm(p: str) -> str:
        return os.path.normcase(os.path.abspath(p)) if p else ""

    @staticmethod
    def _mtime(p: str) -> float:
        try:
            return os.path.getmtime(p)
        except Exception:
            return 0.0

    def has_warm_session_for(self, level_path: str) -> bool:
        norm = self._norm(level_path)
        with self._lock:
            if self._proc is None or self._in_use:
                return False
            if self._proc.poll() is not None:
                return False
            if self._level_path != norm:
                return False
            return os.path.isfile(os.path.join(self._session_dir, "ready.json"))

    def try_claim(
        self, level_path: str, adofai_dir: str, agent_dll: str
    ) -> Optional[Tuple[subprocess.Popen, str]]:
        norm_lvl = self._norm(level_path)
        norm_dir = self._norm(adofai_dir)
        norm_dll = self._norm(agent_dll)
        cur_lvl_mt = self._mtime(level_path)
        cur_dll_mt = self._mtime(agent_dll)

        with self._lock:
            if self._proc is None or self._in_use:
                return None
            if self._proc.poll() is not None:
                stale_dir = self._session_dir
                self._clear_locked()
                self._cleanup_dir(stale_dir)
                return None
            ready_path = os.path.join(self._session_dir, "ready.json")
            if (
                self._level_path == norm_lvl
                and self._adofai_dir == norm_dir
                and self._agent_dll == norm_dll
                and abs(self._level_mtime - cur_lvl_mt) < 0.01
                and abs(self._agent_dll_mtime - cur_dll_mt) < 0.01
                and os.path.isfile(ready_path)
            ):
                self._in_use = True
                return (self._proc, self._session_dir)
            return None

    def release_back_to_idle(
        self,
        proc: subprocess.Popen,
        session_dir: str,
        level_path: str,
        adofai_dir: str,
        agent_dll: str,
    ) -> None:
        old_proc: Optional[subprocess.Popen] = None
        old_dir = ""
        with self._lock:
            if self._proc is not None and self._proc is not proc:
                old_proc = self._proc
                old_dir = self._session_dir
            self._proc = proc
            self._session_dir = session_dir
            self._level_path = self._norm(level_path)
            self._level_mtime = self._mtime(level_path)
            self._adofai_dir = self._norm(adofai_dir)
            self._agent_dll = self._norm(agent_dll)
            self._agent_dll_mtime = self._mtime(agent_dll)
            self._in_use = False
        if old_proc is not None:
            self._terminate_session(old_proc, old_dir, force=True)

    def discard_active(
        self,
        proc: Optional[subprocess.Popen] = None,
        session_dir: str = "",
        force_kill: bool = True,
    ) -> None:
        target_proc = proc
        target_dir = session_dir
        with self._lock:
            if target_proc is None or self._proc is target_proc:
                if target_proc is None:
                    target_proc = self._proc
                if not target_dir:
                    target_dir = self._session_dir
                self._clear_locked()
        self._terminate_session(target_proc, target_dir, force=force_kill)

    def shutdown_if_different_level(self, new_level_path: str) -> None:
        norm = self._norm(new_level_path)
        proc: Optional[subprocess.Popen] = None
        sdir = ""
        with self._lock:
            if self._proc is not None and not self._in_use and self._level_path != norm:
                proc = self._proc
                sdir = self._session_dir
                self._clear_locked()
        if proc is not None or sdir:
            self._terminate_session(proc, sdir, force=False)

    def shutdown(self, force: bool = False) -> None:
        proc: Optional[subprocess.Popen] = None
        sdir = ""
        with self._lock:
            if self._in_use and not force:
                return
            proc = self._proc
            sdir = self._session_dir
            self._clear_locked()
        if proc is not None or sdir:
            self._terminate_session(proc, sdir, force=force)

    def _clear_locked(self) -> None:
        self._proc = None
        self._session_dir = ""
        self._level_path = ""
        self._level_mtime = 0.0
        self._adofai_dir = ""
        self._agent_dll = ""
        self._agent_dll_mtime = 0.0
        self._in_use = False

    @staticmethod
    def _cleanup_dir(sdir: str) -> None:
        if sdir and os.path.isdir(sdir):
            try:
                shutil.rmtree(sdir, ignore_errors=True)
            except Exception:
                pass

    @classmethod
    def _terminate_session(cls, proc: Optional[subprocess.Popen], sdir: str, force: bool = False) -> None:
        if sdir and os.path.isdir(sdir):
            try:
                with open(os.path.join(sdir, "shutdown.flag"), "w", encoding="utf-8") as sf:
                    sf.write("shutdown")
            except Exception:
                pass
        if proc is not None:
            try:
                if proc.poll() is None:
                    if not force:
                        try:
                            proc.wait(timeout=1.2)
                        except Exception:
                            pass
                    if proc.poll() is None:
                        _kill_process_tree(proc)
            except Exception:
                pass
        cls._cleanup_dir(sdir)


_WARM_SESSION_MANAGER = WarmSessionManager()


def get_warm_session_manager() -> WarmSessionManager:
    return _WARM_SESSION_MANAGER


def shutdown_warm_session(force: bool = True) -> None:
    _WARM_SESSION_MANAGER.shutdown(force=force)


atexit.register(shutdown_warm_session, True)


class RenderRunner:
    def __init__(self, caps: Optional[HardwareCapabilities] = None) -> None:
        self.caps = caps or detect_hardware(verify_hw=True)
        self._cancel_event = threading.Event()
        self._cancel_sentinel_path: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None

    def reset_cancel(self) -> None:
        self._cancel_event.clear()

    def cancel(self, force_kill: bool = False) -> None:
        self._cancel_event.set()
        sentinel = self._cancel_sentinel_path
        if sentinel:
            try:
                parent = os.path.dirname(sentinel)
                if parent and os.path.isdir(parent):
                    with open(sentinel, "w", encoding="utf-8") as f:
                        f.write("cancel")
            except Exception:
                pass
        if force_kill:
            _kill_process_tree(self._proc)

    def ensure_agent_built(self, adofai_dir: str, log_cb: Optional[Callable[[str], None]] = None) -> str:
        dll_path = get_agent_dll_path()
        if os.path.isfile(dll_path):
            return dll_path
        if not os.path.isfile(AGENT_BUILD_SCRIPT):
            raise FileNotFoundError(f"렌더 에이전트 빌드 스크립트가 없습니다: {AGENT_BUILD_SCRIPT}")
        if log_cb:
            log_cb("C# 오프라인 렌더 에이전트(AdofaiRenderAgent.dll) 빌드 중...")
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", AGENT_BUILD_SCRIPT, "-AdofaiDir", adofai_dir],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if res.returncode != 0 or not os.path.isfile(AGENT_DLL_PATH):
            raise RuntimeError(f"AdofaiRenderAgent.dll 빌드 실패:\n{res.stdout}\n{res.stderr}")
        return AGENT_DLL_PATH

    def _poll_unity_pass(
        self,
        proc: subprocess.Popen,
        job_cfg: dict,
        start_sec: float,
        target_fps: int,
        progress_cb: Optional[Callable[[RenderProgress], None]],
        is_preview: bool,
        keep_warm: bool,
        ready_json_path: str,
    ) -> Tuple[str, int, float, float, bool]:
        """Poll progress.jsonl & process lifetime. Returns (last_stage, frames, video_sec, render_fps, stayed_warm)."""
        progress_path = job_cfg["progressPath"]
        last_stage = "launching"
        last_frames = 0
        last_video_sec = 0.0
        last_render_fps = 0.0
        prog_offset = 0
        done_seen_at: Optional[float] = None
        cancel_seen_at: Optional[float] = None
        stayed_warm = False
        stride = max(1, int(job_cfg.get("captureStride", 1)))
        out_fps = max(1.0, float(target_fps) / float(stride))

        self._proc = proc
        try:
            while True:
                if os.path.isfile(progress_path):
                    try:
                        with open(progress_path, "r", encoding="utf-8-sig", errors="replace") as pf:
                            pf.seek(prog_offset)
                            chunk = pf.read()
                            prog_offset = pf.tell()
                        for line in chunk.splitlines():
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                ev = json.loads(line)
                            except Exception:
                                continue
                            stage = str(ev.get("stage", ""))
                            frames = int(ev.get("frames", 0))
                            vsec = float(ev.get("videoSec", 0.0))
                            ssec = float(ev.get("songSec", 0.0))
                            est = float(ev.get("estTotalSec", 0.0))
                            fl = int(ev.get("floor", 0))
                            tfl = int(ev.get("totalFloors", 0))
                            rfps = float(ev.get("renderFps", 0.0))
                            wsec = float(ev.get("wallSec", 0.0))

                            last_stage = stage
                            if frames > 0:
                                last_frames = frames
                                last_video_sec = vsec
                            if rfps > 0.05:
                                last_render_fps = rfps

                            pct = 0.0
                            eta = 0.0
                            if stage == "rendering":
                                span = max(0.1, est - max(0.0, start_sec))
                                pct = min(95.0, max(0.0, (vsec / span) * 95.0))
                                if rfps > 0.1 and span > vsec:
                                    rem_frames = (span - vsec) * out_fps
                                    eta = rem_frames / rfps
                            elif stage == "seeking":
                                pct = 5.0
                            elif stage == "done":
                                pct = 96.0
                                if done_seen_at is None:
                                    done_seen_at = time.monotonic()

                            msg_map = {
                                "loading_scene": "게임 씬 초기화 중...",
                                "loading_level": "레벨 데이터 · 장식 · 셰이더 로딩 중...",
                                "starting_playback": "결정론적 가상 클럭 시작 중...",
                                "seeking": f"지정 시작 시점({start_sec:.1f}s)까지 시뮬레이션 중... ({ssec:.1f}s)",
                                "rendering": f"렌더링 중: {frames:,} 프레임 ({vsec:.1f}s) | 타일 {fl}/{tfl} | {rfps:.1f} fps",
                                "done": "비디오 프레임 렌더링 완료! 후처리 진행 중...",
                                "cancelled": "렌더링이 취소되었습니다.",
                            }
                            if progress_cb and stage != "warm_failed":
                                progress_cb(RenderProgress(
                                    stage=stage,
                                    frames=frames,
                                    video_sec=vsec,
                                    song_sec=ssec,
                                    est_total_sec=est,
                                    floor=fl,
                                    total_floors=tfl,
                                    render_fps=rfps,
                                    wall_sec=wsec,
                                    percent=pct,
                                    eta_sec=eta,
                                    message=msg_map.get(stage, stage),
                                ))
                    except Exception:
                        pass

                ret = proc.poll()
                if ret is not None:
                    break

                # Keep-alive warm transition: once agent emits 'done' and writes fresh ready.json, leave proc parked!
                if keep_warm and last_stage == "done" and ready_json_path and os.path.isfile(ready_json_path):
                    stayed_warm = True
                    break

                if done_seen_at is not None and (time.monotonic() - done_seen_at) > (6.0 if keep_warm else 4.0):
                    _kill_process_tree(proc)
                    break

                if self._cancel_event.is_set():
                    sentinel = job_cfg.get("cancelSentinelPath") or self._cancel_sentinel_path
                    if sentinel and not os.path.isfile(sentinel):
                        try:
                            with open(sentinel, "w", encoding="utf-8") as f:
                                f.write("cancel")
                        except Exception:
                            pass
                    if cancel_seen_at is None:
                        cancel_seen_at = time.monotonic()
                    grace = 1.0 if is_preview else 4.0
                    if (time.monotonic() - cancel_seen_at) > grace:
                        _kill_process_tree(proc)
                        last_stage = "cancelled"
                        break

                time.sleep(0.08)
        finally:
            self._proc = None

        if self._cancel_event.is_set():
            last_stage = "cancelled"
            stayed_warm = False
        return last_stage, last_frames, last_video_sec, last_render_fps, stayed_warm

    def _run_unity_pass(
        self,
        adofai_dir: str,
        adofai_exe: str,
        agent_dll: str,
        job_cfg: dict,
        work_dir: str,
        start_sec: float,
        target_fps: int,
        unity_extra_args: List[str],
        progress_cb: Optional[Callable[[RenderProgress], None]],
        is_preview: bool = False,
        allow_warm: bool = True,
        log_cb: Optional[Callable[[str], None]] = None,
    ) -> Tuple[str, int, float, float]:
        """Run a Unity Doorstop offline pass, reusing an IdleWarm session for the same level when available."""
        if self._cancel_event.is_set():
            return "cancelled", 0, 0.0, 0.0

        warm_mgr = get_warm_session_manager()
        keep_warm = bool(allow_warm and warm_mgr.is_eligible(self.caps))
        level_path = str(job_cfg.get("levelPath", ""))
        progress_path = job_cfg["progressPath"]

        # 1) Attempt zero-reload warm session reuse when the exact same level is already loaded in RAM/VRAM
        claimed = warm_mgr.try_claim(level_path, adofai_dir, agent_dll) if allow_warm else None
        if claimed is not None:
            proc, session_dir = claimed
            ready_path = os.path.join(session_dir, "ready.json")
            next_job_path = os.path.join(session_dir, "next_job.json")
            next_job_tmp = next_job_path + ".tmp"

            warm_cfg = dict(job_cfg)
            warm_cfg["keepAliveAfterFinish"] = keep_warm
            warm_cfg["sessionDir"] = session_dir
            warm_cfg["parentPid"] = os.getpid()
            warm_cfg["idleTimeoutSec"] = 300

            try:
                job_json_path = os.path.join(work_dir, "job.json")
                with open(job_json_path, "w", encoding="utf-8") as f:
                    json.dump(warm_cfg, f, ensure_ascii=False, indent=2)
                # Remove previous ready.json BEFORE dispatching next_job.json so ready.json can only
                # reappear after the agent finishes THIS job and re-enters IdleWarm.
                if os.path.isfile(ready_path):
                    os.remove(ready_path)
                with open(next_job_tmp, "w", encoding="utf-8") as f:
                    json.dump(warm_cfg, f, ensure_ascii=False, indent=2)
                os.replace(next_job_tmp, next_job_path)

                if log_cb:
                    log_cb("상주 렌더 세션 재사용: 이미 로딩된 레벨·장식·텍스처로 즉시 렌더링을 시작합니다.")
                if progress_cb:
                    progress_cb(RenderProgress(
                        stage="loading_level",
                        percent=4.0,
                        message="상주 엔진 세션 재사용 중 (레벨 · 장식 재로딩 생략)...",
                    ))

                last_stage, last_frames, last_video_sec, last_render_fps, stayed_warm = self._poll_unity_pass(
                    proc=proc,
                    job_cfg=warm_cfg,
                    start_sec=start_sec,
                    target_fps=target_fps,
                    progress_cb=progress_cb,
                    is_preview=is_preview,
                    keep_warm=keep_warm,
                    ready_json_path=ready_path,
                )

                if stayed_warm:
                    warm_mgr.release_back_to_idle(proc, session_dir, level_path, adofai_dir, agent_dll)
                    return last_stage, last_frames, last_video_sec, last_render_fps

                if self._cancel_event.is_set() or last_stage == "cancelled":
                    warm_mgr.discard_active(proc, session_dir, force_kill=True)
                    return "cancelled", last_frames, last_video_sec, last_render_fps

                if last_stage == "done":
                    warm_mgr.discard_active(proc, session_dir, force_kill=False)
                    return last_stage, last_frames, last_video_sec, last_render_fps

                # Warm restart failed before rendering cleanly -> discard & transparently fall back to cold start!
                warm_mgr.discard_active(proc, session_dir, force_kill=True)
                if log_cb:
                    log_cb(f"상주 세션 재시작 실패(stage={last_stage}) — 일반 엔진 로딩으로 자동 전환합니다.")
                for fpath in (progress_path, job_cfg.get("videoOutputPath", "")):
                    if fpath and os.path.isfile(fpath):
                        try:
                            os.remove(fpath)
                        except Exception:
                            pass
            except Exception as ex:
                warm_mgr.discard_active(proc, session_dir, force_kill=True)
                if log_cb:
                    log_cb(f"상주 세션 디스패치 오류({ex}) — 일반 엔진 로딩으로 자동 전환합니다.")

        # 2) Cold launch (shut down any unrelated warm session first so 2 Unity instances never coexist)
        warm_mgr.shutdown(force=False)
        if self._cancel_event.is_set():
            return "cancelled", 0, 0.0, 0.0

        session_dir = ""
        ready_path = ""
        cold_cfg = dict(job_cfg)
        if keep_warm:
            snonce = f"{os.getpid()}_{int(time.time() * 1000) % 1000000}"
            session_dir = os.path.join(CACHE_ROOT, "jobs", f"warm_session_{snonce}")
            os.makedirs(session_dir, exist_ok=True)
            ready_path = os.path.join(session_dir, "ready.json")
            cold_cfg["keepAliveAfterFinish"] = True
            cold_cfg["sessionDir"] = session_dir
            cold_cfg["parentPid"] = os.getpid()
            cold_cfg["idleTimeoutSec"] = 300
        else:
            cold_cfg["keepAliveAfterFinish"] = False
            cold_cfg["sessionDir"] = ""

        job_json_path = os.path.join(work_dir, "job.json")
        with open(job_json_path, "w", encoding="utf-8") as f:
            json.dump(cold_cfg, f, ensure_ascii=False, indent=2)

        env = os.environ.copy()
        env["ADOFAI_RENDER_JOB"] = job_json_path

        # Offscreen camRT is independent of the host window size; keep dummy window compact (640x360)
        # to minimize wasted swapchain work while maintaining full 16:9 aspect ratio.
        win_w = "640"
        win_h = "360"

        cmd = [
            adofai_exe,
            "--doorstop-enabled", "true",
            "--doorstop-target-assembly", agent_dll,
            "-popupwindow",
            "-screen-width", win_w,
            "-screen-height", win_h,
            "-screen-fullscreen", "0",
        ] + unity_extra_args

        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 4  # SW_SHOWNOACTIVATE: never steal focus from GUI

        if self._cancel_event.is_set():
            if session_dir:
                WarmSessionManager._cleanup_dir(session_dir)
            return "cancelled", 0, 0.0, 0.0

        proc = subprocess.Popen(cmd, cwd=adofai_dir, env=env, startupinfo=startupinfo)
        last_stage, last_frames, last_video_sec, last_render_fps, stayed_warm = self._poll_unity_pass(
            proc=proc,
            job_cfg=cold_cfg,
            start_sec=start_sec,
            target_fps=target_fps,
            progress_cb=progress_cb,
            is_preview=is_preview,
            keep_warm=keep_warm,
            ready_json_path=ready_path,
        )

        if stayed_warm and proc.poll() is None:
            warm_mgr.release_back_to_idle(proc, session_dir, level_path, adofai_dir, agent_dll)
        elif session_dir:
            if proc.poll() is None:
                _kill_process_tree(proc)
            WarmSessionManager._cleanup_dir(session_dir)

        return last_stage, last_frames, last_video_sec, last_render_fps

    def run(
        self,
        settings: RenderSettings,
        progress_cb: Optional[Callable[[RenderProgress], None]] = None,
        log_cb: Optional[Callable[[str], None]] = None,
    ) -> RenderResult:
        self._cancel_event.clear()
        t0 = time.monotonic()

        def emit_log(msg: str) -> None:
            if log_cb:
                log_cb(msg)

        def emit_prog(p: RenderProgress) -> None:
            if progress_cb:
                progress_cb(p)

        # 1. Validate inputs & Steam ADOFAI path
        level_path = os.path.abspath(settings.level_path)
        if not os.path.isfile(level_path):
            return RenderResult(success=False, error=f"레벨 파일을 찾을 수 없습니다: {level_path}")

        adofai_dir = settings.adofai_dir or find_adofai_dir() or ""
        adofai_exe = os.path.join(adofai_dir, "A Dance of Fire and Ice.exe")
        if not adofai_dir or not os.path.isfile(adofai_exe):
            return RenderResult(success=False, error="Steam ADOFAI 설치 경로를 찾을 수 없습니다. 경로를 직접 지정해 주세요.")

        doorstop_dll = os.path.join(adofai_dir, "winhttp.dll")
        if not os.path.isfile(doorstop_dll):
            return RenderResult(
                success=False,
                error=(
                    f"ADOFAI 폴더에 Unity Doorstop(winhttp.dll)이 없습니다: {adofai_dir}\n"
                    "오프라인 렌더 백엔드 로드를 위해 Unity Doorstop이 필요합니다."
                ),
            )

        if not (24 <= settings.fps <= 240):
            return RenderResult(success=False, error=f"FPS는 24~240 범위여야 합니다 (입력값: {settings.fps}).")

        if settings.width <= 0 or settings.height <= 0 or (settings.width % 2 != 0) or (settings.height % 2 != 0):
            return RenderResult(success=False, error="해상도 가로/세로는 짝수 양수여야 합니다.")

        agent_dll = self.ensure_agent_built(adofai_dir, emit_log)

        # 2. Build resolution-validated encoder fallback chain (guarantees low-spec PCs never get stuck!)
        raw_chain = resolve_encoder_fallback_chain(self.caps, settings.gpu_mode, settings.codec)
        validated_chain: List[Tuple[str, str]] = []
        for enc, label in raw_chain:
            if enc.startswith("libx"):
                validated_chain.append((enc, label))
                break
            if test_encoder_usable(self.caps.ffmpeg_path, enc, settings.width, settings.height):
                validated_chain.append((enc, label))
            else:
                emit_log(f"[안전 우회] {label}는 {settings.width}x{settings.height} 해상도를 지원하지 않아 다음 인코더로 우회합니다.")
        if not validated_chain:
            validated_chain.append(("libx264", "CPU 최후 폴백 (libx264)"))

        # Ensure CPU fallback is always at tail of chain
        sw_enc = "libx265" if settings.codec == "hevc" and "libx265" in self.caps.available_encoders else "libx264"
        if all(x[0] != sw_enc for x in validated_chain):
            validated_chain.append((sw_enc, f"CPU 소프트웨어 안전 폴백 ({sw_enc})"))

        gpu_cap = max(10, min(100, int(settings.gpu_limit_percent)))
        initial_low_spec = (
            settings.low_spec_mode == "force"
            or (settings.low_spec_mode == "auto" and self.caps.low_spec_recommended)
        )

        # 3. Prepare per-job workspace
        job_id = time.strftime("%Y%m%d_%H%M%S") + f"_{os.getpid()}"
        work_dir = os.path.join(CACHE_ROOT, "jobs", job_id)
        sfx_cache_dir = os.path.join(CACHE_ROOT, "sfx")
        os.makedirs(work_dir, exist_ok=True)
        os.makedirs(sfx_cache_dir, exist_ok=True)

        out_path = os.path.abspath(settings.output_path)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        ext = os.path.splitext(out_path)[1].lower()
        if ext not in (".mp4", ".mkv"):
            ext = ".mp4"
            out_path += ".mp4"

        video_only_path = os.path.join(work_dir, f"video_only{ext}")
        mixed_wav_path = os.path.join(work_dir, "mixed_audio.wav")
        audio_timeline_path = os.path.join(work_dir, "audio_timeline.json")
        progress_path = os.path.join(work_dir, "progress.jsonl")
        log_path = os.path.join(work_dir, "render_agent.log")
        cancel_sentinel = os.path.join(work_dir, "cancel.flag")
        self._cancel_sentinel_path = cancel_sentinel

        # Build progressive execution attempts (Normal -> Low-Spec Safe Sync ReadPixels + Fallback Encoder -> Unity Auto-API + CPU)
        attempts: List[Tuple[str, str, bool, List[str], str]] = []
        primary_enc, primary_label = validated_chain[0]
        attempts.append((primary_enc, primary_label, initial_low_spec, ["-force-d3d11"], "기본 경로"))

        fallback_enc, fallback_label = validated_chain[-1]
        if not initial_low_spec or fallback_enc != primary_enc:
            attempts.append((fallback_enc, fallback_label, True, ["-force-d3d11"], "저사양 안전 모드 (동기 ReadPixels + 안전 인코더)"))

        attempts.append(("libx264", "CPU 호환 폴백 (libx264)", True, [], "최후 호환 우회 경로 (Unity 자동 그래픽 API + CPU)"))

        used_encoder = primary_enc
        used_enc_label = primary_label
        fallback_note = ""
        last_stage = "launching"
        last_frames = 0
        last_video_sec = 0.0
        last_render_fps = 0.0

        for idx, (enc, enc_label, safe_mode, unity_flags, mode_desc) in enumerate(attempts):
            if self._cancel_event.is_set():
                break

            if idx > 0:
                fallback_note = f"{mode_desc}로 자동 우회 완료"
                emit_log(f"[안전장치 작동] 이전 시도 실패(stage={last_stage}) -> {mode_desc} ({enc_label})로 자동 재시도합니다. (해상도·FPS·화질 유지)")
                for fpath in (progress_path, video_only_path):
                    try:
                        if os.path.isfile(fpath):
                            os.remove(fpath)
                    except Exception:
                        pass

            used_encoder = enc
            used_enc_label = enc_label
            emit_log(f"인코더: {enc_label} | GPU 출력 상한: {gpu_cap}% | 안전모드: {'ON' if safe_mode else 'OFF'}")
            emit_log(f"출력 스펙: {settings.width}x{settings.height} @ {settings.fps}fps CFR | 공: R={settings.red_planet_color}({int(settings.red_planet_opacity*100)}%) / B={settings.blue_planet_color}({int(settings.blue_planet_opacity*100)}%)")

            ffmpeg_args = build_ffmpeg_video_args(
                width=settings.width,
                height=settings.height,
                fps=settings.fps,
                output_path=video_only_path,
                encoder=enc,
                quality=settings.quality,
                max_cpu_cores=settings.max_cpu_cores,
            )

            sub = resolve_substeps(settings, level_path)
            sim_fps = int(settings.fps) * sub
            job_cfg = {
                "levelPath": level_path,
                "videoOutputPath": video_only_path,
                "audioTimelinePath": audio_timeline_path,
                "sfxCacheDir": sfx_cache_dir,
                "progressPath": progress_path,
                "logPath": log_path,
                "cancelSentinelPath": cancel_sentinel,
                "ffmpegPath": self.caps.ffmpeg_path,
                "ffmpegArgs": ffmpeg_args,
                "width": settings.width,
                "height": settings.height,
                "fps": sim_fps,
                "startSec": float(settings.start_sec),
                "endSec": float(settings.end_sec),
                "endPaddingSec": float(settings.end_padding_sec),
                "hideCountdown": bool(settings.hide_countdown),
                "hideHitText": bool(settings.hide_hit_text),
                "flipVertical": True,
                "captureAudioClips": True,
                "maxGpuUsagePercent": gpu_cap,
                "maxRamUsagePercent": max(30, min(95, int(settings.ram_limit_percent))),
                "customPlanetColors": True,
                "redPlanetColor": settings.red_planet_color,
                "redPlanetOpacity": float(max(0.0, min(1.0, settings.red_planet_opacity))),
                "bluePlanetColor": settings.blue_planet_color,
                "bluePlanetOpacity": float(max(0.0, min(1.0, settings.blue_planet_opacity))),
                "lowSpecSafeMode": bool(safe_mode),
                "experimentalOptimizeRender": bool(settings.experimental_optimize_render),
                "previewMode": False,
                "captureStride": sub,
                "arcTails": True,
                "fastStart": os.environ.get("ADOFAI_FAST_START", "1") != "0",
            }

            emit_prog(RenderProgress(stage="launching", message=f"ADOFAI 오프라인 렌더 엔진 시작 중 ({mode_desc})..."))
            last_stage, last_frames, last_video_sec, last_render_fps = self._run_unity_pass(
                adofai_dir=adofai_dir,
                adofai_exe=adofai_exe,
                agent_dll=agent_dll,
                job_cfg=job_cfg,
                work_dir=work_dir,
                start_sec=settings.start_sec,
                target_fps=sim_fps,
                unity_extra_args=unity_flags,
                progress_cb=emit_prog,
                is_preview=False,
                allow_warm=(idx == 0),
                log_cb=emit_log,
            )

            if self._cancel_event.is_set() or last_stage == "cancelled":
                break

            if last_stage == "done" and os.path.isfile(video_only_path) and os.path.getsize(video_only_path) >= 1024:
                break

        if self._cancel_event.is_set() or last_stage == "cancelled":
            return RenderResult(success=False, error="사용자에 의해 렌더링이 취소되었습니다.", log_path=log_path)

        if last_stage != "done" or not os.path.isfile(video_only_path) or os.path.getsize(video_only_path) < 1024:
            err_tail = ""
            if os.path.isfile(log_path):
                try:
                    lines = open(log_path, "r", encoding="utf-8", errors="replace").read().splitlines()
                    err_tail = "\n".join(lines[-14:])
                except Exception:
                    pass
            return RenderResult(
                success=False,
                error=f"모든 안전 우회 경로 시도 후에도 비디오 렌더링이 완료되지 않았습니다 (stage={last_stage}).\n{err_tail}",
                log_path=log_path,
            )

        # Save performance benchmark for more accurate future ETA predictions
        _save_perf_sample(used_encoder, settings.width, settings.height, gpu_cap, last_render_fps)

        # 5. Offline deterministic audio mixing (BGM + real ADOFAI hitsounds/SFX)
        emit_prog(RenderProgress(
            stage="mixing_audio",
            frames=last_frames,
            video_sec=last_video_sec,
            percent=97.0,
            message="원곡 + 실제 인게임 히트사운드 오프라인 정밀 믹싱 중...",
        ))
        has_audio = False
        _fix_timeline_output_fps(audio_timeline_path, float(settings.fps))
        if os.path.isfile(audio_timeline_path):
            mix_opts = AudioMixOptions(
                include_bgm=settings.include_bgm,
                bgm_volume=settings.bgm_volume,
                include_hitsounds=settings.include_hitsounds,
                hitsound_volume=settings.hitsound_volume,
                include_sfx=settings.include_sfx,
                sfx_volume=settings.sfx_volume,
            )
            has_audio = mix_audio_from_timeline(
                timeline_json_path=audio_timeline_path,
                output_wav_path=mixed_wav_path,
                ffmpeg_path=self.caps.ffmpeg_path,
                options=mix_opts,
                log_cb=emit_log,
            )

        # 6. Fast lossless video+audio muxing into final MP4/MKV
        emit_prog(RenderProgress(
            stage="muxing",
            frames=last_frames,
            video_sec=last_video_sec,
            percent=99.0,
            message=f"최종 {ext.upper().lstrip('.')} 파일 합성 중...",
        ))

        if has_audio and os.path.isfile(mixed_wav_path):
            mux_cmd = [
                self.caps.ffmpeg_path,
                "-y", "-hide_banner", "-loglevel", "warning",
                "-i", video_only_path,
                "-i", mixed_wav_path,
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", settings.audio_bitrate,
                "-shortest",
            ]
            if ext == ".mp4":
                mux_cmd += ["-movflags", "+faststart"]
            mux_cmd.append(out_path)

            res = subprocess.run(
                mux_cmd,
                capture_output=True,
                text=True,
                timeout=300,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if res.returncode != 0:
                return RenderResult(
                    success=False,
                    error=f"최종 MP4/MKV 합성 실패:\n{res.stderr}",
                    log_path=log_path,
                )
        else:
            shutil.copy2(video_only_path, out_path)

        # 7. Verify final output resolution & CFR framerate strictly match target
        verify_msg = self._verify_output_stream(out_path, settings.width, settings.height, settings.fps)
        if verify_msg:
            emit_log(verify_msg)

        try:
            if os.path.isfile(out_path):
                _save_bitrate_sample(
                    codec=settings.codec,
                    quality=settings.quality,
                    width=settings.width,
                    height=settings.height,
                    fps=settings.fps,
                    file_bytes=os.path.getsize(out_path),
                    video_sec=last_video_sec,
                )
        except Exception:
            pass

        wall_total = time.monotonic() - t0
        emit_log(f"렌더링 완료! ({last_frames:,}프레임, {last_video_sec:.2f}초 영상, 총 소요 {wall_total:.1f}초) -> {out_path}")
        emit_prog(RenderProgress(
            stage="completed",
            frames=last_frames,
            video_sec=last_video_sec,
            wall_sec=wall_total,
            percent=100.0,
            message=f"완료! ({wall_total:.1f}초 소요)",
        ))

        if not settings.keep_temp:
            for tmp in (video_only_path, mixed_wav_path):
                try:
                    if os.path.isfile(tmp):
                        os.remove(tmp)
                except Exception:
                    pass

        return RenderResult(
            success=True,
            output_path=out_path,
            frames=last_frames,
            video_sec=last_video_sec,
            wall_sec=wall_total,
            encoder_used=used_enc_label,
            fallback_note=fallback_note,
            audio_timeline_path=audio_timeline_path,
            log_path=log_path,
        )

    def generate_rough_preview(
        self,
        level_path: str,
        adofai_dir: str = "",
        red_color: str = "#FF0000",
        red_opacity: float = 1.0,
        blue_color: str = "#0000FF",
        blue_opacity: float = 1.0,
        gpu_limit_percent: int = 50,
        force_rebuild: bool = False,
        progress_cb: Optional[Callable[[RenderProgress], None]] = None,
        log_cb: Optional[Callable[[str], None]] = None,
        proxy_width: int = DEFAULT_PROXY_WIDTH,
        proxy_height: int = DEFAULT_PROXY_HEIGHT,
        proxy_fps: int = DEFAULT_PROXY_FPS,
        proxy_mode: str = DEFAULT_PROXY_MODE,
    ) -> PreviewResult:
        """Pre-render a lightweight proxy MP4 + timeline filmstrip for trim selection (240p..720p, 5..60fps)."""
        if self._cancel_event.is_set():
            return PreviewResult(success=False, error="프록시 생성이 취소되었습니다.")
        abs_lvl = os.path.abspath(level_path)
        if not os.path.isfile(abs_lvl):
            return PreviewResult(success=False, error="레벨 파일을 찾을 수 없습니다.")

        pw, ph, out_fps = clamp_proxy_spec(proxy_width, proxy_height, proxy_fps)
        pmode = normalize_proxy_mode(proxy_mode)
        os.makedirs(PREVIEW_CACHE_DIR, exist_ok=True)
        h = compute_preview_cache_key(
            abs_lvl, red_color, red_opacity, blue_color, blue_opacity, pw, ph, out_fps, pmode
        )
        out_mp4 = os.path.join(PREVIEW_CACHE_DIR, f"preview_{h}.mp4")
        strip_jpg = os.path.join(PREVIEW_CACHE_DIR, f"strip_{h}.jpg")

        ok_marker = out_mp4 + ".ok"
        if not force_rebuild and os.path.isfile(out_mp4) and os.path.getsize(out_mp4) > 2048 and os.path.isfile(ok_marker):
            dur = self._probe_duration(out_mp4)
            if not os.path.isfile(strip_jpg):
                self._build_filmstrip(out_mp4, strip_jpg, dur)
            return PreviewResult(success=True, video_path=out_mp4, filmstrip_path=strip_jpg, duration_sec=dur, cached=True)

        adofai_dir = adofai_dir or find_adofai_dir() or ""
        adofai_exe = os.path.join(adofai_dir, "A Dance of Fire and Ice.exe")
        if not adofai_dir or not os.path.isfile(adofai_exe):
            return PreviewResult(success=False, error="Steam ADOFAI 설치 경로를 찾을 수 없습니다.")

        agent_dll = self.ensure_agent_built(adofai_dir, log_cb)
        if self._cancel_event.is_set():
            return PreviewResult(success=False, error="프록시 생성이 취소되었습니다.")

        # Keep simulation rate >= 10fps via captureStride=2 for very low proxy FPS (5..9fps) so tile stepping stays accurate
        stride = 2 if out_fps < 10 else 1
        sim_fps = out_fps * stride

        nonce = f"{os.getpid()}_{int(time.time() * 1000) % 1000000}"
        work_dir = os.path.join(CACHE_ROOT, "jobs", f"preview_{h}_{nonce}")
        os.makedirs(work_dir, exist_ok=True)
        video_only = os.path.join(work_dir, "preview_video.mp4")
        timeline_json = os.path.join(work_dir, "preview_timeline.json")
        progress_path = os.path.join(work_dir, "progress.jsonl")
        log_path = os.path.join(work_dir, "preview_agent.log")
        cancel_sentinel = os.path.join(work_dir, "cancel.flag")
        self._cancel_sentinel_path = cancel_sentinel

        try:
            enc = "h264_nvenc" if "h264_nvenc" in self.caps.verified_encoders else "libx264"
            ffmpeg_args = build_ffmpeg_preview_args(pw, ph, out_fps, video_only, encoder=enc)

            job_cfg = {
                "levelPath": abs_lvl,
                "videoOutputPath": video_only,
                "audioTimelinePath": timeline_json,
                "sfxCacheDir": os.path.join(CACHE_ROOT, "sfx"),
                "progressPath": progress_path,
                "logPath": log_path,
                "cancelSentinelPath": cancel_sentinel,
                "ffmpegPath": self.caps.ffmpeg_path,
                "ffmpegArgs": ffmpeg_args,
                "width": pw,
                "height": ph,
                "fps": sim_fps,
                "startSec": 0.0,
                "endSec": 0.0,
                "endPaddingSec": 1.0,
                "hideCountdown": True,
                "hideHitText": True,
                "flipVertical": True,
                "captureAudioClips": False,
                "maxGpuUsagePercent": max(30, min(100, int(gpu_limit_percent))),
                "customPlanetColors": True,
                "redPlanetColor": red_color,
                "redPlanetOpacity": float(max(0.0, min(1.0, red_opacity))),
                "bluePlanetColor": blue_color,
                "bluePlanetOpacity": float(max(0.0, min(1.0, blue_opacity))),
                "lowSpecSafeMode": bool(self.caps.low_spec_recommended),
                "experimentalOptimizeRender": (pmode != "chart_only"),
                "previewMode": True,
                "previewRenderMode": pmode,
                "captureStride": stride,
            }

            seen = {"est": 0.0}
            user_cb = progress_cb

            def wrapped_progress_cb(p: RenderProgress) -> None:
                try:
                    if p.est_total_sec and p.est_total_sec > seen["est"]:
                        seen["est"] = float(p.est_total_sec)
                except Exception:
                    pass
                if user_cb and not self._cancel_event.is_set():
                    user_cb(p)

            if log_cb:
                log_cb(f"프록시 생성 중 ({pw}x{ph} · {out_fps}fps Proxy)...")
            wrapped_progress_cb(RenderProgress(stage="launching", message=f"프록시 렌더 시작 중 ({pw}×{ph} · {out_fps}fps)..."))

            stage, _frames, vsec, _rfps = self._run_unity_pass(
                adofai_dir=adofai_dir,
                adofai_exe=adofai_exe,
                agent_dll=agent_dll,
                job_cfg=job_cfg,
                work_dir=work_dir,
                start_sec=0.0,
                target_fps=sim_fps,
                unity_extra_args=["-force-d3d11"],
                progress_cb=wrapped_progress_cb,
                is_preview=True,
                allow_warm=True,
                log_cb=log_cb,
            )

            if self._cancel_event.is_set() or stage == "cancelled":
                return PreviewResult(success=False, error="프록시 생성이 취소되었습니다.")

            # Safe CPU fallback retry if HW preview encoder failed
            if stage != "done" or not os.path.isfile(video_only) or os.path.getsize(video_only) < 1024:
                if enc != "libx264":
                    job_cfg["ffmpegArgs"] = build_ffmpeg_preview_args(pw, ph, out_fps, video_only, encoder="libx264")
                    job_cfg["lowSpecSafeMode"] = True
                    for fpath in (progress_path, video_only):
                        try:
                            if os.path.isfile(fpath):
                                os.remove(fpath)
                        except Exception:
                            pass
                    stage, _frames, vsec, _rfps = self._run_unity_pass(
                        adofai_dir=adofai_dir,
                        adofai_exe=adofai_exe,
                        agent_dll=agent_dll,
                        job_cfg=job_cfg,
                        work_dir=work_dir,
                        start_sec=0.0,
                        target_fps=sim_fps,
                        unity_extra_args=[],
                        progress_cb=wrapped_progress_cb,
                        is_preview=True,
                        allow_warm=False,
                        log_cb=log_cb,
                    )

            if self._cancel_event.is_set() or stage == "cancelled":
                return PreviewResult(success=False, error="프록시 생성이 취소되었습니다.")

            if stage != "done" or not os.path.isfile(video_only) or os.path.getsize(video_only) < 1024:
                return PreviewResult(success=False, error=f"프록시 영상 생성 실패 (stage={stage})")

            # Reject truncated proxies (interrupted run): must cover (almost) the whole level
            if seen["est"] > 5.0 and vsec > 0 and vsec < seen["est"] - 3.0:
                return PreviewResult(
                    success=False,
                    error=f"프록시 생성이 중간에 중단되었습니다 ({vsec:.0f}s / {seen['est']:.0f}s). 다시 생성해 주세요.",
                )

            # Mux BGM-only audio cleanly onto rough preview so scrubbing/playback matches song timing
            _fix_timeline_output_fps(timeline_json, float(out_fps))
            mixed_wav = os.path.join(work_dir, "preview_bgm.wav")
            muxed = False
            if os.path.isfile(timeline_json) and not self._cancel_event.is_set():
                try:
                    has_bgm = mix_audio_from_timeline(
                        timeline_json_path=timeline_json,
                        output_wav_path=mixed_wav,
                        ffmpeg_path=self.caps.ffmpeg_path,
                        options=AudioMixOptions(include_bgm=True, include_hitsounds=False, include_sfx=False),
                    )
                    if has_bgm and os.path.isfile(mixed_wav) and not self._cancel_event.is_set():
                        os.makedirs(PREVIEW_CACHE_DIR, exist_ok=True)
                        r = subprocess.run(
                            [
                                self.caps.ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error",
                                "-i", video_only, "-i", mixed_wav,
                                "-map", "0:v:0", "-map", "1:a:0",
                                "-c:v", "copy", "-c:a", "aac", "-b:a", "96k",
                                "-shortest", "-movflags", "+faststart",
                                out_mp4,
                            ],
                            capture_output=True,
                            timeout=60,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        )
                        muxed = (r.returncode == 0 and os.path.isfile(out_mp4))
                except Exception:
                    pass

            if self._cancel_event.is_set():
                for pth in (out_mp4, strip_jpg, ok_marker):
                    try:
                        if os.path.isfile(pth):
                            os.remove(pth)
                    except Exception:
                        pass
                return PreviewResult(success=False, error="프록시 생성이 취소되었습니다.")

            if not muxed:
                os.makedirs(PREVIEW_CACHE_DIR, exist_ok=True)
                shutil.copy2(video_only, out_mp4)

            dur = self._probe_duration(out_mp4) or vsec
            self._build_filmstrip(out_mp4, strip_jpg, dur)
            try:
                with open(ok_marker, "w", encoding="utf-8") as mf:
                    mf.write(f"{dur:.3f}")
            except Exception:
                pass

            if log_cb and os.path.isfile(log_path):
                try:
                    with open(log_path, "r", encoding="utf-8-sig", errors="replace") as lf:
                        for line in lf:
                            if "Proxy render mode=" in line or "Proxy heavy-filter bypass:" in line:
                                log_cb(line.strip())
                                break
                except Exception:
                    pass

            return PreviewResult(success=True, video_path=out_mp4, filmstrip_path=strip_jpg, duration_sec=dur, cached=False)
        finally:
            self._cancel_sentinel_path = None
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
            except Exception:
                pass

    def _probe_duration(self, path: str) -> float:
        try:
            res = subprocess.run(
                [self.caps.ffprobe_path, "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if res.returncode == 0:
                return float(json.loads(res.stdout).get("format", {}).get("duration", 0.0) or 0.0)
        except Exception:
            pass
        return 0.0

    def _build_filmstrip(self, video_path: str, strip_jpg: str, duration: float, tiles: int = 16) -> None:
        """Create a compact horizontal contact strip of thumbnails for the timeline trim bar."""
        try:
            fps_sample = max(0.02, float(tiles) / max(1.0, duration))
            subprocess.run(
                [
                    self.caps.ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error",
                    "-i", video_path,
                    "-vf", f"fps={fps_sample:.4f},scale=120:68,tile={tiles}x1",
                    "-frames:v", "1",
                    "-q:v", "5",
                    strip_jpg,
                ],
                capture_output=True,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass

    def _verify_output_stream(self, out_path: str, exp_w: int, exp_h: int, exp_fps: int) -> str:
        try:
            res = subprocess.run(
                [
                    self.caps.ffprobe_path, "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate",
                    "-of", "json",
                    out_path,
                ],
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if res.returncode != 0:
                return ""
            streams = json.loads(res.stdout).get("streams", [])
            if not streams:
                return ""
            st = streams[0]
            w = int(st.get("width", 0))
            h = int(st.get("height", 0))

            def _parse_rate(s: str) -> float:
                if "/" in s:
                    a, b = s.split("/", 1)
                    return float(a) / float(b) if float(b) else 0.0
                return float(s)

            r_fps = _parse_rate(str(st.get("r_frame_rate", "0")))
            avg_fps = _parse_rate(str(st.get("avg_frame_rate", "0")))
            ok = (w == exp_w and h == exp_h and abs(r_fps - exp_fps) < 0.05 and abs(avg_fps - exp_fps) < 0.05)
            status = "검증 통과" if ok else "주의(불일치 감지)"
            return f"출력 스트림 {status}: {w}x{h} | r_fps={r_fps:.2f}, avg_fps={avg_fps:.2f} (목표: {exp_w}x{exp_h} @ {exp_fps}fps CFR)"
        except Exception:
            return ""
