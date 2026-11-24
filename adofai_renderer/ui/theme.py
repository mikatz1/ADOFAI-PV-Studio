"""Dual-Theme System (Studio Dark & Pastel Light), PyInstaller-safe assets & vector icons."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Dict

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

from ..paths import get_data_root

SUPPORTED_THEMES = ("dark", "pastel_light")
_REG_SUBKEY = r"Software\ADOFAI PV Renderer"
_REG_THEME_VALUE = "Theme"
_current_theme: str | None = None


@dataclass(frozen=True)
class ThemePalette:
    name: str
    is_light: bool

    # Core surfaces
    bg_window: str
    bg_panel: str
    bg_inspector: str
    bg_inspector_footer: str
    bg_inset: str
    bg_input: str
    bg_canvas: str
    bg_stage: str
    bg_console: str
    bg_timeline_dock: str
    bg_est_box: str

    # Pane header
    hdr_bg: str
    hdr_border: str
    hdr_title: str
    hdr_meta: str
    hdr_close_fg: str
    hdr_close_hover_fg: str
    hdr_close_hover_bg: str

    # Borders & separators
    border_subtle: str
    border_default: str
    border_strong: str
    sep_color: str
    splitter_bg: str
    splitter_dot: str

    # Typography
    text_primary: str
    text_secondary: str
    text_muted: str
    text_mono: str
    text_on_accent: str

    # Accents (Pastel Sky Blue + Pastel Pink in Light; Classic Studio Blue in Dark)
    accent_primary: str
    accent_hover: str
    accent_pressed: str
    accent_status_blue: str
    accent_pink: str
    accent_pink_soft: str
    accent_blue_soft: str
    accent_playhead: str
    accent_success: str
    accent_danger: str

    # Vector icons
    icon_color: str
    icon_muted: str

    # Viewport overlay & subtle pattern
    overlay_bg: str
    overlay_border: str
    overlay_title: str
    overlay_sub: str
    vp_stage_border: str
    vp_grid_dot: str

    # Stereo dB VU Meter
    vu_bg: str
    vu_border: str
    vu_well_bg: str
    vu_well_border: str
    vu_tick: str
    vu_label: str
    vu_low_on: str
    vu_low_off: str
    vu_mid_on: str
    vu_mid_off: str
    vu_high_on: str
    vu_high_off: str

    # 3-Lane Timeline
    tl_bg: str
    tl_gutter_bg: str
    tl_gutter_border: str
    tl_gutter_sep: str
    tl_bpm_text: str
    tl_track_text: str
    tl_track_sub: str
    tl_ruler_bg: str
    tl_ruler_major: str
    tl_ruler_half: str
    tl_ruler_minor: str
    tl_ruler_text: str
    tl_well_v1: str
    tl_well_fx: str
    tl_well_a1: str
    tl_well_border: str
    tl_v1_clip_bg: str
    tl_wave_center: str
    tl_wave_active: str
    tl_wave_muted: str
    tl_trim_mask: str
    tl_trim_mask_alpha: int
    tl_sel_trimmed: str
    tl_sel_full: str
    tl_handle_hover: str
    tl_handle_grip: str
    tl_btn_on_bg: str
    tl_btn_on_border: str
    tl_btn_off_bg: str
    tl_btn_off_border: str
    tl_btn_fg_on: str
    tl_btn_fg_off: str
    tl_mute_on_bg: str
    tl_mute_on_border: str
    tl_fx_filter: str
    tl_fx_camera: str
    tl_fx_speed: str

    # Progress, ETA highlight & Console
    prog_bg: str
    prog_border: str
    prog_chunk_start: str
    prog_chunk_end: str
    eta_box_bg: str
    eta_box_border: str
    eta_val_fg: str
    console_bg: str
    console_border: str
    console_meta: str
    log_bg: str
    log_fg: str
    swatch_border: str


DARK_PALETTE = ThemePalette(
    name="dark",
    is_light=False,
    bg_window="#212121",
    bg_panel="#262626",
    bg_inspector="#232323",
    bg_inspector_footer="#202020",
    bg_inset="#191919",
    bg_input="#141414",
    bg_canvas="#0e0e0e",
    bg_stage="#050505",
    bg_console="#0c0c0c",
    bg_timeline_dock="#1c1c1c",
    bg_est_box="#1b1b1b",
    hdr_bg="#292929",
    hdr_border="#161616",
    hdr_title="#c6c6c6",
    hdr_meta="#8a8a8a",
    hdr_close_fg="#8c8c8c",
    hdr_close_hover_fg="#ffffff",
    hdr_close_hover_bg="#3a3a3a",
    border_subtle="#303030",
    border_default="#3b3b3b",
    border_strong="#505050",
    sep_color="#303030",
    splitter_bg="#161616",
    splitter_dot="#4c4c4c",
    text_primary="#e2e2e2",
    text_secondary="#9a9a9a",
    text_muted="#6d6d6d",
    text_mono="#d6d6d6",
    text_on_accent="#ffffff",
    accent_primary="#356899",
    accent_hover="#4079b0",
    accent_pressed="#2b5680",
    accent_status_blue="#4a9eff",
    accent_pink="#d8709e",
    accent_pink_soft="#3a2530",
    accent_blue_soft="#1f3145",
    accent_playhead="#dfa142",
    accent_success="#5a9e6f",
    accent_danger="#9c363e",
    icon_color="#d4d4d4",
    icon_muted="#7a7a7a",
    overlay_bg="rgba(24, 24, 24, 232)",
    overlay_border="#353535",
    overlay_title="#e2e2e2",
    overlay_sub="#909090",
    vp_stage_border="#282828",
    vp_grid_dot="#1a1a1a",
    vu_bg="#141414",
    vu_border="#242424",
    vu_well_bg="#0c0c0c",
    vu_well_border="#2b2b2b",
    vu_tick="#3e3e3e",
    vu_label="#787878",
    vu_low_on="#4aa867",
    vu_low_off="#19231c",
    vu_mid_on="#d4ad42",
    vu_mid_off="#282419",
    vu_high_on="#d94c4c",
    vu_high_off="#2a1a1a",
    tl_bg="#151515",
    tl_gutter_bg="#1f1f1f",
    tl_gutter_border="#2c2c2c",
    tl_gutter_sep="#292929",
    tl_bpm_text="#7e7e7e",
    tl_track_text="#c8c8c8",
    tl_track_sub="#a0a0a0",
    tl_ruler_bg="#1b1b1b",
    tl_ruler_major="#8a8a8a",
    tl_ruler_half="#626262",
    tl_ruler_minor="#484848",
    tl_ruler_text="#a8a8a8",
    tl_well_v1="#111111",
    tl_well_fx="#141414",
    tl_well_a1="#111111",
    tl_well_border="#282828",
    tl_v1_clip_bg="#1c2228",
    tl_wave_center="#232927",
    tl_wave_active="#628b79",
    tl_wave_muted="#38423e",
    tl_trim_mask="#000000",
    tl_trim_mask_alpha=145,
    tl_sel_trimmed="#4d85ba",
    tl_sel_full="#4a4a4a",
    tl_handle_hover="#7cb5eb",
    tl_handle_grip="#111111",
    tl_btn_on_bg="#2d2d2d",
    tl_btn_on_border="#464646",
    tl_btn_off_bg="#171717",
    tl_btn_off_border="#2a2a2a",
    tl_btn_fg_on="#dadada",
    tl_btn_fg_off="#585858",
    tl_mute_on_bg="#662c30",
    tl_mute_on_border="#803c42",
    tl_fx_filter="#947c58",
    tl_fx_camera="#5c7b94",
    tl_fx_speed="#946060",
    prog_bg="#131313",
    prog_border="#2b2b2b",
    prog_chunk_start="#4a9eff",
    prog_chunk_end="#4a9eff",
    eta_box_bg="#161616",
    eta_box_border="#3b3b3b",
    eta_val_fg="#f0f0f0",
    console_bg="#0c0c0c",
    console_border="#262626",
    console_meta="#8c8c8c",
    log_bg="#0c0c0c",
    log_fg="#a8a8a8",
    swatch_border="#555555",
)

# High-Key Pastel Light Theme
# Dominant crisp whites + cool sky-ice canvas + deep navy typography
# Point colors: Pastel Sky-Blue (#4AAEFF / #78C8FF) & Pastel Pink (#F47CB0 / #FFA8CF)
PASTEL_LIGHT_PALETTE = ThemePalette(
    name="pastel_light",
    is_light=True,
    bg_window="#EDF4FC",
    bg_panel="#FFFFFF",
    bg_inspector="#F7FAFE",
    bg_inspector_footer="#F2F7FD",
    bg_inset="#E9F2FC",
    bg_input="#FFFFFF",
    bg_canvas="#E9F1FA",
    bg_stage="#111927",
    bg_console="#F2F7FF",
    bg_timeline_dock="#F5F9FF",
    bg_est_box="#FFFFFF",
    hdr_bg="#FFFFFF",
    hdr_border="#D4E2F3",
    hdr_title="#243754",
    hdr_meta="#6880A0",
    hdr_close_fg="#7D93B0",
    hdr_close_hover_fg="#FFFFFF",
    hdr_close_hover_bg="#F27BAE",
    border_subtle="#D6E4F4",
    border_default="#C2D6EE",
    border_strong="#9ABAE0",
    sep_color="#DCE7F5",
    splitter_bg="#DCE7F5",
    splitter_dot="#9AB5D6",
    text_primary="#233552",
    text_secondary="#5A7294",
    text_muted="#8CA2BF",
    text_mono="#2B446B",
    text_on_accent="#FFFFFF",
    accent_primary="#43A4FF",
    accent_hover="#5EB4FF",
    accent_pressed="#2D8EEB",
    accent_status_blue="#2A93F8",
    accent_pink="#F278AE",
    accent_pink_soft="#FFF0F7",
    accent_blue_soft="#EAF4FF",
    accent_playhead="#F0629D",
    accent_success="#34B38A",
    accent_danger="#EC5E93",
    icon_color="#3B5578",
    icon_muted="#91A6C2",
    overlay_bg="rgba(255, 255, 255, 246)",
    overlay_border="#C3D8F0",
    overlay_title="#223452",
    overlay_sub="#5D7596",
    vp_stage_border="#BFD5EE",
    vp_grid_dot="#CDDEF2",
    vu_bg="#F5F9FF",
    vu_border="#D5E3F3",
    vu_well_bg="#E4EFFA",
    vu_well_border="#C4D8EE",
    vu_tick="#A3BCD8",
    vu_label="#647D9E",
    vu_low_on="#42BE9C",
    vu_low_off="#D7E7F5",
    vu_mid_on="#F7BE4F",
    vu_mid_off="#E7E6DF",
    vu_high_on="#F2679E",
    vu_high_off="#F0DCE7",
    tl_bg="#EEF5FD",
    tl_gutter_bg="#FFFFFF",
    tl_gutter_border="#D4E3F4",
    tl_gutter_sep="#E3EDF8",
    tl_bpm_text="#3992EA",
    tl_track_text="#253957",
    tl_track_sub="#5C7496",
    tl_ruler_bg="#F6FAFF",
    tl_ruler_major="#7AA2CE",
    tl_ruler_half="#A9C4E2",
    tl_ruler_minor="#CBDDF0",
    tl_ruler_text="#587396",
    tl_well_v1="#E7F1FC",
    tl_well_fx="#F4F1FA",
    tl_well_a1="#E9F3FC",
    tl_well_border="#CEE0F3",
    tl_v1_clip_bg="#D6E9FC",
    tl_wave_center="#C4DCF3",
    tl_wave_active="#4CA5F5",
    tl_wave_muted="#B5CBE2",
    tl_trim_mask="#DCE8F6",
    tl_trim_mask_alpha=178,
    tl_sel_trimmed="#42A3FF",
    tl_sel_full="#AEC7E3",
    tl_handle_hover="#F278AE",
    tl_handle_grip="#FFFFFF",
    tl_btn_on_bg="#EAF4FF",
    tl_btn_on_border="#8FC5FA",
    tl_btn_off_bg="#F7FAFE",
    tl_btn_off_border="#D5E3F3",
    tl_btn_fg_on="#2787E8",
    tl_btn_fg_off="#9EB3CC",
    tl_mute_on_bg="#FFE7F1",
    tl_mute_on_border="#F48FB9",
    tl_fx_filter="#EC8BB5",
    tl_fx_camera="#56ADF8",
    tl_fx_speed="#A58CF5",
    prog_bg="#E4EFFA",
    prog_border="#C7DCF1",
    prog_chunk_start="#58BAFF",
    prog_chunk_end="#F58FBC",
    eta_box_bg="#F4F9FF",
    eta_box_border="#C4DBF2",
    eta_val_fg="#1E3352",
    console_bg="#F2F8FF",
    console_border="#C5DCF3",
    console_meta="#5B7496",
    log_bg="#F8FBFF",
    log_fg="#385070",
    swatch_border="#B3C9E2",
)

THEMES: Dict[str, ThemePalette] = {
    "dark": DARK_PALETTE,
    "pastel_light": PASTEL_LIGHT_PALETTE,
}


# ---------------- Theme Persistence (Registry + cache/theme.json) ----------------
def _normalize_theme(name: str) -> str:
    s = (name or "").strip().lower()
    if s in ("light", "pastel", "pastel_light"):
        return "pastel_light"
    if s in ("dark", "studio_dark"):
        return "dark"
    return ""


def _read_registry_theme() -> str:
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_SUBKEY) as k:
            val, _ = winreg.QueryValueEx(k, _REG_THEME_VALUE)
        return _normalize_theme(str(val or ""))
    except Exception:
        return ""


def _write_registry_theme(theme_name: str) -> None:
    if os.name != "nt":
        return
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_SUBKEY) as k:
            winreg.SetValueEx(k, _REG_THEME_VALUE, 0, winreg.REG_SZ, theme_name)
    except Exception:
        pass


def _theme_fallback_file() -> str:
    return os.path.join(get_data_root(), "cache", "theme.json")


def _read_file_theme() -> str:
    try:
        p = _theme_fallback_file()
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return _normalize_theme(str(data.get("theme", "")))
    except Exception:
        pass
    return ""


def _write_file_theme(theme_name: str) -> None:
    try:
        p = _theme_fallback_file()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"theme": theme_name}, f)
    except Exception:
        pass


def get_theme() -> str:
    global _current_theme
    if _current_theme in SUPPORTED_THEMES:
        return _current_theme
    t = _read_registry_theme() or _read_file_theme() or "dark"
    if t not in SUPPORTED_THEMES:
        t = "dark"
    _current_theme = t
    return _current_theme


def set_theme(theme_name: str, persist: bool = True) -> str:
    global _current_theme
    norm = _normalize_theme(theme_name) or "dark"
    _current_theme = norm
    if persist:
        _write_registry_theme(norm)
        _write_file_theme(norm)
    return _current_theme


def get_palette(theme_name: str | None = None) -> ThemePalette:
    name = _normalize_theme(theme_name) if theme_name else get_theme()
    return THEMES.get(name, DARK_PALETTE)


# Backward-compatible module constants (reflect default dark palette)
BG_WINDOW = DARK_PALETTE.bg_window
BG_PANEL = DARK_PALETTE.bg_panel
BG_INSET = DARK_PALETTE.bg_inset
BG_INPUT = DARK_PALETTE.bg_input
BG_CANVAS = DARK_PALETTE.bg_canvas
BG_CONSOLE = DARK_PALETTE.bg_console

BORDER_SUBTLE = DARK_PALETTE.border_subtle
BORDER_DEFAULT = DARK_PALETTE.border_default
BORDER_STRONG = DARK_PALETTE.border_strong

TEXT_PRIMARY = DARK_PALETTE.text_primary
TEXT_SECONDARY = DARK_PALETTE.text_secondary
TEXT_MUTED = DARK_PALETTE.text_muted

ACCENT_PRIMARY = DARK_PALETTE.accent_primary
ACCENT_HOVER = DARK_PALETTE.accent_hover
ACCENT_PRESSED = DARK_PALETTE.accent_pressed
ACCENT_STATUS_BLUE = DARK_PALETTE.accent_status_blue
ACCENT_WARM = DARK_PALETTE.accent_status_blue
ACCENT_PLAYHEAD = DARK_PALETTE.accent_playhead
ACCENT_DANGER = DARK_PALETTE.accent_danger


def get_asset_path(*parts: str) -> str:
    """Resolve bundled asset paths cleanly in both development and PyInstaller --onefile builds."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "assets", *parts)


