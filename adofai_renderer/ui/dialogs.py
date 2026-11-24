"""Settings & pre-render confirmation dialogs (Planets, Sequence, Export/Hardware, Render Confirmation)."""

from __future__ import annotations

import datetime
import math
import os
from typing import Optional

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..gpu_detect import HardwareCapabilities, resolve_video_encoder
from ..i18n import tr
from ..level_info import AdofaiLevelInfo
from ..render_runner import (
    DEFAULT_PROXY_FPS,
    DEFAULT_PROXY_HEIGHT,
    DEFAULT_PROXY_WIDTH,
    PROXY_RES_PRESETS,
    RenderSettings,
    clamp_proxy_spec,
    estimate_render_time_sec,
)
from .theme import (
    format_duration_korean,
    format_timecode,
    get_palette,
)

RES_PRESETS = [
    ("1280 × 720   (720p HD)", 1280, 720),
    ("1920 × 1080  (1080p Full HD)", 1920, 1080),
    ("2560 × 1440  (1440p QHD)", 2560, 1440),
    ("3840 × 2160  (2160p 4K UHD)", 3840, 2160),
]


def _animate_dialog_in(dlg: QDialog) -> None:
    dlg.setWindowOpacity(0.0)
    anim = QPropertyAnimation(dlg, b"windowOpacity", dlg)
    anim.setDuration(165)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    anim.start()
    dlg._fade_anim = anim  # type: ignore[attr-defined]

    def _glide() -> None:
        try:
            if not dlg.isVisible():
                return
            end_pos = dlg.pos()
            start_pos = end_pos + QPoint(0, 8)
            dlg.move(start_pos)
            slide = QPropertyAnimation(dlg, b"pos", dlg)
            slide.setDuration(165)
            slide.setStartValue(start_pos)
            slide.setEndValue(end_pos)
            slide.setEasingCurve(QEasingCurve.OutCubic)
            slide.start()
            dlg._slide_anim = slide  # type: ignore[attr-defined]
        except RuntimeError:
            pass

    QTimer.singleShot(0, _glide)


