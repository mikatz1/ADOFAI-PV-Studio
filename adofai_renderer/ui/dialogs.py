"""Settings & pre-render confirmation dialogs (Planets, Sequence, Export/Hardware, Render Confirmation)."""

from __future__ import annotations

import datetime
import math
import os
from typing import Optional

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QRadialGradient
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
from ..level_info import AdofaiLevelInfo
from ..render_runner import RenderSettings, estimate_render_time_sec
from .theme import (
    ACCENT_WARM,
    BG_CANVAS,
    BG_PANEL,
    BORDER_DEFAULT,
    BORDER_SUBTLE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    format_duration_korean,
    format_timecode,
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
    anim.setDuration(150)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    anim.start()
    dlg._fade_anim = anim  # type: ignore[attr-defined]


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
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(0, 0, -1, -1)
        p.fillRect(rect, QColor(BG_CANVAS))

        # Subtle checkerboard behind planets so opacity < 100% is clearly visible
        cell = 12
        for y in range(0, rect.height(), cell):
            for x in range(0, rect.width(), cell):
                if ((x // cell) + (y // cell)) % 2 == 0:
                    p.fillRect(x, y, cell, cell, QColor("#141414"))

        # Subtle ADOFAI tile track behind planets
        cx, cy = rect.width() / 2.0, rect.height() / 2.0
        p.setPen(QPen(QColor("#383838"), 1))
        p.setBrush(QColor("#242424"))
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

        p.setPen(QPen(QColor(BORDER_SUBTLE), 1))
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
        self.setWindowTitle("행성(공) 색상 및 불투명도 설정")
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
        grid.addWidget(QLabel("불 행성 (1번 공)"), 0, 0)
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
        grid.addWidget(QLabel("불투명도"), 1, 0)
        grid.addWidget(self.sld_red_a, 1, 1, 1, 2)
        grid.addWidget(self.spn_red_a, 1, 3)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {BORDER_SUBTLE};")
        grid.addWidget(sep, 2, 0, 1, 4)

        # Blue planet (2)
        grid.addWidget(QLabel("얼음 행성 (2번 공)"), 3, 0)
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
        grid.addWidget(QLabel("불투명도"), 4, 0)
        grid.addWidget(self.sld_blue_a, 4, 1, 1, 2)
        grid.addWidget(self.spn_blue_a, 4, 3)

        root.addLayout(grid)

        note = QLabel("기본값: 불투명도 100% 순수 빨강(#FF0000) · 순수 파랑(#0000FF). 3구체 레벨은 두 색상 사이로 자동 보간됩니다.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        root.addWidget(note)

        btn_row = QHBoxLayout()
        btn_reset = QPushButton("기본값 복원")
        btn_reset.clicked.connect(self._reset_defaults)
        btn_cancel = QPushButton("취소")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("적용")
        btn_ok.setObjectName("primaryBtn")
        btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(btn_reset)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        root.addLayout(btn_row)

    def _sync_ui(self) -> None:
        self.btn_red_swatch.setStyleSheet(f"background-color: {self.red_color}; border: 1px solid #555;")
        self.btn_blue_swatch.setStyleSheet(f"background-color: {self.blue_color}; border: 1px solid #555;")
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
        col = QColorDialog.getColor(cur, self, "행성 색상 선택")
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
            self.btn_red_swatch.setStyleSheet(f"background-color: {self.red_color}; border: 1px solid #555;")
            self.btn_blue_swatch.setStyleSheet(f"background-color: {self.blue_color}; border: 1px solid #555;")
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
        self.setWindowTitle("시퀀스 및 구간 설정")
        self.setFixedWidth(410)
        self._duration = max(1.0, duration_sec)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(10)

        g = QGridLayout()
        g.setSpacing(8)

        g.addWidget(QLabel("시작 시점 (초):"), 0, 0)
        self.spn_start = QDoubleSpinBox()
        self.spn_start.setRange(0.0, 36000.0)
        self.spn_start.setDecimals(1)
        self.spn_start.setValue(start_sec)
        self.spn_start.setSuffix(" s")
        g.addWidget(self.spn_start, 0, 1)

        g.addWidget(QLabel("종료 시점 (0 = 끝까지):"), 1, 0)
        self.spn_end = QDoubleSpinBox()
        self.spn_end.setRange(0.0, 36000.0)
        self.spn_end.setDecimals(1)
        self.spn_end.setValue(end_sec)
        self.spn_end.setSuffix(" s")
        g.addWidget(self.spn_end, 1, 1)

        g.addWidget(QLabel("클리어 후 여백:"), 2, 0)
        self.spn_pad = QDoubleSpinBox()
        self.spn_pad.setRange(0.0, 30.0)
        self.spn_pad.setDecimals(1)
        self.spn_pad.setValue(end_padding_sec)
        self.spn_pad.setSuffix(" s")
        g.addWidget(self.spn_pad, 2, 1)

        self.chk_hide_cd = QCheckBox("시작 카운트다운(3-2-1) 숨김 (제목·판정·Auto 아이콘은 항상 숨김)")
        self.chk_hide_cd.setChecked(hide_countdown)
        g.addWidget(self.chk_hide_cd, 3, 0, 1, 2)

        root.addLayout(g)

        btn_row = QHBoxLayout()
        btn_full = QPushButton("전체 구간으로 초기화")
        btn_full.clicked.connect(self._reset_full)
        btn_cancel = QPushButton("취소")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("적용")
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


class ExportSettingsDialog(QDialog):
    """Export video, audio, GPU/RAM/CPU resource limits (default GPU 90% / RAM 80%), and low-spec safety settings."""

    def __init__(
        self,
        state: dict,
        caps: Optional[HardwareCapabilities],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("세부 설정 (하드웨어 · 화질 · 오디오)")
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

        gh.addWidget(QLabel("GPU 가속 백엔드:"), 0, 0)
        self.cmb_gpu = QComboBox()
        self.cmb_gpu.addItem("자동 (NVIDIA CUDA 우선 → 내장 GPU → CPU)", "auto")
        self.cmb_gpu.addItem("NVIDIA CUDA / NVENC", "nvenc")
        self.cmb_gpu.addItem("내장 그래픽 (Intel QSV / AMD AMF)", "igpu")
        self.cmb_gpu.addItem("CPU 소프트웨어 (libx264 / libx265)", "cpu")
        g_idx = {"auto": 0, "nvenc": 1, "igpu": 2, "cpu": 3}.get(self.state["gpu_mode"], 0)
        self.cmb_gpu.setCurrentIndex(g_idx)
        gh.addWidget(self.cmb_gpu, 0, 1, 1, 2)

        gh.addWidget(QLabel("최대 GPU 사용 상한:"), 1, 0)
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

        gh.addWidget(QLabel("최대 RAM 사용 상한:"), 2, 0)
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
        gh.addWidget(QLabel("최대 점유 CPU 코어:"), 3, 0)
        self.cmb_cpu_cores = QComboBox()
        self.cmb_cpu_cores.addItem(f"자동 (전체 {cpu_count} 스레드 활용)", 0)
        for c in sorted({1, 2, 4, 6, 8, 12, 16, cpu_count}):
            if c <= cpu_count:
                self.cmb_cpu_cores.addItem(f"최대 {c} 코어", c)
        cur_cores = int(self.state.get("max_cpu_cores", 0))
        for idx in range(self.cmb_cpu_cores.count()):
            if self.cmb_cpu_cores.itemData(idx) == cur_cores:
                self.cmb_cpu_cores.setCurrentIndex(idx)
                break
        gh.addWidget(self.cmb_cpu_cores, 3, 1, 1, 2)

        gh.addWidget(QLabel("저사양 안전 우회:"), 4, 0)
        self.cmb_lowspec = QComboBox()
        self.cmb_lowspec.addItem("자동 감지 (권장 · 오류 시 동기 캡처/CPU 자동 우회)", "auto")
        self.cmb_lowspec.addItem("강제 활성화 (저VRAM 단일 슬롯 + 동기 ReadPixels)", "force")
        self.cmb_lowspec.addItem("표준 고속 모드 (실패 시에만 안전 우회)", "off")
        ls_idx = {"auto": 0, "force": 1, "off": 2}.get(self.state.get("low_spec_mode", "auto"), 0)
        self.cmb_lowspec.setCurrentIndex(ls_idx)
        gh.addWidget(self.cmb_lowspec, 4, 1, 1, 2)

        self.chk_opt_render = QCheckBox("최적화 렌더 (실험): 불투명도 0%·화면 밖 장식 제외 및 고해상도 장식 LOD 프록시 교체")
        self.chk_opt_render.setChecked(bool(self.state.get("experimental_optimize_render", False)))
        gh.addWidget(self.chk_opt_render, 5, 0, 1, 3)

        hw_note = QLabel(
            "기본값: GPU 90% · RAM 80% · CPU 자동. 최적화 렌더(실험)는 512px 이상 고해상도 장식의 "
            "멀티 해상도 프록시를 사전 생성해 화면 픽셀 크기가 작을 때만 자동 교체하며(1.35× 나이퀴스트 마진), "
            "최종 결과물 화질은 유지하면서 병목을 줄입니다."
        )
        hw_note.setWordWrap(True)
        hw_note.setObjectName("muted")
        gh.addWidget(hw_note, 6, 0, 1, 3)

        tabs.addTab(t_hw, "하드웨어 · 리소스")

        # Tab 2: Encoding Quality & Audio Mixing (non-overlapping with main Output panel)
        t_qa = QWidget()
        gq = QGridLayout(t_qa)
        gq.setContentsMargins(14, 14, 14, 14)
        gq.setSpacing(10)

        gq.addWidget(QLabel("인코딩 화질:"), 0, 0)
        self.cmb_qual = QComboBox()
        self.cmb_qual.addItem("최고 화질 (CQ 15 · PV 마스터)", "ultra")
        self.cmb_qual.addItem("고화질 (CQ 19 · 권장)", "high")
        self.cmb_qual.addItem("표준 용량 (CQ 23)", "balanced")
        q_idx = {"ultra": 0, "high": 1, "balanced": 2}.get(self.state["quality"], 1)
        self.cmb_qual.setCurrentIndex(q_idx)
        gq.addWidget(self.cmb_qual, 0, 1, 1, 2)

        gq.addWidget(QLabel("배경음악 (BGM) 볼륨:"), 1, 0)
        self.sld_bgm = QSlider(Qt.Horizontal)
        self.sld_bgm.setRange(0, 200)
        self.sld_bgm.setValue(int(round(self.state["bgm_volume"] * 100)))
        self.spn_bgm = QSpinBox()
        self.spn_bgm.setRange(0, 200)
        self.spn_bgm.setSuffix("%")
        self.spn_bgm.setValue(int(round(self.state["bgm_volume"] * 100)))
        self.sld_bgm.valueChanged.connect(self.spn_bgm.setValue)
        self.spn_bgm.valueChanged.connect(self.sld_bgm.setValue)
        gq.addWidget(self.sld_bgm, 1, 1)
        gq.addWidget(self.spn_bgm, 1, 2)

        gq.addWidget(QLabel("인게임 타격음 볼륨:"), 2, 0)
        self.sld_hit = QSlider(Qt.Horizontal)
        self.sld_hit.setRange(0, 200)
        self.sld_hit.setValue(int(round(self.state["hitsound_volume"] * 100)))
        self.spn_hit = QSpinBox()
        self.spn_hit.setRange(0, 200)
        self.spn_hit.setSuffix("%")
        self.spn_hit.setValue(int(round(self.state["hitsound_volume"] * 100)))
        self.sld_hit.valueChanged.connect(self.spn_hit.setValue)
        self.spn_hit.valueChanged.connect(self.sld_hit.setValue)
        gq.addWidget(self.sld_hit, 2, 1)
        gq.addWidget(self.spn_hit, 2, 2)

        gq.addWidget(QLabel("오디오 비트레이트:"), 3, 0)
        self.cmb_abit = QComboBox()
        for br in ("320k", "256k", "192k"):
            self.cmb_abit.addItem(f"AAC {br}", br)
        ab_idx = {"320k": 0, "256k": 1, "192k": 2}.get(self.state.get("audio_bitrate", "320k"), 0)
        self.cmb_abit.setCurrentIndex(ab_idx)
        gq.addWidget(self.cmb_abit, 3, 1, 1, 2)

        gq.setRowStretch(4, 1)
        tabs.addTab(t_qa, "화질 · 오디오 믹싱")

        root.addWidget(tabs)

        btn_row = QHBoxLayout()
        btn_def = QPushButton("기본값 복원")
        btn_def.clicked.connect(self._restore_defaults)
        btn_cancel = QPushButton("취소")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("저장")
        btn_ok.setObjectName("primaryBtn")
        btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(btn_def)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        root.addLayout(btn_row)

    def _restore_defaults(self) -> None:
        self.cmb_qual.setCurrentIndex(1)
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
            "quality": self.cmb_qual.currentData(),
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
        self.setWindowTitle("최종 렌더링 설정 확인")
        self.setFixedWidth(510)
        self.open_settings_requested = False

        est_sec, target_frames, clip_dur = estimate_render_time_sec(settings, caps, level_duration_sec)
        eta_dt = datetime.datetime.now() + datetime.timedelta(seconds=est_sec)
        enc_name, enc_label = resolve_video_encoder(caps, settings.gpu_mode, settings.codec) if caps else ("auto", "자동")

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(12)

        hdr = QLabel("렌더링을 시작하기 전에 최종 설정을 확인하세요")
        f = QFont()
        f.setPointSize(11)
        f.setBold(True)
        hdr.setFont(f)
        root.addWidget(hdr)

        # ETA Highlight Banner (clean neutral dark box with warm left accent bar)
        eta_box = QFrame()
        eta_box.setStyleSheet(
            f"QFrame {{ background-color: #161616; border: 1px solid {BORDER_DEFAULT}; "
            f"border-left: 3px solid {ACCENT_WARM}; border-radius: 3px; }}"
        )
        eta_lay = QHBoxLayout(eta_box)
        eta_lay.setContentsMargins(12, 10, 12, 10)
        eta_left = QVBoxLayout()
        eta_left.setSpacing(2)
        lbl_eta_k = QLabel("예상 소요 시간")
        lbl_eta_k.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px; border: none;")
        lbl_eta_v = QLabel(format_duration_korean(est_sec))
        lbl_eta_v.setStyleSheet("color: #f0f0f0; font-size: 15px; font-weight: 600; border: none;")
        eta_left.addWidget(lbl_eta_k)
        eta_left.addWidget(lbl_eta_v)

        eta_right = QVBoxLayout()
        eta_right.setSpacing(2)
        eta_right.setAlignment(Qt.AlignRight)
        lbl_clk_k = QLabel("예상 완료 시각")
        lbl_clk_k.setAlignment(Qt.AlignRight)
        lbl_clk_k.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px; border: none;")
        lbl_clk_v = QLabel(eta_dt.strftime("%H:%M:%S"))
        lbl_clk_v.setAlignment(Qt.AlignRight)
        lbl_clk_v.setStyleSheet(
            f"color: {ACCENT_WARM}; font-family: 'Cascadia Mono', Consolas, monospace; "
            f"font-size: 15px; font-weight: 600; border: none;"
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
            end_str = format_timecode(settings.end_sec) if settings.end_sec > 0.05 else "끝"
            range_str = f"구간 트림 ({format_timecode(settings.start_sec)} – {end_str} · {clip_dur:.1f}초 · {target_frames:,}프레임)"
        else:
            range_str = f"전체 렌더링 ({clip_dur:.1f}초 · {target_frames:,}프레임)"

        ext = os.path.splitext(settings.output_path)[1].lstrip(".").upper() or "MP4"
        q_label = {"ultra": "최고 화질", "high": "고화질", "balanced": "표준"}.get(settings.quality, settings.quality)
        vid_str = f"{settings.width} × {settings.height}  ·  {settings.fps} fps CFR  ·  {ext} ({settings.codec.upper()})  ·  {q_label}"

        bgm_s = f"BGM {int(round(settings.bgm_volume*100))}%" if settings.include_bgm else "BGM 끔"
        hit_s = f"타격음 {int(round(settings.hitsound_volume*100))}%" if settings.include_hitsounds else "타격음 끔"
        aud_str = f"{bgm_s}  ·  {hit_s}  ·  AAC {settings.audio_bitrate}"

        cpu_s = f"CPU {settings.max_cpu_cores}코어" if settings.max_cpu_cores > 0 else "CPU 자동"
        ls_s = {"auto": "안전 폴백 자동", "force": "저사양 안전 강제", "off": "표준 고속"}.get(settings.low_spec_mode, "자동")
        opt_s = " · 최적화 렌더(실험) ON" if settings.experimental_optimize_render else ""
        hw_str = f"{enc_label}  ·  GPU {settings.gpu_limit_percent}% · RAM {settings.ram_limit_percent}% · {cpu_s} ({ls_s}{opt_s})"

        rows = [
            ("레벨", title),
            ("렌더 구간", range_str),
            ("비디오 스펙", vid_str),
            ("오디오", aud_str),
            ("하드웨어", hw_str),
            ("출력 파일", settings.output_path),
        ]
        for r, (k, v) in enumerate(rows):
            lk = QLabel(k)
            lk.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;")
            lk.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            lv = QLabel(v)
            if k == "출력 파일":
                fm = QFontMetrics(lv.font())
                lv.setText(fm.elidedText(v, Qt.ElideMiddle, 360))
                lv.setToolTip(v)
                lv.setStyleSheet(f"color: {TEXT_PRIMARY}; font-family: 'Cascadia Mono', Consolas, monospace; font-size: 11px;")
            else:
                lv.setWordWrap(True)
                lv.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 12px;")
            grid.addWidget(lk, r, 0)
            grid.addWidget(lv, r, 1)

        # Planet color row with real color swatches
        row_p = len(rows)
        lk_p = QLabel("행성 색상")
        lk_p.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;")
        p_box = QHBoxLayout()
        p_box.setSpacing(8)
        for col_hex, op, name in (
            (settings.red_planet_color, settings.red_planet_opacity, "불"),
            (settings.blue_planet_color, settings.blue_planet_opacity, "얼음"),
        ):
            dot = QLabel()
            dot.setFixedSize(11, 11)
            dot.setStyleSheet(f"background-color: {col_hex}; border: 1px solid #555; border-radius: 5px;")
            txt = QLabel(f"{name} {col_hex.upper()} ({int(round(op*100))}%)")
            txt.setStyleSheet("font-family: 'Cascadia Mono', Consolas, monospace; font-size: 11px;")
            p_box.addWidget(dot)
            p_box.addWidget(txt)
        p_box.addStretch(1)
        grid.addWidget(lk_p, row_p, 0)
        grid.addLayout(p_box, row_p, 1)

        grid.setColumnMinimumWidth(0, 74)
        grid.setColumnStretch(1, 1)
        root.addWidget(card)

        btn_row = QHBoxLayout()
        btn_edit = QPushButton("설정 수정...")
        btn_edit.clicked.connect(self._request_edit)
        btn_cancel = QPushButton("취소")
        btn_cancel.clicked.connect(self.reject)
        btn_go = QPushButton("렌더링 시작")
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
