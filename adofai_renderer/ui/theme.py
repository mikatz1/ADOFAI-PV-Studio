"""Authentic native Qt Fusion dark studio theme, PyInstaller-safe asset resolver, and unified vector icons."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

BG_WINDOW = "#212121"
BG_PANEL = "#262626"
BG_INSET = "#191919"
BG_INPUT = "#141414"
BG_CANVAS = "#0e0e0e"
BG_CONSOLE = "#0c0c0c"

BORDER_SUBTLE = "#303030"
BORDER_DEFAULT = "#3b3b3b"
BORDER_STRONG = "#505050"

TEXT_PRIMARY = "#e2e2e2"
TEXT_SECONDARY = "#9a9a9a"
TEXT_MUTED = "#6d6d6d"

ACCENT_PRIMARY = "#356899"
ACCENT_HOVER = "#4079b0"
ACCENT_PRESSED = "#2b5680"
ACCENT_STATUS_BLUE = "#4a9eff"
ACCENT_WARM = "#4a9eff"
ACCENT_PLAYHEAD = "#dfa142"
ACCENT_DANGER = "#9c363e"


def get_asset_path(*parts: str) -> str:
    """Resolve bundled asset paths cleanly in both development and PyInstaller --onefile builds."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "assets", *parts)


def _qss_url(path: str) -> str:
    return path.replace("\\", "/")


def build_studio_qss() -> str:
    chk_url = _qss_url(get_asset_path("ui", "check.png"))
    chev_url = _qss_url(get_asset_path("ui", "chevron_down.png"))
    up_url = _qss_url(get_asset_path("ui", "arrow_up.png"))
    dn_url = _qss_url(get_asset_path("ui", "arrow_down.png"))

    return f"""
QMainWindow, QDialog {{
    background-color: {BG_WINDOW};
    color: {TEXT_PRIMARY};
    font-family: "Segoe UI", "Malgun Gothic", sans-serif;
    font-size: 12px;
}}
QWidget {{
    color: {TEXT_PRIMARY};
    font-family: "Segoe UI", "Malgun Gothic", sans-serif;
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
    color: {TEXT_PRIMARY};
}}
QMenu::item:selected {{
    background-color: {ACCENT_PRIMARY};
    color: #ffffff;
}}
QMenu::item:disabled {{
    color: {TEXT_MUTED};
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
    background-color: {BG_PANEL};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: 3px;
}}
QFrame#inspectorPane {{
    background-color: #232323;
    border-left: 1px solid #141414;
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
    background-color: {BG_INPUT};
    border: 1px solid #363636;
    border-top-color: #282828;
    border-radius: 2px;
    padding: 1px 6px;
    min-height: 20px;
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT_PRIMARY};
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
    selection-background-color: {ACCENT_PRIMARY};
    color: {TEXT_PRIMARY};
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
    color: {TEXT_PRIMARY};
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
    background: {BG_INPUT};
}}
QCheckBox::indicator:hover {{
    border-color: #666666;
}}
QCheckBox::indicator:checked {{
    background: {ACCENT_PRIMARY};
    border-color: #568ec7;
    image: url("{chk_url}");
}}
QTabWidget::pane {{
    border: 1px solid {BORDER_SUBTLE};
    background-color: {BG_PANEL};
    border-radius: 2px;
    top: -1px;
}}
QTabBar::tab {{
    background-color: #1d1d1d;
    border: 1px solid {BORDER_SUBTLE};
    border-bottom: none;
    padding: 5px 14px;
    margin-right: 2px;
    color: {TEXT_SECONDARY};
    border-top-left-radius: 2px;
    border-top-right-radius: 2px;
}}
QTabBar::tab:selected {{
    background-color: {BG_PANEL};
    color: {TEXT_PRIMARY};
    border-color: {BORDER_DEFAULT};
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
    background-color: {ACCENT_PRIMARY};
    border-radius: 1px;
}}
QLabel#muted {{
    color: {TEXT_SECONDARY};
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


STUDIO_QSS = build_studio_qss()


def make_icon(kind: str, color: str = "#d4d4d4", size: int = 16) -> QIcon:
    """Create unified solid-geometric monochrome desktop icons via QPainter."""
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


def format_duration_korean(sec: float) -> str:
    sec = max(0.0, float(sec or 0.0))
    if sec < 60:
        return f"약 {int(round(sec))}초"
    mins = int(sec // 60)
    rem = int(round(sec % 60))
    if mins < 60:
        return f"약 {mins}분 {rem:02d}초"
    hrs = mins // 60
    mins = mins % 60
    return f"약 {hrs}시간 {mins:02d}분"