class _PlanetPreviewCanvas(QWidget):
    """Live preview of Fire & Ice planets with configured colors and opacity on an ADOFAI-like dark floor."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(130)
        self.red_hex = "#FF0000"
        self.red_alpha = 1.0
        self.blue_hex = "#0000FF"
        self.blue_alpha = 1.0

    def set_planets(self, red_hex: str, red_a: float, blue_hex: str, blue_a: float) -> None:
        self.red_hex = red_hex
        self.red_alpha = max(0.0, min(1.0, red_a))
        self.blue_hex = blue_hex
        self.blue_alpha = max(0.0, min(1.0, blue_a))
        self.update()

    def paintEvent(self, _ev) -> None:
        pal = get_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(0, 0, -1, -1)
        p.fillRect(rect, QColor("#121A28" if pal.is_light else pal.bg_canvas))

        # Subtle checkerboard behind planets so opacity < 100% is clearly visible
        p.save()
        p.setClipRect(rect)
        cell = 12
        chk_col = QColor("#182334" if pal.is_light else "#141414")
        for y in range(0, rect.height(), cell):
            for x in range(0, rect.width(), cell):
                if ((x // cell) + (y // cell)) % 2 == 0:
                    p.fillRect(x, y, cell, cell, chk_col)
        p.restore()

        # Subtle ADOFAI tile track behind planets
        cx, cy = rect.width() / 2.0, rect.height() / 2.0
        p.setPen(QPen(QColor("#3E5573" if pal.is_light else "#383838"), 1))
        p.setBrush(QColor("#243347" if pal.is_light else "#242424"))
        p.drawRoundedRect(QRectF(cx - 110, cy - 16, 220, 32), 2, 2)
        for tx in (-75, -35, 5, 45, 85):
            p.drawLine(QPointF(cx + tx, cy - 16), QPointF(cx + tx, cy + 16))

        # Orbit ring around Fire planet
        r_orbit = 46.0
        ring_col = QColor(self.red_hex)
        ring_col.setAlphaF(0.42 * self.red_alpha)
        p.setPen(QPen(ring_col, 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx - 24, cy), r_orbit, r_orbit)

        self._draw_planet(p, QPointF(cx - 24, cy), self.red_hex, self.red_alpha, is_orbiting=False)
        self._draw_planet(p, QPointF(cx - 24 + r_orbit * math.cos(-0.45), cy + r_orbit * math.sin(-0.45)),
                          self.blue_hex, self.blue_alpha, is_orbiting=True)

        p.setPen(QPen(QColor(pal.border_default if pal.is_light else pal.border_subtle), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(rect)
        p.end()

    def _draw_planet(self, p: QPainter, center: QPointF, hex_col: str, alpha: float, is_orbiting: bool) -> None:
        if alpha <= 0.005:
            return
        base = QColor(hex_col)
        if not base.isValid():
            base = QColor("#ffffff")

        # Tail particles hint
        if is_orbiting:
            for k in range(1, 6):
                ang = -0.45 - k * 0.16
                tx = center.x() - (46.0 * (math.cos(-0.45) - math.cos(ang)))
                ty = center.y() - (46.0 * (math.sin(-0.45) - math.sin(ang)))
                tc = QColor(base)
                tc.setAlphaF(max(0.0, alpha * (0.42 - k * 0.07)))
                p.setPen(Qt.NoPen)
                p.setBrush(tc)
                rad = max(2.0, 9.0 - k * 1.3)
                p.drawEllipse(QPointF(tx, ty), rad, rad)

        # Outer soft glow
        glow = QRadialGradient(center, 22)
        gc0 = QColor(base)
        gc0.setAlphaF(0.45 * alpha)
        gc1 = QColor(base)
        gc1.setAlphaF(0.0)
        glow.setColorAt(0.0, gc0)
        glow.setColorAt(1.0, gc1)
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(center, 22, 22)

        # Core sphere
        core = QColor(base)
        core.setAlphaF(alpha)
        p.setBrush(core)
        p.drawEllipse(center, 11.5, 11.5)


class PlanetSettingsDialog(QDialog):
    """Planet color picker & opacity dialog (Default: Pure Red #FF0000 & Pure Blue #0000FF at 100%)."""

    def __init__(
        self,
        red_color: str,
        red_opacity: float,
        blue_color: str,
        blue_opacity: float,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("planet.title"))
        self.setFixedWidth(450)

        self.red_color = red_color.upper()
        self.red_opacity = max(0.0, min(1.0, red_opacity))
        self.blue_color = blue_color.upper()
        self.blue_opacity = max(0.0, min(1.0, blue_opacity))

        self._build_ui()
        self._sync_ui()
        _animate_dialog_in(self)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(12)

        self.preview = _PlanetPreviewCanvas()
        root.addWidget(self.preview)

        grid = QGridLayout()
        grid.setSpacing(10)

        # Red planet (1)
        grid.addWidget(QLabel(tr("planet.fire")), 0, 0)
        self.btn_red_swatch = QPushButton()
        self.btn_red_swatch.setFixedSize(36, 26)
        self.btn_red_swatch.clicked.connect(lambda: self._pick_color("red"))
        self.ed_red_hex = QLineEdit()
        self.ed_red_hex.setFixedWidth(92)
        self.ed_red_hex.setStyleSheet("font-family: 'Cascadia Mono', Consolas, monospace;")
        self.ed_red_hex.textEdited.connect(lambda txt: self._on_hex_edited("red", txt))
        grid.addWidget(self.btn_red_swatch, 0, 1)
        grid.addWidget(self.ed_red_hex, 0, 2)

        self.sld_red_a = QSlider(Qt.Horizontal)
        self.sld_red_a.setRange(0, 100)
        self.spn_red_a = QSpinBox()
        self.spn_red_a.setRange(0, 100)
        self.spn_red_a.setSuffix("%")
        self.sld_red_a.valueChanged.connect(self.spn_red_a.setValue)
        self.spn_red_a.valueChanged.connect(self._on_red_opacity)
        grid.addWidget(QLabel(tr("planet.opacity")), 1, 0)
        grid.addWidget(self.sld_red_a, 1, 1, 1, 2)
        grid.addWidget(self.spn_red_a, 1, 3)

        pal = get_palette()
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {pal.sep_color};")
        grid.addWidget(sep, 2, 0, 1, 4)

        # Blue planet (2)
        grid.addWidget(QLabel(tr("planet.ice")), 3, 0)
        self.btn_blue_swatch = QPushButton()
        self.btn_blue_swatch.setFixedSize(36, 26)
        self.btn_blue_swatch.clicked.connect(lambda: self._pick_color("blue"))
        self.ed_blue_hex = QLineEdit()
        self.ed_blue_hex.setFixedWidth(92)
        self.ed_blue_hex.setStyleSheet("font-family: 'Cascadia Mono', Consolas, monospace;")
        self.ed_blue_hex.textEdited.connect(lambda txt: self._on_hex_edited("blue", txt))
        grid.addWidget(self.btn_blue_swatch, 3, 1)
        grid.addWidget(self.ed_blue_hex, 3, 2)

        self.sld_blue_a = QSlider(Qt.Horizontal)
        self.sld_blue_a.setRange(0, 100)
        self.spn_blue_a = QSpinBox()
        self.spn_blue_a.setRange(0, 100)
        self.spn_blue_a.setSuffix("%")
        self.sld_blue_a.valueChanged.connect(self.spn_blue_a.setValue)
        self.spn_blue_a.valueChanged.connect(self._on_blue_opacity)
        grid.addWidget(QLabel(tr("planet.opacity")), 4, 0)
        grid.addWidget(self.sld_blue_a, 4, 1, 1, 2)
        grid.addWidget(self.spn_blue_a, 4, 3)

        root.addLayout(grid)

        note = QLabel(tr("planet.note"))
        note.setWordWrap(True)
        note.setObjectName("muted")
        root.addWidget(note)

        btn_row = QHBoxLayout()
        btn_reset = QPushButton(tr("btn.restore_defaults"))
        btn_reset.clicked.connect(self._reset_defaults)
        btn_cancel = QPushButton(tr("btn.cancel"))
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton(tr("btn.apply"))
        btn_ok.setObjectName("primaryBtn")
        btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(btn_reset)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        root.addLayout(btn_row)

    def _sync_ui(self) -> None:
        pal = get_palette()
        rad = "border-radius: 5px;" if pal.is_light else ""
        self.btn_red_swatch.setStyleSheet(f"background-color: {self.red_color}; border: 1px solid {pal.swatch_border}; {rad}")
        self.btn_blue_swatch.setStyleSheet(f"background-color: {self.blue_color}; border: 1px solid {pal.swatch_border}; {rad}")
        if self.ed_red_hex.text().upper() != self.red_color:
            self.ed_red_hex.setText(self.red_color)
        if self.ed_blue_hex.text().upper() != self.blue_color:
            self.ed_blue_hex.setText(self.blue_color)
        self.sld_red_a.blockSignals(True)
        self.spn_red_a.blockSignals(True)
        self.sld_red_a.setValue(int(round(self.red_opacity * 100)))
        self.spn_red_a.setValue(int(round(self.red_opacity * 100)))
        self.sld_red_a.blockSignals(False)
        self.spn_red_a.blockSignals(False)

        self.sld_blue_a.blockSignals(True)
        self.spn_blue_a.blockSignals(True)
        self.sld_blue_a.setValue(int(round(self.blue_opacity * 100)))
        self.spn_blue_a.setValue(int(round(self.blue_opacity * 100)))
        self.sld_blue_a.blockSignals(False)
        self.spn_blue_a.blockSignals(False)

        self.preview.set_planets(self.red_color, self.red_opacity, self.blue_color, self.blue_opacity)

    def _pick_color(self, which: str) -> None:
        cur = QColor(self.red_color if which == "red" else self.blue_color)
        col = QColorDialog.getColor(cur, self, tr("planet.pick_color"))
        if col.isValid():
            hx = col.name().upper()
            if which == "red":
                self.red_color = hx
            else:
                self.blue_color = hx
            self._sync_ui()

    def _on_hex_edited(self, which: str, txt: str) -> None:
        s = txt.strip()
        if not s.startswith("#"):
            s = "#" + s
        c = QColor(s)
        if len(s) == 7 and c.isValid():
            if which == "red":
                self.red_color = c.name().upper()
            else:
                self.blue_color = c.name().upper()
            pal = get_palette()
            rad = "border-radius: 5px;" if pal.is_light else ""
            self.btn_red_swatch.setStyleSheet(f"background-color: {self.red_color}; border: 1px solid {pal.swatch_border}; {rad}")
            self.btn_blue_swatch.setStyleSheet(f"background-color: {self.blue_color}; border: 1px solid {pal.swatch_border}; {rad}")
            self.preview.set_planets(self.red_color, self.red_opacity, self.blue_color, self.blue_opacity)

    def _on_red_opacity(self, val: int) -> None:
        self.red_opacity = val / 100.0
        self.sld_red_a.blockSignals(True)
        self.sld_red_a.setValue(val)
        self.sld_red_a.blockSignals(False)
        self.preview.set_planets(self.red_color, self.red_opacity, self.blue_color, self.blue_opacity)

    def _on_blue_opacity(self, val: int) -> None:
        self.blue_opacity = val / 100.0
        self.sld_blue_a.blockSignals(True)
        self.sld_blue_a.setValue(val)
        self.sld_blue_a.blockSignals(False)
        self.preview.set_planets(self.red_color, self.red_opacity, self.blue_color, self.blue_opacity)

    def _reset_defaults(self) -> None:
        self.red_color = "#FF0000"
        self.red_opacity = 1.0
        self.blue_color = "#0000FF"
        self.blue_opacity = 1.0
        self._sync_ui()


class SequenceSettingsDialog(QDialog):
    """Sequence range, end padding, and in-game HUD/countdown visibility settings."""

    def __init__(
        self,
        duration_sec: float,
        start_sec: float,
        end_sec: float,
        end_padding_sec: float,
        hide_countdown: bool,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("seq.title"))
        self.setFixedWidth(410)
        self._duration = max(1.0, duration_sec)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(10)

        g = QGridLayout()
        g.setSpacing(8)

        g.addWidget(QLabel(tr("seq.start")), 0, 0)
        self.spn_start = QDoubleSpinBox()
        self.spn_start.setRange(0.0, 36000.0)
        self.spn_start.setDecimals(1)
        self.spn_start.setValue(start_sec)
        self.spn_start.setSuffix(" s")
        g.addWidget(self.spn_start, 0, 1)

        g.addWidget(QLabel(tr("seq.end")), 1, 0)
        self.spn_end = QDoubleSpinBox()
        self.spn_end.setRange(0.0, 36000.0)
        self.spn_end.setDecimals(1)
        self.spn_end.setValue(end_sec)
        self.spn_end.setSuffix(" s")
        g.addWidget(self.spn_end, 1, 1)

        g.addWidget(QLabel(tr("seq.pad")), 2, 0)
        self.spn_pad = QDoubleSpinBox()
        self.spn_pad.setRange(0.0, 30.0)
        self.spn_pad.setDecimals(1)
        self.spn_pad.setValue(end_padding_sec)
        self.spn_pad.setSuffix(" s")
        g.addWidget(self.spn_pad, 2, 1)

        self.chk_hide_cd = QCheckBox(tr("seq.hide_cd"))
        self.chk_hide_cd.setChecked(hide_countdown)
        g.addWidget(self.chk_hide_cd, 3, 0, 1, 2)

        root.addLayout(g)

        btn_row = QHBoxLayout()
        btn_full = QPushButton(tr("seq.reset_full"))
        btn_full.clicked.connect(self._reset_full)
        btn_cancel = QPushButton(tr("btn.cancel"))
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton(tr("btn.apply"))
        btn_ok.setObjectName("primaryBtn")
        btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(btn_full)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        root.addLayout(btn_row)
        _animate_dialog_in(self)

    def _reset_full(self) -> None:
        self.spn_start.setValue(0.0)
        self.spn_end.setValue(0.0)


class ProxySettingsDialog(QDialog):
    """Detailed proxy resolution (144p..720p) and frame rate (5..60 fps) configuration dialog."""

    def __init__(
        self,
        width: int = DEFAULT_PROXY_WIDTH,
        height: int = DEFAULT_PROXY_HEIGHT,
        fps: int = DEFAULT_PROXY_FPS,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("proxy.title"))
        self.setFixedWidth(440)
        w, h, f = clamp_proxy_spec(width, height, fps)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(12)

        g = QGridLayout()
        g.setSpacing(10)

        g.addWidget(QLabel(tr("proxy.res")), 0, 0)
        self.cmb_res = QComboBox()
        for lbl, pw, ph in PROXY_RES_PRESETS:
            self.cmb_res.addItem(lbl, (pw, ph))
        self._select_res(w, h)
        g.addWidget(self.cmb_res, 0, 1, 1, 2)

        g.addWidget(QLabel(tr("proxy.fps")), 1, 0)
        self.sld_fps = QSlider(Qt.Horizontal)
        self.sld_fps.setRange(5, 60)
        self.sld_fps.setValue(f)
        self.spn_fps = QSpinBox()
        self.spn_fps.setRange(5, 60)
        self.spn_fps.setSuffix(" fps")
        self.spn_fps.setValue(f)
        self.spn_fps.setToolTip(tr("proxy.fps_tip"))
        self.sld_fps.valueChanged.connect(self.spn_fps.setValue)
        self.spn_fps.valueChanged.connect(self.sld_fps.setValue)
        g.addWidget(self.sld_fps, 1, 1)
        g.addWidget(self.spn_fps, 1, 2)

        root.addLayout(g)

        # Quick preset chips
        q_lbl = QLabel(tr("proxy.quick"))
        q_lbl.setObjectName("muted")
        root.addWidget(q_lbl)

        preset_grid = QGridLayout()
        preset_grid.setSpacing(6)
        presets = [
            (tr("proxy.preset_ultra_fast"), 256, 144, 5),
            (tr("proxy.preset_std"), 480, 270, 12),
            (tr("proxy.preset_smooth"), 854, 480, 30),
            (tr("proxy.preset_hd"), 1280, 720, 60),
        ]
        for idx, (label, pw, ph, pfps) in enumerate(presets):
            b = QPushButton(label)
            b.setObjectName("chipBtn")
            b.clicked.connect(lambda _=False, _w=pw, _h=ph, _f=pfps: self._apply_preset(_w, _h, _f))
            preset_grid.addWidget(b, idx // 2, idx % 2)
        root.addLayout(preset_grid)

        note = QLabel(tr("proxy.note"))
        note.setWordWrap(True)
        note.setObjectName("muted")
        root.addWidget(note)

        btn_row = QHBoxLayout()
        btn_def = QPushButton(tr("btn.restore_defaults"))
        btn_def.clicked.connect(lambda: self._apply_preset(DEFAULT_PROXY_WIDTH, DEFAULT_PROXY_HEIGHT, DEFAULT_PROXY_FPS))
        btn_cancel = QPushButton(tr("btn.cancel"))
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton(tr("btn.apply"))
        btn_ok.setObjectName("primaryBtn")
        btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(btn_def)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        root.addLayout(btn_row)
        _animate_dialog_in(self)

    def _select_res(self, w: int, h: int) -> None:
        best_idx = 2
        best_dist = 10**9
        for i in range(self.cmb_res.count()):
            pw, ph = self.cmb_res.itemData(i)
            d = abs(ph - h) + abs(pw - w)
            if d < best_dist:
                best_dist = d
                best_idx = i
        self.cmb_res.setCurrentIndex(best_idx)

    def _apply_preset(self, w: int, h: int, fps: int) -> None:
        self._select_res(w, h)
        self.spn_fps.setValue(fps)

    def result_spec(self) -> tuple[int, int, int]:
        pw, ph = self.cmb_res.currentData()
        return clamp_proxy_spec(pw, ph, self.spn_fps.value())


class ExportSettingsDialog(QDialog):
    """Export video, audio, GPU/RAM/CPU resource limits (default GPU 90% / RAM 80%), and low-spec safety settings."""

    def __init__(
        self,
        state: dict,
        caps: Optional[HardwareCapabilities],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("exp.title"))
        self.setFixedWidth(520)
        self.state = dict(state)
        self.caps = caps

        self._build_ui()
        _animate_dialog_in(self)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)

        tabs = QTabWidget()

        # Tab 1: Hardware, Resource Limits (Default GPU 90% / RAM 80%), Low-Spec Safeguards, Optimizer
        t_hw = QWidget()
        gh = QGridLayout(t_hw)
        gh.setContentsMargins(14, 14, 14, 14)
        gh.setSpacing(9)

        gh.addWidget(QLabel(tr("exp.gpu_backend")), 0, 0)
        self.cmb_gpu = QComboBox()
        self.cmb_gpu.addItem(tr("exp.gpu_auto"), "auto")
        self.cmb_gpu.addItem(tr("exp.gpu_nvenc"), "nvenc")
        self.cmb_gpu.addItem(tr("exp.gpu_igpu"), "igpu")
        self.cmb_gpu.addItem(tr("exp.gpu_cpu"), "cpu")
        g_idx = {"auto": 0, "nvenc": 1, "igpu": 2, "cpu": 3}.get(self.state["gpu_mode"], 0)
        self.cmb_gpu.setCurrentIndex(g_idx)
        gh.addWidget(self.cmb_gpu, 0, 1, 1, 2)

        gh.addWidget(QLabel(tr("exp.gpu_cap")), 1, 0)
        self.sld_gpu_cap = QSlider(Qt.Horizontal)
        self.sld_gpu_cap.setRange(10, 100)
        self.sld_gpu_cap.setValue(self.state["gpu_limit_percent"])
        self.spn_gpu_cap = QSpinBox()
        self.spn_gpu_cap.setRange(10, 100)
        self.spn_gpu_cap.setSuffix("%")
        self.spn_gpu_cap.setValue(self.state["gpu_limit_percent"])
        self.sld_gpu_cap.valueChanged.connect(self.spn_gpu_cap.setValue)
        self.spn_gpu_cap.valueChanged.connect(self.sld_gpu_cap.setValue)
        gh.addWidget(self.sld_gpu_cap, 1, 1)
        gh.addWidget(self.spn_gpu_cap, 1, 2)

        gh.addWidget(QLabel(tr("exp.ram_cap")), 2, 0)
        self.sld_ram_cap = QSlider(Qt.Horizontal)
        self.sld_ram_cap.setRange(30, 95)
        self.sld_ram_cap.setValue(self.state["ram_limit_percent"])
        self.spn_ram_cap = QSpinBox()
        self.spn_ram_cap.setRange(30, 95)
        self.spn_ram_cap.setSuffix("%")
        self.spn_ram_cap.setValue(self.state["ram_limit_percent"])
        self.sld_ram_cap.valueChanged.connect(self.spn_ram_cap.setValue)
        self.spn_ram_cap.valueChanged.connect(self.sld_ram_cap.setValue)
        gh.addWidget(self.sld_ram_cap, 2, 1)
        gh.addWidget(self.spn_ram_cap, 2, 2)

        cpu_count = os.cpu_count() or 8
        gh.addWidget(QLabel(tr("exp.cpu_cores")), 3, 0)
        self.cmb_cpu_cores = QComboBox()
        self.cmb_cpu_cores.addItem(tr("exp.cpu_auto", n=cpu_count), 0)
        for c in sorted({1, 2, 4, 6, 8, 12, 16, cpu_count}):
            if c <= cpu_count:
                self.cmb_cpu_cores.addItem(tr("exp.cpu_n", c=c), c)
        cur_cores = int(self.state.get("max_cpu_cores", 0))
        for idx in range(self.cmb_cpu_cores.count()):
            if self.cmb_cpu_cores.itemData(idx) == cur_cores:
                self.cmb_cpu_cores.setCurrentIndex(idx)
                break
        gh.addWidget(self.cmb_cpu_cores, 3, 1, 1, 2)

        gh.addWidget(QLabel(tr("exp.lowspec")), 4, 0)
        self.cmb_lowspec = QComboBox()
        self.cmb_lowspec.addItem(tr("exp.lowspec_auto"), "auto")
        self.cmb_lowspec.addItem(tr("exp.lowspec_force"), "force")
        self.cmb_lowspec.addItem(tr("exp.lowspec_off"), "off")
        ls_idx = {"auto": 0, "force": 1, "off": 2}.get(self.state.get("low_spec_mode", "auto"), 0)
        self.cmb_lowspec.setCurrentIndex(ls_idx)
        gh.addWidget(self.cmb_lowspec, 4, 1, 1, 2)

        self.chk_opt_render = QCheckBox(tr("exp.opt_chk"))
        self.chk_opt_render.setChecked(bool(self.state.get("experimental_optimize_render", False)))
        gh.addWidget(self.chk_opt_render, 5, 0, 1, 3)

        hw_note = QLabel(tr("exp.hw_note"))
        hw_note.setWordWrap(True)
        hw_note.setObjectName("muted")
        gh.addWidget(hw_note, 6, 0, 1, 3)

        tabs.addTab(t_hw, tr("exp.tab_hw"))

        # Tab 2: Audio Mixing (non-overlapping with main Output panel's video bitrate/codec controls)
        t_qa = QWidget()
        gq = QGridLayout(t_qa)
        gq.setContentsMargins(14, 14, 14, 14)
        gq.setSpacing(10)

        gq.addWidget(QLabel(tr("exp.bgm_vol")), 0, 0)
        self.sld_bgm = QSlider(Qt.Horizontal)
        self.sld_bgm.setRange(0, 200)
        self.sld_bgm.setValue(int(round(self.state["bgm_volume"] * 100)))
        self.spn_bgm = QSpinBox()
        self.spn_bgm.setRange(0, 200)
        self.spn_bgm.setSuffix("%")
        self.spn_bgm.setValue(int(round(self.state["bgm_volume"] * 100)))
        self.sld_bgm.valueChanged.connect(self.spn_bgm.setValue)
        self.spn_bgm.valueChanged.connect(self.sld_bgm.setValue)
        gq.addWidget(self.sld_bgm, 0, 1)
        gq.addWidget(self.spn_bgm, 0, 2)

        gq.addWidget(QLabel(tr("exp.hit_vol")), 1, 0)
        self.sld_hit = QSlider(Qt.Horizontal)
        self.sld_hit.setRange(0, 200)
        self.sld_hit.setValue(int(round(self.state["hitsound_volume"] * 100)))
        self.spn_hit = QSpinBox()
        self.spn_hit.setRange(0, 200)
        self.spn_hit.setSuffix("%")
        self.spn_hit.setValue(int(round(self.state["hitsound_volume"] * 100)))
        self.sld_hit.valueChanged.connect(self.spn_hit.setValue)
        self.spn_hit.valueChanged.connect(self.sld_hit.setValue)
        gq.addWidget(self.sld_hit, 1, 1)
        gq.addWidget(self.spn_hit, 1, 2)

        gq.addWidget(QLabel(tr("exp.abit")), 2, 0)
        self.cmb_abit = QComboBox()
        for br in ("320k", "256k", "192k"):
            self.cmb_abit.addItem(f"AAC {br}", br)
        ab_idx = {"320k": 0, "256k": 1, "192k": 2}.get(self.state.get("audio_bitrate", "320k"), 0)
        self.cmb_abit.setCurrentIndex(ab_idx)
        gq.addWidget(self.cmb_abit, 2, 1, 1, 2)

        gq.setRowStretch(3, 1)
        tabs.addTab(t_qa, tr("exp.tab_qa"))

        root.addWidget(tabs)

        btn_row = QHBoxLayout()
        btn_def = QPushButton(tr("btn.restore_defaults"))
        btn_def.clicked.connect(self._restore_defaults)
        btn_cancel = QPushButton(tr("btn.cancel"))
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton(tr("btn.save"))
        btn_ok.setObjectName("primaryBtn")
        btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(btn_def)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        root.addLayout(btn_row)

    def _restore_defaults(self) -> None:
        self.spn_bgm.setValue(100)
        self.spn_hit.setValue(100)
        self.cmb_abit.setCurrentIndex(0)
        self.cmb_gpu.setCurrentIndex(0)
        self.spn_gpu_cap.setValue(90)
        self.spn_ram_cap.setValue(80)
        self.cmb_cpu_cores.setCurrentIndex(0)
        self.cmb_lowspec.setCurrentIndex(0)
        self.chk_opt_render.setChecked(False)

    def result_state(self) -> dict:
        out = dict(self.state)
        out.update({
            "bgm_volume": self.spn_bgm.value() / 100.0,
            "hitsound_volume": self.spn_hit.value() / 100.0,
            "audio_bitrate": self.cmb_abit.currentData(),
            "gpu_mode": self.cmb_gpu.currentData(),
            "gpu_limit_percent": self.spn_gpu_cap.value(),
            "ram_limit_percent": self.spn_ram_cap.value(),
            "max_cpu_cores": self.cmb_cpu_cores.currentData(),
            "low_spec_mode": self.cmb_lowspec.currentData(),
            "experimental_optimize_render": self.chk_opt_render.isChecked(),
        })
        return out


class RenderConfirmDialog(QDialog):
    """Pre-render final confirmation dialog displaying full settings summary and estimated completion time."""

    def __init__(
        self,
        settings: RenderSettings,
        level_info: Optional[AdofaiLevelInfo],
        caps: Optional[HardwareCapabilities],
        level_duration_sec: float,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("conf.title"))
        self.setFixedWidth(510)
        self.open_settings_requested = False

        est_sec, target_frames, clip_dur = estimate_render_time_sec(settings, caps, level_duration_sec)
        eta_dt = datetime.datetime.now() + datetime.timedelta(seconds=est_sec)
        enc_name, enc_label = resolve_video_encoder(caps, settings.gpu_mode, settings.codec) if caps else ("auto", "Auto")

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(12)

        hdr = QLabel(tr("conf.hdr"))
        f = QFont()
        f.setPointSize(11)
        f.setBold(True)
        hdr.setFont(f)
        root.addWidget(hdr)

        pal = get_palette()
        # ETA Highlight Banner
        eta_box = QFrame()
        if pal.is_light:
            eta_box.setStyleSheet(
                f"QFrame {{ background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #EEF7FF, stop:0.65 #FFFFFF, stop:1 #FFF2F8); "
                f"border: 1px solid {pal.eta_box_border}; border-left: 4px solid {pal.accent_primary}; border-radius: 7px; }}"
            )
        else:
            eta_box.setStyleSheet(
                f"QFrame {{ background-color: {pal.eta_box_bg}; border: 1px solid {pal.eta_box_border}; "
                f"border-left: 3px solid {pal.accent_status_blue}; border-radius: 3px; }}"
            )
        eta_lay = QHBoxLayout(eta_box)
        eta_lay.setContentsMargins(12, 10, 12, 10)
        eta_left = QVBoxLayout()
        eta_left.setSpacing(2)
        lbl_eta_k = QLabel(tr("conf.est_time"))
        lbl_eta_k.setStyleSheet(f"color: {pal.text_secondary}; font-size: 11px; border: none; background: transparent;")
        lbl_eta_v = QLabel(format_duration_korean(est_sec))
        lbl_eta_v.setStyleSheet(f"color: {pal.eta_val_fg}; font-size: 15px; font-weight: 700; border: none; background: transparent;")
        eta_left.addWidget(lbl_eta_k)
        eta_left.addWidget(lbl_eta_v)

        eta_right = QVBoxLayout()
        eta_right.setSpacing(2)
        eta_right.setAlignment(Qt.AlignRight)
        lbl_clk_k = QLabel(tr("conf.est_clk"))
        lbl_clk_k.setAlignment(Qt.AlignRight)
        lbl_clk_k.setStyleSheet(f"color: {pal.text_secondary}; font-size: 11px; border: none; background: transparent;")
        lbl_clk_v = QLabel(eta_dt.strftime("%H:%M:%S"))
        lbl_clk_v.setAlignment(Qt.AlignRight)
        lbl_clk_v.setStyleSheet(
            f"color: {pal.accent_pink if pal.is_light else pal.accent_status_blue}; font-family: 'Cascadia Mono', Consolas, monospace; "
            f"font-size: 15px; font-weight: 700; border: none; background: transparent;"
        )
        eta_right.addWidget(lbl_clk_k)
        eta_right.addWidget(lbl_clk_v)

        eta_lay.addLayout(eta_left, 1)
        eta_lay.addLayout(eta_right, 0)
        root.addWidget(eta_box)

        # Summary Table
        card = QFrame()
        card.setObjectName("panel")
        grid = QGridLayout(card)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setVerticalSpacing(7)
        grid.setHorizontalSpacing(14)

        title = level_info.display_title if level_info else os.path.basename(settings.level_path)
        is_trim = (settings.start_sec > 0.05 or settings.end_sec > 0.05)
        if is_trim:
            end_str = format_timecode(settings.end_sec) if settings.end_sec > 0.05 else tr("conf.end_word")
            range_str = tr("conf.range_trim", s=format_timecode(settings.start_sec), e=end_str, dur=clip_dur, frames=target_frames)
        else:
            range_str = tr("conf.range_full", dur=clip_dur, frames=target_frames)

        ext = os.path.splitext(settings.output_path)[1].lstrip(".").upper() or "MP4"
        q_label = {
            "ultra": tr("conf.q_ultra"),
            "high": tr("conf.q_high"),
            "balanced": tr("conf.q_bal"),
        }.get(settings.quality, settings.quality)
        vid_str = f"{settings.width} × {settings.height}  ·  {settings.fps} fps CFR  ·  {ext} ({settings.codec.upper()})  ·  {q_label}"

        bgm_s = tr("conf.bgm_on", v=int(round(settings.bgm_volume * 100))) if settings.include_bgm else tr("conf.bgm_off")
        hit_s = tr("conf.hit_on", v=int(round(settings.hitsound_volume * 100))) if settings.include_hitsounds else tr("conf.hit_off")
        aud_str = f"{bgm_s}  ·  {hit_s}  ·  AAC {settings.audio_bitrate}"

        cpu_s = tr("conf.cpu_n", n=settings.max_cpu_cores) if settings.max_cpu_cores > 0 else tr("conf.cpu_auto")
        ls_s = {
            "auto": tr("conf.ls_auto"),
            "force": tr("conf.ls_force"),
            "off": tr("conf.ls_off"),
        }.get(settings.low_spec_mode, tr("conf.ls_auto"))
        opt_s = tr("conf.opt_tag") if settings.experimental_optimize_render else ""
        hw_str = f"{enc_label}  ·  GPU {settings.gpu_limit_percent}% · RAM {settings.ram_limit_percent}% · {cpu_s} ({ls_s}{opt_s})"

        rows = [
            (tr("conf.row_level"), title, False),
            (tr("conf.row_range"), range_str, False),
            (tr("conf.row_video"), vid_str, False),
            (tr("conf.row_audio"), aud_str, False),
            (tr("conf.row_hw"), hw_str, False),
            (tr("conf.row_out"), settings.output_path, True),
        ]
        for r, (k, v, is_path) in enumerate(rows):
            lk = QLabel(k)
            lk.setStyleSheet(f"color: {pal.text_secondary}; font-size: 11px; background: transparent;")
            lk.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            lv = QLabel(v)
            if is_path:
                fm = QFontMetrics(lv.font())
                lv.setText(fm.elidedText(v, Qt.ElideMiddle, 360))
                lv.setToolTip(v)
                lv.setStyleSheet(f"color: {pal.text_primary}; font-family: 'Cascadia Mono', Consolas, monospace; font-size: 11px; background: transparent;")
            else:
                lv.setWordWrap(True)
                lv.setStyleSheet(f"color: {pal.text_primary}; font-size: 12px; background: transparent;")
            grid.addWidget(lk, r, 0)
            grid.addWidget(lv, r, 1)

        # Planet color row with real color swatches
        row_p = len(rows)
        lk_p = QLabel(tr("conf.row_planets"))
        lk_p.setStyleSheet(f"color: {pal.text_secondary}; font-size: 11px; background: transparent;")
        p_box = QHBoxLayout()
        p_box.setSpacing(8)
        for col_hex, op, name in (
            (settings.red_planet_color, settings.red_planet_opacity, tr("insp.fire")),
            (settings.blue_planet_color, settings.blue_planet_opacity, tr("insp.ice")),
        ):
            dot = QLabel()
            dot.setFixedSize(11, 11)
            dot.setStyleSheet(f"background-color: {col_hex}; border: 1px solid {pal.swatch_border}; border-radius: 5px;")
            txt = QLabel(f"{name} {col_hex.upper()} ({int(round(op*100))}%)")
            txt.setStyleSheet(f"color: {pal.text_primary}; font-family: 'Cascadia Mono', Consolas, monospace; font-size: 11px; background: transparent;")
            p_box.addWidget(dot)
            p_box.addWidget(txt)
        p_box.addStretch(1)
        grid.addWidget(lk_p, row_p, 0)
        grid.addLayout(p_box, row_p, 1)

        grid.setColumnMinimumWidth(0, 74)
        grid.setColumnStretch(1, 1)
        root.addWidget(card)

        btn_row = QHBoxLayout()
        btn_edit = QPushButton(tr("conf.btn_edit"))
        btn_edit.clicked.connect(self._request_edit)
        btn_cancel = QPushButton(tr("btn.cancel"))
        btn_cancel.clicked.connect(self.reject)
        btn_go = QPushButton(tr("conf.btn_start"))
        btn_go.setObjectName("primaryBtn")
        btn_go.setDefault(True)
        btn_go.clicked.connect(self.accept)
        btn_row.addWidget(btn_edit)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_go)
        root.addLayout(btn_row)
        _animate_dialog_in(self)

    def _request_edit(self) -> None:
        self.open_settings_requested = True
        self.reject()
