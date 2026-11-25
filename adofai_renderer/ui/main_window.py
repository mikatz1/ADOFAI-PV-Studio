"""Main Studio Workstation Window: Native Dock Panes + Calibrated Stereo VU Meter + Interactive Inspector + 3-Lane Timeline."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from typing import List, Optional

import cv2
from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QThread, QTimer, QUrl, QVariantAnimation, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QDesktopServices, QDragEnterEvent, QDropEvent, QFont, QFontMetrics, QImage, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QScrollBar,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QSplitterHandle,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..gpu_detect import HardwareCapabilities, resolve_video_encoder
from ..i18n import LANGUAGE_NAMES, SUPPORTED_LANGUAGES, get_language, set_language, tr
from ..level_info import AdofaiLevelInfo, parse_adofai_info
from ..level_loader import estimate_level_duration_sec, extract_audio_waveform, resolve_level_input
from ..paths import get_default_output_dir
from ..updater import UpdateInfo, check_for_update, download_installer, launch_installer
from ..version import __version__
from ..preloader import PreloadResult
from ..render_runner import (
    DEFAULT_PROXY_FPS,
    DEFAULT_PROXY_HEIGHT,
    DEFAULT_PROXY_MODE,
    DEFAULT_PROXY_WIDTH,
    PREVIEW_CACHE_DIR,
    PROJECT_ROOT,
    PreviewResult,
    RenderProgress,
    RenderResult,
    RenderRunner,
    RenderSettings,
    compute_preview_cache_key,
    estimate_output_size_mb,
    estimate_render_time_sec,
    get_warm_session_manager,
    load_proxy_settings,
    normalize_proxy_mode,
    save_proxy_settings,
)
from .dialogs import (
    ExportSettingsDialog,
    PlanetSettingsDialog,
    ProxySettingsDialog,
    RES_PRESETS,
    RenderConfirmDialog,
    SequenceSettingsDialog,
)
from .render_card import RenderCardDialog
from .theme import (
    SUPPORTED_THEMES,
    build_studio_qss,
    fade_in_widget,
    format_duration_korean,
    format_timecode,
    format_timecode_ms,
    get_palette,
    get_theme,
    make_icon,
    set_theme,
)
from .timeline_widget import TimelineTrimWidget


class WaveformWorker(QThread):
    ready = Signal(str, object)

    def __init__(self, level_path: str, song_path: str, ffmpeg_path: str) -> None:
        super().__init__()
        self.level_path = level_path
        self.song_path = song_path
        self.ffmpeg_path = ffmpeg_path

    def run(self) -> None:
        peaks = extract_audio_waveform(self.song_path, self.ffmpeg_path, bins=1000)
        self.ready.emit(self.level_path, peaks)


class TimecodeSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox displaying unified MM:SS.d timecodes while storing exact seconds."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setDecimals(1)
        self.setSingleStep(0.5)
        self.setStyleSheet("font-family: 'Cascadia Mono', Consolas, monospace; font-size: 11px;")

    def textFromValue(self, value: float) -> str:
        return format_timecode(value)

    def valueFromText(self, text: str) -> float:
        s = text.strip()
        if ":" in s:
            parts = s.split(":")
            try:
                if len(parts) == 2:
                    return max(0.0, int(parts[0]) * 60.0 + float(parts[1]))
                if len(parts) == 3:
                    return max(0.0, int(parts[0]) * 3600.0 + int(parts[1]) * 60.0 + float(parts[2]))
            except ValueError:
                return self.value()
        try:
            return max(0.0, float(s.replace("초", "").strip()))
        except ValueError:
            return self.value()

    def validate(self, text: str, pos: int):
        from PySide6.QtGui import QValidator
        return (QValidator.Acceptable, text, pos)


class GripSplitterHandle(QSplitterHandle):
    """Native Qt splitter handle with subtle centered 3-dot resize grip."""

    def paintEvent(self, ev) -> None:
        pal = get_palette()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(pal.splitter_bg))
        cx = self.width() // 2
        cy = self.height() // 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(pal.splitter_dot))
        if self.orientation() == Qt.Horizontal:
            for dy in (-6, 0, 6):
                p.drawRect(cx - 1, cy + dy - 1, 2, 2)
        else:
            for dx in (-6, 0, 6):
                p.drawRect(cx + dx - 1, cy - 1, 2, 2)
        p.end()


class GripSplitter(QSplitter):
    """QSplitter using GripSplitterHandle for authentic desktop dock resize handles."""

    def createHandle(self) -> QSplitterHandle:
        return GripSplitterHandle(self.orientation(), self)


class UpdateCheckWorker(QThread):
    result = Signal(object, str)  # (UpdateInfo | None, error)

    def run(self) -> None:
        try:
            self.result.emit(check_for_update(), "")
        except Exception as ex:  # offline / API limits must never disturb the app
            self.result.emit(None, str(ex))


class UpdateDownloadWorker(QThread):
    progress = Signal(int, int)
    done = Signal(str, str)  # (installer path, error)

    def __init__(self, info: UpdateInfo) -> None:
        super().__init__()
        self.info = info
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            path = download_installer(self.info, lambda d, t: self.progress.emit(d, t), lambda: self._cancel)
            self.done.emit(path, "")
        except Exception as ex:
            self.done.emit("", str(ex))


class PreviewBuildWorker(QThread):
    progress = Signal(int, object)
    done = Signal(int, object)

    def __init__(
        self,
        gen_id: int,
        runner: RenderRunner,
        level_path: str,
        adofai_dir: str,
        red_color: str,
        red_opacity: float,
        blue_color: str,
        blue_opacity: float,
        gpu_limit: int,
        force_rebuild: bool = False,
        proxy_width: int = DEFAULT_PROXY_WIDTH,
        proxy_height: int = DEFAULT_PROXY_HEIGHT,
        proxy_fps: int = DEFAULT_PROXY_FPS,
        proxy_mode: str = DEFAULT_PROXY_MODE,
    ) -> None:
        super().__init__()
        self.gen_id = int(gen_id)
        self.runner = runner
        self.level_path = level_path
        self.adofai_dir = adofai_dir
        self.red_color = red_color
        self.red_opacity = red_opacity
        self.blue_color = blue_color
        self.blue_opacity = blue_opacity
        self.gpu_limit = gpu_limit
        self.force_rebuild = force_rebuild
        self.proxy_width = proxy_width
        self.proxy_height = proxy_height
        self.proxy_fps = proxy_fps
        self.proxy_mode = normalize_proxy_mode(proxy_mode)

    def run(self) -> None:
        res = self.runner.generate_rough_preview(
            level_path=self.level_path,
            adofai_dir=self.adofai_dir,
            red_color=self.red_color,
            red_opacity=self.red_opacity,
            blue_color=self.blue_color,
            blue_opacity=self.blue_opacity,
            gpu_limit_percent=self.gpu_limit,
            force_rebuild=self.force_rebuild,
            progress_cb=lambda p: self.progress.emit(self.gen_id, p),
            proxy_width=self.proxy_width,
            proxy_height=self.proxy_height,
            proxy_fps=self.proxy_fps,
            proxy_mode=self.proxy_mode,
        )
        self.done.emit(self.gen_id, res)


class StereoDbMeter(QWidget):
    """Calibrated vertical stereo (L / R) broadcast VU meter with smooth ballistic decay and peak headroom."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(44)
        self._l: float = 0.0
        self._r: float = 0.0
        self._peak_l: float = 0.0
        self._peak_r: float = 0.0
        self._decay_timer = QTimer(self)
        self._decay_timer.setInterval(33)
        self._decay_timer.timeout.connect(self._tick_decay)

    def set_levels(self, left: float, right: float, clear_peak: bool = False) -> None:
        nl = max(0.0, min(1.0, float(left)))
        nr = max(0.0, min(1.0, float(right)))
        if clear_peak and nl <= 0.001 and nr <= 0.001:
            # Smooth ballistic release when stopping playback instead of jarring snap to 0
            if self._l > 0.01 or self._r > 0.01 or self._peak_l > 0.01 or self._peak_r > 0.01:
                if not self._decay_timer.isActive():
                    self._decay_timer.start()
            else:
                self._l = self._r = self._peak_l = self._peak_r = 0.0
                self._decay_timer.stop()
                self.update()
            return
        self._decay_timer.stop()
        self._l = nl
        self._r = nr
        if clear_peak:
            self._peak_l = self._l
            self._peak_r = self._r
        else:
            self._peak_l = max(self._l, self._peak_l * 0.94)
            self._peak_r = max(self._r, self._peak_r * 0.94)
        self.update()

    def _tick_decay(self) -> None:
        self._l *= 0.72
        self._r *= 0.72
        self._peak_l *= 0.78
        self._peak_r *= 0.78
        if max(self._l, self._r, self._peak_l, self._peak_r) < 0.015:
            self._l = self._r = self._peak_l = self._peak_r = 0.0
            self._decay_timer.stop()
        self.update()

    def paintEvent(self, _ev) -> None:
        pal = get_palette()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(pal.vu_bg))
        p.setPen(QPen(QColor(pal.vu_border), 1))
        p.drawLine(0, 0, 0, self.height())

        top_y = 10
        bot_y = self.height() - 18
        meter_h = max(40, bot_y - top_y)

        # Stereo L / R bar area (left 13px) + dB ticks/labels (right 22px)
        bar_rect = QRectF(4, top_y, 13, meter_h)
        p.fillRect(bar_rect, QColor(pal.vu_well_bg))
        p.setPen(QPen(QColor(pal.vu_well_border), 1))
        p.drawRect(bar_rect)

        segs = 32
        gap = 1.5
        seg_h = max(1.8, (meter_h - 4 - (segs - 1) * gap) / segs)
        ch_w = 4.5
        for idx, (lv, pk) in enumerate(((self._l, self._peak_l), (self._r, self._peak_r))):
            bx = bar_rect.left() + 1.5 + idx * (ch_w + 1.5)
            lit = int(round(lv * segs))
            pk_seg = int(round(pk * segs))
            for s in range(segs):
                sy = bar_rect.bottom() - 2 - (s + 1) * seg_h - s * gap
                frac = s / float(segs)
                if s < lit or (pk > 0.03 and s == pk_seg):
                    if frac >= 0.88:
                        col = QColor(pal.vu_high_on)
                    elif frac >= 0.68:
                        col = QColor(pal.vu_mid_on)
                    else:
                        col = QColor(pal.vu_low_on)
                else:
                    if frac >= 0.88:
                        col = QColor(pal.vu_high_off)
                    elif frac >= 0.68:
                        col = QColor(pal.vu_mid_off)
                    else:
                        col = QColor(pal.vu_low_off)
                p.fillRect(QRectF(bx, sy, ch_w, seg_h), col)

        # dB scale markings on right side with unambiguous spacing so tick never reads as a minus sign
        p.setFont(QFont("Cascadia Mono", 6))
        for db_label, frac in (("0", 0.98), ("-6", 0.84), ("-12", 0.68), ("-24", 0.44), ("-48", 0.14)):
            yy = bar_rect.bottom() - frac * meter_h
            p.setPen(QPen(QColor(pal.vu_tick), 1))
            p.drawLine(QPointF(bar_rect.right() + 1, yy), QPointF(bar_rect.right() + 2, yy))
            p.setPen(QColor(pal.vu_label))
            p.drawText(QRectF(bar_rect.right() + 6, yy - 5, 20, 10), Qt.AlignRight | Qt.AlignVCenter, db_label)

        # L / R footer label
        p.setFont(QFont("Cascadia Mono", 7))
        p.setPen(QColor(pal.vu_label))
        p.drawText(QRectF(2, bot_y + 2, 18, 13), Qt.AlignCenter, "L R")
        p.end()


class _ViewportOverlayCard(QFrame):
    """Viewport overlay dialog card with pastel-blue/pink top accent ribbon in Light Mode."""

    def paintEvent(self, _ev) -> None:
        pal = get_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if pal.is_light:
            p.setPen(QPen(QColor(pal.overlay_border), 1.0))
            p.setBrush(QColor(255, 255, 255, 246))
            p.drawRoundedRect(r, 8.0, 8.0)

            # Top pastel blue -> pastel pink bar clipped to rounded card
            clip = QPainterPath()
            clip.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 7.5, 7.5)
            p.setClipPath(clip)

            grad = QLinearGradient(r.left(), r.top(), r.right(), r.top())
            grad.setColorAt(0.0, QColor("#58BCFF"))
            grad.setColorAt(0.65, QColor("#44A2FF"))
            grad.setColorAt(1.0, QColor("#F58CBE"))
            p.fillRect(QRectF(r.left(), r.top(), r.width(), 3.5), grad)
        else:
            p.setPen(QPen(QColor(pal.overlay_border), 1.0))
            p.setBrush(QColor(24, 24, 24, 232))
            p.drawRoundedRect(r, 3.0, 3.0)
        p.end()


