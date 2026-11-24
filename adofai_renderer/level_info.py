"""Tolerant .adofai level metadata parser for GUI/CLI preview."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import List


@dataclass
class AdofaiLevelInfo:
    level_path: str
    artist: str = ""
    song: str = ""
    author: str = ""
    bpm: float = 100.0
    pitch: float = 100.0
    offset_ms: float = 0.0
    version: int = 0
    tile_count: int = 0
    action_count: int = 0
    decoration_count: int = 0
    song_filename: str = ""
    song_path: str = ""
    hitsound: str = "Kick"
    filters: List[str] = field(default_factory=list)
    reference_mp4s: List[str] = field(default_factory=list)
    # Normalized (0.0..1.0) chart event markers: list of (pos_ratio, kind) where kind in ("filter", "camera", "speed")
    event_markers: List[tuple[float, str]] = field(default_factory=list)

    @property
    def display_title(self) -> str:
        clean_artist = re.sub(r"<[^>]+>", "", self.artist).strip()
        clean_song = re.sub(r"<[^>]+>", "", self.song).strip()
        if clean_artist and clean_song:
            return f"{clean_artist} - {clean_song}"
        if clean_song:
            return clean_song
        folder = os.path.basename(os.path.dirname(self.level_path))
        return f"{folder} ({os.path.basename(self.level_path)})" if folder else os.path.basename(self.level_path)


def _tolerant_json_loads(raw: str) -> dict:
    # Remove trailing commas before } or ], double commas, and missing commas between ]/} and next "key"
    s = re.sub(r",\s*([}\]])", r"\1", raw)
    s = re.sub(r",\s*,+", ",", s)
    s = re.sub(r'([\]}])\s*(")', r'\1,\2', s)
    try:
        return json.loads(s, strict=False)
    except json.JSONDecodeError:
        # Fallback: extract settings block alone if chart has exotic formatting
        m = re.search(r'"settings"\s*:\s*\{[^{}]*\}', s, flags=re.DOTALL)
        if m:
            try:
                return json.loads("{" + m.group(0) + "}", strict=False)
            except Exception:
                pass
        return {}


def parse_adofai_info(level_path: str) -> AdofaiLevelInfo:
    info = AdofaiLevelInfo(level_path=os.path.abspath(level_path))
    level_dir = os.path.dirname(info.level_path)

    if os.path.isdir(level_dir):
        for fname in sorted(os.listdir(level_dir)):
            if fname.lower().endswith((".mp4", ".mkv")):
                info.reference_mp4s.append(os.path.join(level_dir, fname))

    try:
        with open(info.level_path, "r", encoding="utf-8-sig", errors="replace") as f:
            raw = f.read()
        data = _tolerant_json_loads(raw)
    except Exception:
        return info

    settings = data.get("settings", {}) or {}
    info.artist = str(settings.get("artist", ""))
    info.song = str(settings.get("song", ""))
    info.author = str(settings.get("author", ""))
    info.bpm = float(settings.get("bpm", 100.0) or 100.0)
    info.pitch = float(settings.get("pitch", 100.0) or 100.0)
    info.offset_ms = float(settings.get("offset", 0.0) or 0.0)
    info.version = int(settings.get("version", 0) or 0)
    info.song_filename = str(settings.get("songFilename", ""))
    info.hitsound = str(settings.get("hitsound", "Kick"))
    if info.song_filename:
        info.song_path = os.path.join(level_dir, info.song_filename)

    if "angleData" in data and isinstance(data["angleData"], list):
        info.tile_count = len(data["angleData"])
    elif "pathData" in data and isinstance(data["pathData"], str):
        info.tile_count = len(data["pathData"])

    actions = data.get("actions", []) or []
    decorations = data.get("decorations", []) or []
    info.action_count = len(actions)
    info.decoration_count = len(decorations)

    filters = sorted({
        str(a.get("filter"))
        for a in actions
        if a.get("eventType") in ("SetFilter", "SetFilterAdvanced") and a.get("filter")
    })
    info.filters = filters

    # Collect normalized timeline markers for major chart events (filters, camera moves, speed changes)
    denom = float(max(1, info.tile_count))
    markers: List[tuple[float, str]] = []
    seen_buckets = set()
    for a in actions:
        et = a.get("eventType", "")
        kind = ""
        if et in ("SetFilter", "SetFilterAdvanced", "Bloom", "Flash"):
            kind = "filter"
        elif et == "MoveCamera":
            kind = "camera"
        elif et == "SetSpeed":
            kind = "speed"
        if not kind:
            continue
        fl = a.get("floor", 0)
        try:
            ratio = max(0.0, min(1.0, float(fl) / denom))
        except Exception:
            continue
        bucket = (int(ratio * 240), kind)
        if bucket not in seen_buckets:
            seen_buckets.add(bucket)
            markers.append((ratio, kind))
    info.event_markers = markers
    return info
