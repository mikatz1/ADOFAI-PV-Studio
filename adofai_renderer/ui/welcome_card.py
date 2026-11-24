"""Startup Welcome Card: preloads core engine, GPU/encoders, and binaries before launching the dashboard."""

from __future__ import annotations

from typing import List, Optional
import os

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from ..preloader import PreloadResult, run_startup_preload
from ..version import __version__
from .render_card import ClaudeSpinnerGlyph
from .theme import (
    get_asset_path,
    get_palette,
)


class PreloadWorker(QThread):
    step_updated = Signal(int, int, str, str, float)
    preload_done = Signal(object)

    def run(self) -> None:
        res = run_startup_preload(
            progress_cb=lambda s, t, title, detail, pct: self.step_updated.emit(s, t, title, detail, pct)
        )
        self.preload_done.emit(res)


class _DualOrbitMark(QWidget):
    """Minimal geometric Fire & Ice dual-orbit emblem drawn cleanly with QPainter."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(36, 36)
        self._angle = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def _tick(self) -> None:
        self._angle = (self._angle + 3.2) % 360.0
        self.update()

    def stop(self) -> None:
        self._timer.stop()

    def paintEvent(self, _ev) -> None:
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        cx, cy = self.width() / 2.0, self.height() / 2.0
        r = 11.0
        p.setPen(QPen(QColor("#383838"), 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))

        rad = math.radians(self._angle)
        x1 = cx + math.cos(rad) * r
        y1 = cy + math.sin(rad) * r
        x2 = cx - math.cos(rad) * r
        y2 = cy - math.sin(rad) * r

        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#e53935"))
        p.drawEllipse(int(x1 - 4), int(y1 - 4), 8, 8)
        p.setBrush(QColor("#2979ff"))
        p.drawEllipse(int(x2 - 4), int(y2 - 4), 8, 8)
        p.end()


class WelcomeCard(QWidget):
    ready = Signal(object)  # PreloadResult

    STEP_KEYS = [
        "welcome.step1",
        "welcome.step2",
        "welcome.step3",
        "welcome.step4",
        "welcome.step5",
    ]

    def __init__(self, parent: Optional[QWidget] = None, auto_start: bool = True) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setFixedSize(460, 300)
        pal = get_palette()
        self.setStyleSheet(f"WelcomeCard {{ background-color: {pal.bg_panel}; border: 1px solid {pal.border_default}; }}")

        self._result: Optional[PreloadResult] = None
        self._step_labels: List[QLabel] = []
        self._step_dots: List[QLabel] = []
        self._sheen_pos: float = -0.25
        self._sheen_timer = QTimer(self)
        self._sheen_timer.setInterval(33)
        self._sheen_timer.timeout.connect(self._tick_sheen)

        self._build_ui()
        if auto_start:
            self._sheen_timer.start()
            self._setup_fade_in()
            self._start_preload()

    def _tick_sheen(self) -> None:
        self._sheen_pos += 0.024
        if self._sheen_pos > 1.35:
            self._sheen_pos = -0.35
        self.update()

    def paintEvent(self, _ev) -> None:
        pal = get_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = self.rect()
        p.fillRect(r, QColor(pal.bg_panel))

        if pal.is_light:
            # Subtle top header pastel tint
            hdr_rect = QRectF(1, 1, r.width() - 2, 74)
            hdr_grad = QLinearGradient(0, 0, r.width(), 74)
            hdr_grad.setColorAt(0.0, QColor("#F3F9FF"))
            hdr_grad.setColorAt(0.65, QColor("#FFFFFF"))
            hdr_grad.setColorAt(1.0, QColor("#FFF5FA"))
            p.fillRect(hdr_rect, hdr_grad)

            # Delicate geometric diagonal accent stripes on upper-right
            p.save()
            p.setClipRect(hdr_rect)
            for idx, (col_hex, x0, w) in enumerate((("#E2F1FF", r.width() - 96, 18), ("#FFE4F0", r.width() - 68, 12), ("#ECF6FF", r.width() - 46, 26))):
                poly = QPainterPath()
                poly.moveTo(x0, 0)
                poly.lineTo(x0 + w, 0)
                poly.lineTo(x0 + w - 26, 76)
                poly.lineTo(x0 - 26, 76)
                poly.closeSubpath()
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(col_hex))
                p.drawPath(poly)
            p.restore()

            # Top pastel blue -> pastel pink dual ribbon with subtle animated sheen
            bar_grad = QLinearGradient(0, 0, r.width(), 0)
            bar_grad.setColorAt(0.0, QColor("#5CC0FF"))
            bar_grad.setColorAt(0.62, QColor("#49A4FF"))
            bar_grad.setColorAt(1.0, QColor("#F58CBE"))
            p.fillRect(QRectF(0, 0, r.width(), 4), bar_grad)
            if self._sheen_timer.isActive():
                sx = self._sheen_pos * r.width()
                sh = QLinearGradient(sx - 45, 0, sx + 45, 0)
                sh.setColorAt(0.0, QColor(255, 255, 255, 0))
                sh.setColorAt(0.5, QColor(255, 255, 255, 175))
                sh.setColorAt(1.0, QColor(255, 255, 255, 0))
                p.fillRect(QRectF(0, 0, r.width(), 4), sh)
        elif self._sheen_timer.isActive():
            # Subtle cool accent shimmer along top hairline in Dark Mode
            sx = self._sheen_pos * r.width()
            sh = QLinearGradient(sx - 55, 0, sx + 55, 0)
            sh.setColorAt(0.0, QColor(74, 158, 255, 0))
            sh.setColorAt(0.5, QColor(74, 158, 255, 160))
            sh.setColorAt(1.0, QColor(74, 158, 255, 0))
            p.fillRect(QRectF(0, 0, r.width(), 2), sh)

        p.setPen(QPen(QColor(pal.border_default), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r.adjusted(0, 0, -1, -1))
        p.end()

    def _build_ui(self) -> None:
        pal = get_palette()
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24 if pal.is_light else 22, 24, 20)
        root.setSpacing(12)

        # Header row
        hdr = QHBoxLayout()
        hdr.setSpacing(12)
        icon_png = get_asset_path("app_icon.png")
        if os.path.isfile(icon_png):
            self._mark = None
            lbl_icon = QLabel()
            lbl_icon.setFixedSize(36, 36)
            lbl_icon.setStyleSheet("background: transparent;")
            pm = QPixmap(icon_png).scaled(36, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            lbl_icon.setPixmap(pm)
            hdr.addWidget(lbl_icon)
        else:
            self._mark = _DualOrbitMark()
            hdr.addWidget(self._mark)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        lbl_title = QLabel("ADOFAI PV Renderer")
        f = QFont()
        f.setPointSize(13)
        f.setBold(True)
        lbl_title.setFont(f)
        lbl_title.setStyleSheet(f"color: {pal.text_primary}; background: transparent;")
        self.lbl_sub = QLabel(tr("welcome.sub_init"))
        self.lbl_sub.setStyleSheet(f"color: {pal.text_secondary}; font-size: 11px; background: transparent;")
        title_col.addWidget(lbl_title)
        title_col.addWidget(self.lbl_sub)
        hdr.addLayout(title_col, 1)

        lbl_ver = QLabel(f"v{__version__}")
        if pal.is_light:
            lbl_ver.setStyleSheet(
                "color: #2484E8; background-color: #EAF4FF; border: 1px solid #B7D9FA; "
                "border-radius: 9px; padding: 2px 8px; "
                "font-family: 'Cascadia Mono', Consolas, monospace; font-size: 10px; font-weight: 700;"
            )
        else:
            lbl_ver.setStyleSheet(
                f"color: {pal.text_muted}; font-family: 'Cascadia Mono', Consolas, monospace; font-size: 11px; background: transparent;"
            )
        hdr.addWidget(lbl_ver, 0, Qt.AlignTop)
        root.addLayout(hdr)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {pal.border_subtle};")
        root.addWidget(sep)

        # Step checklist
        list_box = QVBoxLayout()
        list_box.setSpacing(6)
        for key in self.STEP_KEYS:
            row = QHBoxLayout()
            row.setSpacing(8)
            dot = QLabel("·")
            dot.setFixedWidth(14)
            dot.setAlignment(Qt.AlignCenter)
            dot.setStyleSheet(f"color: {pal.text_muted}; font-family: 'Cascadia Mono', Consolas, monospace; font-size: 12px; background: transparent;")
            lbl = QLabel(tr(key))
            lbl.setStyleSheet(f"color: {pal.text_muted}; font-size: 12px; background: transparent;")
            row.addWidget(dot)
            row.addWidget(lbl, 1)
            list_box.addLayout(row)
            self._step_dots.append(dot)
            self._step_labels.append(lbl)
        root.addLayout(list_box)

        root.addStretch(1)

        # Detail status line with blue starburst spinner matching current preload stage
        det_row = QHBoxLayout()
        det_row.setContentsMargins(0, 0, 0, 0)
        det_row.setSpacing(7)
        self.spinner = ClaudeSpinnerGlyph()
        det_row.addWidget(self.spinner)
        self.lbl_detail = QLabel(tr("welcome.detail_init"))
        self.lbl_detail.setStyleSheet(
            f"color: {pal.accent_status_blue}; font-family: 'Cascadia Mono', Consolas, 'Malgun Gothic', 'Yu Gothic UI', monospace; font-size: 11px; background: transparent;"
        )
        det_row.addWidget(self.lbl_detail, 1)
        root.addLayout(det_row)

        # Slim progress bar
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6 if pal.is_light else 5)
        self.progress.setStyleSheet(
            f"QProgressBar {{ background: {pal.prog_bg}; border: none; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {pal.prog_chunk_start}, stop:1 {pal.prog_chunk_end}); border-radius: 3px; }}"
        )
        root.addWidget(self.progress)

    def _setup_fade_in(self) -> None:
        self.setWindowOpacity(0.0)
        self._fade = QPropertyAnimation(self, b"windowOpacity")
        self._fade.setDuration(230)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._fade.start()

        def _slide_up() -> None:
            try:
                end_pos = self.pos()
                start_pos = end_pos + QPointF(0, 10).toPoint()
                self.move(start_pos)
                self._slide = QPropertyAnimation(self, b"pos", self)
                self._slide.setDuration(230)
                self._slide.setStartValue(start_pos)
                self._slide.setEndValue(end_pos)
                self._slide.setEasingCurve(QEasingCurve.OutCubic)
                self._slide.start()
            except RuntimeError:
                pass

        QTimer.singleShot(0, _slide_up)

    def _start_preload(self) -> None:
        self._worker = PreloadWorker(self)
        self._worker.step_updated.connect(self._on_step)
        self._worker.preload_done.connect(self._on_done)
        self._worker.start()

    def _on_step(self, step: int, _total: int, title: str, detail: str, pct: float) -> None:
        pal = get_palette()
        idx = max(0, min(len(self._step_labels) - 1, step - 1))
        for i in range(len(self._step_labels)):
            if i < idx or pct >= 99.9:
                self._step_dots[i].setText("✓")
                self._step_dots[i].setStyleSheet(f"color: {pal.accent_success}; font-weight: bold; font-size: 11px; background: transparent;")
                self._step_labels[i].setStyleSheet(f"color: {pal.text_secondary}; font-size: 12px; background: transparent;")
            elif i == idx:
                self._step_dots[i].setText("›")
                self._step_dots[i].setStyleSheet(f"color: {pal.accent_status_blue}; font-weight: bold; font-size: 13px; background: transparent;")
                self._step_labels[i].setStyleSheet(f"color: {pal.text_primary}; font-weight: 600; font-size: 12px; background: transparent;")
            else:
                self._step_dots[i].setText("·")
                self._step_dots[i].setStyleSheet(f"color: {pal.text_muted}; font-size: 12px; background: transparent;")
                self._step_labels[i].setStyleSheet(f"color: {pal.text_muted}; font-size: 12px; background: transparent;")

        if pct >= 99.9:
            self.lbl_sub.setText(tr("welcome.ready"))
            self.spinner.set_state("done")
            self.lbl_detail.setStyleSheet(
                f"color: {pal.accent_success}; font-family: 'Cascadia Mono', Consolas, 'Malgun Gothic', 'Yu Gothic UI', monospace; font-size: 11px; background: transparent;"
            )
        else:
            if title:
                self.lbl_sub.setText(f"[{step}/{_total}] {title}")
            self.spinner.set_state("active")
        self.lbl_detail.setText(detail)
        # Smooth progress bar animation
        self._bar_anim = QPropertyAnimation(self.progress, b"value")
        self._bar_anim.setDuration(180)
        self._bar_anim.setStartValue(self.progress.value())
        self._bar_anim.setEndValue(int(round(pct * 10)))
        self._bar_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._bar_anim.start()

    def _on_done(self, res: PreloadResult) -> None:
        self._result = res
        QTimer.singleShot(260, self._fade_out_and_emit)

    def _fade_out_and_emit(self) -> None:
        self._sheen_timer.stop()
        if self._mark is not None:
            self._mark.stop()
        self.spinner.set_state("done")
        self._fade_out = QPropertyAnimation(self, b"windowOpacity")
        self._fade_out.setDuration(180)
        self._fade_out.setStartValue(self.windowOpacity())
        self._fade_out.setEndValue(0.0)
        self._fade_out.setEasingCurve(QEasingCurve.InCubic)
        self._fade_out.finished.connect(self._emit_ready)
        self._fade_out.start()

    def _emit_ready(self) -> None:
        self.hide()
        if self._result is not None:
            self.ready.emit(self._result)
