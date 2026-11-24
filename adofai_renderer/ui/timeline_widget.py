"""ADOFAI 3-lane NLE Timeline (영상 / 효과 / 오디오) with crisp vector track toggles, BPM ticks, zoom, and In/Out handles."""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap, QWheelEvent
from PySide6.QtWidgets import QWidget

from ..i18n import tr
from .theme import format_timecode, get_palette


class TimelineTrimWidget(QWidget):
    """3-lane NLE timeline: Video filmstrip, ADOFAI chart event markers, BGM waveform, vector track controls, zoom & trim."""

    playhead_changed = Signal(float)
    trim_changed = Signal(float, float, bool)  # (start_sec, end_sec, is_trimmed)
    zoom_changed = Signal(float)
    audio_mute_toggled = Signal(bool)
    view_changed = Signal()  # visible window (start/span/zoom/duration) changed

    GUTTER_W = 114
    RULER_H = 22
    FX_H = 15
    HANDLE_W = 9
    RIGHT_PAD = 8

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(112)
        self.setMaximumHeight(240)
        self.setMouseTracking(True)

        self._duration: float = 60.0
        self._bpm: float = 120.0
        self._playhead: float = 0.0
        self._trim_in: float = 0.0
        self._trim_out: float = 60.0
        self._is_trimmed: bool = False

        self._zoom: float = 1.0
        self._view_start: float = 0.0
        self._show_waveform: bool = True
        self._show_filmstrip: bool = True
        self._show_fx: bool = True
        self._audio_muted: bool = False

        self._waveform: Optional[List[float]] = None
        self._filmstrip: Optional[QPixmap] = None
        self._event_markers: List[Tuple[float, str]] = []

        self._drag_mode: Optional[str] = None
        self._pan_origin_x: float = 0.0
        self._pan_origin_start: float = 0.0
        self._hover_target: Optional[str] = None
        self._zoom_anim: Optional[QVariantAnimation] = None

    @property
    def duration(self) -> float:
        return self._duration

    @property
    def playhead(self) -> float:
        return self._playhead

    @property
    def trim_in(self) -> float:
        return self._trim_in if self._is_trimmed else 0.0

    @property
    def trim_out(self) -> float:
        return self._trim_out if self._is_trimmed else 0.0

    @property
    def is_trimmed(self) -> bool:
        return self._is_trimmed

    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def view_start(self) -> float:
        return self._view_start

    @property
    def visible_span(self) -> float:
        return self._visible_span()

    def set_view_start(self, start_sec: float) -> None:
        span = self._visible_span()
        ns = max(0.0, min(max(0.0, self._duration - span), float(start_sec)))
        if abs(ns - self._view_start) > 1e-6:
            self._view_start = ns
            self.update()
            self.view_changed.emit()

    def set_bpm(self, bpm: float) -> None:
        self._bpm = max(20.0, float(bpm or 120.0))
        self.update()

    def set_event_markers(self, markers: List[Tuple[float, str]]) -> None:
        self._event_markers = list(markers or [])
        self.update()

    def set_audio_muted(self, muted: bool) -> None:
        self._audio_muted = bool(muted)
        self.update()

    def animate_zoom(self, target_zoom: float, center_sec: Optional[float] = None, duration_ms: int = 140) -> None:
        """Smoothly ease timeline zoom level for toolbar buttons & Fit action."""
        target = max(1.0, min(8.0, float(target_zoom)))
        if abs(target - self._zoom) < 0.01:
            self.set_zoom(target, center_sec=center_sec)
            return
        if self._zoom_anim is not None:
            self._zoom_anim.stop()
        anchor = self._playhead if center_sec is None else center_sec
        anim = QVariantAnimation(self)
        anim.setDuration(duration_ms)
        anim.setStartValue(float(self._zoom))
        anim.setEndValue(float(target))
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(lambda v: self.set_zoom(float(v), center_sec=anchor))
        self._zoom_anim = anim
        anim.start()

    def set_zoom(self, zoom: float, center_sec: Optional[float] = None) -> None:
        new_z = max(1.0, min(8.0, float(zoom)))
        if abs(new_z - self._zoom) < 1e-3:
            return
        anchor = self._playhead if center_sec is None else center_sec
        old_span = self._visible_span()
        rel = (anchor - self._view_start) / max(0.001, old_span)
        self._zoom = new_z
        new_span = self._visible_span()
        self._view_start = max(0.0, min(self._duration - new_span, anchor - rel * new_span))
        self.zoom_changed.emit(self._zoom)
        self.view_changed.emit()
        self.update()

    def set_duration(self, duration_sec: float, reset_trim: bool = True) -> None:
        self._duration = max(1.0, float(duration_sec or 60.0))
        self._zoom = 1.0
        self._view_start = 0.0
        if reset_trim:
            self._trim_in = 0.0
            self._trim_out = self._duration
            self._is_trimmed = False
            self._playhead = 0.0
        else:
            self._trim_in = min(self._trim_in, self._duration - 0.5)
            self._trim_out = min(self._trim_out, self._duration) if self._is_trimmed else self._duration
            self._playhead = min(self._playhead, self._duration)
        self.update()
        self.view_changed.emit()

    def set_waveform(self, peaks: Optional[List[float]]) -> None:
        self._waveform = peaks
        self.update()

    def set_filmstrip(self, image_path: str) -> None:
        if image_path:
            pm = QPixmap(image_path)
            self._filmstrip = pm if not pm.isNull() else None
        else:
            self._filmstrip = None
        self.update()

    def set_playhead(self, sec: float, emit: bool = False) -> None:
        clamped = max(0.0, min(self._duration, float(sec)))
        if abs(clamped - self._playhead) > 1e-4:
            self._playhead = clamped
            span = self._visible_span()
            if self._playhead < self._view_start:
                self._view_start = max(0.0, self._playhead)
            elif self._playhead > self._view_start + span:
                self._view_start = min(self._duration - span, self._playhead - span)
            self.view_changed.emit()
            self.update()
            if emit:
                self.playhead_changed.emit(self._playhead)

    def set_trim_range(self, start_sec: float, end_sec: float, emit: bool = True) -> None:
        s = max(0.0, min(self._duration - 0.5, float(start_sec)))
        e = float(end_sec)
        if e <= 0.0 or e >= self._duration - 0.05:
            e = self._duration
        if e <= s + 0.5:
            e = min(self._duration, s + 0.5)
        self._trim_in = s
        self._trim_out = e
        self._is_trimmed = (s > 0.05) or (e < self._duration - 0.05)
        self.update()
        if emit:
            self.trim_changed.emit(self.trim_in, self.trim_out, self._is_trimmed)

    def reset_trim_to_full(self, emit: bool = True) -> None:
        self._trim_in = 0.0
        self._trim_out = self._duration
        self._is_trimmed = False
        self.update()
        if emit:
            self.trim_changed.emit(0.0, 0.0, False)

    def mark_in_at_playhead(self) -> None:
        end = self._trim_out if self._is_trimmed else self._duration
        if self._playhead >= end - 0.5:
            end = self._duration
        self.set_trim_range(self._playhead, end, emit=True)

    def mark_out_at_playhead(self) -> None:
        start = self._trim_in if self._is_trimmed else 0.0
        if self._playhead <= start + 0.5:
            start = 0.0
        self.set_trim_range(start, self._playhead, emit=True)

    def _visible_span(self) -> float:
        return self._duration / max(1.0, self._zoom)

    def _tracks_rect(self) -> QRectF:
        return QRectF(
            self.GUTTER_W,
            self.RULER_H,
            max(20.0, self.width() - self.GUTTER_W - self.RIGHT_PAD),
            max(48.0, self.height() - self.RULER_H - 4),
        )

    def _v1_rect(self) -> QRectF:
        trect = self._tracks_rect()
        rem = trect.height() - self.FX_H - 4
        return QRectF(trect.left(), trect.top() + 1, trect.width(), rem * 0.50)

    def _fx_rect(self) -> QRectF:
        v1 = self._v1_rect()
        return QRectF(v1.left(), v1.bottom() + 2, v1.width(), self.FX_H)

    def _a1_rect(self) -> QRectF:
        fx = self._fx_rect()
        trect = self._tracks_rect()
        return QRectF(fx.left(), fx.bottom() + 2, fx.width(), max(18.0, trect.bottom() - fx.bottom() - 2))

    def _btn_v1_vis(self) -> QRectF:
        v1 = self._v1_rect()
        return QRectF(self.GUTTER_W - 24, v1.center().y() - 8, 18, 16)

    def _btn_fx_vis(self) -> QRectF:
        fx = self._fx_rect()
        return QRectF(self.GUTTER_W - 24, fx.center().y() - 6, 18, 12)

    def _btn_a1_mute(self) -> QRectF:
        a1 = self._a1_rect()
        return QRectF(self.GUTTER_W - 44, a1.center().y() - 8, 18, 16)

    def _btn_a1_wave(self) -> QRectF:
        a1 = self._a1_rect()
        return QRectF(self.GUTTER_W - 24, a1.center().y() - 8, 18, 16)

    def _sec_to_x(self, sec: float) -> float:
        trect = self._tracks_rect()
        ratio = (sec - self._view_start) / max(0.001, self._visible_span())
        return trect.left() + ratio * trect.width()

    def _x_to_sec(self, x: float) -> float:
        trect = self._tracks_rect()
        ratio = (x - trect.left()) / max(1.0, trect.width())
        return max(0.0, min(self._duration, self._view_start + ratio * self._visible_span()))

    def _hit_test(self, x: float, y: float) -> str:
        if x < self.GUTTER_W:
            pt = QPointF(x, y)
            if self._btn_v1_vis().contains(pt):
                return "btn_v1"
            if self._btn_fx_vis().contains(pt):
                return "btn_fx"
            if self._btn_a1_mute().contains(pt):
                return "btn_mute"
            if self._btn_a1_wave().contains(pt):
                return "btn_wave"
            return "gutter"
        x_in = self._sec_to_x(self._trim_in)
        x_out = self._sec_to_x(self._trim_out)
        if y >= self.RULER_H:
            if abs(x - x_in) <= self.HANDLE_W + 2:
                return "in"
            if abs(x - x_out) <= self.HANDLE_W + 2:
                return "out"
        return "playhead"

    def wheelEvent(self, ev: QWheelEvent) -> None:
        delta = ev.angleDelta().y()
        if delta == 0:
            return
        pos_sec = self._x_to_sec(ev.position().x())
        factor = 1.25 if delta > 0 else 0.80
        self.set_zoom(self._zoom * factor, center_sec=pos_sec)

    def mousePressEvent(self, ev: QMouseEvent) -> None:
        pos = ev.position()
        if ev.button() == Qt.MiddleButton or (ev.button() == Qt.RightButton and self._zoom > 1.01):
            self._drag_mode = "pan"
            self._pan_origin_x = pos.x()
            self._pan_origin_start = self._view_start
            return
        if ev.button() != Qt.LeftButton:
            return
        hit = self._hit_test(pos.x(), pos.y())
        if hit == "btn_v1":
            self._show_filmstrip = not self._show_filmstrip
            self.update()
            return
        if hit == "btn_fx":
            self._show_fx = not self._show_fx
            self.update()
            return
        if hit == "btn_mute":
            self._audio_muted = not self._audio_muted
            self.audio_mute_toggled.emit(self._audio_muted)
            self.update()
            return
        if hit == "btn_wave":
            self._show_waveform = not self._show_waveform
            self.update()
            return
        if hit == "gutter":
            return
        self._drag_mode = hit
        self._apply_drag(pos.x())

    def mouseMoveEvent(self, ev: QMouseEvent) -> None:
        pos = ev.position()
        if self._drag_mode == "pan":
            trect = self._tracks_rect()
            dx_sec = -((pos.x() - self._pan_origin_x) / max(1.0, trect.width())) * self._visible_span()
            span = self._visible_span()
            self._view_start = max(0.0, min(self._duration - span, self._pan_origin_start + dx_sec))
            self.view_changed.emit()
            self.update()
            return
        if self._drag_mode:
            self._apply_drag(pos.x())
            return
        hit = self._hit_test(pos.x(), pos.y())
        if hit != self._hover_target:
            self._hover_target = hit
            if hit in ("in", "out"):
                self.setCursor(Qt.SizeHorCursor)
            elif hit in ("playhead", "btn_v1", "btn_fx", "btn_mute", "btn_wave"):
                self.setCursor(Qt.PointingHandCursor)
            else:
                self.unsetCursor()
            self.update()

    def mouseReleaseEvent(self, _ev: QMouseEvent) -> None:
        self._drag_mode = None

    def leaveEvent(self, _ev) -> None:
        self._hover_target = None
        self.unsetCursor()
        self.update()

    def _apply_drag(self, x: float) -> None:
        sec = self._x_to_sec(x)
        if self._drag_mode == "in":
            clamped = max(0.0, min(self._trim_out - 0.5, sec))
            self.set_trim_range(clamped, self._trim_out, emit=True)
            self.set_playhead(clamped, emit=True)
        elif self._drag_mode == "out":
            clamped = max(self._trim_in + 0.5, min(self._duration, sec))
            self.set_trim_range(self._trim_in, clamped, emit=True)
            self.set_playhead(clamped, emit=True)
        else:
            self.set_playhead(sec, emit=True)

    def _draw_icon_toggle(
        self,
        p: QPainter,
        rect: QRectF,
        icon: str,
        active: bool,
        active_bg: str | None = None,
        active_border: str | None = None,
    ) -> None:
        pal = get_palette()
        abg = active_bg or pal.tl_btn_on_bg
        abdr = active_border or pal.tl_btn_on_border
        p.setPen(QPen(QColor(abdr if active else pal.tl_btn_off_border), 1))
        p.setBrush(QColor(abg if active else pal.tl_btn_off_bg))
        if pal.is_light:
            p.drawRoundedRect(rect, 3.0, 3.0)
        else:
            p.drawRect(rect)
        if icon == "mute" and active and pal.is_light:
            fg = QColor(pal.accent_danger)
        else:
            fg = QColor(pal.tl_btn_fg_on if active else pal.tl_btn_fg_off)
        cx, cy = rect.center().x(), rect.center().y()

        if icon == "eye":
            p.setPen(QPen(fg, 1.15))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), 4.8, 2.7)
            p.setBrush(fg)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(cx, cy), 1.5, 1.5)
        elif icon == "bars":
            p.setPen(QPen(fg, 1.25))
            p.drawLine(QPointF(cx - 3.5, cy - 2.5), QPointF(cx - 3.5, cy + 2.5))
            p.drawLine(QPointF(cx, cy - 3.0), QPointF(cx, cy + 3.0))
            p.drawLine(QPointF(cx + 3.5, cy - 2.0), QPointF(cx + 3.5, cy + 2.0))
        elif icon == "mute":
            p.setPen(QPen(fg, 1.2))
            p.setBrush(fg)
            path = QPainterPath()
            path.moveTo(cx - 4.0, cy - 1.5)
            path.lineTo(cx - 2.0, cy - 1.5)
            path.lineTo(cx + 0.5, cy - 3.8)
            path.lineTo(cx + 0.5, cy + 3.8)
            path.lineTo(cx - 2.0, cy + 1.5)
            path.lineTo(cx - 4.0, cy + 1.5)
            path.closeSubpath()
            p.drawPath(path)
            if active:
                p.drawLine(QPointF(cx + 2.2, cy - 2.2), QPointF(cx + 5.2, cy + 2.2))
                p.drawLine(QPointF(cx + 5.2, cy - 2.2), QPointF(cx + 2.2, cy + 2.2))
        elif icon == "wave":
            p.setPen(QPen(fg, 1.2))
            heights = (1.8, 4.0, 2.5, 4.5, 2.0)
            for idx, hh in enumerate(heights):
                wx = cx - 4.4 + idx * 2.2
                p.drawLine(QPointF(wx, cy - hh), QPointF(wx, cy + hh))

    def paintEvent(self, _ev) -> None:
        pal = get_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        trect = self._tracks_rect()
        v1 = self._v1_rect()
        fx = self._fx_rect()
        a1 = self._a1_rect()
        span = self._visible_span()
        v_end = self._view_start + span

        p.fillRect(self.rect(), QColor(pal.tl_bg))

        # Left track header gutter
        p.fillRect(QRectF(0, 0, self.GUTTER_W - 1, self.height()), QColor(pal.tl_gutter_bg))
        p.setPen(QPen(QColor(pal.tl_gutter_border), 1))
        p.drawLine(QPointF(self.GUTTER_W - 1, 0), QPointF(self.GUTTER_W - 1, self.height()))
        p.drawLine(QPointF(0, self.RULER_H), QPointF(self.width(), self.RULER_H))

        # Horizontal lane separators in gutter
        p.setPen(QPen(QColor(pal.tl_gutter_sep), 1))
        p.drawLine(QPointF(0, v1.bottom() + 1), QPointF(self.GUTTER_W - 1, v1.bottom() + 1))
        p.drawLine(QPointF(0, fx.bottom() + 1), QPointF(self.GUTTER_W - 1, fx.bottom() + 1))

        mono8 = QFont("Cascadia Mono", 8)
        if pal.is_light:
            mono8.setBold(True)
        ui8 = QFont("Segoe UI", 8)
        if pal.is_light:
            ui8.setWeight(QFont.DemiBold)
        p.setFont(mono8)
        if pal.is_light:
            # Subtle pastel pill tag for BPM in light mode
            pill = QRectF(6, 3, self.GUTTER_W - 14, self.RULER_H - 6)
            p.setPen(QPen(QColor("#C6DEF6"), 1))
            p.setBrush(QColor("#EAF4FF"))
            p.drawRoundedRect(pill, 4.0, 4.0)
            p.setPen(QColor(pal.tl_bpm_text))
            p.drawText(pill, Qt.AlignCenter, f"BPM {int(round(self._bpm))}")
        else:
            p.setPen(QColor(pal.tl_bpm_text))
            p.drawText(QRectF(8, 0, self.GUTTER_W - 12, self.RULER_H), Qt.AlignLeft | Qt.AlignVCenter, f"BPM {int(round(self._bpm))}")

        # Track 1: Video
        p.setFont(ui8)
        p.setPen(QColor(pal.tl_track_text))
        p.drawText(QRectF(8, v1.top(), 72, v1.height()), Qt.AlignLeft | Qt.AlignVCenter, tr("tl.track_v1"))
        self._draw_icon_toggle(p, self._btn_v1_vis(), "eye", self._show_filmstrip)

        # Track 2: Chart FX events
        fx_font = QFont("Segoe UI", 7)
        if pal.is_light:
            fx_font.setWeight(QFont.DemiBold)
        p.setFont(fx_font)
        p.setPen(QColor(pal.tl_track_sub))
        p.drawText(QRectF(8, fx.top(), 72, fx.height()), Qt.AlignLeft | Qt.AlignVCenter, tr("tl.track_fx"))
        self._draw_icon_toggle(p, self._btn_fx_vis(), "bars", self._show_fx)

        # Track 3: Audio
        p.setFont(ui8)
        p.setPen(QColor(pal.tl_track_text))
        p.drawText(QRectF(8, a1.top(), 58, a1.height()), Qt.AlignLeft | Qt.AlignVCenter, tr("tl.track_a1"))
        self._draw_icon_toggle(p, self._btn_a1_mute(), "mute", self._audio_muted, active_bg=pal.tl_mute_on_bg, active_border=pal.tl_mute_on_border)
        self._draw_icon_toggle(p, self._btn_a1_wave(), "wave", self._show_waveform)

        # Crisp Ruler ticks (major + medium + minor subdivisions aligned to timestamps)
        p.save()
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setClipRect(QRectF(trect.left(), 0, trect.width(), self.RULER_H))
        p.fillRect(QRectF(trect.left(), 0, trect.width(), self.RULER_H), QColor(pal.tl_ruler_bg))
        p.setFont(QFont("Cascadia Mono", 8))
        approx_ticks = max(4, int(trect.width() // 92))
        raw_step = span / approx_ticks
        step = 10.0
        for cand in (0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 30.0, 60.0, 120.0):
            step = cand
            if cand >= raw_step:
                break

        sub_div = 5 if step in (5.0, 10.0, 15.0, 30.0, 60.0, 120.0) else 4
        minor_step = step / float(sub_div)
        first_minor = (int(self._view_start // minor_step)) * minor_step
        mt = first_minor
        guard = 0
        while mt <= v_end + minor_step * 0.5 and guard < 600:
            guard += 1
            mx = self._sec_to_x(mt)
            if mx >= trect.left() - 2 and mx <= trect.right() + 2:
                idx_major = round(mt / step)
                is_major = abs(mt - idx_major * step) < (minor_step * 0.15)
                if is_major:
                    p.setPen(QPen(QColor(pal.tl_ruler_major), 1))
                    p.drawLine(int(round(mx)), self.RULER_H - 9, int(round(mx)), self.RULER_H)
                    p.setPen(QColor(pal.tl_ruler_text))
                    p.drawText(QRectF(mx + 3, 1, 56, self.RULER_H - 9), Qt.AlignLeft | Qt.AlignVCenter, format_timecode(mt))
                else:
                    is_half = (sub_div % 2 == 0) and (abs((mt % step) - step * 0.5) < minor_step * 0.2)
                    if is_half:
                        p.setPen(QPen(QColor(pal.tl_ruler_half), 1))
                        p.drawLine(int(round(mx)), self.RULER_H - 6, int(round(mx)), self.RULER_H)
                    else:
                        p.setPen(QPen(QColor(pal.tl_ruler_minor), 1))
                        p.drawLine(int(round(mx)), self.RULER_H - 4, int(round(mx)), self.RULER_H)
            mt += minor_step
        p.restore()

        # Track wells
        p.fillRect(v1, QColor(pal.tl_well_v1))
        p.fillRect(fx, QColor(pal.tl_well_fx))
        p.fillRect(a1, QColor(pal.tl_well_a1))
        p.setPen(QPen(QColor(pal.tl_well_border), 1))
        p.drawRect(v1)
        p.drawRect(fx)
        p.drawRect(a1)

        p.save()
        p.setClipRect(trect)

        full_x0 = self._sec_to_x(0.0)
        full_x1 = self._sec_to_x(self._duration)
        full_w = max(1.0, full_x1 - full_x0)

        # Video Filmstrip
        if self._show_filmstrip and self._filmstrip is not None and not self._filmstrip.isNull():
            p.drawPixmap(QRectF(full_x0, v1.top() + 1, full_w, v1.height() - 2).toRect(), self._filmstrip)
            p.fillRect(v1, QColor(0, 0, 0, 20 if pal.is_light else 38))
        else:
            p.fillRect(QRectF(full_x0, v1.top() + 3, full_w, v1.height() - 6), QColor(pal.tl_v1_clip_bg))

        # Subtle NLE markers for ADOFAI chart events
        if self._show_fx and self._event_markers:
            p.save()
            p.setRenderHint(QPainter.Antialiasing, False)
            col_map = {
                "filter": QColor(pal.tl_fx_filter),
                "camera": QColor(pal.tl_fx_camera),
                "speed": QColor(pal.tl_fx_speed),
            }
            for ratio, kind in self._event_markers:
                ex = int(round(self._sec_to_x(ratio * self._duration)))
                if ex < trect.left() or ex > trect.right():
                    continue
                p.setPen(QPen(col_map.get(kind, QColor(pal.text_muted)), 1))
                p.drawLine(ex, int(fx.top() + 3), ex, int(fx.bottom() - 3))
            p.restore()

        # Continuous dense 1px studio audio waveform
        p.save()
        p.setRenderHint(QPainter.Antialiasing, False)
        mid_y = int(round(a1.center().y()))
        p.setPen(QPen(QColor(pal.tl_wave_center), 1))
        p.drawLine(int(trect.left()), mid_y, int(trect.right()), mid_y)
        if self._show_waveform and self._waveform:
            n = len(self._waveform)
            max_h = a1.height() * 0.44
            x_in_px = self._sec_to_x(self._trim_in)
            x_out_px = self._sec_to_x(self._trim_out)
            px_start = max(int(trect.left()), int(full_x0))
            px_end = min(int(trect.right()), int(full_x1))
            pen_active = QPen(QColor(pal.tl_wave_active), 1)
            pen_muted = QPen(QColor(pal.tl_wave_muted), 1)
            for px_x in range(px_start, px_end + 1):
                rel0 = (px_x - full_x0) / max(1.0, full_w)
                rel1 = (px_x + 1 - full_x0) / max(1.0, full_w)
                i0 = max(0, min(n - 1, int(rel0 * n)))
                i1 = max(i0 + 1, min(n, int(rel1 * n) + 1))
                amp = max(self._waveform[i0:i1])
                bh = max(1, int(round(amp * max_h)))
                in_sel = (px_x >= x_in_px and px_x <= x_out_px) and not self._audio_muted
                p.setPen(pen_active if in_sel else pen_muted)
                p.drawLine(px_x, mid_y - bh, px_x, mid_y + bh)
        p.restore()

        # Out-of-trim shaded masks
        x_in = self._sec_to_x(self._trim_in)
        x_out = self._sec_to_x(self._trim_out)
        mask_col = QColor(pal.tl_trim_mask)
        mask_col.setAlpha(pal.tl_trim_mask_alpha)
        if x_in > trect.left():
            p.fillRect(QRectF(trect.left(), trect.top(), min(trect.right(), x_in) - trect.left(), trect.height()), mask_col)
        if x_out < trect.right():
            rx0 = max(trect.left(), x_out)
            p.fillRect(QRectF(rx0, trect.top(), trect.right() - rx0, trect.height()), mask_col)

        # Selection box + In/Out handles
        sel_col = QColor(pal.tl_sel_trimmed) if self._is_trimmed else QColor(pal.tl_sel_full)
        p.setPen(QPen(sel_col, 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(x_in, trect.top() + 1, max(2.0, x_out - x_in), trect.height() - 2))

        in_hl = (self._hover_target == "in" or self._drag_mode == "in")
        out_hl = (self._hover_target == "out" or self._drag_mode == "out")

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(pal.tl_handle_hover) if in_hl else sel_col)
        p.drawRect(QRectF(x_in, trect.top() + 1, 6, trect.height() - 2))
        p.setPen(QPen(QColor(pal.tl_handle_grip), 1.2))
        p.drawLine(QPointF(x_in + 3, trect.center().y() - 10), QPointF(x_in + 3, trect.center().y() + 10))

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(pal.tl_handle_hover) if out_hl else sel_col)
        p.drawRect(QRectF(x_out - 6, trect.top() + 1, 6, trect.height() - 2))
        p.setPen(QPen(QColor(pal.tl_handle_grip), 1.2))
        p.drawLine(QPointF(x_out - 3, trect.center().y() - 10), QPointF(x_out - 3, trect.center().y() + 10))

        p.restore()

        # Playhead
        px = self._sec_to_x(self._playhead)
        if px >= trect.left() - 2 and px <= trect.right() + 2:
            p.setPen(QPen(QColor(pal.accent_playhead), 1.6))
            p.drawLine(QPointF(px, self.RULER_H - 2), QPointF(px, trect.bottom()))

            cap = QPainterPath()
            cap.moveTo(px - 5, self.RULER_H - 8)
            cap.lineTo(px + 5, self.RULER_H - 8)
            cap.lineTo(px + 5, self.RULER_H - 2)
            cap.lineTo(px, self.RULER_H + 3)
            cap.lineTo(px - 5, self.RULER_H - 2)
            cap.closeSubpath()
            p.setPen(QPen(QColor("#FFFFFF"), 0.8) if pal.is_light else Qt.NoPen)
            p.setBrush(QColor(pal.accent_playhead))
            p.drawPath(cap)

        p.end()
