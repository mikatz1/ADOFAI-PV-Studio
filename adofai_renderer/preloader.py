"""Welcome-card startup preloader: pre-initializes bottleneck subsystems with zero quality loss."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from .gpu_detect import HardwareCapabilities, detect_hardware, find_adofai_dir
from .level_info import AdofaiLevelInfo, parse_adofai_info
from .level_loader import estimate_level_duration_sec, find_adofai_in_directory
from .paths import get_agent_dll_path, get_default_output_dir
from .render_runner import CACHE_ROOT, PROJECT_ROOT, RenderRunner


@dataclass
class PreloadResult:
    caps: HardwareCapabilities
    adofai_dir: str = ""
    doorstop_ready: bool = False
    agent_dll: str = ""
    sample_levels: List[Tuple[str, str, AdofaiLevelInfo, float]] = field(default_factory=list)
    warmed_mb: float = 0.0
    elapsed_sec: float = 0.0
    warnings: List[str] = field(default_factory=list)


def _warm_file_os_cache(paths: List[str], max_mb: float = 96.0) -> float:
    """Sequentially touch key engine binaries/metadata into OS file page cache to cut cold-launch latency."""
    total_bytes = 0
    budget = int(max_mb * 1024 * 1024)
    buf_size = 1024 * 1024
    for p in paths:
        if not p or not os.path.isfile(p):
            continue
        try:
            with open(p, "rb") as f:
                while total_bytes < budget:
                    chunk = f.read(buf_size)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
        except Exception:
            pass
    return total_bytes / (1024.0 * 1024.0)


def run_startup_preload(
    progress_cb: Optional[Callable[[int, int, str, str, float], None]] = None,
) -> PreloadResult:
    """Execute staged preloading of hardware, FFmpeg, ADOFAI engine binaries, agent DLL, and samples."""
    t0 = time.monotonic()
    total_steps = 5
    warnings: List[str] = []

    def emit(step: int, title: str, detail: str, pct: float) -> None:
        if progress_cb:
            progress_cb(step, total_steps, title, detail, pct)

    # Step 1: Cache folders & Steam ADOFAI / Doorstop discovery
    emit(1, "엔진 경로 확인", "Steam ADOFAI 및 Unity Doorstop 구성 확인 중...", 10.0)
    for sub in ("jobs", "sfx", "previews", "extracted_zips"):
        os.makedirs(os.path.join(CACHE_ROOT, sub), exist_ok=True)
    os.makedirs(get_default_output_dir(), exist_ok=True)

    adofai_dir = find_adofai_dir() or ""
    doorstop_ready = False
    if adofai_dir:
        doorstop_ready = os.path.isfile(os.path.join(adofai_dir, "winhttp.dll"))
        if not doorstop_ready:
            warnings.append("ADOFAI 폴더에 winhttp.dll(Unity Doorstop)이 감지되지 않았습니다.")
    else:
        warnings.append("Steam ADOFAI 설치 경로를 자동 감지하지 못했습니다. 파일 메뉴에서 경로를 지정해 주세요.")

    # Step 2: Hardware GPU, VRAM & FFmpeg encoder verification
    emit(2, "GPU · 인코더 프로브", "외장/내장 GPU 및 FFmpeg 하드웨어 인코더 실동작 검증 중...", 32.0)
    caps = detect_hardware(verify_hw=True)

    # Step 3: Verify / compile C# offline render agent DLL
    emit(3, "오프라인 렌더 에이전트", "AdofaiRenderAgent.dll 무결성 및 빌드 상태 확인 중...", 56.0)
    agent_dll = get_agent_dll_path()
    if adofai_dir and not os.path.isfile(agent_dll):
        try:
            runner = RenderRunner(caps=caps)
            agent_dll = runner.ensure_agent_built(adofai_dir)
        except Exception as ex:
            warnings.append(f"C# 에이전트 자동 빌드 실패: {ex}")

    # Step 4: Pre-warm critical Unity & ADOFAI binaries into OS page cache (0 quality loss)
    emit(4, "Unity 엔진 프리워밍", "핵심 UnityPlayer · Assembly-CSharp 바이너리 메모리 캐싱 중...", 78.0)
    warm_targets = [agent_dll]
    if adofai_dir:
        data_dir = os.path.join(adofai_dir, "A Dance of Fire and Ice_Data")
        warm_targets.extend([
            os.path.join(adofai_dir, "UnityPlayer.dll"),
            os.path.join(data_dir, "Managed", "Assembly-CSharp.dll"),
            os.path.join(data_dir, "Managed", "Assembly-CSharp-firstpass.dll"),
            os.path.join(data_dir, "globalgamemanagers"),
            os.path.join(data_dir, "globalgamemanagers.assets"),
        ])
    warmed_mb = _warm_file_os_cache(warm_targets, max_mb=96.0)

    # Step 5: Pre-scan workspace sample levels & metadata
    emit(5, "워크스페이스 초기화", "로컬 샘플 레벨 메타데이터 및 타임라인 길이 분석 중...", 94.0)
    samples: List[Tuple[str, str, AdofaiLevelInfo, float]] = []
    sample_root = os.path.join(PROJECT_ROOT, "level")
    if os.path.isdir(sample_root):
        for entry in sorted(os.listdir(sample_root)):
            d = os.path.join(sample_root, entry)
            if not os.path.isdir(d):
                continue
            cands = find_adofai_in_directory(d)
            if cands:
                info = parse_adofai_info(cands[0])
                dur = estimate_level_duration_sec(info, caps.ffprobe_path)
                samples.append((entry, cands[0], info, dur))

    elapsed = time.monotonic() - t0
    emit(5, "준비 완료", f"모든 시스템 준비 완료 ({elapsed:.1f}s · 캐시 {warmed_mb:.0f}MB)", 100.0)

    return PreloadResult(
        caps=caps,
        adofai_dir=adofai_dir,
        doorstop_ready=doorstop_ready,
        agent_dll=agent_dll,
        sample_levels=samples,
        warmed_mb=warmed_mb,
        elapsed_sec=elapsed,
        warnings=warnings,
    )
