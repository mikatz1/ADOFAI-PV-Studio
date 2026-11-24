"""Hardware GPU, VRAM, FFmpeg encoder, and Steam ADOFAI path auto-detection with low-spec fallbacks."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class GpuDeviceInfo:
    name: str
    vendor: str  # "nvidia", "intel", "amd", "other"
    is_discrete: bool
    vram_mb: int = 0


@dataclass
class HardwareCapabilities:
    ffmpeg_path: str
    ffprobe_path: str = "ffprobe"
    gpus: List[GpuDeviceInfo] = field(default_factory=list)
    available_encoders: List[str] = field(default_factory=list)
    verified_encoders: List[str] = field(default_factory=list)
    has_nvidia_cuda: bool = False
    has_intel_igpu: bool = False
    has_amd_gpu: bool = False
    max_vram_mb: int = 0
    low_spec_recommended: bool = False
    recommended_backend: str = "cpu"  # "nvenc", "qsv", "amf", "d3d12va", "cpu"
    recommended_label: str = "CPU (libx264)"


def find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    winget_root = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
    if os.path.isdir(winget_root):
        for root, _dirs, files in os.walk(winget_root):
            if "ffmpeg.exe" in files:
                return os.path.join(root, "ffmpeg.exe")
    return "ffmpeg"


def find_ffprobe(ffmpeg_path: str = "") -> str:
    if ffmpeg_path and os.path.isfile(ffmpeg_path):
        cand = os.path.join(os.path.dirname(ffmpeg_path), "ffprobe.exe")
        if os.path.isfile(cand):
            return cand
    found = shutil.which("ffprobe")
    return found or "ffprobe"


def find_adofai_dir() -> Optional[str]:
    """Auto-detect Steam 'A Dance of Fire and Ice' installation folder."""
    candidates: List[str] = []

    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam_path, _ = winreg.QueryValueEx(key, "SteamPath")
            if steam_path:
                candidates.append(os.path.normpath(steam_path))
    except Exception:
        pass

    candidates.extend([
        r"C:\Program Files (x86)\Steam",
        r"C:\Program Files\Steam",
        r"D:\Steam",
        r"D:\SteamLibrary",
        r"E:\Steam",
        r"E:\SteamLibrary",
        r"F:\SteamLibrary",
    ])

    seen = set()
    for base in candidates:
        norm = os.path.normcase(os.path.normpath(base))
        if norm in seen or not os.path.isdir(base):
            continue
        seen.add(norm)

        direct = os.path.join(base, "steamapps", "common", "A Dance of Fire and Ice")
        if os.path.isfile(os.path.join(direct, "A Dance of Fire and Ice.exe")):
            return direct

        vdf = os.path.join(base, "steamapps", "libraryfolders.vdf")
        if os.path.isfile(vdf):
            try:
                text = open(vdf, "r", encoding="utf-8", errors="ignore").read()
                for m in re.finditer(r'"path"\s+"([^"]+)"', text):
                    lib = m.group(1).replace("\\\\", "\\")
                    path = os.path.join(lib, "steamapps", "common", "A Dance of Fire and Ice")
                    if os.path.isfile(os.path.join(path, "A Dance of Fire and Ice.exe")):
                        return path
            except Exception:
                pass
    return None


def _query_nvidia_vram_mb() -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2 and parts[1].isdigit():
                    out[parts[0].lower()] = int(parts[1])
    except Exception:
        pass
    return out


def _detect_gpus() -> List[GpuDeviceInfo]:
    gpus: List[GpuDeviceInfo] = []
    nv_vram = _query_nvidia_vram_mb()
    try:
        res = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_VideoController | ForEach-Object { \"$($_.Name)|$($_.AdapterRAM)\" }",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|", 1)
            name = parts[0].strip()
            if not name:
                continue
            raw_ram = 0
            if len(parts) > 1 and parts[1].strip().isdigit():
                raw_ram = int(parts[1].strip()) // (1024 * 1024)
            low = name.lower()
            vram_mb = nv_vram.get(low, raw_ram)
            if "nvidia" in low or "geforce" in low or "rtx" in low or "gtx" in low or "quadro" in low:
                gpus.append(GpuDeviceInfo(name=name, vendor="nvidia", is_discrete=True, vram_mb=vram_mb))
            elif "intel" in low:
                is_disc = ("arc a" in low or "arc b" in low) and "140t" not in low and "130t" not in low
                gpus.append(GpuDeviceInfo(name=name, vendor="intel", is_discrete=is_disc, vram_mb=vram_mb))
            elif "amd" in low or "radeon" in low:
                is_disc = "rx " in low or "pro " in low
                gpus.append(GpuDeviceInfo(name=name, vendor="amd", is_discrete=is_disc, vram_mb=vram_mb))
            else:
                gpus.append(GpuDeviceInfo(name=name, vendor="other", is_discrete=False, vram_mb=vram_mb))
    except Exception:
        pass
    return gpus


def _list_ffmpeg_encoders(ffmpeg_path: str) -> List[str]:
    try:
        res = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        found = []
        for enc in [
            "h264_nvenc", "hevc_nvenc",
            "h264_qsv", "hevc_qsv",
            "h264_amf", "hevc_amf",
            "h264_d3d12va", "hevc_d3d12va",
            "libx264", "libx265",
        ]:
            if re.search(rf"\b{re.escape(enc)}\b", res.stdout):
                found.append(enc)
        return found
    except Exception:
        return ["libx264"]


def test_encoder_usable(ffmpeg_path: str, encoder: str, width: int = 256, height: int = 256) -> bool:
    """Verify an FFmpeg encoder initializes cleanly at the given resolution with a 2-frame test."""
    pix_fmt = "nv12" if (encoder.endswith("_qsv") or encoder.endswith("_d3d12va")) else "yuv420p"
    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"color=c=black:s={width}x{height}:r=30:d=0.1",
        "-frames:v", "2",
        "-pix_fmt", pix_fmt,
        "-c:v", encoder,
        "-f", "null",
        "-",
    ]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            timeout=12,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return res.returncode == 0
    except Exception:
        return False


def detect_hardware(verify_hw: bool = True) -> HardwareCapabilities:
    ffmpeg_path = find_ffmpeg()
    ffprobe_path = find_ffprobe(ffmpeg_path)
    gpus = _detect_gpus()
    encoders = _list_ffmpeg_encoders(ffmpeg_path)

    has_nv = any(g.vendor == "nvidia" for g in gpus)
    has_intel = any(g.vendor == "intel" for g in gpus)
    has_amd = any(g.vendor == "amd" for g in gpus)
    max_vram = max((g.vram_mb for g in gpus), default=0)

    verified: List[str] = []
    if verify_hw:
        candidates = []
        if has_nv:
            candidates += ["h264_nvenc", "hevc_nvenc"]
        if has_intel:
            candidates += ["h264_qsv", "hevc_qsv"]
        if has_amd:
            candidates += ["h264_amf", "hevc_amf"]
        candidates += ["h264_d3d12va", "libx264", "libx265"]

        for enc in candidates:
            if enc in encoders and enc not in verified:
                if test_encoder_usable(ffmpeg_path, enc):
                    verified.append(enc)
    else:
        verified = list(encoders)

    has_any_hw_enc = any(
        e.endswith(("_nvenc", "_qsv", "_amf", "_d3d12va")) for e in verified
    )
    has_discrete = any(g.is_discrete for g in gpus)
    low_spec = (not has_discrete and max_vram > 0 and max_vram < 2048) or (not has_any_hw_enc)

    caps = HardwareCapabilities(
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        gpus=gpus,
        available_encoders=encoders,
        verified_encoders=verified,
        has_nvidia_cuda=has_nv and ("h264_nvenc" in verified),
        has_intel_igpu=has_intel,
        has_amd_gpu=has_amd,
        max_vram_mb=max_vram,
        low_spec_recommended=low_spec,
    )

    if "h264_nvenc" in verified:
        nv_name = next((g.name for g in gpus if g.vendor == "nvidia"), "NVIDIA GPU")
        caps.recommended_backend = "nvenc"
        caps.recommended_label = f"NVIDIA CUDA / NVENC ({nv_name})"
    elif "h264_qsv" in verified:
        igpu_name = next((g.name for g in gpus if g.vendor == "intel"), "Intel iGPU")
        caps.recommended_backend = "qsv"
        caps.recommended_label = f"Intel QSV ({igpu_name})"
    elif "h264_amf" in verified:
        amd_name = next((g.name for g in gpus if g.vendor == "amd"), "AMD GPU")
        caps.recommended_backend = "amf"
        caps.recommended_label = f"AMD AMF ({amd_name})"
    elif "h264_d3d12va" in verified:
        caps.recommended_backend = "d3d12va"
        caps.recommended_label = "Direct3D 12VA"
    else:
        caps.recommended_backend = "cpu"
        caps.recommended_label = "CPU Software (libx264)"

    return caps


def resolve_encoder_fallback_chain(
    caps: HardwareCapabilities,
    gpu_mode: str = "auto",   # "auto", "nvenc", "igpu", "cpu"
    codec: str = "h264",      # "h264", "hevc"
) -> List[Tuple[str, str]]:
    """Return an ordered fallback chain of (ffmpeg_encoder, label), always terminating in CPU software."""
    prefix = "hevc" if codec.lower() in ("h265", "hevc") else "h264"
    sw = "libx265" if prefix == "hevc" else "libx264"
    ver = caps.verified_encoders or caps.available_encoders
    chain: List[Tuple[str, str]] = []

    def add_if_ver(enc: str, label: str) -> None:
        if enc in ver and all(x[0] != enc for x in chain):
            chain.append((enc, label))

    if gpu_mode == "cpu":
        chain.append((sw, f"CPU ({sw})"))
    elif gpu_mode == "nvenc":
        add_if_ver(f"{prefix}_nvenc", f"NVIDIA NVENC ({prefix}_nvenc)")
        add_if_ver(f"{prefix}_qsv", f"Intel QSV 폴백 ({prefix}_qsv)")
        add_if_ver(f"{prefix}_amf", f"AMD AMF 폴백 ({prefix}_amf)")
        chain.append((sw, f"CPU 안전 폴백 ({sw})"))
    elif gpu_mode == "igpu":
        add_if_ver(f"{prefix}_qsv", f"Intel iGPU QSV ({prefix}_qsv)")
        add_if_ver(f"{prefix}_amf", f"AMD AMF ({prefix}_amf)")
        add_if_ver(f"{prefix}_d3d12va", f"D3D12VA ({prefix}_d3d12va)")
        chain.append((sw, f"CPU 안전 폴백 ({sw})"))
    else:
        add_if_ver(f"{prefix}_nvenc", f"NVIDIA CUDA / NVENC ({prefix}_nvenc)")
        add_if_ver(f"{prefix}_qsv", f"Intel QSV ({prefix}_qsv)")
        add_if_ver(f"{prefix}_amf", f"AMD AMF ({prefix}_amf)")
        add_if_ver(f"{prefix}_d3d12va", f"Direct3D 12VA ({prefix}_d3d12va)")
        chain.append((sw, f"CPU 소프트웨어 ({sw})"))

    # Ultimate guarantee: if libx265 isn't available, fall back to libx264
    if sw == "libx265" and "libx264" in (caps.available_encoders or ["libx264"]):
        if all(x[0] != "libx264" for x in chain):
            chain.append(("libx264", "CPU 최후 폴백 (libx264)"))
    return chain


def resolve_video_encoder(
    caps: HardwareCapabilities,
    gpu_mode: str = "auto",
    codec: str = "h264",
) -> Tuple[str, str]:
    chain = resolve_encoder_fallback_chain(caps, gpu_mode, codec)
    return chain[0]


def build_ffmpeg_video_args(
    width: int,
    height: int,
    fps: int,
    output_path: str,
    encoder: str,
    quality: str = "high",  # "ultra" (High bitrate), "high" (Medium bitrate), "balanced" (Low bitrate)
    max_cpu_cores: int = 0, # 0 = auto, >0 = explicit FFmpeg -threads N
) -> str:
    """Build FFmpeg CLI argument string for streaming raw RGBA frames -> encoded video-only file."""
    cq_map = {"ultra": 15, "high": 19, "balanced": 24}
    cq = cq_map.get(quality, 19)

    is_mkv = output_path.lower().endswith(".mkv")
    movflags = "" if is_mkv else " -movflags +faststart"
    threads_opt = f" -threads {int(max_cpu_cores)}" if max_cpu_cores and int(max_cpu_cores) > 0 else ""

    if encoder.endswith("_nvenc"):
        nv_preset = {"ultra": "p6", "high": "p4", "balanced": "p3"}.get(quality, "p4")
        # NVENC accepts rgb0 (same 4-byte RGBA layout from Unity) natively and converts to yuv420p on GPU hardware,
        # bypassing CPU swscale for 2.3x-2.6x higher throughput with identical yuv420p High/Main output.
        in_pix = "rgb0"
        enc_opts = f"-c:v {encoder} -preset {nv_preset} -tune hq -rc vbr -cq {cq} -b:v 0"
    elif encoder.endswith("_qsv"):
        in_pix = "rgba"
        enc_opts = f"-c:v {encoder} -preset medium -global_quality {cq} -pix_fmt nv12"
    elif encoder.endswith("_amf"):
        in_pix = "rgba"
        enc_opts = f"-c:v {encoder} -quality quality -rc cqp -qp_i {cq} -qp_p {cq} -pix_fmt yuv420p"
    elif encoder.endswith("_d3d12va"):
        in_pix = "rgba"
        enc_opts = f"-c:v {encoder} -qp {cq} -pix_fmt nv12"
    elif encoder == "libx265":
        in_pix = "rgba"
        enc_opts = f"-c:v libx265 -preset medium -crf {cq + 2} -pix_fmt yuv420p"
    else:
        in_pix = "rgba"
        enc_opts = f"-c:v libx264 -preset medium -crf {cq} -pix_fmt yuv420p"

    quoted_out = output_path.replace('"', '\\"')
    return (
        f"-y -hide_banner -loglevel warning{threads_opt} "
        f"-f rawvideo -pix_fmt {in_pix} -s {width}x{height} -r {fps} -i - "
        f"-fps_mode cfr -r {fps} "
        f"{enc_opts}{threads_opt}{movflags} \"{quoted_out}\""
    )


def build_ffmpeg_preview_args(
    width: int,
    height: int,
    fps: int,
    output_path: str,
    encoder: str = "libx264",
) -> str:
    """Build fast, seek-friendly FFmpeg args for proxy video generation (144p..720p, 5..60fps)."""
    gop = max(2, fps)
    cq = 26 if height >= 480 else 28
    if encoder == "h264_nvenc":
        in_pix = "rgb0"
        enc_opts = f"-c:v h264_nvenc -preset p1 -rc vbr -cq {cq} -b:v 0 -g {gop}"
    else:
        in_pix = "rgba"
        enc_opts = f"-c:v libx264 -preset ultrafast -crf {cq} -g {gop} -pix_fmt yuv420p"

    quoted_out = output_path.replace('"', '\\"')
    return (
        f"-y -hide_banner -loglevel warning "
        f"-f rawvideo -pix_fmt {in_pix} -s {width}x{height} -r {fps} -i - "
        f"-fps_mode cfr -r {fps} "
        f"{enc_opts} -movflags +faststart \"{quoted_out}\""
    )