def _qss_url(path: str) -> str:
    return path.replace("\\", "/")


def _build_dark_qss() -> str:
    p = DARK_PALETTE
    chk_url = _qss_url(get_asset_path("ui", "check.png"))
    chev_url = _qss_url(get_asset_path("ui", "chevron_down.png"))
    up_url = _qss_url(get_asset_path("ui", "arrow_up.png"))
    dn_url = _qss_url(get_asset_path("ui", "arrow_down.png"))

    return f"""
QMainWindow, QDialog {{
    background-color: {p.bg_window};
    color: {p.text_primary};
    font-family: "Segoe UI", "Malgun Gothic", "Yu Gothic UI", "Meiryo", sans-serif;
    font-size: 12px;
}}
QWidget {{
    color: {p.text_primary};
    font-family: "Segoe UI", "Malgun Gothic", "Yu Gothic UI", "Meiryo", sans-serif;
    font-size: 12px;
}}
QMenuBar {{
    background-color: #262626;
    color: #dcdcdc;
    border-bottom: 1px solid #161616;
    padding: 1px 4px;
    spacing: 2px;
}}
QMenuBar::item {{
    background: transparent;
    padding: 4px 9px;
    border-radius: 2px;
}}
QMenuBar::item:selected {{
    background-color: #383838;
    color: #ffffff;
}}
QMenu {{
    background-color: #272727;
    border: 1px solid #3f3f3f;
    padding: 3px 0px;
}}
QMenu::item {{
    padding: 5px 28px 5px 22px;
    color: {p.text_primary};
}}
QMenu::item:selected {{
    background-color: {p.accent_primary};
    color: #ffffff;
}}
QMenu::item:disabled {{
    color: {p.text_muted};
}}
QMenu::separator {{
    height: 1px;
    background: #353535;
    margin: 3px 8px;
}}
QStatusBar {{
    background-color: #1b1b1b;
    color: #a6a6a6;
    border-top: 1px solid #121212;
    font-size: 11px;
}}
QStatusBar::item {{
    border: none;
}}
QSplitter::handle {{
    background-color: #161616;
}}
QSplitter::handle:horizontal {{
    width: 5px;
}}
QSplitter::handle:vertical {{
    height: 5px;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QFrame#panel, QWidget#panel {{
    background-color: {p.bg_panel};
    border: 1px solid {p.border_subtle};
    border-radius: 3px;
}}
QFrame#inspectorPane {{
    background-color: #232323;
    border-left: 1px solid #141414;
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
    background-color: {p.bg_input};
    border: 1px solid #363636;
    border-top-color: #282828;
    border-radius: 2px;
    padding: 1px 6px;
    min-height: 20px;
    color: {p.text_primary};
    selection-background-color: {p.accent_primary};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: #5888b8;
}}
QComboBox {{
    padding: 1px 20px 1px 6px;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 18px;
    border-left: 1px solid #2b2b2b;
    background: #1c1c1c;
}}
QComboBox::down-arrow {{
    image: url("{chev_url}");
    width: 9px;
    height: 9px;
}}
QComboBox QAbstractItemView {{
    background-color: #242424;
    border: 1px solid #3f3f3f;
    selection-background-color: {p.accent_primary};
    color: {p.text_primary};
}}
QSpinBox, QDoubleSpinBox {{
    padding: 1px 18px 1px 6px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 15px;
    margin: 1px 1px 0px 0px;
    background: #1e1e1e;
    border-left: 1px solid #2e2e2e;
    border-bottom: 1px solid #282828;
    border-top-right-radius: 1px;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 15px;
    margin: 0px 1px 1px 0px;
    background: #1e1e1e;
    border-left: 1px solid #2e2e2e;
    border-bottom-right-radius: 1px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: #2c2c2c;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url("{up_url}");
    width: 7px;
    height: 7px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url("{dn_url}");
    width: 7px;
    height: 7px;
}}
QPushButton {{
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #343434, stop:1 #2a2a2a);
    border: 1px solid #414141;
    border-bottom-color: #2b2b2b;
    border-radius: 2px;
    padding: 4px 10px;
    color: {p.text_primary};
}}
QPushButton:hover {{
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3d3d3d, stop:1 #323232);
    border-color: #545454;
}}
QPushButton:pressed {{
    background-color: #232323;
    border-color: #363636;
}}
QPushButton:disabled {{
    background-color: #232323;
    color: #5c5c5c;
    border-color: #2c2c2c;
}}
QPushButton#primaryBtn {{
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3f76ac, stop:1 #2f5e8c);
    border: 1px solid #4e89c4;
    border-bottom-color: #23476b;
    color: #ffffff;
    font-weight: 600;
    padding: 6px 16px;
}}
QPushButton#primaryBtn:hover {{
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #4a84bd, stop:1 #396ca0);
}}
QPushButton#primaryBtn:pressed {{
    background-color: #285178;
}}
QPushButton#primaryBtn:disabled {{
    background-color: #263340;
    border-color: #2e3d4c;
    color: #6d7f91;
}}
QPushButton#dangerBtn {{
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ab3f47, stop:1 #8d3038);
    border: 1px solid #be4d56;
    color: #ffffff;
    font-weight: 600;
}}
QPushButton#dangerBtn:hover {{
    background-color: #b8454e;
}}
QPushButton#toolBtn {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 2px;
    padding: 3px 4px;
    color: #d0d0d0;
}}
QPushButton#toolBtn:hover {{
    background-color: #333333;
    border-color: #444444;
}}
QPushButton#toolBtn:pressed {{
    background-color: #222222;
}}
QPushButton#chipBtn {{
    background-color: #2c2c2c;
    border: 1px solid #3e3e3e;
    border-radius: 2px;
    padding: 2px 8px;
    color: #d6d6d6;
    font-size: 11px;
}}
QPushButton#chipBtn:hover {{
    background-color: #373737;
    border-color: #505050;
    color: #ffffff;
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: #141414;
    border: 1px solid #2e2e2e;
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: #3d3d3d;
    border: 1px solid #484848;
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #dfdfdf, stop:1 #b8b8b8);
    border: 1px solid #181818;
    width: 9px;
    height: 13px;
    margin: -5px 0;
    border-radius: 2px;
}}
QSlider::handle:horizontal:hover {{
    background: #f2f2f2;
}}
QCheckBox {{
    spacing: 6px;
    color: #d2d2d2;
    font-size: 11px;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid #4c4c4c;
    border-radius: 2px;
    background: {p.bg_input};
}}
QCheckBox::indicator:hover {{
    border-color: #666666;
}}
QCheckBox::indicator:checked {{
    background: {p.accent_primary};
    border-color: #568ec7;
    image: url("{chk_url}");
}}
QTabWidget::pane {{
    border: 1px solid {p.border_subtle};
    background-color: {p.bg_panel};
    border-radius: 2px;
    top: -1px;
}}
QTabBar::tab {{
    background-color: #1d1d1d;
    border: 1px solid {p.border_subtle};
    border-bottom: none;
    padding: 5px 14px;
    margin-right: 2px;
    color: {p.text_secondary};
    border-top-left-radius: 2px;
    border-top-right-radius: 2px;
}}
QTabBar::tab:selected {{
    background-color: {p.bg_panel};
    color: {p.text_primary};
    border-color: {p.border_default};
}}
QTabWidget#inspectorTabs::pane {{
    border: none;
    border-top: 1px solid #303030;
    background-color: #232323;
    top: -1px;
}}
QTabWidget#inspectorTabs QTabBar::tab {{
    background-color: #1c1c1c;
    border: 1px solid #2b2b2b;
    border-bottom: none;
    padding: 4px 12px;
    margin-right: 1px;
    color: #909090;
    font-size: 11px;
}}
QTabWidget#inspectorTabs QTabBar::tab:selected {{
    background-color: #232323;
    color: #e2e2e2;
    border-color: #363636;
}}
QProgressBar {{
    background-color: #141414;
    border: 1px solid #2e2e2e;
    border-radius: 2px;
    height: 8px;
    text-align: center;
}}
QProgressBar::chunk {{
    background-color: {p.accent_primary};
    border-radius: 1px;
}}
QLabel#muted {{
    color: {p.text_secondary};
    font-size: 11px;
}}
QLabel#sectionHdr {{
    color: #c2c2c2;
    font-size: 11px;
    font-weight: 600;
}}
QLabel#mono {{
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 11px;
    color: #d4d4d4;
}}
QLabel#timecodeBox {{
    background-color: #121212;
    border: 1px solid #303030;
    border-top-color: #202020;
    border-radius: 2px;
    padding: 2px 7px;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 12px;
    color: #e8e8e8;
}}
QScrollBar:vertical, QScrollBar:horizontal {{
    background: #181818;
    border: none;
}}
QScrollBar:vertical {{
    width: 10px;
}}
QScrollBar:horizontal {{
    height: 10px;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: #3d3d3d;
    min-height: 22px;
    min-width: 22px;
    border-radius: 2px;
    margin: 1px;
}}
QScrollBar::handle:hover {{
    background: #525252;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}
"""


