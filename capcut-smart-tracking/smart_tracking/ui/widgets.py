"""Custom widgets: preview with box selection, tracking timeline strip, slider rows."""

from __future__ import annotations

from typing import List, Optional, Tuple

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QSlider, QWidget

from . import theme


def bgr_to_qimage(frame: np.ndarray) -> QImage:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()


class PreviewView(QWidget):
    """Shows either the source frame (with selection/overlays) or the camera result."""

    boxDrawn = Signal(tuple)  # (x, y, w, h) in source-video pixels

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(480, 270)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.image: Optional[QImage] = None
        self.source_size: Tuple[int, int] = (1, 1)   # video px the image represents
        self.box = None
        self.box_color = theme.OK
        self.look_at = None
        self.result_mode = False
        self.target_point = None   # normalized, result mode
        self.select_enabled = True
        self.message = "Откройте проект CapCut и выберите видеоклип"
        self._drag_start: Optional[QPointF] = None
        self._drag_now: Optional[QPointF] = None

    # -- data -------------------------------------------------------------
    def set_frame(self, frame: Optional[np.ndarray], source_size: Tuple[int, int], result_mode: bool):
        self.image = bgr_to_qimage(frame) if frame is not None else None
        self.source_size = source_size
        self.result_mode = result_mode
        self.update()

    def set_overlay(self, box=None, color=theme.OK, look_at=None, target_point=None):
        self.box, self.box_color, self.look_at, self.target_point = box, color, look_at, target_point
        self.update()

    # -- geometry ---------------------------------------------------------
    def _display_rect(self) -> QRectF:
        if self.image is None:
            return QRectF()
        iw, ih = self.image.width(), self.image.height()
        k = min(self.width() / iw, self.height() / ih)
        w, h = iw * k, ih * k
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _to_widget(self, x, y) -> QPointF:
        r = self._display_rect()
        return QPointF(r.x() + x / self.source_size[0] * r.width(), r.y() + y / self.source_size[1] * r.height())

    def _to_source(self, p: QPointF):
        r = self._display_rect()
        x = (p.x() - r.x()) / r.width() * self.source_size[0]
        y = (p.y() - r.y()) / r.height() * self.source_size[1]
        return min(max(x, 0), self.source_size[0]), min(max(y, 0), self.source_size[1])

    # -- painting ---------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#121316"))
        if self.image is None:
            p.setPen(QColor(theme.TEXT_DIM))
            p.drawText(self.rect(), Qt.AlignCenter, self.message)
            return
        r = self._display_rect()
        p.drawImage(r, self.image)
        if self.result_mode:
            p.setPen(QPen(QColor(255, 255, 255, 40), 1))
            for k in (1 / 3, 2 / 3):
                p.drawLine(QPointF(r.x() + r.width() * k, r.y()), QPointF(r.x() + r.width() * k, r.bottom()))
                p.drawLine(QPointF(r.x(), r.y() + r.height() * k), QPointF(r.right(), r.y() + r.height() * k))
            if self.target_point:
                c = QPointF(r.x() + r.width() * self.target_point[0], r.y() + r.height() * self.target_point[1])
                p.setPen(QPen(QColor(theme.ACCENT), 1.5))
                p.drawLine(c + QPointF(-8, 0), c + QPointF(8, 0))
                p.drawLine(c + QPointF(0, -8), c + QPointF(0, 8))
            p.setPen(QPen(QColor(theme.BORDER), 1))
            p.drawRect(r)
            return
        if self.box is not None and self._drag_start is None:
            x, y, w, h = self.box
            a, b = self._to_widget(x, y), self._to_widget(x + w, y + h)
            p.setPen(QPen(QColor(self.box_color), 2))
            p.drawRect(QRectF(a, b))
            p.setPen(QPen(QColor(self.box_color), 1))
            for cx, cy in ((a.x(), a.y()), (b.x(), a.y()), (a.x(), b.y()), (b.x(), b.y())):
                p.fillRect(QRectF(cx - 3, cy - 3, 6, 6), QColor(self.box_color))
        if self.look_at is not None:
            c = self._to_widget(*self.look_at)
            p.setPen(QPen(QColor(theme.ACCENT), 1.5))
            p.drawEllipse(c, 5, 5)
        if self._drag_start is not None and self._drag_now is not None:
            p.setPen(QPen(QColor(theme.ACCENT), 1.5, Qt.DashLine))
            p.fillRect(QRectF(self._drag_start, self._drag_now).normalized(), QColor(47, 211, 196, 40))
            p.drawRect(QRectF(self._drag_start, self._drag_now).normalized())

    # -- selection --------------------------------------------------------
    def mousePressEvent(self, e):
        if self.result_mode or not self.select_enabled or self.image is None or e.button() != Qt.LeftButton:
            return
        if self._display_rect().contains(e.position()):
            self._drag_start = self._drag_now = e.position()

    def mouseMoveEvent(self, e):
        if self._drag_start is not None:
            self._drag_now = e.position()
            self.update()

    def mouseReleaseEvent(self, e):
        if self._drag_start is None:
            return
        x0, y0 = self._to_source(self._drag_start)
        x1, y1 = self._to_source(e.position())
        self._drag_start = self._drag_now = None
        x, y, w, h = min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)
        if w >= 8 and h >= 8:
            self.boxDrawn.emit((x, y, w, h))
        self.update()


