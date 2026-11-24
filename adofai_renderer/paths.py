"""Unified resource and writable data path resolution for both source and PyInstaller --onefile builds."""

from __future__ import annotations

import os
import shutil
import sys


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def get_bundle_root() -> str:
    """Directory containing bundled read-only assets (assets/, agent/build/)."""
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        return str(sys._MEIPASS)  # type: ignore[attr-defined]
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_app_root() -> str:
    """Directory where the app lives (workspace root in dev, exe directory when frozen)."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _is_writable_dir(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_test.tmp")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except Exception:
        return False


def get_data_root() -> str:
    """Writable root for cache/ and perf_profile.json."""
    app_root = get_app_root()
    if not is_frozen():
        return app_root
    cand = os.path.join(app_root, "cache")
    if _is_writable_dir(cand):
        return app_root
    fallback = os.path.join(os.path.expandvars(r"%LOCALAPPDATA%"), "AdofaiPvRenderer")
    os.makedirs(fallback, exist_ok=True)
    return fallback


def _read_installer_output_dir() -> str:
    """MP4 export folder chosen in the installer (registry: HKCU, Software/ADOFAI PV Renderer, OutputDir)."""
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\ADOFAI PV Renderer") as k:
            val, _ = winreg.QueryValueEx(k, "OutputDir")
        return str(val or "").strip()
    except Exception:
        return ""


def get_default_output_dir() -> str:
    chosen = _read_installer_output_dir()
    if chosen and _is_writable_dir(chosen):
        return chosen
    app_root = get_app_root()
    cand = os.path.join(app_root, "output")
    if _is_writable_dir(cand):
        return cand
    videos = os.path.join(os.path.expanduser("~"), "Videos", "ADOFAI_PV_Renderer")
    os.makedirs(videos, exist_ok=True)
    return videos


def get_agent_dll_path() -> str:
    """Return an on-disk path to AdofaiRenderAgent.dll (with sibling 0Harmony.dll) usable by Unity Doorstop."""
    bundle_agent = os.path.join(get_bundle_root(), "agent", "build", "AdofaiRenderAgent.dll")
    if not is_frozen():
        return bundle_agent

    # Copy bundled agent DLL + 0Harmony.dll into persistent writable cache dir so Unity Doorstop loads reliably
    dest_dir = os.path.join(get_data_root(), "cache", "agent")
    os.makedirs(dest_dir, exist_ok=True)
    src_dir = os.path.join(get_bundle_root(), "agent", "build")
    for fn in ("AdofaiRenderAgent.dll", "0Harmony.dll"):
        src = os.path.join(src_dir, fn)
        dst = os.path.join(dest_dir, fn)
        if os.path.isfile(src):
            try:
                if (not os.path.isfile(dst)) or (os.path.getsize(src) != os.path.getsize(dst)) or (os.path.getmtime(src) > os.path.getmtime(dst)):
                    shutil.copy2(src, dst)
            except Exception:
                if os.path.isfile(dst):
                    continue
                return bundle_agent
    dest_dll = os.path.join(dest_dir, "AdofaiRenderAgent.dll")
    return dest_dll if os.path.isfile(dest_dll) else bundle_agent