class PreviewViewportWidget(QWidget):
    """16:9 matte video canvas with OpenCV frame-accurate scrubbing."""

    request_generate_preview = Signal()
    request_open_level = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(560, 320)

        self._cap: Optional[cv2.VideoCapture] = None
        self._video_path: str = ""
        self._video_fps: float = 6.0
        self._video_frames: int = 0
        self._current_frame: Optional[QPixmap] = None
        self._last_frame_idx: int = -1

        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setContentsMargins(16, 16, 16, 16)

        self.overlay_card = _ViewportOverlayCard()
        self.overlay_card.setObjectName("viewportOverlayCard")
        self.overlay_card.setStyleSheet("QFrame#viewportOverlayCard { background: transparent; border: none; }")
        oc_lay = QVBoxLayout(self.overlay_card)
        oc_lay.setContentsMargins(22, 18, 22, 16)
        oc_lay.setSpacing(8)

        self.lbl_ov_title = QLabel(tr("vp.empty_title"))
        self.lbl_ov_title.setAlignment(Qt.AlignCenter)
        self.lbl_ov_sub = QLabel(tr("vp.empty_sub"))
        self.lbl_ov_sub.setAlignment(Qt.AlignCenter)

        self.ov_progress = QProgressBar()
        self.ov_progress.setRange(0, 1000)
        self.ov_progress.setFixedSize(250, 5)
        self.ov_progress.setTextVisible(False)
        self.ov_progress.hide()

        btn_row = QHBoxLayout()
        btn_row.setAlignment(Qt.AlignCenter)
        btn_row.setSpacing(8)
        self.btn_ov_open = QPushButton(tr("vp.btn_open"))
        self.btn_ov_open.clicked.connect(lambda _=False: self.request_open_level.emit())
        self.btn_ov_preview = QPushButton(tr("vp.btn_preview"))
        self.btn_ov_preview.clicked.connect(lambda _=False: self.request_generate_preview.emit())
        self.btn_ov_preview.hide()
        btn_row.addWidget(self.btn_ov_open)
        btn_row.addWidget(self.btn_ov_preview)

        oc_lay.addWidget(self.lbl_ov_title)
        oc_lay.addWidget(self.lbl_ov_sub)
        oc_lay.addWidget(self.ov_progress, 0, Qt.AlignCenter)
        oc_lay.addLayout(btn_row)
        lay.addWidget(self.overlay_card)
        self.apply_theme()

    def apply_theme(self) -> None:
        pal = get_palette()
        self.lbl_ov_title.setStyleSheet(
            f"color: {pal.overlay_title}; font-size: 13px; font-weight: 700; background: transparent; border: none;"
        )
        self.lbl_ov_sub.setStyleSheet(
            f"color: {pal.overlay_sub}; font-size: 11px; background: transparent; border: none;"
        )
        self.ov_progress.setStyleSheet(
            f"QProgressBar {{ background: {pal.prog_bg}; border: none; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {pal.prog_chunk_start}, stop:1 {pal.prog_chunk_end}); border-radius: 2px; }}"
        )
        self.btn_ov_open.setObjectName("primaryBtn" if pal.is_light else "")
        self.btn_ov_open.style().unpolish(self.btn_ov_open)
        self.btn_ov_open.style().polish(self.btn_ov_open)
        self.btn_ov_preview.setIcon(make_icon("film", pal.icon_color, 14))
        self.overlay_card.update()
        self.update()

    def retranslate_ui(self) -> None:
        self.btn_ov_open.setText(tr("vp.btn_open"))
        self.btn_ov_preview.setText(tr("vp.btn_preview"))
        if self.btn_ov_open.isVisible():
            self.lbl_ov_title.setText(tr("vp.empty_title"))
            self.lbl_ov_sub.setText(tr("vp.empty_sub"))

    def clear_video(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        self._video_path = ""
        self._current_frame = None
        self._last_frame_idx = -1
        self.update()

    def set_level_placeholder(self, title: str, detail: str) -> None:
        self.clear_video()
        self.lbl_ov_title.setText(title)
        self.lbl_ov_sub.setText(detail + "\n" + tr("vp.preview_hint"))
        self.ov_progress.hide()
        self.btn_ov_open.hide()
        self.btn_ov_preview.setEnabled(True)
        self.btn_ov_preview.show()
        self.overlay_card.show()
        fade_in_widget(self.overlay_card, start=0.25, duration_ms=160)
        self.update()

    def set_generating_state(self, active: bool, status_text: str = "", pct: float = 0.0) -> None:
        if active:
            self.lbl_ov_title.setText(tr("vp.gen_title"))
            self.lbl_ov_sub.setText(status_text or tr("vp.gen_sub_default"))
            self.ov_progress.setValue(int(round(pct * 10)))
            self.ov_progress.show()
            self.btn_ov_preview.setEnabled(False)
            self.btn_ov_preview.show()
            self.overlay_card.show()
        else:
            self.ov_progress.hide()
            self.btn_ov_preview.setEnabled(True)
            if self._current_frame is not None:
                self.overlay_card.hide()
        self.update()

    def load_video(self, video_path: str) -> bool:
        self.clear_video()
        if not video_path or not os.path.isfile(video_path):
            return False
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return False
        self._cap = cap
        self._video_path = video_path
        self._video_fps = float(cap.get(cv2.CAP_PROP_FPS) or 6.0)
        self._video_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self._last_frame_idx = -1
        self.overlay_card.hide()
        self.seek_seconds(0.0)
        return True

    def has_video(self) -> bool:
        return self._cap is not None and self._video_frames > 0

    def seek_seconds(self, sec: float) -> None:
        if self._cap is None or self._video_frames <= 0:
            return
        idx = max(0, min(self._video_frames - 1, int(round(sec * self._video_fps))))
        if idx == self._last_frame_idx and self._current_frame is not None:
            return
        if self._last_frame_idx >= 0 and idx > self._last_frame_idx + 1 and idx <= self._last_frame_idx + 4:
            for _ in range(idx - self._last_frame_idx - 1):
                self._cap.grab()
        elif idx != self._last_frame_idx + 1:
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, bgr = self._cap.read()
        if not ok or bgr is None:
            return
        self._last_frame_idx = idx
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy()
        self._current_frame = QPixmap.fromImage(qimg)
        self.update()

    def paintEvent(self, _ev) -> None:
        pal = get_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.fillRect(self.rect(), QColor(pal.bg_canvas))

        if pal.is_light:
            # Subtle blueprint micro dot-grid on light surround
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(pal.vp_grid_dot))
            step = 18
            for yy in range(9, self.height(), step):
                for xx in range(9, self.width(), step):
                    p.drawRect(xx, yy, 2, 2)

        avail = self.rect().adjusted(8, 8, -8, -8)
        target_ratio = 16.0 / 9.0
        w = float(avail.width())
        h = float(avail.height())
        if w / max(1.0, h) > target_ratio:
            dw = h * target_ratio
            dh = h
        else:
            dw = w
            dh = w / target_ratio
        stage = QRectF(avail.center().x() - dw / 2.0, avail.center().y() - dh / 2.0, dw, dh)

        if pal.is_light:
            # Soft offset drop frame behind 16:9 stage
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(152, 184, 218, 65))
            p.drawRoundedRect(stage.translated(0, 2), 6.0, 6.0)

        has_frame = self._current_frame is not None and not self._current_frame.isNull()
        if pal.is_light and not has_frame:
            # Bright airy pastel light viewport stage when awaiting level/proxy
            p.save()
            p.setClipRect(stage)
            st_grad = QLinearGradient(stage.left(), stage.top(), stage.right(), stage.bottom())
            st_grad.setColorAt(0.0, QColor("#E0EEFC"))
            st_grad.setColorAt(0.55, QColor("#F1F7FE"))
            st_grad.setColorAt(1.0, QColor("#FDEFF7"))
            p.fillRect(stage, st_grad)

            # Delicate blueprint grid lines inside empty stage
            p.setPen(QPen(QColor(174, 204, 236, 90), 1))
            gstep = 36
            gx = int(stage.left()) + gstep
            while gx < stage.right():
                p.drawLine(gx, int(stage.top()), gx, int(stage.bottom()))
                gx += gstep
            gy = int(stage.top()) + gstep
            while gy < stage.bottom():
                p.drawLine(int(stage.left()), gy, int(stage.right()), gy)
                gy += gstep
            p.restore()
        else:
            p.fillRect(stage, QColor(pal.bg_stage))
            if has_frame:
                p.drawPixmap(stage.toRect(), self._current_frame)

        p.setPen(QPen(QColor(pal.vp_stage_border), 1.5 if pal.is_light else 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawRect(stage)

        if pal.is_light:
            # Delicate corner registration accents (pastel blue top-left, pastel pink bottom-right)
            p.setPen(QPen(QColor("#52B4FF"), 2.0))
            p.drawLine(QPointF(stage.left(), stage.top()), QPointF(stage.left() + 18, stage.top()))
            p.drawLine(QPointF(stage.left(), stage.top()), QPointF(stage.left(), stage.top() + 18))
            p.setPen(QPen(QColor("#F484B6"), 2.0))
            p.drawLine(QPointF(stage.right(), stage.bottom()), QPointF(stage.right() - 18, stage.bottom()))
            p.drawLine(QPointF(stage.right(), stage.bottom()), QPointF(stage.right(), stage.bottom() - 18))
        p.end()


class ColorPickerButton(QPushButton):
    """Authentic native framed color-picker button showing inset color chip + label + opacity with hover glow."""

    def __init__(self, label: str, hex_col: str, opacity: float, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(24)
        self._label = label
        self._hex = hex_col
        self._opacity = opacity
        self._hover_t: float = 0.0
        self._hover_anim: Optional[QVariantAnimation] = None

    def _animate_hover(self, target: float) -> None:
        if self._hover_anim is not None:
            self._hover_anim.stop()
        anim = QVariantAnimation(self)
        anim.setDuration(110)
        anim.setStartValue(float(self._hover_t))
        anim.setEndValue(float(target))
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(self._set_hover_t)
        self._hover_anim = anim
        anim.start()

    def _set_hover_t(self, val) -> None:
        self._hover_t = float(val)
        self.update()

    def enterEvent(self, ev) -> None:
        self._animate_hover(1.0)
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        self._animate_hover(0.0)
        super().leaveEvent(ev)

    def set_color_state(self, hex_col: str, opacity: float) -> None:
        self._hex = hex_col
        self._opacity = opacity
        self.update()

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        pal = get_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        if self._hover_t > 0.02:
            glow = QColor(pal.accent_primary)
            glow.setAlphaF(0.45 * self._hover_t)
            p.setPen(QPen(glow, 1.4))
            p.setBrush(Qt.NoBrush)
            rr = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
            p.drawRoundedRect(rr, 5.0 if pal.is_light else 2.0, 5.0 if pal.is_light else 2.0)
        chip = QRectF(6, 5, 14, 14)
        # Checker behind chip if translucent
        p.fillRect(chip, QColor("#E2ECF8" if pal.is_light else "#1a1a1a"))
        c = QColor(self._hex)
        c.setAlphaF(max(0.0, min(1.0, self._opacity)))
        if pal.is_light:
            p.setPen(QPen(QColor(pal.swatch_border), 1))
            p.setBrush(c)
            p.drawRoundedRect(chip, 3.0, 3.0)
        else:
            p.fillRect(chip, c)
            p.setPen(QPen(QColor("#111111"), 1))
            p.drawRect(chip)
        p.setPen(QColor(pal.text_primary))
        p.setFont(QFont("Segoe UI", 8))
        txt = f"{self._label} {int(round(self._opacity * 100))}%"
        p.drawText(QRectF(25, 0, self.width() - 30, self.height()), Qt.AlignLeft | Qt.AlignVCenter, txt)
        p.end()

    def set_label(self, label: str) -> None:
        self._label = label
        self.update()


class _PaneHeaderBar(QFrame):
    """Dock pane header with clean typography and crisp border."""

    def __init__(self, left_title: str, right_text: str = "", on_close=None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("paneHdr")
        self.setFixedHeight(24)
        self._has_close = on_close is not None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 5 if on_close else 8, 0)
        lay.setSpacing(6)
        self._lay = lay

        self.l_lbl = QLabel(left_title)
        self.r_lbl = QLabel(right_text)
        lay.addWidget(self.l_lbl)
        lay.addStretch(1)
        lay.addWidget(self.r_lbl)

        self.btn_x: Optional[QPushButton] = None
        if on_close is not None:
            btn_x = QPushButton("×")
            btn_x.setObjectName("toolBtn")
            btn_x.setFixedSize(16, 16)
            btn_x.setToolTip(tr("pane.close_tip"))
            btn_x.clicked.connect(on_close)
            lay.addWidget(btn_x)
            self.btn_x = btn_x
        self.apply_theme()

    def apply_theme(self) -> None:
        pal = get_palette()
        self.setFixedHeight(25 if pal.is_light else 22)
        self._lay.setContentsMargins(10 if pal.is_light else 8, 0, 6 if self._has_close else 10, 0)
        self.setStyleSheet("QFrame#paneHdr { background: transparent; border: none; }")
        self.l_lbl.setStyleSheet(
            f"color: {pal.hdr_title}; font-size: 11px; font-weight: {'700' if pal.is_light else '600'}; background: transparent; border: none;"
        )
        self.r_lbl.setStyleSheet(
            f"color: {pal.hdr_meta}; font-family: 'Cascadia Mono', Consolas, monospace; font-size: 10px; background: transparent; border: none;"
        )
        if self.btn_x is not None:
            self.btn_x.setStyleSheet(
                f"QPushButton {{ color: {pal.hdr_close_fg}; font-size: 12px; padding: 0; border: none; background: transparent; }}"
                f"QPushButton:hover {{ color: {pal.hdr_close_hover_fg}; background: {pal.hdr_close_hover_bg}; border-radius: 3px; }}"
            )
        self.update()

    def retranslate_ui(self) -> None:
        if self.btn_x is not None:
            self.btn_x.setToolTip(tr("pane.close_tip"))

    def paintEvent(self, _ev) -> None:
        pal = get_palette()
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, QColor(pal.hdr_bg))
        p.setPen(QPen(QColor(pal.hdr_border), 1))
        p.drawLine(0, r.height() - 1, r.width(), r.height() - 1)
        p.end()


def _vsep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.VLine)
    f.setFixedWidth(1)
    f.setStyleSheet(f"background-color: {get_palette().sep_color}; border: none;")
    return f


def _hsep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setFixedHeight(1)
    f.setStyleSheet(f"background-color: {get_palette().sep_color}; border: none;")
    return f


def _pane_header(
    left_title: str,
    right_text: str = "",
    on_close=None,
) -> tuple[_PaneHeaderBar, QLabel, QLabel]:
    bar = _PaneHeaderBar(left_title, right_text, on_close=on_close)
    return bar, bar.l_lbl, bar.r_lbl


class MainWindow(QMainWindow):
    """Authentic desktop workstation: Native Dock Panes + Calibrated Stereo VU + Interactive Inspector + 3-Lane Timeline."""

    def __init__(self, preload: PreloadResult, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ADOFAI PV Studio")
        self.setMinimumSize(1020, 660)
        self.resize(1220, 720)
        self.setAcceptDrops(True)

        self.preload = preload
        self.caps: HardwareCapabilities = preload.caps
        self.adofai_dir: str = preload.adofai_dir

        self.level_path: str = ""
        self.level_info: Optional[AdofaiLevelInfo] = None
        self.level_duration: float = 60.0
        self._waveform_peaks: Optional[List[float]] = None

        # In-app defaults: GPU 90%, RAM 80%, Pure Red/Blue 100%, Full render
        self.export_state: dict = {
            "width": 1920,
            "height": 1080,
            "fps": 60,
            "format": "mp4",
            "codec": "h264",
            "quality": "high",
            "output_dir": get_default_output_dir(),
            "include_bgm": True,
            "bgm_volume": 1.0,
            "include_hitsounds": True,
            "hitsound_volume": 1.0,
            "audio_bitrate": "320k",
            "gpu_mode": "auto",
            "gpu_limit_percent": 90,
            "ram_limit_percent": 80,
            "max_cpu_cores": 0,
            "low_spec_mode": "auto",
            "experimental_optimize_render": False,
        }
        self.planet_state: dict = {
            "red_color": "#FF0000",
            "red_opacity": 1.0,
            "blue_color": "#0000FF",
            "blue_opacity": 1.0,
        }
        self.sequence_state: dict = {
            "start_sec": 0.0,
            "end_sec": 0.0,
            "end_padding_sec": 2.0,
            "hide_countdown": True,
        }
        self.proxy_state: dict = load_proxy_settings()
        self.output_path: str = ""

        self._playing: bool = False
        self._muted: bool = False
        self._playback_rate: float = 1.0
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(16)
        self._play_timer.timeout.connect(self._on_play_tick)

        self._audio_out = QAudioOutput(self)
        self._audio_out.setVolume(0.75)
        self._audio_player = QMediaPlayer(self)
        self._audio_player.setAudioOutput(self._audio_out)

        self._wf_workers: List[WaveformWorker] = []
        self._preview_worker: Optional[PreviewBuildWorker] = None
        self._preview_runner: Optional[RenderRunner] = None
        self._preview_gen_id: int = 0
        self._live_preview_workers: List[PreviewBuildWorker] = []
        self._pending_preview_action: Optional[str] = None
        self._syncing_controls: bool = False

        self._pane_headers: List[_PaneHeaderBar] = []
        self._hseps: List[QFrame] = []
        self._vseps: List[QFrame] = []
        self._muted_labels: List[QLabel] = []
        self._est_vals: List[QLabel] = []
        self._tool_btn_specs: List[tuple[QPushButton, str]] = []

        self._build_menubar()
        self._build_workstation()
        self._build_statusbar()
        self.apply_theme()
        self._refresh_summary_ui()

        self.setWindowOpacity(0.0)
        self._fade_in = QPropertyAnimation(self, b"windowOpacity")
        self._fade_in.setDuration(200)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.OutCubic)
        self._fade_in.start()

    # ------------------------------------------------------------------ Native Top Menu Bar
    def _build_menubar(self) -> None:
        mb = self.menuBar()

        # File
        self.m_file = mb.addMenu(tr("menu.file"))
        self.act_open = QAction(tr("menu.open_level"), self)
        self.act_open.setShortcut(QKeySequence("Ctrl+O"))
        self.act_open.triggered.connect(lambda _=False: self._open_file_dialog())
        self.m_file.addAction(self.act_open)

        self.m_file.addSeparator()
        self.act_steam = QAction(tr("menu.steam_dir"), self)
        self.act_steam.triggered.connect(self._browse_adofai_dir)
        self.m_file.addAction(self.act_steam)
        self.m_file.addSeparator()
        self.act_quit = QAction(tr("menu.quit"), self)
        self.act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        self.act_quit.triggered.connect(self.close)
        self.m_file.addAction(self.act_quit)

        # Edit
        self.m_edit = mb.addMenu(tr("menu.edit"))
        self.act_in = QAction(tr("menu.mark_in"), self)
        self.act_in.setShortcut(QKeySequence("I"))
        self.act_in.triggered.connect(self._mark_in)
        self.m_edit.addAction(self.act_in)

        self.act_out = QAction(tr("menu.mark_out"), self)
        self.act_out.setShortcut(QKeySequence("O"))
        self.act_out.triggered.connect(self._mark_out)
        self.m_edit.addAction(self.act_out)

        self.act_reset_trim = QAction(tr("menu.reset_trim"), self)
        self.act_reset_trim.setShortcut(QKeySequence("Ctrl+0"))
        self.act_reset_trim.triggered.connect(self._reset_trim)
        self.m_edit.addAction(self.act_reset_trim)

        self.m_edit.addSeparator()
        self.act_planet = QAction(tr("menu.planet_cfg"), self)
        self.act_planet.setShortcut(QKeySequence("Ctrl+Shift+P"))
        self.act_planet.triggered.connect(self._open_planet_dialog)
        self.m_edit.addAction(self.act_planet)

        self.act_planet_def = QAction(tr("menu.planet_reset"), self)
        self.act_planet_def.triggered.connect(self._reset_planet_defaults)
        self.m_edit.addAction(self.act_planet_def)

        self.m_edit.addSeparator()
        self.act_exp_cfg = QAction(tr("menu.export_cfg"), self)
        self.act_exp_cfg.setShortcut(QKeySequence("Ctrl+,"))
        self.act_exp_cfg.triggered.connect(self._open_export_dialog)
        self.m_edit.addAction(self.act_exp_cfg)

        self.m_lang = QMenu(tr("menu.language"), self)
        self._lang_group = QActionGroup(self)
        self._lang_group.setExclusive(True)
        self._lang_actions: dict[str, QAction] = {}
        cur_lang = get_language()
        for code in SUPPORTED_LANGUAGES:
            la = QAction(LANGUAGE_NAMES[code], self)
            la.setCheckable(True)
            la.setChecked(code == cur_lang)
            la.triggered.connect(lambda _=False, c=code: self._switch_language(c))
            self._lang_group.addAction(la)
            self.m_lang.addAction(la)
            self._lang_actions[code] = la
        self.m_edit.addMenu(self.m_lang)

        # Sequence
        self.m_seq = mb.addMenu(tr("menu.seq"))
        self.act_prev = QAction(tr("menu.prev_build"), self)
        self.act_prev.setShortcut(QKeySequence("Ctrl+P"))
        self.act_prev.triggered.connect(lambda: self._start_preview_build(force_rebuild=False))
        self.m_seq.addAction(self.act_prev)

        self.act_prev_force = QAction(tr("menu.prev_rebuild"), self)
        self.act_prev_force.triggered.connect(lambda: self._start_preview_build(force_rebuild=True))
        self.m_seq.addAction(self.act_prev_force)

        self.act_proxy_cfg = QAction(tr("menu.proxy_cfg"), self)
        self.act_proxy_cfg.triggered.connect(self._open_proxy_dialog)
        self.m_seq.addAction(self.act_proxy_cfg)

        self.m_seq.addSeparator()
        self.act_seq_cfg = QAction(tr("menu.open_seq_tab"), self)
        self.act_seq_cfg.triggered.connect(lambda: self._focus_inspector_tab(1))
        self.m_seq.addAction(self.act_seq_cfg)

        self.act_clear_cache = QAction(tr("menu.clear_cache"), self)
        self.act_clear_cache.triggered.connect(self._clear_preview_cache)
        self.m_seq.addAction(self.act_clear_cache)

        # View
        self.m_view = mb.addMenu(tr("menu.view"))
        self.act_toggle_inspector = QAction(tr("menu.toggle_inspector"), self)
        self.act_toggle_inspector.setCheckable(True)
        self.act_toggle_inspector.setChecked(True)
        self.act_toggle_inspector.setShortcut(QKeySequence("Tab"))
        self.act_toggle_inspector.toggled.connect(self._toggle_inspector)
        self.m_view.addAction(self.act_toggle_inspector)

        self.m_theme = QMenu(tr("menu.theme"), self)
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        self._theme_actions: dict[str, QAction] = {}
        cur_theme = get_theme()
        for tcode, tkey in (("dark", "menu.theme_dark"), ("pastel_light", "menu.theme_light")):
            ta = QAction(tr(tkey), self)
            ta.setCheckable(True)
            ta.setChecked(tcode == cur_theme)
            ta.triggered.connect(lambda _=False, tc=tcode: self._switch_theme(tc))
            self._theme_group.addAction(ta)
            self.m_theme.addAction(ta)
            self._theme_actions[tcode] = ta
        self.m_view.addMenu(self.m_theme)

        self.m_view.addSeparator()
        self.act_zi = QAction(tr("menu.zoom_in"), self)
        self.act_zi.setShortcut(QKeySequence("Ctrl+="))
        self.act_zi.triggered.connect(lambda: self._adjust_zoom(1.4))
        self.m_view.addAction(self.act_zi)

        self.act_zo = QAction(tr("menu.zoom_out"), self)
        self.act_zo.setShortcut(QKeySequence("Ctrl+-"))
        self.act_zo.triggered.connect(lambda: self._adjust_zoom(0.7))
        self.m_view.addAction(self.act_zo)

        self.act_zf = QAction(tr("menu.zoom_fit"), self)
        self.act_zf.triggered.connect(lambda: self.timeline.animate_zoom(1.0))
        self.m_view.addAction(self.act_zf)

        # Render
        self.m_exp = mb.addMenu(tr("menu.render"))
        self.act_render = QAction(tr("menu.render_start"), self)
        self.act_render.setShortcut(QKeySequence("Ctrl+E"))
        self.act_render.triggered.connect(self._open_render_confirm)
        self.m_exp.addAction(self.act_render)

        self.act_opt_render = QAction(tr("menu.opt_render"), self)
        self.act_opt_render.setCheckable(True)
        self.act_opt_render.setChecked(False)
        self.act_opt_render.toggled.connect(self._on_toggle_opt_render)
        self.m_exp.addAction(self.act_opt_render)

        self.m_exp.addSeparator()
        self.act_out_file = QAction(tr("menu.out_file"), self)
        self.act_out_file.triggered.connect(self._browse_output_file)
        self.m_exp.addAction(self.act_out_file)

        self.act_out_dir = QAction(tr("menu.out_dir"), self)
        self.act_out_dir.triggered.connect(self._browse_output_dir)
        self.m_exp.addAction(self.act_out_dir)

        self.act_open_out = QAction(tr("menu.open_out_dir"), self)
        self.act_open_out.triggered.connect(self._open_output_dir)
        self.m_exp.addAction(self.act_open_out)

        # Help
        self.m_help = mb.addMenu(tr("menu.help"))
        self.act_diag = QAction(tr("menu.diag"), self)
        self.act_diag.triggered.connect(self._show_hw_diagnostics)
        self.m_help.addAction(self.act_diag)
        self.act_upd = QAction(tr("menu.check_upd"), self)
        self.act_upd.triggered.connect(lambda: self._check_updates(manual=True))
        self.m_help.addAction(self.act_upd)
        self.act_about = QAction(tr("menu.about"), self)
        self.act_about.triggered.connect(self._show_about)
        self.m_help.addAction(self.act_about)

    # ------------------------------------------------------------------ Workstation Layout
    def _build_workstation(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.vert_split = GripSplitter(Qt.Vertical)
        self.vert_split.setChildrenCollapsible(False)

        self.main_split = GripSplitter(Qt.Horizontal)
        self.main_split.setChildrenCollapsible(False)

        # Left Dock Pane: Viewer Header + 16:9 Canvas + Calibrated Stereo dB Meter
        self._left_pane = QFrame()
        self._left_pane.setObjectName("leftPane")
        lp = QVBoxLayout(self._left_pane)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(0)

        vp_hdr, self.lbl_vp_title, self.lbl_vp_mode = _pane_header(tr("vp.header"), tr("vp.mode_fit"))
        self._pane_headers.append(vp_hdr)
        lp.addWidget(vp_hdr)

        vp_body = QHBoxLayout()
        vp_body.setContentsMargins(0, 0, 0, 0)
        vp_body.setSpacing(0)
        self.viewport = PreviewViewportWidget()
        self.viewport.request_open_level.connect(self._open_file_dialog)
        self.viewport.request_generate_preview.connect(lambda: self._start_preview_build(force_rebuild=False))
        self.vu_meter = StereoDbMeter()
        vp_body.addWidget(self.viewport, 1)
        vp_body.addWidget(self.vu_meter, 0)
        lp.addLayout(vp_body, 1)

        self.main_split.addWidget(self._left_pane)

        # Right Dock Pane: Native Tabbed Inspector Dock (QTabWidget + QScrollArea + Closable Header)
        self.inspector = QFrame()
        self.inspector.setObjectName("inspectorPane")
        self.inspector.setMinimumWidth(316)
        self.inspector.setMaximumWidth(390)
        insp_outer = QVBoxLayout(self.inspector)
        insp_outer.setContentsMargins(0, 0, 0, 0)
        insp_outer.setSpacing(0)

        insp_hdr, self.lbl_insp_hdr, self.lbl_insp_bpm = _pane_header(
            tr("insp.header"), "", on_close=lambda: self.act_toggle_inspector.setChecked(False)
        )
        self._pane_headers.append(insp_hdr)
        insp_outer.addWidget(insp_hdr)

        self.insp_tabs = QTabWidget()
        self.insp_tabs.setObjectName("inspectorTabs")
        self.insp_tabs.setDocumentMode(False)
        self.insp_tabs.currentChanged.connect(self._on_inspector_tab_changed)
        insp_outer.addWidget(self.insp_tabs, 1)

        # ---- Tab 1: Output (Export & Range) inside QScrollArea ----
        tab_out_scroll = QScrollArea()
        tab_out_scroll.setFrameShape(QFrame.NoFrame)
        tab_out_scroll.setWidgetResizable(True)
        tab_out_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tab_out_body = QWidget()
        insp = QVBoxLayout(self._tab_out_body)
        insp.setContentsMargins(9, 6, 9, 6)
        insp.setSpacing(5)
        tab_out_scroll.setWidget(self._tab_out_body)
        self.insp_tabs.addTab(tab_out_scroll, tr("insp.tab_out"))

        # 1) Level Identity
        self.lbl_lvl_name = QLabel(tr("insp.no_level"))
        f_b = QFont()
        f_b.setBold(True)
        self.lbl_lvl_name.setFont(f_b)
        insp.addWidget(self.lbl_lvl_name)

        self.lbl_lvl_sub = QLabel(tr("insp.no_level_sub"))
        self.lbl_lvl_sub.setObjectName("muted")
        insp.addWidget(self.lbl_lvl_sub)

        insp.addWidget(self._make_hsep())

        # 2) Video & Audio Controls
        g_vid = QGridLayout()
        g_vid.setVerticalSpacing(5)
        g_vid.setHorizontalSpacing(7)

        self.lbl_k_res = self._muted_lbl(tr("insp.res"))
        g_vid.addWidget(self.lbl_k_res, 0, 0)
        self.cmb_res = QComboBox()
        self.cmb_res.setFixedHeight(24)
        for _lbl, w, h in RES_PRESETS:
            self.cmb_res.addItem(f"{w}×{h} ({h}p)", (w, h))
        for i in range(self.cmb_res.count()):
            if self.cmb_res.itemData(i) == (1920, 1080):
                self.cmb_res.setCurrentIndex(i)
                break
        self.cmb_res.currentIndexChanged.connect(self._on_quick_export_changed)
        g_vid.addWidget(self.cmb_res, 0, 1, 1, 3)

        self.lbl_k_fps = self._muted_lbl(tr("insp.fps"))
        g_vid.addWidget(self.lbl_k_fps, 1, 0)
        self.spn_fps = QSpinBox()
        self.spn_fps.setFixedHeight(24)
        self.spn_fps.setRange(24, 240)
        self.spn_fps.setValue(60)
        self.spn_fps.setSuffix(" fps")
        self.spn_fps.setKeyboardTracking(False)
        self.spn_fps.setToolTip(tr("insp.fps_tip"))
        self.spn_fps.valueChanged.connect(self._on_quick_export_changed)
        g_vid.addWidget(self.spn_fps, 1, 1, 1, 3)

        self.lbl_k_fmt = self._muted_lbl(tr("insp.fmt"))
        g_vid.addWidget(self.lbl_k_fmt, 2, 0)
        self.cmb_fmt = QComboBox()
        self.cmb_fmt.setFixedHeight(24)
        self.cmb_fmt.addItem("MP4", "mp4")
        self.cmb_fmt.addItem("MKV", "mkv")
        self.cmb_fmt.currentIndexChanged.connect(self._on_quick_export_changed)
        g_vid.addWidget(self.cmb_fmt, 2, 1)

        self.lbl_k_codec = self._muted_lbl(tr("insp.codec"))
        g_vid.addWidget(self.lbl_k_codec, 2, 2)
        self.cmb_codec = QComboBox()
        self.cmb_codec.setFixedHeight(24)
        self.cmb_codec.addItem("H.264", "h264")
        self.cmb_codec.addItem("H.265", "hevc")
        self.cmb_codec.currentIndexChanged.connect(self._on_quick_export_changed)
        g_vid.addWidget(self.cmb_codec, 2, 3)

        self.lbl_k_bitrate = self._muted_lbl(tr("insp.bitrate"))
        g_vid.addWidget(self.lbl_k_bitrate, 3, 0)
        self.cmb_bitrate = QComboBox()
        self.cmb_bitrate.setFixedHeight(24)
        self.cmb_bitrate.addItem(tr("insp.br_high"), "ultra")
        self.cmb_bitrate.addItem(tr("insp.br_mid"), "high")
        self.cmb_bitrate.addItem(tr("insp.br_low"), "balanced")
        self.cmb_bitrate.setCurrentIndex(1)
        self.cmb_bitrate.currentIndexChanged.connect(self._on_quick_export_changed)
        g_vid.addWidget(self.cmb_bitrate, 3, 1, 1, 3)

        self.lbl_k_audio = self._muted_lbl(tr("insp.audio"))
        g_vid.addWidget(self.lbl_k_audio, 4, 0)
        aud_row = QHBoxLayout()
        aud_row.setSpacing(12)
        self.chk_bgm = QCheckBox(tr("insp.bgm"))
        self.chk_bgm.setChecked(True)
        self.chk_bgm.toggled.connect(self._on_quick_export_changed)
        self.chk_hit = QCheckBox(tr("insp.hitsound"))
        self.chk_hit.setChecked(True)
        self.chk_hit.toggled.connect(self._on_quick_export_changed)
        aud_row.addWidget(self.chk_bgm)
        aud_row.addWidget(self.chk_hit)
        aud_row.addStretch(1)
        g_vid.addLayout(aud_row, 4, 1, 1, 3)

        g_vid.setColumnStretch(1, 1)
        g_vid.setColumnStretch(3, 1)
        insp.addLayout(g_vid)

        insp.addWidget(self._make_hsep())

        # 3) Interactive Trim Range (unified MM:SS.d TimecodeSpinBox)
        rng_hdr = QHBoxLayout()
        self.lbl_rng = QLabel(tr("insp.range"))
        self.lbl_rng.setObjectName("sectionHdr")
        rng_hdr.addWidget(self.lbl_rng)
        rng_hdr.addStretch(1)
        self.btn_reset_trim = QPushButton(tr("insp.reset_full"))
        self.btn_reset_trim.setObjectName("chipBtn")
        self.btn_reset_trim.setFixedHeight(20)
        self.btn_reset_trim.clicked.connect(self._reset_trim)
        self.btn_reset_trim.hide()
        rng_hdr.addWidget(self.btn_reset_trim)
        insp.addLayout(rng_hdr)

        g_trim = QGridLayout()
        g_trim.setHorizontalSpacing(5)
        self.lbl_k_start = self._muted_lbl(tr("insp.start"))
        g_trim.addWidget(self.lbl_k_start, 0, 0)
        self.spn_in = TimecodeSpinBox()
        self.spn_in.setFixedHeight(23)
        self.spn_in.setMinimumWidth(84)
        self.spn_in.setRange(0.0, 36000.0)
        self.spn_in.valueChanged.connect(self._on_trim_spin_changed)
        g_trim.addWidget(self.spn_in, 0, 1)

        self.lbl_k_end = self._muted_lbl(tr("insp.end"))
        g_trim.addWidget(self.lbl_k_end, 0, 2)
        self.spn_out = TimecodeSpinBox()
        self.spn_out.setFixedHeight(23)
        self.spn_out.setMinimumWidth(84)
        self.spn_out.setRange(0.0, 36000.0)
        self.spn_out.valueChanged.connect(self._on_trim_spin_changed)
        g_trim.addWidget(self.spn_out, 0, 3)
        g_trim.setColumnStretch(1, 1)
        g_trim.setColumnStretch(3, 1)
        insp.addLayout(g_trim)

        insp.addWidget(self._make_hsep())

        # 4) Output File Destination (filename field + folder row with '...' browse button)
        g_out = QGridLayout()
        g_out.setVerticalSpacing(4)
        g_out.setHorizontalSpacing(5)
        self.lbl_k_fname = self._muted_lbl(tr("insp.filename"))
        g_out.addWidget(self.lbl_k_fname, 0, 0)
        self.ed_output_name = QLineEdit()
        self.ed_output_name.setFixedHeight(23)
        self.ed_output_name.setMinimumWidth(80)
        self.ed_output_name.setStyleSheet("font-family: 'Cascadia Mono', Consolas, monospace; font-size: 11px;")
        self.ed_output_name.editingFinished.connect(self._on_output_name_edited)
        g_out.addWidget(self.ed_output_name, 0, 1, 1, 2)

        self.lbl_k_dir = self._muted_lbl(tr("insp.folder"))
        g_out.addWidget(self.lbl_k_dir, 1, 0)
        self.ed_out_dir = QLineEdit()
        self.ed_out_dir.setFixedHeight(23)
        self.ed_out_dir.setMinimumWidth(80)
        self.ed_out_dir.setReadOnly(True)
        g_out.addWidget(self.ed_out_dir, 1, 1)

        self.btn_browse_dir = QPushButton("...")
        self.btn_browse_dir.setFixedSize(28, 23)
        self.btn_browse_dir.setStyleSheet("padding: 0px;")
        self.btn_browse_dir.setToolTip(tr("insp.folder_tip"))
        self.btn_browse_dir.clicked.connect(self._browse_output_dir)
        g_out.addWidget(self.btn_browse_dir, 1, 2)
        g_out.setColumnStretch(1, 1)
        insp.addLayout(g_out)

        insp.addWidget(self._make_hsep())

        # 5) Live Output Estimate Box (concise 3 rows; encoder already shown in status bar)
        self._est_frame = QFrame()
        self._est_frame.setObjectName("estBox")
        ge = QGridLayout(self._est_frame)
        ge.setContentsMargins(8, 5, 8, 5)
        ge.setVerticalSpacing(3)
        ge.setHorizontalSpacing(8)
        self.lbl_est_dur, self.val_est_dur = self._add_est_row(ge, 0, tr("insp.est_dur"), "-")
        self.lbl_est_size, self.val_est_size = self._add_est_row(ge, 1, tr("insp.est_size"), "-")
        self.lbl_est_time, self.val_est_time = self._add_est_row(ge, 2, tr("insp.est_time"), "-")
        ge.setColumnStretch(1, 1)
        insp.addWidget(self._est_frame)
        insp.addStretch(1)

        # ---- Tab 2: Planets & Sequence inside QScrollArea ----
        tab_seq_scroll = QScrollArea()
        tab_seq_scroll.setFrameShape(QFrame.NoFrame)
        tab_seq_scroll.setWidgetResizable(True)
        tab_seq_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tab_seq_body = QWidget()
        slay = QVBoxLayout(self._tab_seq_body)
        slay.setContentsMargins(10, 8, 10, 8)
        slay.setSpacing(7)
        tab_seq_scroll.setWidget(self._tab_seq_body)
        self.insp_tabs.addTab(tab_seq_scroll, tr("insp.tab_seq"))

        self.lbl_pl_hdr = QLabel(tr("insp.planet_hdr"))
        self.lbl_pl_hdr.setObjectName("sectionHdr")
        slay.addWidget(self.lbl_pl_hdr)

        pl_row = QHBoxLayout()
        pl_row.setSpacing(6)
        self.btn_red_picker = ColorPickerButton(tr("insp.fire"), "#FF0000", 1.0)
        self.btn_red_picker.setFixedHeight(26)
        self.btn_red_picker.setToolTip(tr("insp.fire_tip"))
        self.btn_red_picker.clicked.connect(self._open_planet_dialog)
        self.btn_blue_picker = ColorPickerButton(tr("insp.ice"), "#0000FF", 1.0)
        self.btn_blue_picker.setFixedHeight(26)
        self.btn_blue_picker.setToolTip(tr("insp.ice_tip"))
        self.btn_blue_picker.clicked.connect(self._open_planet_dialog)
        pl_row.addWidget(self.btn_red_picker, 1)
        pl_row.addWidget(self.btn_blue_picker, 1)
        slay.addLayout(pl_row)

        self.btn_pl_rst = QPushButton(tr("insp.planet_reset_btn"))
        self.btn_pl_rst.setFixedHeight(24)
        self.btn_pl_rst.clicked.connect(self._reset_planet_defaults)
        slay.addWidget(self.btn_pl_rst)

        slay.addWidget(self._make_hsep())

        self.lbl_seq_hdr = QLabel(tr("insp.seq_hdr"))
        self.lbl_seq_hdr.setObjectName("sectionHdr")
        slay.addWidget(self.lbl_seq_hdr)

        g_seq = QGridLayout()
        g_seq.setHorizontalSpacing(8)
        g_seq.setVerticalSpacing(5)
        self.lbl_k_pad = self._muted_lbl(tr("insp.end_pad"))
        g_seq.addWidget(self.lbl_k_pad, 0, 0)
        self.spn_end_pad = QDoubleSpinBox()
        self.spn_end_pad.setFixedHeight(24)
        self.spn_end_pad.setRange(0.0, 15.0)
        self.spn_end_pad.setDecimals(1)
        self.spn_end_pad.setSingleStep(0.5)
        self.spn_end_pad.setSuffix(tr("insp.sec_suffix"))
        self.spn_end_pad.setValue(2.0)
        self.spn_end_pad.valueChanged.connect(self._on_seq_quick_changed)
        g_seq.addWidget(self.spn_end_pad, 0, 1)
        g_seq.setColumnStretch(1, 1)
        slay.addLayout(g_seq)

        self.chk_hide_cd_quick = QCheckBox(tr("insp.hide_cd"))
        self.chk_hide_cd_quick.setChecked(True)
        self.chk_hide_cd_quick.toggled.connect(self._on_seq_quick_changed)
        slay.addWidget(self.chk_hide_cd_quick)
        slay.addStretch(1)

        # Pinned Dock Footer Actions (always accessible regardless of tab or scroll)
        self._footer_bar = QFrame()
        self._footer_bar.setObjectName("inspFooter")
        act_row = QHBoxLayout(self._footer_bar)
        act_row.setContentsMargins(10, 7, 10, 8)
        act_row.setSpacing(6)
        self.btn_adv = QPushButton(tr("insp.btn_adv"))
        self.btn_adv.setFixedHeight(28)
        self.btn_adv.setToolTip(tr("insp.btn_adv_tip"))
        self.btn_adv.clicked.connect(self._open_export_dialog)

        self.btn_render = QPushButton(tr("insp.btn_render"))
        self.btn_render.setObjectName("primaryBtn")
        self.btn_render.setFixedHeight(28)
        self.btn_render.clicked.connect(self._open_render_confirm)

        act_row.addWidget(self.btn_adv, 1)
        act_row.addWidget(self.btn_render, 2)
        insp_outer.addWidget(self._footer_bar)

        self.main_split.addWidget(self.inspector)
        self.main_split.setSizes([864, 316])
        self.main_split.setStretchFactor(0, 1)
        self.main_split.setStretchFactor(1, 0)

        self.vert_split.addWidget(self.main_split)

        # Bottom Dock: Resizable Timeline Header + Balanced Transport + 3-Lane Timeline
        self._tl_dock = QFrame()
        self._tl_dock.setObjectName("tlDock")
        dl = QVBoxLayout(self._tl_dock)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(0)

        tl_hdr, self.lbl_tl_hdr, self.lbl_tl_range = _pane_header(tr("tl.header"), tr("tl.full_range"))
        self._pane_headers.append(tl_hdr)
        dl.addWidget(tl_hdr)

        inner_tl = QVBoxLayout()
        inner_tl.setContentsMargins(8, 5, 8, 6)
        inner_tl.setSpacing(5)
        dl.addLayout(inner_tl)

        # Balanced Transport Row (no redundant span repetition, clean NLE layout)
        tr_row = QHBoxLayout()
        tr_row.setSpacing(5)

        self.lbl_tc = QLabel("00:00.000")
        self.lbl_tc.setObjectName("timecodeBox")
        self.lbl_tc.setFixedWidth(84)
        self.lbl_tc.setAlignment(Qt.AlignCenter)
        tr_row.addWidget(self.lbl_tc)

        self.lbl_frame_num = QLabel(tr("tl.frame_num", n=0))
        self.lbl_frame_num.setMinimumWidth(72)
        tr_row.addWidget(self.lbl_frame_num)

        tr_row.addWidget(self._make_vsep())

        self.btn_to_start = self._make_tool_btn("start", tr("tl.tip_start"), self._jump_start)
        self.btn_step_back = self._make_tool_btn("step_back", tr("tl.tip_step_back"), lambda: self._step_frame(-1))
        self.btn_play = self._make_tool_btn("play", tr("tl.tip_play"), self._toggle_play)
        self.btn_play.setFixedWidth(30)
        self.btn_step_fwd = self._make_tool_btn("step_fwd", tr("tl.tip_step_fwd"), lambda: self._step_frame(1))
        self.btn_to_end = self._make_tool_btn("end", tr("tl.tip_end"), self._jump_end)
        for b in (self.btn_to_start, self.btn_step_back, self.btn_play, self.btn_step_fwd, self.btn_to_end):
            tr_row.addWidget(b)

        tr_row.addWidget(self._make_vsep())

        self.btn_mark_in = self._make_tool_btn("mark_in", tr("tl.tip_mark_in"), self._mark_in)
        self.btn_mark_out = self._make_tool_btn("mark_out", tr("tl.tip_mark_out"), self._mark_out)
        tr_row.addWidget(self.btn_mark_in)
        tr_row.addWidget(self.btn_mark_out)

        tr_row.addWidget(self._make_vsep())

        self.cmb_speed = QComboBox()
        self.cmb_speed.setFixedSize(76, 24)
        for lbl, val in (("0.5x", 0.5), ("1.0x", 1.0), ("1.5x", 1.5), ("2.0x", 2.0)):
            self.cmb_speed.addItem(lbl, val)
        self.cmb_speed.setCurrentIndex(1)
        self.cmb_speed.currentIndexChanged.connect(self._on_speed_changed)
        tr_row.addWidget(self.cmb_speed)

        self.btn_mute = self._make_tool_btn("volume_on", tr("tl.tip_mute"), self._toggle_mute)
        tr_row.addWidget(self.btn_mute)

        self.sld_vol = QSlider(Qt.Horizontal)
        self.sld_vol.setRange(0, 100)
        self.sld_vol.setValue(75)
        self.sld_vol.setFixedWidth(72)
        self.sld_vol.setToolTip(tr("tl.tip_vol"))
        self.sld_vol.valueChanged.connect(self._on_volume_changed)
        tr_row.addWidget(self.sld_vol)

        tr_row.addStretch(1)

        btn_z_out = QPushButton("-")
        btn_z_out.setObjectName("toolBtn")
        btn_z_out.setFixedSize(20, 22)
        btn_z_out.clicked.connect(lambda: self._adjust_zoom(0.75))
        tr_row.addWidget(btn_z_out)

        self.sld_zoom = QSlider(Qt.Horizontal)
        self.sld_zoom.setRange(0, 70)
        self.sld_zoom.setValue(0)
        self.sld_zoom.setFixedWidth(84)
        self.sld_zoom.setToolTip(tr("tl.tip_zoom"))
        self.sld_zoom.valueChanged.connect(self._on_zoom_slider_changed)
        tr_row.addWidget(self.sld_zoom)

        btn_z_in = QPushButton("+")
        btn_z_in.setObjectName("toolBtn")
        btn_z_in.setFixedSize(20, 22)
        btn_z_in.clicked.connect(lambda: self._adjust_zoom(1.35))
        tr_row.addWidget(btn_z_in)

        self.lbl_zoom_val = QLabel("100%")
        self.lbl_zoom_val.setFixedWidth(36)
        self.lbl_zoom_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        tr_row.addWidget(self.lbl_zoom_val)

        self.btn_fit = QPushButton(tr("tl.btn_fit"))
        self.btn_fit.setObjectName("chipBtn")
        self.btn_fit.setFixedHeight(22)
        self.btn_fit.clicked.connect(lambda: self.timeline.animate_zoom(1.0))
        tr_row.addWidget(self.btn_fit)

        inner_tl.addLayout(tr_row)

        self.timeline = TimelineTrimWidget()
        self.timeline.playhead_changed.connect(self._on_timeline_scrub)
        self.timeline.trim_changed.connect(self._on_timeline_trim_changed)
        self.timeline.zoom_changed.connect(self._on_timeline_zoom_changed)
        self.timeline.audio_mute_toggled.connect(self._on_track_mute_toggled)
        inner_tl.addWidget(self.timeline, 1)

        # Horizontal scrollbar for the zoomed timeline (aligned with the track area, hidden at 100% zoom)
        sb_row = QHBoxLayout()
        sb_row.setContentsMargins(self.timeline.GUTTER_W, 0, self.timeline.RIGHT_PAD, 0)
        self.tl_scroll = QScrollBar(Qt.Horizontal)
        self.tl_scroll.setRange(0, 0)
        self.tl_scroll.valueChanged.connect(self._on_tl_scroll_changed)
        sb_row.addWidget(self.tl_scroll)
        self._tl_scroll_wrap = QWidget()
        self._tl_scroll_wrap.setLayout(sb_row)
        self._tl_scroll_wrap.hide()
        inner_tl.addWidget(self._tl_scroll_wrap)
        self.timeline.view_changed.connect(self._sync_tl_scroll)

        self.vert_split.addWidget(self._tl_dock)
        self.vert_split.setSizes([505, 178])
        self.vert_split.setStretchFactor(0, 1)
        self.vert_split.setStretchFactor(1, 0)

        root.addWidget(self.vert_split, 1)

    def _make_hsep(self) -> QFrame:
        f = _hsep()
        self._hseps.append(f)
        return f

    def _make_vsep(self) -> QFrame:
        f = _vsep()
        self._vseps.append(f)
        return f

    def _muted_lbl(self, text: str) -> QLabel:
        l = QLabel(text)
        l.setStyleSheet(f"color: {get_palette().text_secondary}; font-size: 11px; background: transparent;")
        self._muted_labels.append(l)
        return l

    def _add_est_row(self, grid: QGridLayout, row: int, key: str, val: str) -> tuple[QLabel, QLabel]:
        lk = self._muted_lbl(key)
        lv = QLabel(val)
        lv.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lv.setStyleSheet(f"font-family: 'Cascadia Mono', Consolas, monospace; font-size: 11px; color: {get_palette().text_mono}; background: transparent;")
        self._est_vals.append(lv)
        grid.addWidget(lk, row, 0)
        grid.addWidget(lv, row, 1)
        return lk, lv

    def _build_statusbar(self) -> None:
        sb = QStatusBar()
        sb.setSizeGripEnabled(True)
        self.setStatusBar(sb)

        self.sb_enc = QLabel()
        self.sb_engine = QLabel()
        self.sb_chart = QLabel()
        self.sb_state = QLabel(tr("sb.ready"))
        self.sb_state.setContentsMargins(0, 0, 10, 0)

        sb.addWidget(self.sb_enc, 1)
        sb.addPermanentWidget(self._make_vsep())
        sb.addPermanentWidget(self.sb_engine)
        sb.addPermanentWidget(self._make_vsep())
        sb.addPermanentWidget(self.sb_chart)
        sb.addPermanentWidget(self._make_vsep())
        sb.addPermanentWidget(self.sb_state)
        self._update_statusbar()

    def _make_tool_btn(self, icon_kind: str, tip: str, slot) -> QPushButton:
        b = QPushButton()
        b.setObjectName("toolBtn")
        b.setIcon(make_icon(icon_kind, get_palette().icon_color, 14))
        b.setIconSize(QSize(14, 14))
        b.setFixedSize(26, 24)
        b.setToolTip(tip)
        b.clicked.connect(slot)
        self._tool_btn_specs.append((b, icon_kind))
        return b

    # ------------------------------------------------------------------ Drag & Drop / Keyboard
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                p = url.toLocalFile()
                if os.path.isdir(p) or p.lower().endswith((".adofai", ".zip")):
                    event.acceptProposedAction()
                    return

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if os.path.isdir(p) or p.lower().endswith((".adofai", ".zip")):
                self._import_any_path(p)
                return

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key_Space:
            self._toggle_play()
            return
        if ev.key() == Qt.Key_Left:
            self._step_frame(-1)
            return
        if ev.key() == Qt.Key_Right:
            self._step_frame(1)
            return
        if ev.key() == Qt.Key_Home:
            self._jump_start()
            return
        if ev.key() == Qt.Key_End:
            self._jump_end()
            return
        super().keyPressEvent(ev)

    # ------------------------------------------------------------------ Level Import (Unified Auto-Format)
    def _open_file_dialog(self) -> None:
        start_dir = getattr(self, "_last_open_dir", "")
        if not start_dir or not os.path.isdir(start_dir):
            cand = os.path.join(PROJECT_ROOT, "level")
            if os.path.isdir(cand):
                start_dir = cand
            else:
                dl = os.path.join(os.path.expanduser("~"), "Downloads")
                start_dir = dl if os.path.isdir(dl) else os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self, tr("dlg.open_title"), start_dir, tr("dlg.open_filter")
        )
        if path:
            self._last_open_dir = os.path.dirname(path)
            self._import_any_path(path)

    def _import_any_path(self, raw_path: str) -> None:
        try:
            resolved = resolve_level_input(raw_path)
            self._load_level(resolved)
        except Exception as ex:
            QMessageBox.warning(self, tr("dlg.load_fail_title"), str(ex))

    def _load_level(
        self,
        adofai_path: str,
        pre_info: Optional[AdofaiLevelInfo] = None,
        pre_duration: Optional[float] = None,
    ) -> None:
        self._stop_playback()
        if self._is_preview_busy():
            self._cancel_active_preview(next_action=None)
        self.level_path = os.path.abspath(adofai_path)
        get_warm_session_manager().shutdown_if_different_level(self.level_path)
        self.level_info = pre_info or parse_adofai_info(self.level_path)
        self.level_duration = (
            pre_duration if (pre_duration and pre_duration > 1.0)
            else estimate_level_duration_sec(self.level_info, self.caps.ffprobe_path)
        )
        self._waveform_peaks = None

        self.sequence_state["start_sec"] = 0.0
        self.sequence_state["end_sec"] = 0.0

        self.timeline.set_bpm(self.level_info.bpm)
        self.timeline.set_event_markers(self.level_info.event_markers)
        self.timeline.set_duration(self.level_duration, reset_trim=True)
        self.timeline.set_filmstrip("")
        self.timeline.set_waveform(None)
        self.sld_zoom.blockSignals(True)
        self.sld_zoom.setValue(0)
        self.sld_zoom.blockSignals(False)
        self.lbl_zoom_val.setText("100%")

        self.setWindowTitle(f"{self.level_info.display_title} — ADOFAI PV Studio")
        self.lbl_vp_title.setText(tr("vp.header_file", name=os.path.basename(self.level_path)))
        self._update_vp_mode_label()
        detail = tr(
            "vp.level_detail",
            bpm=self.level_info.bpm,
            tiles=self.level_info.tile_count,
            decos=self.level_info.decoration_count,
            filts=len(self.level_info.filters),
        )
        self.viewport.set_level_placeholder(self.level_info.display_title, detail)

        self._sync_default_output_path()
        self._refresh_summary_ui()
        self._try_load_cached_preview()

        # Only fall back to raw song file when no synced preview MP4 is loaded;
        # otherwise _apply_preview_video() already attached the synced preview audio + waveform.
        if not self.viewport.has_video():
            if self.level_info.song_path and os.path.isfile(self.level_info.song_path):
                self._audio_player.setSource(QUrl.fromLocalFile(self.level_info.song_path))
                self._start_waveform_worker(self.level_info.song_path)

    def _proxy_mode_short_label(self) -> str:
        mode = normalize_proxy_mode(self.proxy_state.get("mode", DEFAULT_PROXY_MODE))
        key_map = {
            "chart_only": "proxy.short_chart_only",
            "no_heavy_filters": "proxy.short_no_heavy",
            "all": "proxy.short_all",
        }
        return tr(key_map.get(mode, "proxy.short_no_heavy"))

    def _update_vp_mode_label(self) -> None:
        pw = self.proxy_state["width"]
        ph = self.proxy_state["height"]
        pf = self.proxy_state["fps"]
        mlbl = self._proxy_mode_short_label()
        if not self.level_path:
            self.lbl_vp_mode.setText(tr("vp.mode_fit"))
        elif self.viewport.has_video():
            self.lbl_vp_mode.setText(tr("vp.mode_proxy", w=pw, h=ph, fps=pf, mode=mlbl))
        else:
            self.lbl_vp_mode.setText(tr("vp.mode_none_spec", h=ph, fps=pf, mode=mlbl))

    def _start_waveform_worker(self, audio_src: str) -> None:
        if not audio_src or not os.path.isfile(audio_src):
            return
        wfw = WaveformWorker(self.level_path, audio_src, self.caps.ffmpeg_path)
        wfw.ready.connect(self._on_waveform_ready)
        wfw.finished.connect(lambda w=wfw: self._wf_workers.remove(w) if w in self._wf_workers else None)
        self._wf_workers.append(wfw)
        wfw.start()

    def _on_waveform_ready(self, lvl_path: str, peaks) -> None:
        if lvl_path == self.level_path and peaks:
            self._waveform_peaks = peaks
            self.timeline.set_waveform(peaks)
            self._update_vu_at_playhead()

    def _try_load_cached_preview(self) -> None:
        if not self.level_path or not os.path.isfile(self.level_path):
            return
        h = compute_preview_cache_key(
            self.level_path,
            self.planet_state["red_color"],
            self.planet_state["red_opacity"],
            self.planet_state["blue_color"],
            self.planet_state["blue_opacity"],
            self.proxy_state["width"],
            self.proxy_state["height"],
            self.proxy_state["fps"],
            self.proxy_state.get("mode", DEFAULT_PROXY_MODE),
        )
        mp4 = os.path.join(PREVIEW_CACHE_DIR, f"preview_{h}.mp4")
        strip = os.path.join(PREVIEW_CACHE_DIR, f"strip_{h}.jpg")
        if os.path.isfile(mp4) and os.path.getsize(mp4) > 2048 and os.path.isfile(mp4 + ".ok"):
            dur = 0.0
            try:
                with open(mp4 + ".ok", "r", encoding="utf-8") as mf:
                    dur = float(mf.read().strip() or 0.0)
            except Exception:
                dur = 0.0
            self._apply_preview_video(mp4, strip, dur)

    def _apply_preview_video(self, video_path: str, filmstrip_path: str, dur: float = 0.0) -> None:
        if dur > 1.0:
            self.level_duration = dur
            self.timeline.set_duration(dur, reset_trim=False)
        if self.viewport.load_video(video_path):
            if filmstrip_path and os.path.isfile(filmstrip_path):
                self.timeline.set_filmstrip(filmstrip_path)
            self._audio_player.setSource(QUrl())
            self._audio_player.setSource(QUrl.fromLocalFile(video_path))
            self._start_waveform_worker(video_path)
            self.viewport.seek_seconds(self.timeline.playhead)
            self._update_vp_mode_label()
            self._update_timecode_label()
            self._refresh_trim_summary()

    # ------------------------------------------------------------------ Proxy Builder
    def _is_preview_busy(self) -> bool:
        return bool(self._live_preview_workers)

    def _set_render_actions_enabled(self, enabled: bool) -> None:
        self.btn_render.setEnabled(enabled)
        self.act_render.setEnabled(enabled)

    def _reset_viewport_for_level(self) -> None:
        self._stop_playback()
        self._audio_player.setSource(QUrl())
        self.viewport.clear_video()
        self.timeline.set_filmstrip("")
        if self.level_info:
            detail = tr(
                "vp.level_detail",
                bpm=self.level_info.bpm,
                tiles=self.level_info.tile_count,
                decos=self.level_info.decoration_count,
                filts=len(self.level_info.filters),
            )
            self.viewport.set_level_placeholder(self.level_info.display_title, detail)
        self._try_load_cached_preview()
        if not self.viewport.has_video() and self.level_info and self.level_info.song_path and os.path.isfile(self.level_info.song_path):
            self._audio_player.setSource(QUrl.fromLocalFile(self.level_info.song_path))
            self._start_waveform_worker(self.level_info.song_path)
        self._update_vp_mode_label()

    def _cancel_active_preview(self, next_action: Optional[str] = None) -> bool:
        was_busy = self._is_preview_busy()
        self._preview_gen_id += 1
        self._pending_preview_action = next_action if was_busy else None

        runner = self._preview_runner
        self._preview_runner = None
        self._preview_worker = None

        if runner is not None:
            runner.cancel()
        for lw in list(self._live_preview_workers):
            if getattr(lw, "runner", None) is not None:
                lw.runner.cancel()

        if was_busy and next_action in ("restart", "restart_force"):
            pw = self.proxy_state["width"]
            ph = self.proxy_state["height"]
            pf = self.proxy_state["fps"]
            self._set_render_actions_enabled(False)
            self.sb_state.setText(tr("sb.previewing"))
            self.viewport.set_generating_state(True, tr("vp.gen_restarting", w=pw, h=ph, fps=pf), 3.0)
        elif was_busy and next_action == "clear_cache":
            self._set_render_actions_enabled(False)
            self.sb_state.setText(tr("sb.previewing"))
            self.viewport.set_generating_state(True, tr("vp.gen_cancelling"), 2.0)
        elif not was_busy:
            self._set_render_actions_enabled(True)
            self.sb_state.setText(tr("sb.ready"))
            self.viewport.set_generating_state(False)
        else:
            # Cancelled without restart (e.g. loading another level): keep render locked briefly until Unity exits
            self._set_render_actions_enabled(False)
            self.sb_state.setText(tr("sb.ready"))
            self.viewport.set_generating_state(False)

        return was_busy

    def _on_preview_thread_finished(self, worker: PreviewBuildWorker) -> None:
        if worker in self._live_preview_workers:
            self._live_preview_workers.remove(worker)
        was_current = (self._preview_worker is worker)
        if was_current:
            self._preview_worker = None
            self._preview_runner = None
        worker.deleteLater()

        if self._live_preview_workers:
            return

        action = self._pending_preview_action
        self._pending_preview_action = None

        if action in ("restart", "restart_force"):
            if self.level_path and os.path.isfile(self.level_path):
                force = (action == "restart_force")
                if not force:
                    self._reset_viewport_for_level()
                    if self.viewport.has_video():
                        self._set_render_actions_enabled(True)
                        self.viewport.set_generating_state(False)
                        self.sb_state.setText(tr("sb.ready"))
                        self.statusBar().showMessage(tr("sb.preview_loaded"), 4000)
                        return
                self._start_preview_build(force_rebuild=force)
                return
        elif action == "clear_cache":
            self._set_render_actions_enabled(True)
            self.viewport.set_generating_state(False)
            self.sb_state.setText(tr("sb.ready"))
            self._do_clear_preview_cache()
            return

        if not was_current:
            # A cancelled/retired worker finished with no queued follow-up action
            self._set_render_actions_enabled(True)
            self.viewport.set_generating_state(False)
            self.sb_state.setText(tr("sb.ready"))

    def _open_proxy_dialog(self) -> None:
        dlg = ProxySettingsDialog(
            width=self.proxy_state["width"],
            height=self.proxy_state["height"],
            fps=self.proxy_state["fps"],
            mode=self.proxy_state.get("mode", DEFAULT_PROXY_MODE),
            parent=self,
        )
        if dlg.exec() == dlg.DialogCode.Accepted:
            pw, ph, pf, pmode = dlg.result_spec()
            changed = (
                pw != self.proxy_state["width"]
                or ph != self.proxy_state["height"]
                or pf != self.proxy_state["fps"]
                or pmode != normalize_proxy_mode(self.proxy_state.get("mode", DEFAULT_PROXY_MODE))
            )
            self.proxy_state = save_proxy_settings(pw, ph, pf, pmode)
            if changed and self.level_path:
                if self._is_preview_busy():
                    self._cancel_active_preview(next_action="restart")
                    self._update_vp_mode_label()
                    self.statusBar().showMessage(tr("sb.proxy_restarting", w=pw, h=ph, fps=pf), 4000)
                    return
                self._reset_viewport_for_level()
            self._update_vp_mode_label()
            self.statusBar().showMessage(tr("sb.proxy_updated", w=pw, h=ph, fps=pf), 4000)

    def _start_preview_build(self, force_rebuild: bool = False) -> None:
        if not self.level_path or not os.path.isfile(self.level_path):
            QMessageBox.information(self, tr("dlg.no_level_title"), tr("dlg.no_level_preview_msg"))
            return
        if self._preview_worker is not None:
            return
        if self._live_preview_workers:
            # Previous cancelled proxy job is still winding down; queue this build cleanly
            self._pending_preview_action = "restart_force" if force_rebuild else "restart"
            pw = self.proxy_state["width"]
            ph = self.proxy_state["height"]
            pf = self.proxy_state["fps"]
            self._set_render_actions_enabled(False)
            self.sb_state.setText(tr("sb.previewing"))
            self.viewport.set_generating_state(True, tr("vp.gen_restarting", w=pw, h=ph, fps=pf), 3.0)
            return

        self._stop_playback()
        if force_rebuild:
            self._audio_player.setSource(QUrl())
            self.viewport.clear_video()
        self._set_render_actions_enabled(False)
        self.sb_state.setText(tr("sb.previewing"))
        self.viewport.set_generating_state(True, tr("vp.gen_engine_prep"), 5.0)

        self._preview_gen_id += 1
        gen_id = self._preview_gen_id
        self._preview_runner = RenderRunner(caps=self.caps)
        worker = PreviewBuildWorker(
            gen_id=gen_id,
            runner=self._preview_runner,
            level_path=self.level_path,
            adofai_dir=self.adofai_dir,
            red_color=self.planet_state["red_color"],
            red_opacity=self.planet_state["red_opacity"],
            blue_color=self.planet_state["blue_color"],
            blue_opacity=self.planet_state["blue_opacity"],
            gpu_limit=self.export_state["gpu_limit_percent"],
            force_rebuild=force_rebuild,
            proxy_width=self.proxy_state["width"],
            proxy_height=self.proxy_state["height"],
            proxy_fps=self.proxy_state["fps"],
            proxy_mode=self.proxy_state.get("mode", DEFAULT_PROXY_MODE),
        )
        self._preview_worker = worker
        self._live_preview_workers.append(worker)
        worker.progress.connect(self._on_preview_progress)
        worker.done.connect(self._on_preview_done)
        worker.finished.connect(lambda w=worker: self._on_preview_thread_finished(w))
        worker.start()

    def _on_preview_progress(self, gen_id: int, p: RenderProgress) -> None:
        if gen_id != self._preview_gen_id:
            return
        self.viewport.set_generating_state(True, p.message, p.percent)

    def _on_preview_done(self, gen_id: int, res: PreviewResult) -> None:
        if gen_id != self._preview_gen_id:
            return
        self._set_render_actions_enabled(True)
        self.viewport.set_generating_state(False)
        self.sb_state.setText(tr("sb.ready"))
        if res.success:
            self._apply_preview_video(res.video_path, res.filmstrip_path, res.duration_sec)
            self.statusBar().showMessage(tr("sb.preview_loaded"), 4000)
        else:
            if not self.viewport.has_video() and self.level_info:
                self._reset_viewport_for_level()
            QMessageBox.warning(self, tr("dlg.preview_fail_title"), res.error or tr("dlg.preview_fail_default"))

    def _clear_preview_cache(self) -> None:
        if self._is_preview_busy():
            self._cancel_active_preview(next_action="clear_cache")
            return
        self._do_clear_preview_cache()

    def _do_clear_preview_cache(self) -> None:
        self._stop_playback()
        self._audio_player.setSource(QUrl())
        self.viewport.clear_video()
        if os.path.isdir(PREVIEW_CACHE_DIR):
            shutil.rmtree(PREVIEW_CACHE_DIR, ignore_errors=True)
            os.makedirs(PREVIEW_CACHE_DIR, exist_ok=True)
        self.timeline.set_filmstrip("")
        self._update_vp_mode_label()
        if self.level_info:
            detail = tr(
                "vp.level_detail",
                bpm=self.level_info.bpm,
                tiles=self.level_info.tile_count,
                decos=self.level_info.decoration_count,
                filts=len(self.level_info.filters),
            )
            self.viewport.set_level_placeholder(self.level_info.display_title, detail)
            if self.level_info.song_path and os.path.isfile(self.level_info.song_path):
                self._audio_player.setSource(QUrl.fromLocalFile(self.level_info.song_path))
                self._start_waveform_worker(self.level_info.song_path)

    # ------------------------------------------------------------------ Interactive Sidebar Handlers
    def _on_quick_export_changed(self) -> None:
        if self._syncing_controls:
            return
        w, h = self.cmb_res.currentData()
        self.export_state["width"] = w
        self.export_state["height"] = h
        self.export_state["fps"] = self.spn_fps.value()
        self.export_state["format"] = self.cmb_fmt.currentData()
        self.export_state["codec"] = self.cmb_codec.currentData()
        self.export_state["quality"] = self.cmb_bitrate.currentData() or "high"
        self.export_state["include_bgm"] = self.chk_bgm.isChecked()
        self.export_state["include_hitsounds"] = self.chk_hit.isChecked()
        self._sync_default_output_path()
        self._refresh_summary_ui()

    def _on_seq_quick_changed(self) -> None:
        if self._syncing_controls:
            return
        self.sequence_state["end_padding_sec"] = self.spn_end_pad.value()
        self.sequence_state["hide_countdown"] = self.chk_hide_cd_quick.isChecked()
        self._refresh_live_estimates()

    def _focus_inspector_tab(self, index: int) -> None:
        if not self.inspector.isVisible():
            self.act_toggle_inspector.setChecked(True)
        self.insp_tabs.setCurrentIndex(index)

    def _on_trim_spin_changed(self) -> None:
        if self._syncing_controls:
            return
        s = self.spn_in.value()
        e = self.spn_out.value()
        if e <= s + 0.5 and e > 0.0:
            e = min(self.level_duration, s + 0.5)
        self.timeline.set_trim_range(s, e, emit=True)

    def _on_output_name_edited(self) -> None:
        name = self.ed_output_name.text().strip()
        if not name:
            return
        if os.path.isabs(name):
            self.output_path = name
            self.export_state["output_dir"] = os.path.dirname(name)
        else:
            out_dir = self.export_state["output_dir"] or get_default_output_dir()
            self.output_path = os.path.join(out_dir, name)
        self.ed_output_name.setToolTip(self.output_path)

    # ------------------------------------------------------------------ Transport, Frame-step & Trim
    def _toggle_play(self) -> None:
        if not self.level_path:
            return
        if self._playing:
            self._stop_playback()
            return
        self._playing = True
        self.btn_play.setIcon(make_icon("pause", get_palette().icon_color, 14))
        if self._audio_player.source().isValid():
            self._audio_player.setPlaybackRate(self._playback_rate)
            self._audio_player.setPosition(int(self.timeline.playhead * 1000))
            self._audio_player.play()
        self._play_timer.start()

    def _stop_playback(self) -> None:
        self._playing = False
        self._play_timer.stop()
        self._audio_player.pause()
        self.btn_play.setIcon(make_icon("play", get_palette().icon_color, 14))
        self.vu_meter.set_levels(0.0, 0.0, clear_peak=True)

    def _on_play_tick(self) -> None:
        if not self._playing:
            return
        if self._audio_player.playbackState() == QMediaPlayer.PlayingState:
            cur = self._audio_player.position() / 1000.0
        else:
            cur = self.timeline.playhead + (self._play_timer.interval() / 1000.0) * self._playback_rate

        limit = self.timeline.trim_out if self.timeline.is_trimmed else self.level_duration
        if cur >= limit:
            cur = self.timeline.trim_in if self.timeline.is_trimmed else 0.0
            self._stop_playback()
        self.timeline.set_playhead(cur, emit=False)
        self.viewport.seek_seconds(cur)
        self._update_timecode_label()
        self._update_vu_at_playhead()

    def _update_vu_at_playhead(self) -> None:
        # Keep stereo VU meter genuinely idle (-inf) whenever paused or muted
        if not self._playing or self._muted or not self._waveform_peaks:
            self.vu_meter.set_levels(0.0, 0.0, clear_peak=True)
            return
        ratio = max(0.0, min(0.999, self.timeline.playhead / max(0.001, self.level_duration)))
        idx = int(ratio * len(self._waveform_peaks))
        vol = self.sld_vol.value() / 100.0
        v = self._waveform_peaks[idx] * vol
        # Slight stereo spread using neighbor bin
        v2 = self._waveform_peaks[min(len(self._waveform_peaks) - 1, idx + 1)] * vol * 0.96
        self.vu_meter.set_levels(v, v2)

    def _step_frame(self, delta_frames: int) -> None:
        self._stop_playback()
        step_sec = delta_frames / float(max(1, self.export_state["fps"]))
        self.timeline.set_playhead(self.timeline.playhead + step_sec, emit=True)

    def _on_speed_changed(self) -> None:
        self._playback_rate = float(self.cmb_speed.currentData() or 1.0)
        self._audio_player.setPlaybackRate(self._playback_rate)

    def _on_volume_changed(self, val: int) -> None:
        self._audio_out.setVolume(val / 100.0)
        if val == 0 and not self._muted:
            self._set_muted(True)
        elif val > 0 and self._muted:
            self._set_muted(False)

    def _set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self._audio_out.setMuted(self._muted)
        self.timeline.set_audio_muted(self._muted)
        pal = get_palette()
        ic_col = pal.accent_danger if (self._muted and pal.is_light) else pal.icon_color
        self.btn_mute.setIcon(make_icon("volume_off" if self._muted else "volume_on", ic_col, 14))
        if self._muted:
            self.vu_meter.set_levels(0.0, 0.0)

    def _toggle_mute(self) -> None:
        self._set_muted(not self._muted)

    def _on_track_mute_toggled(self, muted: bool) -> None:
        self._set_muted(muted)

    def _on_timeline_scrub(self, sec: float) -> None:
        self.viewport.seek_seconds(sec)
        if self._playing and self._audio_player.source().isValid():
            self._audio_player.setPosition(int(sec * 1000))
        self._update_timecode_label()
        self._update_vu_at_playhead()

    def _on_timeline_trim_changed(self, start_sec: float, end_sec: float, is_trimmed: bool) -> None:
        self.sequence_state["start_sec"] = start_sec if is_trimmed else 0.0
        self.sequence_state["end_sec"] = end_sec if is_trimmed else 0.0
        self._refresh_trim_summary()

    def _sync_tl_scroll(self) -> None:
        dur = self.timeline.duration
        span = self.timeline.visible_span
        zoomed = span < dur - 0.01
        self._tl_scroll_wrap.setVisible(zoomed)
        self.tl_scroll.blockSignals(True)
        self.tl_scroll.setRange(0, int(max(0.0, dur - span) * 100))
        self.tl_scroll.setPageStep(int(span * 100))
        self.tl_scroll.setSingleStep(max(1, int(span * 10)))
        self.tl_scroll.setValue(int(self.timeline.view_start * 100))
        self.tl_scroll.blockSignals(False)

    def _on_tl_scroll_changed(self, val: int) -> None:
        self.timeline.set_view_start(val / 100.0)

    def _on_zoom_slider_changed(self, val: int) -> None:
        z = 1.0 + (val / 10.0)
        self.lbl_zoom_val.setText(f"{int(round(z * 100))}%")
        self.timeline.set_zoom(z)

    def _on_timeline_zoom_changed(self, z: float) -> None:
        self.lbl_zoom_val.setText(f"{int(round(z * 100))}%")
        self.sld_zoom.blockSignals(True)
        self.sld_zoom.setValue(int(round((z - 1.0) * 10)))
        self.sld_zoom.blockSignals(False)

    def _adjust_zoom(self, factor: float) -> None:
        self.timeline.animate_zoom(self.timeline.zoom * factor)

    def _jump_start(self) -> None:
        pos = self.timeline.trim_in if self.timeline.is_trimmed else 0.0
        self.timeline.set_playhead(pos, emit=True)

    def _jump_end(self) -> None:
        pos = self.timeline.trim_out if self.timeline.is_trimmed else self.level_duration
        self.timeline.set_playhead(pos, emit=True)

    def _mark_in(self) -> None:
        self.timeline.mark_in_at_playhead()

    def _mark_out(self) -> None:
        self.timeline.mark_out_at_playhead()

    def _reset_trim(self) -> None:
        self.timeline.reset_trim_to_full(emit=True)

    def _toggle_inspector(self, visible: bool) -> None:
        self.inspector.setVisible(visible)

    # ------------------------------------------------------------------ Menu Dialogs
    def _open_planet_dialog(self) -> None:
        dlg = PlanetSettingsDialog(
            red_color=self.planet_state["red_color"],
            red_opacity=self.planet_state["red_opacity"],
            blue_color=self.planet_state["blue_color"],
            blue_opacity=self.planet_state["blue_opacity"],
            parent=self,
        )
        if dlg.exec() == dlg.DialogCode.Accepted:
            new_state = {
                "red_color": dlg.red_color,
                "red_opacity": dlg.red_opacity,
                "blue_color": dlg.blue_color,
                "blue_opacity": dlg.blue_opacity,
            }
            changed = new_state != self.planet_state
            self.planet_state = new_state
            self._refresh_summary_ui()
            if changed and self.level_path:
                if self._is_preview_busy():
                    self._cancel_active_preview(next_action="restart")
                else:
                    self._try_load_cached_preview()

    def _reset_planet_defaults(self) -> None:
        new_state = {
            "red_color": "#FF0000",
            "red_opacity": 1.0,
            "blue_color": "#0000FF",
            "blue_opacity": 1.0,
        }
        changed = new_state != self.planet_state
        self.planet_state = new_state
        self._refresh_summary_ui()
        if changed and self.level_path and self._is_preview_busy():
            self._cancel_active_preview(next_action="restart")

    def _open_sequence_dialog(self) -> None:
        dlg = SequenceSettingsDialog(
            duration_sec=self.level_duration,
            start_sec=self.sequence_state["start_sec"],
            end_sec=self.sequence_state["end_sec"],
            end_padding_sec=self.sequence_state["end_padding_sec"],
            hide_countdown=self.sequence_state["hide_countdown"],
            parent=self,
        )
        if dlg.exec() == dlg.DialogCode.Accepted:
            s = dlg.spn_start.value()
            e = dlg.spn_end.value()
            self.sequence_state["start_sec"] = s
            self.sequence_state["end_sec"] = e
            self.sequence_state["end_padding_sec"] = dlg.spn_pad.value()
            self.sequence_state["hide_countdown"] = dlg.chk_hide_cd.isChecked()
            if s > 0.05 or e > 0.05:
                self.timeline.set_trim_range(s, e, emit=False)
            else:
                self.timeline.reset_trim_to_full(emit=False)
            self._refresh_trim_summary()

    def _on_toggle_opt_render(self, checked: bool) -> None:
        self.export_state["experimental_optimize_render"] = bool(checked)
        self._update_statusbar()

    def _open_export_dialog(self) -> None:
        dlg = ExportSettingsDialog(self.export_state, self.caps, parent=self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self.export_state = dlg.result_state()
            self.act_opt_render.blockSignals(True)
            self.act_opt_render.setChecked(bool(self.export_state.get("experimental_optimize_render", False)))
            self.act_opt_render.blockSignals(False)
            self._sync_default_output_path()
            self._refresh_summary_ui()

    def _browse_adofai_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("dlg.steam_dir_title"), self.adofai_dir)
        if d:
            self.adofai_dir = d
            self._update_statusbar()

    def _browse_output_file(self) -> None:
        ext = self.export_state["format"]
        flt = "MP4 Video (*.mp4);;MKV Video (*.mkv)" if ext == "mp4" else "MKV Video (*.mkv);;MP4 Video (*.mp4)"
        start = self.output_path or self.export_state["output_dir"]
        path, _ = QFileDialog.getSaveFileName(self, tr("dlg.out_file_title"), start, flt)
        if path:
            self.output_path = path
            chosen_ext = os.path.splitext(path)[1].lower().lstrip(".")
            if chosen_ext in ("mp4", "mkv"):
                self.export_state["format"] = chosen_ext
            self.export_state["output_dir"] = os.path.dirname(path)
            self._refresh_summary_ui()

    def _browse_output_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("dlg.out_dir_title"), self.export_state["output_dir"])
        if d:
            self.export_state["output_dir"] = d
            self._sync_default_output_path()
            self._refresh_summary_ui()

    def _open_output_dir(self) -> None:
        target = self.output_path if (self.output_path and os.path.exists(self.output_path)) else self.export_state["output_dir"]
        if os.path.isfile(target):
            subprocess.Popen(["explorer", "/select,", target])
        elif os.path.isdir(target):
            os.startfile(target)

    def _show_hw_diagnostics(self) -> None:
        gpus = "\n".join(f"  - {g.name} ({g.vram_mb} MB)" for g in self.caps.gpus) or tr("dlg.diag_none")
        msg = tr(
            "dlg.diag_body",
            adofai=self.adofai_dir or tr("dlg.diag_undetected"),
            ffmpeg=self.caps.ffmpeg_path,
            gpus=gpus,
            encs=", ".join(self.caps.verified_encoders),
            rec=self.caps.recommended_label,
        )
        QMessageBox.information(self, tr("dlg.diag_title"), msg)

    # ------------------------------------------------------------------ Updates (GitHub Releases)
    def _check_updates(self, manual: bool = False) -> None:
        if getattr(self, "_upd_checker", None) is not None and self._upd_checker.isRunning():
            return
        self._upd_manual = manual
        self._upd_checker = UpdateCheckWorker()
        self._upd_checker.result.connect(self._on_update_checked)
        self._upd_checker.start()
        if manual:
            self.statusBar().showMessage(tr("sb.checking_upd"), 3000)

    def _on_update_checked(self, info, err: str) -> None:
        manual = getattr(self, "_upd_manual", False)
        if err:
            if manual:
                QMessageBox.warning(self, tr("upd.fail_title"), tr("upd.fail_msg", err=err))
            return
        if info is None:
            if manual:
                QMessageBox.information(self, tr("upd.title"), tr("upd.latest", ver=__version__))
            return
        if not manual and getattr(self, "_skip_update_version", "") == info.version:
            return
        notes = (info.notes or "").strip()
        if len(notes) > 900:
            notes = notes[:900] + "…"
        box = QMessageBox(self)
        box.setWindowTitle(tr("upd.avail_title"))
        box.setIcon(QMessageBox.Information)
        box.setText(tr("upd.avail_text", new=info.version, cur=__version__))
        box.setInformativeText(notes or tr("upd.no_notes"))
        btn_up = box.addButton(tr("upd.btn_now"), QMessageBox.AcceptRole) if info.asset_url else None
        btn_page = box.addButton(tr("upd.btn_page"), QMessageBox.ActionRole)
        btn_skip = box.addButton(tr("upd.btn_skip"), QMessageBox.DestructiveRole)
        box.addButton(tr("upd.btn_later"), QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if btn_up is not None and clicked is btn_up:
            self._download_update(info)
        elif clicked is btn_page and info.page_url:
            QDesktopServices.openUrl(QUrl(info.page_url))
        elif clicked is btn_skip:
            self._skip_update_version = info.version

    def _download_update(self, info) -> None:
        dlg = QProgressDialog(tr("upd.dl_msg", ver=info.version), tr("btn.cancel"), 0, 100, self)
        dlg.setWindowTitle(tr("upd.title"))
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setAutoClose(False)
        dlg.setMinimumDuration(0)
        worker = UpdateDownloadWorker(info)
        self._upd_dl = worker

        def on_progress(done: int, total: int) -> None:
            dlg.setValue(int(done * 100 / total) if total else 0)

        def on_done(path: str, err: str) -> None:
            dlg.close()
            if err:
                if "취소" not in err and "cancel" not in err.lower():
                    QMessageBox.warning(self, tr("upd.dl_fail"), err)
                return
            if self._is_busy_for_update():
                QMessageBox.information(self, tr("upd.title"), tr("upd.busy_msg", path=path))
                return
            ans = QMessageBox.question(self, tr("upd.install_title"), tr("upd.install_confirm"))
            if ans == QMessageBox.Yes:
                launch_installer(path)
                QApplication.quit()

        worker.progress.connect(on_progress)
        worker.done.connect(on_done)
        dlg.canceled.connect(worker.cancel)
        worker.start()

    def _is_busy_for_update(self) -> bool:
        return self._is_preview_busy() or not self.btn_render.isEnabled()

    def _show_about(self) -> None:
        QMessageBox.information(
            self,
            "ADOFAI PV Studio",
            tr("dlg.about_body", ver=__version__),
        )

    # ------------------------------------------------------------------ Pre-Render Confirm -> Render Card
    def _build_render_settings(self) -> RenderSettings:
        return RenderSettings(
            level_path=self.level_path,
            output_path=self.output_path,
            adofai_dir=self.adofai_dir,
            width=self.export_state["width"],
            height=self.export_state["height"],
            fps=self.export_state["fps"],
            gpu_mode=self.export_state["gpu_mode"],
            gpu_limit_percent=self.export_state["gpu_limit_percent"],
            ram_limit_percent=self.export_state["ram_limit_percent"],
            max_cpu_cores=self.export_state["max_cpu_cores"],
            codec=self.export_state["codec"],
            quality=self.export_state["quality"],
            include_bgm=self.export_state["include_bgm"],
            bgm_volume=self.export_state["bgm_volume"],
            include_hitsounds=self.export_state["include_hitsounds"],
            hitsound_volume=self.export_state["hitsound_volume"],
            audio_bitrate=self.export_state["audio_bitrate"],
            hide_countdown=self.sequence_state["hide_countdown"],
            hide_hit_text=True,
            start_sec=self.sequence_state["start_sec"],
            end_sec=self.sequence_state["end_sec"],
            end_padding_sec=self.sequence_state["end_padding_sec"],
            red_planet_color=self.planet_state["red_color"],
            red_planet_opacity=self.planet_state["red_opacity"],
            blue_planet_color=self.planet_state["blue_color"],
            blue_planet_opacity=self.planet_state["blue_opacity"],
            low_spec_mode=self.export_state["low_spec_mode"],
            experimental_optimize_render=bool(self.export_state.get("experimental_optimize_render", False)),
        )

    def _open_render_confirm(self) -> None:
        if self._is_preview_busy():
            return
        if not self.level_path or not os.path.isfile(self.level_path):
            QMessageBox.warning(self, tr("dlg.no_level_title"), tr("dlg.no_level_render_msg"))
            return
        if not self.output_path:
            self._sync_default_output_path()

        self._stop_playback()
        settings = self._build_render_settings()
        confirm = RenderConfirmDialog(
            settings=settings,
            level_info=self.level_info,
            caps=self.caps,
            level_duration_sec=self.level_duration,
            parent=self,
        )
        res = confirm.exec()
        if confirm.open_settings_requested:
            self._open_export_dialog()
            return
        if res != confirm.DialogCode.Accepted:
            return

        est_sec, target_frames, _clip_dur = estimate_render_time_sec(settings, self.caps, self.level_duration)
        runner = RenderRunner(caps=self.caps)
        title = self.level_info.display_title if self.level_info else os.path.basename(self.level_path)
        self.sb_state.setText(tr("sb.rendering"))
        card = RenderCardDialog(
            runner=runner,
            settings=settings,
            level_title=title,
            initial_est_sec=est_sec,
            target_frames=target_frames,
            parent=self,
        )
        card.render_finished.connect(self._on_render_finished)
        card.exec()
        self.sb_state.setText(tr("sb.ready"))

    def _on_render_finished(self, res: RenderResult) -> None:
        if res.success:
            self.statusBar().showMessage(tr("sb.render_done", path=res.output_path), 8000)

    # ------------------------------------------------------------------ UI Sync
    def _sync_default_output_path(self) -> None:
        if not self.level_path:
            return
        # Clean concise filename (song stem if available, else folder name) so filename fits cleanly in input box
        stem = ""
        if self.level_info and self.level_info.song_filename:
            stem = os.path.splitext(self.level_info.song_filename)[0]
        if not stem:
            stem = os.path.basename(os.path.dirname(self.level_path))
        safe_stem = "".join(c if c.isalnum() or c in " _-()" else "_" for c in stem).strip()[:28] or "adofai_pv"
        h = self.export_state["height"]
        fps = self.export_state["fps"]
        ext = self.export_state["format"]
        out_dir = self.export_state["output_dir"] or get_default_output_dir()
        self.output_path = os.path.join(out_dir, f"{safe_stem}_{h}p{fps}.{ext}")

    def _refresh_summary_ui(self) -> None:
        self._syncing_controls = True
        try:
            if self.level_info:
                fm = QFontMetrics(self.lbl_lvl_name.font())
                self.lbl_lvl_name.setText(fm.elidedText(self.level_info.display_title, Qt.ElideRight, 246))
                self.lbl_lvl_name.setToolTip(self.level_info.display_title)
                bgm = self.level_info.song_filename or tr("insp.no_bgm")
                sub = tr("insp.audio_label", bgm=bgm)
                fm_sub = QFontMetrics(self.lbl_lvl_sub.font())
                self.lbl_lvl_sub.setText(fm_sub.elidedText(sub, Qt.ElideRight, 246))
                self.lbl_lvl_sub.setToolTip(sub)
                self.lbl_insp_bpm.setText("")
            else:
                self.lbl_lvl_name.setText(tr("insp.no_level"))
                self.lbl_lvl_sub.setText(tr("insp.no_level_sub"))

            cur_wh = (self.export_state["width"], self.export_state["height"])
            for i in range(self.cmb_res.count()):
                if self.cmb_res.itemData(i) == cur_wh:
                    self.cmb_res.setCurrentIndex(i)
                    break
            if self.spn_fps.value() != self.export_state["fps"]:
                self.spn_fps.setValue(self.export_state["fps"])
            self.cmb_fmt.setCurrentIndex(1 if self.export_state["format"] == "mkv" else 0)
            self.cmb_codec.setCurrentIndex(1 if self.export_state["codec"] == "hevc" else 0)
            q_idx = {"ultra": 0, "high": 1, "balanced": 2}.get(self.export_state.get("quality", "high"), 1)
            self.cmb_bitrate.setCurrentIndex(q_idx)
            self.chk_bgm.setChecked(self.export_state["include_bgm"])
            self.chk_hit.setChecked(self.export_state["include_hitsounds"])

            self.btn_red_picker.set_color_state(self.planet_state["red_color"], self.planet_state["red_opacity"])
            self.btn_blue_picker.set_color_state(self.planet_state["blue_color"], self.planet_state["blue_opacity"])

            out_fn = os.path.basename(self.output_path) if self.output_path else ""
            self.ed_output_name.setText(out_fn)
            self.ed_output_name.home(False)
            self.ed_output_name.setToolTip(self.output_path)

            out_dir = os.path.dirname(self.output_path) if self.output_path else self.export_state["output_dir"]
            fm_d = QFontMetrics(self.ed_out_dir.font())
            self.ed_out_dir.setText(fm_d.elidedText(out_dir, Qt.ElideMiddle, 175))
            self.ed_out_dir.setToolTip(out_dir)

            if hasattr(self, "spn_end_pad"):
                self.spn_end_pad.setValue(self.sequence_state["end_padding_sec"])
                self.chk_hide_cd_quick.setChecked(self.sequence_state["hide_countdown"])
        finally:
            self._syncing_controls = False

        self._update_timecode_label()
        self._refresh_trim_summary()
        self._update_statusbar()

    def _update_timecode_label(self) -> None:
        sec = self.timeline.playhead
        self.lbl_tc.setText(format_timecode_ms(sec))
        frame = int(round(sec * self.export_state["fps"]))
        self.lbl_frame_num.setText(tr("tl.frame_num", n=frame))

    def _refresh_trim_summary(self) -> None:
        fps = self.export_state["fps"]
        self._syncing_controls = True
        try:
            self.spn_in.setRange(0.0, max(1.0, self.level_duration))
            self.spn_out.setRange(0.0, max(1.0, self.level_duration))
            if self.timeline.is_trimmed:
                s = self.timeline.trim_in
                e = self.timeline.trim_out
                self.spn_in.setValue(s)
                self.spn_out.setValue(e)
                self.btn_reset_trim.show()
                self.lbl_tl_range.setText(tr("tl.range_trim", s=format_timecode(s), e=format_timecode(e)))
            else:
                span = self.level_duration
                self.spn_in.setValue(0.0)
                self.spn_out.setValue(self.level_duration)
                self.btn_reset_trim.hide()
                self.lbl_tl_range.setText(tr("tl.range_full", span=format_timecode(span)))
        finally:
            self._syncing_controls = False

        self._refresh_live_estimates()

    def _refresh_live_estimates(self) -> None:
        settings = self._build_render_settings()
        est_sec, target_frames, clip_dur = estimate_render_time_sec(settings, self.caps, self.level_duration)
        est_mb = estimate_output_size_mb(settings, self.level_info, clip_dur)

        self.val_est_dur.setText(f"{format_timecode(clip_dur)} ({target_frames:,}f)")
        self.val_est_size.setText(tr("insp.size_mb", v=est_mb) if est_mb < 1024 else tr("insp.size_gb", v=est_mb / 1024.0))
        self.val_est_time.setText(format_duration_korean(est_sec))

    def _update_statusbar(self) -> None:
        _enc, enc_label = resolve_video_encoder(self.caps, self.export_state["gpu_mode"], self.export_state["codec"])
        self.sb_enc.setText(f" {enc_label}")
        eng_str = tr("sb.steam_ok") if self.adofai_dir else tr("sb.steam_none")
        if self.export_state.get("experimental_optimize_render"):
            eng_str = tr("sb.opt_on")
        self.sb_engine.setText(eng_str)
        if self.level_info:
            self.sb_chart.setText(
                tr(
                    "sb.chart_info",
                    tiles=self.level_info.tile_count,
                    decos=self.level_info.decoration_count,
                    filts=len(self.level_info.filters),
                )
            )
        else:
            self.sb_chart.setText(tr("sb.no_level"))

    def _on_inspector_tab_changed(self, idx: int) -> None:
        target = self._tab_out_body if idx == 0 else self._tab_seq_body
        if self.isVisible():
            fade_in_widget(target, start=0.45, duration_ms=130)

    # ------------------------------------------------------------------ Live Theme Switching
    def _switch_theme(self, theme_name: str) -> None:
        chosen = set_theme(theme_name, persist=True)
        for code, act in self._theme_actions.items():
            act.blockSignals(True)
            act.setChecked(code == chosen)
            act.blockSignals(False)
        self.apply_theme()
        if self.isVisible():
            anim = QPropertyAnimation(self, b"windowOpacity", self)
            anim.setDuration(140)
            anim.setStartValue(0.86)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            self._theme_fade_anim = anim
            anim.start()

    def apply_theme(self) -> None:
        pal = get_palette()
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_studio_qss())

        # Pane containers
        self._left_pane.setStyleSheet(
            f"QFrame#leftPane {{ background-color: {pal.bg_canvas}; border-right: 1px solid {pal.hdr_border}; }}"
        )
        self._tab_out_body.setStyleSheet(f"background-color: {pal.bg_inspector};")
        self._tab_seq_body.setStyleSheet(f"background-color: {pal.bg_inspector};")
        self._footer_bar.setStyleSheet(
            f"QFrame#inspFooter {{ background-color: {pal.bg_inspector_footer}; border-top: 1px solid {pal.border_subtle}; }}"
        )
        if pal.is_light:
            self._est_frame.setStyleSheet(
                f"QFrame#estBox {{ background-color: {pal.bg_est_box}; border: 1px solid {pal.border_subtle}; "
                f"border-left: 3px solid {pal.accent_pink}; border-radius: 6px; }}"
            )
        else:
            self._est_frame.setStyleSheet(
                f"QFrame#estBox {{ background-color: {pal.bg_est_box}; border: 1px solid {pal.border_subtle}; border-radius: 2px; }}"
            )
        self._tl_dock.setStyleSheet(
            f"QFrame#tlDock {{ background-color: {pal.bg_timeline_dock}; border-top: 1px solid {pal.hdr_border}; }}"
        )

        # Separators
        for hsep in self._hseps:
            hsep.setStyleSheet(f"background-color: {pal.sep_color}; border: none;")
        for vsep in self._vseps:
            vsep.setStyleSheet(f"background-color: {pal.sep_color}; border: none;")

        # Headers & labels
        for hdr in self._pane_headers:
            hdr.apply_theme()
        for ml in self._muted_labels:
            ml.setStyleSheet(f"color: {pal.text_secondary}; font-size: 11px; background: transparent;")
        for ev in self._est_vals:
            ev.setStyleSheet(
                f"font-family: 'Cascadia Mono', Consolas, monospace; font-size: 11px; "
                f"font-weight: {'600' if pal.is_light else '400'}; color: {pal.text_mono}; background: transparent;"
            )
        self.ed_out_dir.setStyleSheet(f"color: {pal.text_secondary}; font-size: 11px;")
        self.lbl_frame_num.setStyleSheet(
            f"color: {pal.text_secondary}; font-family: 'Cascadia Mono', Consolas, monospace; font-size: 11px; background: transparent;"
        )
        self.lbl_zoom_val.setStyleSheet(
            f"color: {pal.text_secondary}; font-family: 'Cascadia Mono', Consolas, monospace; font-size: 11px; background: transparent;"
        )
        if pal.is_light:
            self.sb_enc.setStyleSheet("color: #217BE0; font-weight: 600;")
            self.sb_state.setStyleSheet("color: #D84D88; font-weight: 600;")
        else:
            self.sb_enc.setStyleSheet("")
            self.sb_state.setStyleSheet("")

        # Re-tint vector icons for current theme
        for btn, kind in self._tool_btn_specs:
            if btn is self.btn_play:
                btn.setIcon(make_icon("pause" if self._playing else "play", pal.icon_color, 14))
            elif btn is self.btn_mute:
                ic_col = pal.accent_danger if (self._muted and pal.is_light) else pal.icon_color
                btn.setIcon(make_icon("volume_off" if self._muted else "volume_on", ic_col, 14))
            else:
                btn.setIcon(make_icon(kind, pal.icon_color, 14))

        # Custom painted widgets
        self.viewport.apply_theme()
        self.vu_meter.update()
        self.timeline.update()
        self.btn_red_picker.update()
        self.btn_blue_picker.update()
        for sp in (self.main_split, self.vert_split):
            for idx in range(1, sp.count()):
                sp.handle(idx).update()
        self.update()

    # ------------------------------------------------------------------ Live Language Switching
    def _switch_language(self, lang_code: str) -> None:
        chosen = set_language(lang_code, persist=True)
        for code, act in self._lang_actions.items():
            act.blockSignals(True)
            act.setChecked(code == chosen)
            act.blockSignals(False)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        # Menus & actions
        self.m_file.setTitle(tr("menu.file"))
        self.act_open.setText(tr("menu.open_level"))
        self.act_steam.setText(tr("menu.steam_dir"))
        self.act_quit.setText(tr("menu.quit"))

        self.m_edit.setTitle(tr("menu.edit"))
        self.act_in.setText(tr("menu.mark_in"))
        self.act_out.setText(tr("menu.mark_out"))
        self.act_reset_trim.setText(tr("menu.reset_trim"))
        self.act_planet.setText(tr("menu.planet_cfg"))
        self.act_planet_def.setText(tr("menu.planet_reset"))
        self.act_exp_cfg.setText(tr("menu.export_cfg"))
        self.m_lang.setTitle(tr("menu.language"))

        self.m_seq.setTitle(tr("menu.seq"))
        self.act_prev.setText(tr("menu.prev_build"))
        self.act_prev_force.setText(tr("menu.prev_rebuild"))
        self.act_proxy_cfg.setText(tr("menu.proxy_cfg"))
        self.act_seq_cfg.setText(tr("menu.open_seq_tab"))
        self.act_clear_cache.setText(tr("menu.clear_cache"))

        self.m_view.setTitle(tr("menu.view"))
        self.act_toggle_inspector.setText(tr("menu.toggle_inspector"))
        self.m_theme.setTitle(tr("menu.theme"))
        self._theme_actions["dark"].setText(tr("menu.theme_dark"))
        self._theme_actions["pastel_light"].setText(tr("menu.theme_light"))
        self.act_zi.setText(tr("menu.zoom_in"))
        self.act_zo.setText(tr("menu.zoom_out"))
        self.act_zf.setText(tr("menu.zoom_fit"))

        self.m_exp.setTitle(tr("menu.render"))
        self.act_render.setText(tr("menu.render_start"))
        self.act_opt_render.setText(tr("menu.opt_render"))
        self.act_out_file.setText(tr("menu.out_file"))
        self.act_out_dir.setText(tr("menu.out_dir"))
        self.act_open_out.setText(tr("menu.open_out_dir"))

        self.m_help.setTitle(tr("menu.help"))
        self.act_diag.setText(tr("menu.diag"))
        self.act_upd.setText(tr("menu.check_upd"))
        self.act_about.setText(tr("menu.about"))

        for hdr in self._pane_headers:
            hdr.retranslate_ui()

        # Viewer & Inspector headers / tabs / labels
        preview_busy = self._is_preview_busy()
        if self.level_path:
            self.lbl_vp_title.setText(tr("vp.header_file", name=os.path.basename(self.level_path)))
            if not preview_busy and not self.viewport.has_video() and self.level_info:
                detail = tr(
                    "vp.level_detail",
                    bpm=self.level_info.bpm,
                    tiles=self.level_info.tile_count,
                    decos=self.level_info.decoration_count,
                    filts=len(self.level_info.filters),
                )
                self.viewport.set_level_placeholder(self.level_info.display_title, detail)
        else:
            self.lbl_vp_title.setText(tr("vp.header"))
        self._update_vp_mode_label()
        self.viewport.retranslate_ui()

        self.lbl_insp_hdr.setText(tr("insp.header"))
        self.insp_tabs.setTabText(0, tr("insp.tab_out"))
        self.insp_tabs.setTabText(1, tr("insp.tab_seq"))
        self.lbl_k_res.setText(tr("insp.res"))
        self.lbl_k_fps.setText(tr("insp.fps"))
        self.spn_fps.setToolTip(tr("insp.fps_tip"))
        self.lbl_k_fmt.setText(tr("insp.fmt"))
        self.lbl_k_codec.setText(tr("insp.codec"))
        self.lbl_k_bitrate.setText(tr("insp.bitrate"))
        self.cmb_bitrate.blockSignals(True)
        self.cmb_bitrate.setItemText(0, tr("insp.br_high"))
        self.cmb_bitrate.setItemText(1, tr("insp.br_mid"))
        self.cmb_bitrate.setItemText(2, tr("insp.br_low"))
        self.cmb_bitrate.blockSignals(False)
        self.lbl_k_audio.setText(tr("insp.audio"))
        self.chk_bgm.setText(tr("insp.bgm"))
        self.chk_hit.setText(tr("insp.hitsound"))
        self.lbl_rng.setText(tr("insp.range"))
        self.btn_reset_trim.setText(tr("insp.reset_full"))
        self.lbl_k_start.setText(tr("insp.start"))
        self.lbl_k_end.setText(tr("insp.end"))
        self.lbl_k_fname.setText(tr("insp.filename"))
        self.lbl_k_dir.setText(tr("insp.folder"))
        self.btn_browse_dir.setToolTip(tr("insp.folder_tip"))
        self.lbl_est_dur.setText(tr("insp.est_dur"))
        self.lbl_est_size.setText(tr("insp.est_size"))
        self.lbl_est_time.setText(tr("insp.est_time"))

        self.lbl_pl_hdr.setText(tr("insp.planet_hdr"))
        self.btn_red_picker.set_label(tr("insp.fire"))
        self.btn_red_picker.setToolTip(tr("insp.fire_tip"))
        self.btn_blue_picker.set_label(tr("insp.ice"))
        self.btn_blue_picker.setToolTip(tr("insp.ice_tip"))
        self.btn_pl_rst.setText(tr("insp.planet_reset_btn"))
        self.lbl_seq_hdr.setText(tr("insp.seq_hdr"))
        self.lbl_k_pad.setText(tr("insp.end_pad"))
        self.spn_end_pad.setSuffix(tr("insp.sec_suffix"))
        self.chk_hide_cd_quick.setText(tr("insp.hide_cd"))
        self.btn_adv.setText(tr("insp.btn_adv"))
        self.btn_adv.setToolTip(tr("insp.btn_adv_tip"))
        self.btn_render.setText(tr("insp.btn_render"))

        # Timeline & transport
        self.lbl_tl_hdr.setText(tr("tl.header"))
        self.btn_to_start.setToolTip(tr("tl.tip_start"))
        self.btn_step_back.setToolTip(tr("tl.tip_step_back"))
        self.btn_play.setToolTip(tr("tl.tip_play"))
        self.btn_step_fwd.setToolTip(tr("tl.tip_step_fwd"))
        self.btn_to_end.setToolTip(tr("tl.tip_end"))
        self.btn_mark_in.setToolTip(tr("tl.tip_mark_in"))
        self.btn_mark_out.setToolTip(tr("tl.tip_mark_out"))
        self.btn_mute.setToolTip(tr("tl.tip_mute"))
        self.sld_vol.setToolTip(tr("tl.tip_vol"))
        self.sld_zoom.setToolTip(tr("tl.tip_zoom"))
        self.btn_fit.setText(tr("tl.btn_fit"))
        self.sb_state.setText(tr("sb.previewing") if preview_busy else tr("sb.ready"))

        self._refresh_summary_ui()
        self.timeline.update()

    def closeEvent(self, event) -> None:
        self._stop_playback()
        self._pending_preview_action = None
        self._preview_gen_id += 1
        if self._preview_runner is not None:
            self._preview_runner.cancel(force_kill=True)
        workers = list(self._live_preview_workers)
        for lw in workers:
            if getattr(lw, "runner", None) is not None:
                lw.runner.cancel(force_kill=True)
        for w in workers:
            if w.isRunning():
                w.wait(2500)
        self._live_preview_workers.clear()
        self._preview_worker = None
        self._preview_runner = None
        for wf in list(self._wf_workers):
            if wf.isRunning():
                wf.wait(1000)
        get_warm_session_manager().shutdown(force=True)
        super().closeEvent(event)
