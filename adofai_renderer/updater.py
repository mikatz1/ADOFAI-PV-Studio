"""GitHub Releases based updater: check for a newer release, download the installer, verify it and run it."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from .version import GITHUB_REPO, __version__

API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_ALLOWED_HOSTS = (
    "https://github.com/",
    "https://objects.githubusercontent.com/",
    "https://release-assets.githubusercontent.com/",
)
_UA = "ADOFAI-PV-Studio-Updater"


@dataclass
class UpdateInfo:
    version: str
    notes: str
    page_url: str
    asset_name: str = ""
    asset_url: str = ""
    asset_size: int = 0
    sha256: str = ""


def parse_version(text: str) -> Tuple[int, ...]:
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(remote: str, local: str = __version__) -> bool:
    a, b = list(parse_version(remote)), list(parse_version(local))
    n = max(len(a), len(b))
    a += [0] * (n - len(a))
    b += [0] * (n - len(b))
    return a > b


def check_for_update(timeout: float = 8.0) -> Optional[UpdateInfo]:
    """Return UpdateInfo if a newer release exists, else None. Raises on network/API errors."""
    req = urllib.request.Request(API_URL, headers={"Accept": "application/vnd.github+json", "User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        if ex.code == 404:  # no release published yet
            return None
        raise
    tag = str(data.get("tag_name", ""))
    if data.get("draft") or data.get("prerelease") or not is_newer(tag):
        return None
    info = UpdateInfo(version=tag.lstrip("vV"), notes=str(data.get("body") or ""), page_url=str(data.get("html_url") or ""))
    for a in data.get("assets", []):
        name = str(a.get("name", ""))
        if re.search(r"setup.*\.exe$", name, re.I):
            info.asset_name = name
            info.asset_url = str(a.get("browser_download_url", ""))
            info.asset_size = int(a.get("size", 0) or 0)
            digest = str(a.get("digest", "") or "")
            if digest.startswith("sha256:"):
                info.sha256 = digest.split(":", 1)[1].lower()
            break
    return info


def download_installer(
    info: UpdateInfo,
    progress: Optional[Callable[[int, int], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> str:
    """Download the installer asset to a temp dir, verifying host, size and (when published) SHA-256."""
    if not info.asset_url or not info.asset_url.startswith(_ALLOWED_HOSTS):
        raise RuntimeError("업데이트 설치 파일 주소를 확인할 수 없습니다.")
    dest_dir = tempfile.mkdtemp(prefix="adofai_pv_update_")
    dest = os.path.join(dest_dir, os.path.basename(info.asset_name) or "setup.exe")
    req = urllib.request.Request(info.asset_url, headers={"User-Agent": _UA})
    h = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or info.asset_size or 0)
        while True:
            if cancelled and cancelled():
                raise RuntimeError("다운로드가 취소되었습니다.")
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if info.asset_size and done != info.asset_size:
        raise RuntimeError("다운로드한 파일 크기가 올바르지 않습니다.")
    if info.sha256 and h.hexdigest().lower() != info.sha256:
        raise RuntimeError("다운로드한 파일의 무결성 검증(SHA-256)에 실패했습니다.")
    return dest


def launch_installer(path: str) -> None:
    """Start the Inno Setup installer detached (it closes the running app and relaunches it afterwards)."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(
        [path, "/SILENT", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS", "/NORESTART"],
        creationflags=flags,
        close_fds=True,
    )