def _build_pastel_light_qss() -> str:
    """Crisp high-key light theme with Pastel Sky-Blue & Pastel Pink accents."""
    p = PASTEL_LIGHT_PALETTE
    chk_url = _qss_url(get_asset_path("ui", "check.png"))
    chev_path = get_asset_path("ui", "chevron_down_light.png")
    if not os.path.isfile(chev_path):
        chev_path = get_asset_path("ui", "chevron_down.png")
    up_path = get_asset_path("ui", "arrow_up_light.png")
    if not os.path.isfile(up_path):
        up_path = get_asset_path("ui", "arrow_up.png")
    dn_path = get_asset_path("ui", "arrow_down_light.png")
    if not os.path.isfile(dn_path):
        dn_path = get_asset_path("ui", "arrow_down.png")
    chev_url = _qss_url(chev_path)
    up_url = _qss_url(up_path)
    dn_url = _qss_url(dn_path)

    return f"""
QMainWindow, QDialog {{
    background-color: {p.bg_window};
    color: {p.text_primary};
    font-family: "Segoe UI", "Malgun Gothic", "Yu Gothic UI", "Meiryo", sans-serif;
    font-size: 12px;
}}
QWidget {{
    color: {p.text_primary};
    font-family: "Segoe UI", "Malgun Gothic", "Yu Gothic UI", "Meiryo", sans-serif;
    font-size: 12px;
}}
QMenuBar {{
    background-color: #FFFFFF;
    color: #273B59;
    border-bottom: 1px solid #D4E2F3;
    padding: 2px 6px;
    spacing: 3px;
}}
QMenuBar::item {{
    background: transparent;
    padding: 4px 10px;
    border-radius: 5px;
    font-weight: 500;
}}
QMenuBar::item:selected {{
    background-color: #E7F3FF;
    color: #1D7CE0;
}}
QMenuBar::item:pressed {{
    background-color: #D5EBFF;
    color: #156BC8;
}}
QMenu {{
    background-color: #FFFFFF;
    border: 1px solid #C4DAF2;
    border-radius: 6px;
    padding: 4px 3px;
}}
QMenu::item {{
    padding: 5px 28px 5px 24px;
    color: {p.text_primary};
    border-radius: 4px;
    margin: 1px 2px;
}}
QMenu::item:selected {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #55B8FF, stop:1 #459FF8);
    color: #FFFFFF;
}}
QMenu::item:disabled {{
    color: {p.text_muted};
}}
QMenu::separator {{
    height: 1px;
    background: #E2ECF8;
    margin: 4px 10px;
}}
QStatusBar {{
    background-color: #FFFFFF;
    color: #546C8E;
    border-top: 1px solid #D4E2F3;
    font-size: 11px;
}}
QStatusBar::item {{
    border: none;
}}
QSplitter::handle {{
    background-color: #DCE7F5;
}}
QSplitter::handle:horizontal {{
    width: 5px;
}}
QSplitter::handle:vertical {{
    height: 5px;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QFrame#panel, QWidget#panel {{
    background-color: #FFFFFF;
    border: 1px solid #D3E2F4;
    border-radius: 7px;
}}
QFrame#inspectorPane {{
    background-color: #F7FAFE;
    border-left: 1px solid #D2E1F3;
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
    background-color: #FFFFFF;
    border: 1px solid #C6DAF0;
    border-bottom-color: #B4CCE8;
    border-radius: 5px;
    padding: 1px 7px;
    min-height: 20px;
    color: {p.text_primary};
    selection-background-color: #5BB8FF;
    selection-color: #FFFFFF;
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
    border-color: #8FC4F8;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid #3FA0FF;
    background-color: #FCFDFE;
}}
QComboBox {{
    padding: 1px 22px 1px 7px;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 20px;
    border-left: 1px solid #DCE8F6;
    background: #F3F8FE;
    border-top-right-radius: 4px;
    border-bottom-right-radius: 4px;
}}
QComboBox::down-arrow {{
    image: url("{chev_url}");
    width: 10px;
    height: 10px;
}}
QComboBox QAbstractItemView {{
    background-color: #FFFFFF;
    border: 1px solid #BDD4EE;
    selection-background-color: #49AAFF;
    selection-color: #FFFFFF;
    color: {p.text_primary};
    outline: 0;
}}
QSpinBox, QDoubleSpinBox {{
    padding: 1px 19px 1px 7px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 16px;
    margin: 1px 1px 0px 0px;
    background: #F3F8FE;
    border-left: 1px solid #DCE8F6;
    border-bottom: 1px solid #E3EDF8;
    border-top-right-radius: 4px;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 16px;
    margin: 0px 1px 1px 0px;
    background: #F3F8FE;
    border-left: 1px solid #DCE8F6;
    border-bottom-right-radius: 4px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: #E2F0FF;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url("{up_url}");
    width: 8px;
    height: 8px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url("{dn_url}");
    width: 8px;
    height: 8px;
}}
QPushButton {{
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #FFFFFF, stop:1 #F2F7FD);
    border: 1px solid #C2D7EE;
    border-bottom: 2px solid #AFC8E5;
    border-radius: 6px;
    padding: 4px 11px;
    color: #243754;
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #FFFFFF, stop:1 #E8F4FF);
    border-color: #79BEFC;
    border-bottom-color: #5EAFF8;
    color: #196FD6;
}}
QPushButton:pressed {{
    background-color: #DFEEFF;
    border-color: #63B0FA;
    border-bottom: 1px solid #63B0FA;
}}
QPushButton:disabled {{
    background-color: #F0F5FB;
    color: #A2B5CE;
    border-color: #DBE6F3;
    border-bottom-color: #D2DFEE;
}}
QPushButton#primaryBtn {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #68C5FF, stop:0.55 #42A3FF, stop:1 #3A96F6);
    border: 1px solid #67BEFF;
    border-bottom: 2px solid #247EE0;
    border-radius: 6px;
    color: #FFFFFF;
    font-weight: 700;
    padding: 6px 16px;
}}
QPushButton#primaryBtn:hover {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #7BCEFF, stop:0.55 #55B0FF, stop:1 #45A1FC);
    border-color: #82CBFF;
    border-bottom-color: #2A89EC;
}}
QPushButton#primaryBtn:pressed {{
    background-color: #2E8FEE;
    border-bottom: 1px solid #2279D6;
}}
QPushButton#primaryBtn:disabled {{
    background-color: #BFDDFB;
    border-color: #B4D4F5;
    border-bottom-color: #A7CBEF;
    color: #F2F8FF;
}}
QPushButton#dangerBtn {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #FF8DBC, stop:1 #EC5D95);
    border: 1px solid #FF9BC4;
    border-bottom: 2px solid #D1477D;
    border-radius: 6px;
    color: #FFFFFF;
    font-weight: 700;
}}
QPushButton#dangerBtn:hover {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #FF9EC7, stop:1 #F46DA1);
}}
QPushButton#dangerBtn:pressed {{
    background-color: #DF4E86;
    border-bottom: 1px solid #C63F74;
}}
QPushButton#toolBtn {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 3px 4px;
    color: #3B5578;
}}
QPushButton#toolBtn:hover {{
    background-color: #E6F2FF;
    border-color: #B6D7F8;
    color: #1D7AE0;
}}
QPushButton#toolBtn:pressed {{
    background-color: #D5E9FF;
    border-color: #8BC4FA;
}}
QPushButton#chipBtn {{
    background-color: #FFF0F6;
    border: 1px solid #F6B9D5;
    border-bottom: 1px solid #EE9EC3;
    border-radius: 10px;
    padding: 2px 9px;
    color: #C94982;
    font-size: 11px;
    font-weight: 600;
}}
QPushButton#chipBtn:hover {{
    background-color: #FFE2EF;
    border-color: #F28CBA;
    color: #B63871;
}}
QPushButton#chipBtn:pressed {{
    background-color: #FFD4E8;
}}
QSlider::groove:horizontal {{
    height: 5px;
    background: #DCE8F6;
    border: 1px solid #C9DBEF;
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5CBCFF, stop:1 #F592BE);
    border: 1px solid #75B8F0;
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: #FFFFFF;
    border: 2px solid #44A3FF;
    width: 10px;
    height: 14px;
    margin: -6px 0;
    border-radius: 5px;
}}
QSlider::handle:horizontal:hover {{
    background: #FFF4F9;
    border-color: #F073AA;
}}
QCheckBox {{
    spacing: 6px;
    color: #293D5C;
    font-size: 11px;
    font-weight: 500;
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid #B6CCE5;
    border-radius: 4px;
    background: #FFFFFF;
}}
QCheckBox::indicator:hover {{
    border-color: #54AEFF;
    background: #F4FAFF;
}}
QCheckBox::indicator:checked {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #62C0FF, stop:1 #3A9AF8);
    border-color: #3491EE;
    image: url("{chk_url}");
}}
QTabWidget::pane {{
    border: 1px solid #C8DCF2;
    background-color: #FFFFFF;
    border-radius: 6px;
    top: -1px;
}}
QTabBar::tab {{
    background-color: #E7F0FA;
    border: 1px solid #CEE0F3;
    border-bottom: none;
    padding: 5px 15px;
    margin-right: 3px;
    color: #5E779A;
    font-weight: 600;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}
QTabBar::tab:hover {{
    background-color: #F2F8FF;
    color: #2A6FC2;
}}
QTabBar::tab:selected {{
    background-color: #FFFFFF;
    color: #1F3556;
    border-color: #C4DBF2;
    border-top: 2px solid #45A5FF;
}}
QTabWidget#inspectorTabs::pane {{
    border: none;
    border-top: 1px solid #D3E2F3;
    background-color: #F7FAFE;
    top: -1px;
}}
QTabWidget#inspectorTabs QTabBar::tab {{
    background-color: #E8F1FB;
    border: 1px solid #D3E2F3;
    border-bottom: none;
    padding: 5px 13px;
    margin-right: 2px;
    color: #637B9D;
    font-size: 11px;
    font-weight: 600;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}
QTabWidget#inspectorTabs QTabBar::tab:selected {{
    background-color: #F7FAFE;
    color: #203556;
    border-color: #C7DBF0;
    border-top: 2px solid #45A5FF;
}}
QProgressBar {{
    background-color: #E4EFFA;
    border: 1px solid #C8DDF2;
    border-radius: 4px;
    height: 8px;
    text-align: center;
}}
QProgressBar::chunk {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5AB9FF, stop:1 #F48CBA);
    border-radius: 3px;
}}
QLabel#muted {{
    color: {p.text_secondary};
    font-size: 11px;
}}
QLabel#sectionHdr {{
    color: #274166;
    font-size: 11px;
    font-weight: 700;
}}
QLabel#mono {{
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 11px;
    color: #2A4369;
}}
QLabel#timecodeBox {{
    background-color: #FFFFFF;
    border: 1px solid #C2D7EE;
    border-left: 3px solid #48A8FF;
    border-radius: 5px;
    padding: 2px 7px;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 12px;
    font-weight: 600;
    color: #1F3556;
}}
QScrollBar:vertical, QScrollBar:horizontal {{
    background: #EFF5FC;
    border: none;
}}
QScrollBar:vertical {{
    width: 10px;
}}
QScrollBar:horizontal {{
    height: 10px;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: #C2D6EC;
    min-height: 22px;
    min-width: 22px;
    border-radius: 4px;
    margin: 1px;
}}
QScrollBar::handle:hover {{
    background: #8FC0F0;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}
QMessageBox {{
    background-color: #F7FAFE;
}}
QToolTip {{
    background-color: #233552;
    color: #FFFFFF;
    border: 1px solid #4DA8FF;
    border-radius: 4px;
    padding: 4px 7px;
    font-size: 11px;
}}
"""


