"""End-to-end render orchestration: low-spec safe fallback chain, planet colors, rough preview, ETA calibration."""

from __future__ import annotations

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


class RenderRunner:
    def __init__(self, caps: Optional[HardwareCapabilities] = None) -> None:
        self.caps = caps or detect_hardware(verify_hw=True)
        self._cancel_event = threading.Event()
        self._cancel_sentinel_path: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None

    def cancel(self) -> None:
        self._cancel_event.set()
        if self._cancel_sentinel_path:
            try:
                with open(self._cancel_sentinel_path, "w", encoding="utf-8") as f:
                    f.write("cancel")
            except Exception:
                pass

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
    ) -> Tuple[str, int, float, float]:
        """Run a single Unity Doorstop offline pass. Returns (last_stage, frames, video_sec, render_fps)."""
        job_json_path = os.path.join(work_dir, "job.json")
        progress_path = job_cfg["progressPath"]
        with open(job_json_path, "w", encoding="utf-8") as f:
            json.dump(job_cfg, f, ensure_ascii=False, indent=2)

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

        proc = subprocess.Popen(cmd, cwd=adofai_dir, env=env, startupinfo=startupinfo)
        self._proc = proc

        last_stage = "launching"
        last_frames = 0
        last_video_sec = 0.0
        last_render_fps = 0.0
        prog_offset = 0
        done_seen_at: Optional[float] = None
        stride = max(1, int(job_cfg.get("captureStride", 1)))
        out_fps = max(1.0, float(target_fps) / float(stride))

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
                            if progress_cb:
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

                if done_seen_at is not None and (time.monotonic() - done_seen_at) > 4.0:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    break

                if self._cancel_event.is_set():
                    if done_seen_at is None:
                        done_seen_at = time.monotonic()
                    elif (time.monotonic() - done_seen_at) > 5.0:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        break

                time.sleep(0.10)
        finally:
            self._proc = None

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
            message="원곡 + 실제 인게임 타격음 오프라인 정밀 믹싱 중...",
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
    ) -> PreviewResult:
        """Pre-render a lightweight low-res / low-fps rough preview MP4 + timeline filmstrip for trim selection."""
        self._cancel_event.clear()
        abs_lvl = os.path.abspath(level_path)
        if not os.path.isfile(abs_lvl):
            return PreviewResult(success=False, error="레벨 파일을 찾을 수 없습니다.")

        os.makedirs(PREVIEW_CACHE_DIR, exist_ok=True)
        st = os.stat(abs_lvl)
        key_src = f"{abs_lvl}|{st.st_size}|{int(st.st_mtime)}|{red_color}|{red_opacity:.2f}|{blue_color}|{blue_opacity:.2f}|v5"
        h = hashlib.sha1(key_src.encode("utf-8", errors="ignore")).hexdigest()[:14]
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

        # Rough preview specs: 480x270 (16:9 thumbnail), simulated at 12fps and captured every frame => 12fps rough video
        # (half the simulation steps of the previous 24fps/stride-2 setup for the same output frame rate)
        pw, ph = 480, 270
        sim_fps = 12
        stride = 1
        out_fps = sim_fps // stride  # 12 fps

        work_dir = os.path.join(CACHE_ROOT, "jobs", f"preview_{h}_{os.getpid()}")
        os.makedirs(work_dir, exist_ok=True)
        video_only = os.path.join(work_dir, "preview_video.mp4")
        timeline_json = os.path.join(work_dir, "preview_timeline.json")
        progress_path = os.path.join(work_dir, "progress.jsonl")
        log_path = os.path.join(work_dir, "preview_agent.log")
        cancel_sentinel = os.path.join(work_dir, "cancel.flag")
        self._cancel_sentinel_path = cancel_sentinel

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
            "experimentalOptimizeRender": True,
            "previewMode": True,
            "captureStride": stride,
        }

        seen = {"est": 0.0}
        user_cb = progress_cb

        def progress_cb(p: RenderProgress) -> None:  # noqa: F811 - wrapper that records expected length
            try:
                if p.est_total_sec and p.est_total_sec > seen["est"]:
                    seen["est"] = float(p.est_total_sec)
            except Exception:
                pass
            if user_cb:
                user_cb(p)

        if log_cb:
            log_cb(f"사전 미리보기 생성 중 ({pw}x{ph} · {out_fps}fps Rough Thumbnail)...")
        if progress_cb:
            progress_cb(RenderProgress(stage="launching", message="미리보기 썸네일 렌더 시작 중..."))

        stage, _frames, vsec, _rfps = self._run_unity_pass(
            adofai_dir=adofai_dir,
            adofai_exe=adofai_exe,
            agent_dll=agent_dll,
            job_cfg=job_cfg,
            work_dir=work_dir,
            start_sec=0.0,
            target_fps=sim_fps,
            unity_extra_args=["-force-d3d11"],
            progress_cb=progress_cb,
            is_preview=True,
        )

        if self._cancel_event.is_set() or stage == "cancelled":
            return PreviewResult(success=False, error="미리보기 생성이 취소되었습니다.")

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
                    progress_cb=progress_cb,
                    is_preview=True,
                )

        if stage != "done" or not os.path.isfile(video_only) or os.path.getsize(video_only) < 1024:
            return PreviewResult(success=False, error=f"미리보기 영상 생성 실패 (stage={stage})")

        # Reject truncated previews (interrupted run): must cover (almost) the whole level
        if seen["est"] > 5.0 and vsec > 0 and vsec < seen["est"] - 3.0:
            return PreviewResult(
                success=False,
                error=f"미리보기 생성이 중간에 중단되었습니다 ({vsec:.0f}s / {seen['est']:.0f}s). 다시 생성해 주세요.",
            )

        # Mux BGM-only audio cleanly onto rough preview so scrubbing/playback matches song timing
        _fix_timeline_output_fps(timeline_json, float(out_fps))
        mixed_wav = os.path.join(work_dir, "preview_bgm.wav")
        muxed = False
        if os.path.isfile(timeline_json):
            try:
                has_bgm = mix_audio_from_timeline(
                    timeline_json_path=timeline_json,
                    output_wav_path=mixed_wav,
                    ffmpeg_path=self.caps.ffmpeg_path,
                    options=AudioMixOptions(include_bgm=True, include_hitsounds=False, include_sfx=False),
                )
                if has_bgm and os.path.isfile(mixed_wav):
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
        if not muxed:
            shutil.copy2(video_only, out_mp4)

        dur = self._probe_duration(out_mp4) or vsec
        self._build_filmstrip(out_mp4, strip_jpg, dur)
        try:
            with open(ok_marker, "w", encoding="utf-8") as mf:
                mf.write(f"{dur:.3f}")
        except Exception:
            pass

        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass

        return PreviewResult(success=True, video_path=out_mp4, filmstrip_path=strip_jpg, duration_sec=dur, cached=False)

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