class TrackStrip(QWidget):
    """Timeline: tracking status per frame, manual anchors, keyframes, playhead."""

    seek = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(34)
        self.setCursor(Qt.PointingHandCursor)
        self.status: Optional[np.ndarray] = None
        self.count = 0
        self.position = 0
        self.keyframes: List[int] = []

    def set_data(self, count: int, status: Optional[np.ndarray], keyframes: Optional[List[int]] = None):
        self.count, self.status = count, status
        self.keyframes = keyframes or []
        self.update()

    def set_position(self, i: int):
        self.position = i
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(theme.BG))
        if self.count <= 0:
            return
        bar = QRectF(0, 6, w, 14)
        p.fillRect(bar, QColor(theme.STATUS_COLORS[0]))
        if self.status is not None and len(self.status):
            n = len(self.status)
            start = 0
            for i in range(1, n + 1):
                if i == n or self.status[i] != self.status[start]:
                    s = int(self.status[start])
                    if s:
                        col = QColor(theme.STATUS_COLORS[1 if s == 3 else s])
                        col.setAlpha(200)
                        p.fillRect(QRectF(start / n * w, bar.y(), (i - start) / n * w + 0.5, bar.height()), col)
                    start = i
            p.setPen(QPen(QColor(theme.WARN), 2))
            for i in np.nonzero(self.status == 3)[0]:
                x = (i + 0.5) / n * w
                p.drawLine(QPointF(x, bar.y() - 3), QPointF(x, bar.bottom() + 1))
        p.setPen(QPen(QColor(theme.TEXT_DIM), 1))
        for f in self.keyframes:
            x = (f + 0.5) / self.count * w
            p.drawLine(QPointF(x, 24), QPointF(x, 30))
        x = (self.position + 0.5) / self.count * w
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.drawLine(QPointF(x, 0), QPointF(x, h))

    def _emit(self, e):
        if self.count:
            self.seek.emit(int(min(max(e.position().x() / self.width(), 0), 0.9999) * self.count))

    def mousePressEvent(self, e):
        self._emit(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._emit(e)


class SliderRow(QWidget):
    """Label + slider + numeric field mapped to a float range."""

    valueChanged = Signal(float)

    def __init__(self, label: str, lo: float, hi: float, step: float, decimals: int = 2,
                 suffix: str = "", tooltip: str = "", percent: bool = False, parent=None):
        super().__init__(parent)
        self.lo, self.hi, self.step, self.percent = lo, hi, step, percent
        self.decimals = 0 if percent else decimals
        self.suffix = suffix
        self._value = lo
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 1, 0, 1)
        lay.setSpacing(8)
        self.label = QLabel(label)
        self.label.setFixedWidth(150)
        self.label.setToolTip(tooltip)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, int(round((hi - lo) / step)))
        self.slider.setToolTip(tooltip)
        self.field = QLineEdit()
        self.field.setFixedWidth(58)
        self.field.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.label)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.field)
        self.slider.valueChanged.connect(self._from_slider)
        self.field.editingFinished.connect(self._from_field)
        self._show()

    def _k(self):
        return 100 if self.percent else 1

    def _show(self):
        self.field.setText(f"{self._value * self._k():.{self.decimals}f}{self.suffix}")

    def value(self) -> float:
        return self._value

    def setValue(self, v: float, silent: bool = False):
        self._value = min(max(float(v), self.lo), self.hi)
        self.slider.blockSignals(True)
        self.slider.setValue(int(round((self._value - self.lo) / self.step)))
        self.slider.blockSignals(False)
        self._show()
        if not silent:
            self.valueChanged.emit(self._value)

    def _from_slider(self, i):
        self._value = round(self.lo + i * self.step, 6)
        self._show()
        self.valueChanged.emit(self._value)

    def _from_field(self):
        txt = self.field.text().replace(self.suffix.strip(), "").replace(",", ".").strip()
        try:
            v = float(txt) / self._k()
        except ValueError:
            self._show()
            return
        if abs(v - self._value) > 1e-9:
            self.setValue(v)
        else:
            self._show()