def build_studio_qss(theme_name: str | None = None) -> str:
    pal = get_palette(theme_name)
    if pal.is_light:
        return _build_pastel_light_qss()
    return _build_dark_qss()


STUDIO_QSS = build_studio_qss("dark")


def make_icon(kind: str, color: str | None = None, size: int = 16) -> QIcon:
    """Create unified solid-geometric monochrome desktop icons via QPainter."""
    if color is None:
        color = get_palette().icon_color
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    c = QColor(color)
    s = float(size)

    if kind == "play":
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        path = QPainterPath()
        path.moveTo(s * 0.28, s * 0.18)
        path.lineTo(s * 0.82, s * 0.50)
        path.lineTo(s * 0.28, s * 0.82)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "pause":
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawRect(QRectF(s * 0.24, s * 0.20, s * 0.18, s * 0.60))
        p.drawRect(QRectF(s * 0.58, s * 0.20, s * 0.18, s * 0.60))
    elif kind == "start":
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawRect(QRectF(s * 0.18, s * 0.22, s * 0.12, s * 0.56))
        path = QPainterPath()
        path.moveTo(s * 0.78, s * 0.22)
        path.lineTo(s * 0.34, s * 0.50)
        path.lineTo(s * 0.78, s * 0.78)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "end":
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawRect(QRectF(s * 0.70, s * 0.22, s * 0.12, s * 0.56))
        path = QPainterPath()
        path.moveTo(s * 0.22, s * 0.22)
        path.lineTo(s * 0.66, s * 0.50)
        path.lineTo(s * 0.22, s * 0.78)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "step_back":
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawRect(QRectF(s * 0.64, s * 0.24, s * 0.12, s * 0.52))
        path = QPainterPath()
        path.moveTo(s * 0.56, s * 0.24)
        path.lineTo(s * 0.24, s * 0.50)
        path.lineTo(s * 0.56, s * 0.76)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "step_fwd":
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawRect(QRectF(s * 0.24, s * 0.24, s * 0.12, s * 0.52))
        path = QPainterPath()
        path.moveTo(s * 0.44, s * 0.24)
        path.lineTo(s * 0.76, s * 0.50)
        path.lineTo(s * 0.44, s * 0.76)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "mark_in":
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawRect(QRectF(s * 0.30, s * 0.18, s * 0.14, s * 0.64))
        p.drawRect(QRectF(s * 0.30, s * 0.18, s * 0.38, s * 0.12))
        p.drawRect(QRectF(s * 0.30, s * 0.70, s * 0.38, s * 0.12))
    elif kind == "mark_out":
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawRect(QRectF(s * 0.56, s * 0.18, s * 0.14, s * 0.64))
        p.drawRect(QRectF(s * 0.32, s * 0.18, s * 0.38, s * 0.12))
        p.drawRect(QRectF(s * 0.32, s * 0.70, s * 0.38, s * 0.12))
    elif kind == "film":
        pen = QPen(c, 1.35, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin)
        p.setPen(pen)
        p.drawRect(QRectF(s * 0.16, s * 0.22, s * 0.68, s * 0.56))
        p.drawLine(QPointF(s * 0.33, s * 0.22), QPointF(s * 0.33, s * 0.78))
        p.drawLine(QPointF(s * 0.67, s * 0.22), QPointF(s * 0.67, s * 0.78))
        p.drawLine(QPointF(s * 0.16, s * 0.50), QPointF(s * 0.33, s * 0.50))
        p.drawLine(QPointF(s * 0.67, s * 0.50), QPointF(s * 0.84, s * 0.50))
    elif kind == "volume_on":
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        path = QPainterPath()
        path.moveTo(s * 0.16, s * 0.40)
        path.lineTo(s * 0.34, s * 0.40)
        path.lineTo(s * 0.54, s * 0.22)
        path.lineTo(s * 0.54, s * 0.78)
        path.lineTo(s * 0.34, s * 0.60)
        path.lineTo(s * 0.16, s * 0.60)
        path.closeSubpath()
        p.drawPath(path)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(c, 1.4, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(QRectF(s * 0.48, s * 0.30, s * 0.24, s * 0.40), -55 * 16, 110 * 16)
        p.drawArc(QRectF(s * 0.52, s * 0.20, s * 0.34, s * 0.60), -55 * 16, 110 * 16)
    elif kind == "volume_off":
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        path = QPainterPath()
        path.moveTo(s * 0.16, s * 0.40)
        path.lineTo(s * 0.34, s * 0.40)
        path.lineTo(s * 0.52, s * 0.22)
        path.lineTo(s * 0.52, s * 0.78)
        path.lineTo(s * 0.34, s * 0.60)
        path.lineTo(s * 0.16, s * 0.60)
        path.closeSubpath()
        p.drawPath(path)
        p.setPen(QPen(c, 1.5, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(s * 0.64, s * 0.36), QPointF(s * 0.84, s * 0.64))
        p.drawLine(QPointF(s * 0.84, s * 0.36), QPointF(s * 0.64, s * 0.64))

    p.end()
    return QIcon(pm)


def format_timecode(sec: float) -> str:
    sec = max(0.0, float(sec or 0.0))
    mins = int(sec // 60)
    rem = sec - mins * 60
    return f"{mins:02d}:{rem:04.1f}"


def format_timecode_ms(sec: float) -> str:
    sec = max(0.0, float(sec or 0.0))
    mins = int(sec // 60)
    rem = sec - mins * 60
    return f"{mins:02d}:{rem:06.3f}"


def fade_in_widget(widget: QWidget, start: float = 0.35, duration_ms: int = 140) -> None:
    """Lightweight one-shot opacity fade-in that cleanly detaches QGraphicsOpacityEffect on finish."""
    if widget is None or not widget.isVisible():
        return
    try:
        prev = getattr(widget, "_micro_fade_anim", None)
        if prev is not None:
            prev.stop()
    except RuntimeError:
        pass
    eff = QGraphicsOpacityEffect(widget)
    eff.setOpacity(max(0.0, min(1.0, float(start))))
    widget.setGraphicsEffect(eff)
    anim = QPropertyAnimation(eff, b"opacity", widget)
    anim.setDuration(max(60, int(duration_ms)))
    anim.setStartValue(max(0.0, min(1.0, float(start))))
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)

    def _cleanup() -> None:
        try:
            if widget.graphicsEffect() is eff:
                widget.setGraphicsEffect(None)
        except RuntimeError:
            pass

    anim.finished.connect(_cleanup)
    widget._micro_fade_anim = anim  # type: ignore[attr-defined]
    anim.start()


def format_duration_korean(sec: float) -> str:
    from ..i18n import format_duration

    return format_duration(sec)
