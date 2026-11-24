"""Rendering Card Window featuring progress bar, live ETA/completion clock, and Claude Code-style console bar."""

from __future__ import annotations

import datetime
import math
import os
import subprocess
import time
from typing import Optional

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from ..render_runner import RenderProgress, RenderResult, RenderRunner, RenderSettings
from .theme import (
    format_duration_korean,
    get_palette,
)


class RenderWorker(QThread):
    progress = Signal(object)
    log = Signal(str)
    finished_result = Signal(object)

    def __init__(self, runner: RenderRunner, settings: RenderSettings) -> None:
        super().__init__()
        self.runner = runner
        self.settings = settings

    def run(self) -> None:
        res = self.runner.run(
            self.settings,
            progress_cb=lambda p: self.progress.emit(p),
            log_cb=lambda m: self.log.emit(m),
        )
        self.finished_result.emit(res)


class ClaudeSpinnerGlyph(QWidget):
    """Vector-painted Claude Code-style breathing starburst/asterisk spinner (zero missing font glyphs)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self._phase = 0
        self._state = "active"  # "active", "done", "error"
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(95)

    def set_state(self, state: str) -> None:
        self._state = state
        if state != "active":
            self._timer.stop()
        elif not self._timer.isActive():
            self._timer.start(95)
        self.update()

    def _tick(self) -> None:
        self._phase = (self._phase + 1) % 12
        self.update()

    def paintEvent(self, _ev) -> None:
        pal = get_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        cx, cy = self.width() / 2.0, self.height() / 2.0

        if self._state == "done":
            p.setPen(QPen(QColor(pal.accent_success), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawLine(QPointF(cx - 4.5, cy + 0.5), QPointF(cx - 1.2, cy + 3.8))
            p.drawLine(QPointF(cx - 1.2, cy + 3.8), QPointF(cx + 5.0, cy - 3.5))
            p.end()
            return

        if self._state == "error":
            p.setPen(QPen(QColor(pal.accent_danger), 2.0, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(cx - 4.0, cy - 4.0), QPointF(cx + 4.0, cy + 4.0))
            p.drawLine(QPointF(cx + 4.0, cy - 4.0), QPointF(cx - 4.0, cy + 4.0))
            p.end()
            return

        # Breathing starburst spinner cycle: dot -> 4-ray -> 6-ray -> 8-ray -> 6-ray -> 4-ray
        step_map = [0, 4, 6, 8, 8, 6, 4, 6, 8, 8, 6, 4]
        rays = step_map[self._phase]
        col = QColor(pal.accent_status_blue)

        if rays == 0:
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawEllipse(QPointF(cx, cy), 2.1, 2.1)
        else:
            rot_deg = (self._phase * 15.0) % 360.0
            radius = 5.6 if rays == 8 else (5.0 if rays == 6 else 4.4)
            pen = QPen(col, 1.55, Qt.SolidLine, Qt.RoundCap)
            p.setPen(pen)
            for i in range(rays):
                ang = math.radians(rot_deg + (360.0 / rays) * i)
                x2 = cx + math.cos(ang) * radius
                y2 = cy + math.sin(ang) * radius
                p.drawLine(QPointF(cx, cy), QPointF(x2, y2))
        p.end()


class ClaudeConsoleBar(QFrame):
    """Monospace dark console status strip with blue starburst spinner and stage-accurate localized status."""

    STAGE_VERB_KEYS = {
        "launching": "rc.v_launching",
        "loading_scene": "rc.v_loading_scene",
        "loading_level": "rc.v_loading_level",
        "starting_playback": "rc.v_starting_playback",
        "seeking": "rc.v_seeking",
        "rendering": "rc.v_rendering",
        "done": "rc.v_done",
        "mixing_audio": "rc.v_mixing_audio",
        "muxing": "rc.v_muxing",
        "completed": "rc.v_completed",
        "cancelled": "rc.v_cancelled",
    }

    STAGE_HINT_KEYS = {
        "launching": "rc.h_launching",
        "loading_scene": "rc.h_loading_scene",
        "loading_level": "rc.h_loading_level",
        "starting_playback": "rc.h_starting_playback",
        "done": "rc.h_done",
        "mixing_audio": "rc.h_mixing_audio",
        "muxing": "rc.h_muxing",
    }

    def __init__(self, optimize_render: bool = False, start_sec: float = 0.0, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        pal = get_palette()
        self._optimize_render = optimize_render
        self._start_sec = start_sec
        self.setObjectName("claudeConsoleBar")
        self.setFixedHeight(40)
        if pal.is_light:
            self.setStyleSheet(
                f"QFrame#claudeConsoleBar {{ background-color: {pal.console_bg}; "
                f"border: 1px solid {pal.console_border}; border-left: 3px solid {pal.accent_primary}; border-radius: 6px; }}"
            )
        else:
            self.setStyleSheet(
                f"QFrame#claudeConsoleBar {{ background-color: {pal.console_bg}; "
                f"border: 1px solid {pal.console_border}; border-radius: 4px; }}"
            )

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(8)

        self.spinner = ClaudeSpinnerGlyph()
        lay.addWidget(self.spinner)

        self.lbl_verb = QLabel(tr("rc.v_launching"))
        self.lbl_verb.setStyleSheet(
            f"color: {pal.accent_status_blue}; font-family: 'Cascadia Mono', Consolas, 'Malgun Gothic', 'Yu Gothic UI', monospace; "
            f"font-size: 12px; font-weight: 600; background: transparent;"
        )
        lay.addWidget(self.lbl_verb)

        self.lbl_meta = QLabel(f"(0s · {tr('rc.h_launching')} · {tr('rc.esc_cancel')})")
        self.lbl_meta.setStyleSheet(
            f"color: {pal.console_meta}; font-family: 'Cascadia Mono', Consolas, 'Malgun Gothic', 'Yu Gothic UI', monospace; "
            f"font-size: 12px; background: transparent;"
        )
        lay.addWidget(self.lbl_meta, 1)

    def update_console(
        self,
        stage: str,
        elapsed_s: float,
        frames: int,
        total_frames: int,
        rfps: float,
        active: bool,
        song_sec: float = 0.0,
    ) -> None:
        pal = get_palette()
        vkey = self.STAGE_VERB_KEYS.get(stage, "rc.v_working")
        if stage == "loading_level" and self._optimize_render:
            vkey = "rc.v_loading_level_opt"
        self.lbl_verb.setText(tr(vkey))
        if not active:
            if stage == "completed":
                self.spinner.set_state("done")
                self.lbl_verb.setStyleSheet(
                    f"color: {pal.accent_success}; font-family: 'Cascadia Mono', Consolas, 'Malgun Gothic', 'Yu Gothic UI', monospace; "
                    f"font-size: 12px; font-weight: 600; background: transparent;"
                )
                self.lbl_meta.setText(f"({int(elapsed_s)}s · {tr('rc.frames_done', n=frames)})")
            else:
                self.spinner.set_state("error")
                self.lbl_verb.setStyleSheet(
                    f"color: {pal.accent_danger}; font-family: 'Cascadia Mono', Consolas, 'Malgun Gothic', 'Yu Gothic UI', monospace; "
                    f"font-size: 12px; font-weight: 600; background: transparent;"
                )
                self.lbl_meta.setText(f"({int(elapsed_s)}s · {tr('rc.stopped_word')})")
            return

        self.spinner.set_state("active")
        self.lbl_verb.setStyleSheet(
            f"color: {pal.accent_status_blue}; font-family: 'Cascadia Mono', Consolas, 'Malgun Gothic', 'Yu Gothic UI', monospace; "
            f"font-size: 12px; font-weight: 600; background: transparent;"
        )
        parts = [f"{int(elapsed_s)}s"]
        if stage == "seeking" and self._start_sec > 0.1:
            parts.append(f"{song_sec:.1f}s / {self._start_sec:.1f}s")
        elif stage == "rendering" and frames > 0:
            if total_frames > 0:
                parts.append(f"{frames:,} / {total_frames:,}f")
            else:
                parts.append(f"{frames:,}f")
            if rfps > 0.1:
                parts.append(f"{rfps:.1f} fps")
        else:
            hkey = self.STAGE_HINT_KEYS.get(stage, "")
            if stage == "loading_level" and self._optimize_render:
                hkey = "rc.h_loading_level_opt"
            if hkey:
                parts.append(tr(hkey))
        parts.append(tr("rc.esc_cancel"))
        self.lbl_meta.setText("(" + " · ".join(parts) + ")")


class RenderCardDialog(QDialog):
    """Dedicated rendering card window with progress bar, ETA, Claude-style console bar, and collapsible logs."""

    render_finished = Signal(object)

    def __init__(
        self,
        runner: RenderRunner,
        settings: RenderSettings,
        level_title: str,
        initial_est_sec: float,
        target_frames: int,
        parent: Optional[QWidget] = None,
        auto_start: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("rc.title_running"))
        self.setMinimumWidth(620)
        self.resize(620, 290)
        self.setModal(True)

        self.runner = runner
        self.settings = settings
        self.level_title = level_title
        self._target_frames = max(1, target_frames)
        self._initial_est_sec = max(1.0, initial_est_sec)
        self._started_mono = time.monotonic()
        self._last_progress = RenderProgress(stage="launching")
        self._running = auto_start
        self._result: Optional[RenderResult] = None

        self._build_ui()
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick_elapsed)
        if auto_start:
            self._start_worker()
            self._clock_timer.start(250)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(12)

        # Header
        hdr = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        self.lbl_title = QLabel(self.level_title)
        f = QFont()
        f.setPointSize(11)
        f.setBold(True)
        self.lbl_title.setFont(f)
        spec = f"{self.settings.width}×{self.settings.height} · {self.settings.fps}fps CFR · {os.path.basename(self.settings.output_path)}"
        self.lbl_spec = QLabel(spec)
        self.lbl_spec.setObjectName("muted")
        title_col.addWidget(self.lbl_title)
        title_col.addWidget(self.lbl_spec)
        hdr.addLayout(title_col, 1)
        root.addLayout(hdr)

        # Console status bar (blue starburst spinner + stage-accurate status)
        self.console_bar = ClaudeConsoleBar(
            optimize_render=bool(self.settings.experimental_optimize_render),
            start_sec=float(self.settings.start_sec),
        )
        root.addWidget(self.console_bar)

        pal = get_palette()
        # Progress bar
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(9)
        self.progress.setStyleSheet(
            f"QProgressBar {{ background: {pal.prog_bg}; border: 1px solid {pal.prog_border}; border-radius: 4px; }}"
            f"QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {pal.prog_chunk_start}, stop:1 {pal.prog_chunk_end}); border-radius: 3px; }}"
        )
        root.addWidget(self.progress)

        # Metrics row (Progress % | Remaining | Estimated Finish Time)
        metrics_card = QFrame()
        metrics_card.setObjectName("panel")
        ml = QHBoxLayout(metrics_card)
        ml.setContentsMargins(14, 10, 14, 10)
        ml.setSpacing(16)

        self.lbl_pct = self._add_metric(ml, tr("rc.metric_pct"), "0.0%")
        self._add_vsep(ml)
        self.lbl_rem = self._add_metric(ml, tr("rc.metric_rem"), format_duration_korean(self._initial_est_sec))
        self._add_vsep(ml)
        eta_clk = (datetime.datetime.now() + datetime.timedelta(seconds=self._initial_est_sec)).strftime("%H:%M:%S")
        self.lbl_eta_clock = self._add_metric(ml, tr("rc.metric_eta"), eta_clk, accent=True)
        root.addWidget(metrics_card)

        self.lbl_detail = QLabel(tr("rc.detail_init"))
        self.lbl_detail.setObjectName("muted")
        root.addWidget(self.lbl_detail)

        # Collapsible console log drawer
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumHeight(0)
        self.txt_log.setStyleSheet(
            f"background-color: {pal.log_bg}; border: 1px solid {pal.border_subtle}; "
            f"font-family: 'Cascadia Mono', Consolas, monospace; font-size: 11px; color: {pal.log_fg};"
        )
        self.txt_log.hide()
        self._log_expanded = False
        root.addWidget(self.txt_log)

        # Footer controls
        btn_row = QHBoxLayout()
        self.btn_log = QPushButton(tr("rc.btn_log_show"))
        self.btn_log.setObjectName("chipBtn")
        self.btn_log.clicked.connect(self._toggle_log)

        self.btn_open_dir = QPushButton(tr("rc.btn_open_dir"))
        self.btn_open_dir.setEnabled(False)
        self.btn_open_dir.clicked.connect(self._open_output)

        self.btn_cancel = QPushButton(tr("rc.btn_cancel"))
        self.btn_cancel.setObjectName("dangerBtn")
        self.btn_cancel.clicked.connect(self._on_cancel_or_close)

        btn_row.addWidget(self.btn_log)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_open_dir)
        btn_row.addWidget(self.btn_cancel)
        root.addLayout(btn_row)

    def _add_metric(self, parent_lay: QHBoxLayout, label: str, value: str, accent: bool = False) -> QLabel:
        pal = get_palette()
        col = QVBoxLayout()
        col.setSpacing(2)
        lk = QLabel(label)
        lk.setStyleSheet(f"color: {pal.text_secondary}; font-size: 11px; background: transparent;")
        lv = QLabel(value)
        color = (pal.accent_pink if pal.is_light else pal.accent_status_blue) if accent else pal.text_primary
        lv.setStyleSheet(
            f"color: {color}; font-family: 'Cascadia Mono', Consolas, monospace; font-size: 14px; font-weight: 700; background: transparent;"
        )
        col.addWidget(lk)
        col.addWidget(lv)
        parent_lay.addLayout(col, 1)
        return lv

    def _add_vsep(self, parent_lay: QHBoxLayout) -> None:
        pal = get_palette()
        line = QFrame()
        line.setFixedWidth(1)
        line.setStyleSheet(f"background-color: {pal.sep_color};")
        parent_lay.addWidget(line)

    def _toggle_log(self) -> None:
        if getattr(self, "_log_anim", None) is not None:
            self._log_anim.stop()
        if self._log_expanded:
            self._log_expanded = False
            self.btn_log.setText(tr("rc.btn_log_show"))
            anim = QPropertyAnimation(self.txt_log, b"maximumHeight", self)
            anim.setDuration(160)
            anim.setStartValue(self.txt_log.maximumHeight())
            anim.setEndValue(0)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.finished.connect(lambda: (self.txt_log.hide(), self.resize(self.width(), 290)))
            self._log_anim = anim
            anim.start()
        else:
            self._log_expanded = True
            self.btn_log.setText(tr("rc.btn_log_hide"))
            self.txt_log.show()
            self.resize(self.width(), 440)
            anim = QPropertyAnimation(self.txt_log, b"maximumHeight", self)
            anim.setDuration(175)
            anim.setStartValue(self.txt_log.maximumHeight())
            anim.setEndValue(140)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            self._log_anim = anim
            anim.start()

    def _start_worker(self) -> None:
        self._worker = RenderWorker(self.runner, self.settings)
        self._worker.progress.connect(self._on_progress)
        self._worker.log.connect(self._append_log)
        self._worker.finished_result.connect(self._on_finished)
        self._worker.start()

    def _append_log(self, msg: str) -> None:
        self.txt_log.appendPlainText(msg)

    def _tick_elapsed(self) -> None:
        if not self._running:
            return
        elapsed = time.monotonic() - self._started_mono
        p = self._last_progress
        self.console_bar.update_console(
            p.stage, elapsed, p.frames, self._target_frames, p.render_fps, active=True, song_sec=p.song_sec
        )

    def _on_progress(self, p: RenderProgress) -> None:
        self._last_progress = p
        elapsed = time.monotonic() - self._started_mono
        self.console_bar.update_console(
            p.stage, elapsed, p.frames, self._target_frames, p.render_fps, active=True, song_sec=p.song_sec
        )

        target_val = int(round(max(0.0, min(100.0, p.percent)) * 10))
        self._p_anim = QPropertyAnimation(self.progress, b"value")
        self._p_anim.setDuration(150)
        self._p_anim.setStartValue(self.progress.value())
        self._p_anim.setEndValue(target_val)
        self._p_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._p_anim.start()

        self.lbl_pct.setText(f"{p.percent:.1f}%")
        self.lbl_detail.setText(p.message)

        if p.eta_sec > 0.5:
            rem = p.eta_sec + 3.0  # include small audio mux tail
            self.lbl_rem.setText(format_duration_korean(rem))
            eta_dt = datetime.datetime.now() + datetime.timedelta(seconds=rem)
            self.lbl_eta_clock.setText(eta_dt.strftime("%H:%M:%S"))
        elif p.stage in ("mixing_audio", "muxing"):
            self.lbl_rem.setText(tr("rc.finishing"))

    def _on_finished(self, res: RenderResult) -> None:
        self._running = False
        self._result = res
        self._clock_timer.stop()
        elapsed = time.monotonic() - self._started_mono

        if res.success:
            pal = get_palette()
            self.setWindowTitle(tr("rc.title_done"))
            self.progress.setValue(1000)
            self.progress.setStyleSheet(
                f"QProgressBar {{ background: {pal.prog_bg}; border: 1px solid {pal.prog_border}; border-radius: 4px; }}"
                f"QProgressBar::chunk {{ background: {pal.accent_success}; border-radius: 3px; }}"
            )
            self.console_bar.update_console("completed", elapsed, res.frames, res.frames, 0.0, active=False)
            self.lbl_pct.setText("100.0%")
            self.lbl_rem.setText(tr("rc.done_word"))
            self.lbl_eta_clock.setText(datetime.datetime.now().strftime("%H:%M:%S"))
            fb = f" · {res.fallback_note}" if res.fallback_note else ""
            self.lbl_detail.setText(tr("rc.done_detail", frames=res.frames, sec=res.video_sec, enc=res.encoder_used, fb=fb))
            self.btn_open_dir.setEnabled(True)
            self.btn_cancel.setObjectName("primaryBtn")
            self.btn_cancel.setText(tr("btn.close"))
            self.btn_cancel.style().unpolish(self.btn_cancel)
            self.btn_cancel.style().polish(self.btn_cancel)
        else:
            self.setWindowTitle(tr("rc.title_stop"))
            self.console_bar.update_console("cancelled", elapsed, self._last_progress.frames, self._target_frames, 0.0, active=False)
            self.lbl_rem.setText("-")
            first_line = (res.error or tr("rc.stopped_default")).splitlines()[0]
            self.lbl_detail.setText(first_line)
            if res.error:
                self._append_log("[ERROR] " + res.error)
                if not self.txt_log.isVisible():
                    self._toggle_log()
            self.btn_cancel.setObjectName("")
            self.btn_cancel.setText(tr("btn.close"))
            self.btn_cancel.style().unpolish(self.btn_cancel)
            self.btn_cancel.style().polish(self.btn_cancel)

        self.render_finished.emit(res)

    def _on_cancel_or_close(self) -> None:
        if self._running:
            self.btn_cancel.setEnabled(False)
            self.btn_cancel.setText(tr("rc.btn_cancelling"))
            self._append_log(tr("rc.cancel_sent"))
            self.runner.cancel()
        else:
            self.accept()

    def _open_output(self) -> None:
        if self._result and self._result.output_path and os.path.exists(self._result.output_path):
            subprocess.Popen(["explorer", "/select,", self._result.output_path])

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            self._on_cancel_or_close()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        if self._running:
            self._on_cancel_or_close()
            event.ignore()
            return
        super().closeEvent(event)
