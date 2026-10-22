"""Main window of CapCut Smart Tracking."""

from __future__ import annotations

import os
import time
from typing import Optional

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QGridLayout,
                               QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QProgressBar, QPushButton, QScrollArea,
                               QTabBar, QToolButton, QVBoxLayout, QWidget)

from .. import __version__, storage
from ..camera import MODE_TITLES, MODES, CameraSettings, compute_camera, settings_for_mode, _MODE_PRESETS
from ..capcut_draft import (DraftError, DraftProject, EncryptedDraftError, SegmentInfo, capcut_running,
                            find_draft_root, list_backups, list_projects, restore_backup)
from ..capcut_transform import ClipGeometry, render_canvas
from ..keyframes import build_keyframes, needs_rotation, summary
from ..tracking import (STATUS_LOST, STATUS_MANUAL, ObjectTracker, TrackData, TrackerConfig,
                        TrackingCancelled, detect_largest)
from ..video_io import FrameSource, VideoOpenError, resize_to_max
from . import theme
from .icons import icon
from .widgets import PreviewView, SliderRow, TrackStrip

# keys a mode preset controls: touching one of these switches the mode to Custom
_MODE_KEYS = set().union(*(_MODE_PRESETS[m].keys() for m in _MODE_PRESETS))


class TrackWorker(QThread):
    progress = Signal(float)
    done = Signal(object, str)   # TrackData | None, error message

    def __init__(self, video_path, data: TrackData, anchor: int, box, forward, backward, cfg: TrackerConfig):
        super().__init__()
        self.video_path, self.data, self.anchor, self.box = video_path, data, anchor, box
        self.forward, self.backward, self.cfg = forward, backward, cfg
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            src = FrameSource(self.video_path)
            try:
                ObjectTracker(self.cfg).track(src, self.data, self.anchor, self.box, self.forward, self.backward,
                                              progress=self.progress.emit, cancelled=lambda: self._cancel)
            finally:
                src.close()
            self.done.emit(self.data, "")
        except TrackingCancelled:
            self.done.emit(None, "Трекинг остановлен")
        except Exception as e:  # surfaced to the user, never swallowed
            self.done.emit(None, f"Ошибка трекинга: {e}")


def _panel() -> QWidget:
    w = QWidget()
    w.setObjectName("Panel")
    return w


def _label(text, name=None):
    l = QLabel(text)
    if name:
        l.setObjectName(name)
    l.setWordWrap(True)
    return l


class MainWindow(QMainWindow):
    def __init__(self, draft_root: Optional[str] = None):
        super().__init__()
        self.setWindowTitle(f"CapCut Smart Tracking {__version__}")
        self.resize(1440, 860)
        self.draft_root = draft_root or find_draft_root() or ""
        self.project: Optional[DraftProject] = None
        self.segment: Optional[SegmentInfo] = None
        self.source: Optional[FrameSource] = None
        self.track: Optional[TrackData] = None
        self.geo: Optional[ClipGeometry] = None
        self.cam = None
        self.keys = None
        self.pos = 0
        self.pending_box = None
        self.worker: Optional[TrackWorker] = None
        self.settings = settings_for_mode("smooth")
        self._loading_settings = False

        self._build()
        self.recalc_timer = QTimer(self, singleShot=True, interval=120, timeout=self.recompute)
        self.play_timer = QTimer(self, timeout=self._play_tick)
        self._apply_settings_to_ui(self.settings)
        self.refresh_projects()
        self._update_capcut_badge()
        self.badge_timer = QTimer(self, interval=5000, timeout=self._update_capcut_badge)
        self.badge_timer.start()

    # ------------------------------------------------------------------ UI
    def _build(self):
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(8)

        # top bar
        top = QHBoxLayout()
        a, b = QLabel("CapCut"), QLabel("Smart Tracking")
        a.setObjectName("Brand")
        b.setObjectName("BrandAccent")
        top.addWidget(a)
        top.addWidget(b)
        mode_badge = QLabel("автоматизация проекта CapCut")
        mode_badge.setObjectName("Badge")
        mode_badge.setToolTip("CapCut Desktop не поддерживает сторонние плагины. Инструмент работает рядом с CapCut "
                              "и записывает ключевые кадры прямо в файл вашего проекта.")
        top.addWidget(mode_badge)
        top.addStretch(1)
        self.capcut_badge = QLabel("")
        self.capcut_badge.setObjectName("Badge")
        top.addWidget(self.capcut_badge)
        outer.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(8)
        outer.addLayout(body, 1)
        body.addWidget(self._build_left(), 0)
        body.addWidget(self._build_center(), 1)
        body.addWidget(self._build_right(), 0)

        # status bar
        bottom = QHBoxLayout()
        self.status_icon = QLabel()
        self.status_label = _label("", "Dim")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(240)
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.cancel_btn = QPushButton(icon("stop", theme.ERROR, 14), "Стоп")
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(self.cancel_tracking)
        bottom.addWidget(self.status_icon)
        bottom.addWidget(self.status_label, 1)
        bottom.addWidget(self.progress)
        bottom.addWidget(self.cancel_btn)
        outer.addLayout(bottom)

        QShortcut(QKeySequence(Qt.Key_Space), self, self.toggle_play)
        QShortcut(QKeySequence(Qt.Key_Left), self, lambda: self.seek(self.pos - 1))
        QShortcut(QKeySequence(Qt.Key_Right), self, lambda: self.seek(self.pos + 1))
        QShortcut(QKeySequence("T"), self, self.start_tracking)

    def _build_left(self):
        p = _panel()
        p.setFixedWidth(280)
        l = QVBoxLayout(p)
        l.setContentsMargins(10, 10, 10, 10)
        l.addWidget(_label("Проекты CapCut", "Title"))
        row = QHBoxLayout()
        self.root_label = _label("", "Dim")
        row.addWidget(self.root_label, 1)
        for name, tip, fn in (("folder", "Выбрать папку черновиков CapCut", self.choose_root),
                              ("refresh", "Обновить список", self.refresh_projects)):
            b = QToolButton()
            b.setIcon(icon(name))
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        l.addLayout(row)
        self.project_list = QListWidget()
        self.project_list.currentItemChanged.connect(self._project_selected)
        l.addWidget(self.project_list, 3)
        l.addWidget(_label("Видеоклипы на таймлайне", "Section"))
        self.segment_list = QListWidget()
        self.segment_list.currentItemChanged.connect(self._segment_selected)
        l.addWidget(self.segment_list, 2)
        self.info_label = _label("", "Dim")
        l.addWidget(self.info_label)
        return p

    def _build_center(self):
        p = _panel()
        l = QVBoxLayout(p)
        l.setContentsMargins(10, 8, 10, 10)
        l.setSpacing(6)
        tabs_row = QHBoxLayout()
        self.view_tabs = QTabBar()
        self.view_tabs.addTab(icon("select", size=14), "Исходник · выделение")
        self.view_tabs.addTab(icon("camera", size=14), "Результат камеры")
        self.view_tabs.currentChanged.connect(lambda _: self.show_frame())
        tabs_row.addWidget(self.view_tabs)
        tabs_row.addStretch(1)
        self.frame_label = _label("", "Dim")
        tabs_row.addWidget(self.frame_label)
        l.addLayout(tabs_row)

        self.preview = PreviewView()
        self.preview.boxDrawn.connect(self._box_drawn)
        l.addWidget(self.preview, 1)
        self.strip = TrackStrip()
        self.strip.seek.connect(self.seek)
        l.addWidget(self.strip)

        transport = QHBoxLayout()
        transport.setSpacing(2)
        for name, tip, fn in (("start", "В начало клипа", lambda: self.seek(0)),
                              ("prev", "Кадр назад (←)", lambda: self.seek(self.pos - 1))):
            transport.addWidget(self._tool(name, tip, fn))
        self.play_btn = self._tool("play", "Воспроизвести (Пробел)", self.toggle_play)
        transport.addWidget(self.play_btn)
        for name, tip, fn in (("next", "Кадр вперёд (→)", lambda: self.seek(self.pos + 1)),
                              ("end", "В конец клипа", lambda: self.seek(10 ** 9))):
            transport.addWidget(self._tool(name, tip, fn))
        self.time_label = _label("", "Dim")
        transport.addSpacing(8)
        transport.addWidget(self.time_label)
        transport.addStretch(1)
        transport.addWidget(_label("Подсказать:", "Dim"))
        transport.addWidget(self._tool("face", "Найти лицо в кадре", lambda: self.auto_detect("face")))
        transport.addWidget(self._tool("person", "Найти человека в кадре", lambda: self.auto_detect("person")))
        l.addLayout(transport)

        actions = QHBoxLayout()
        self.track_btn = QPushButton(icon("track", "#0d1716"), "  Start Tracking")
        self.track_btn.setObjectName("Primary")
        self.track_btn.setToolTip("Отследить объект по всему клипу от текущего кадра в обе стороны (T)")
        self.track_btn.clicked.connect(self.start_tracking)
        self.retrack_btn = QPushButton(icon("retrack"), "  Исправить отсюда")
        self.retrack_btn.setToolTip("Новая рамка на этом кадре становится опорной точкой; трекинг пересчитывается "
                                    "вперёд до следующей опорной точки")
        self.retrack_btn.clicked.connect(lambda: self.start_tracking(correction=True))
        self.bad_btn = QPushButton(icon("lost", theme.ERROR), "  Ошибка в кадре")
        self.bad_btn.setToolTip("Пометить кадр как ошибочный — камера проинтерполирует движение через него")
        self.bad_btn.clicked.connect(self.mark_bad)
        self.next_lost_btn = QPushButton(icon("warning", theme.WARN), "  К потере")
        self.next_lost_btn.setToolTip("Перейти к следующему участку, где объект потерян")
        self.next_lost_btn.clicked.connect(self.goto_next_lost)
        self.quality = QComboBox()
        self.quality.addItem("Точный (720p)", 720)
        self.quality.addItem("Быстрый (480p)", 480)
        self.quality.addItem("Максимальный (1080p)", 1080)
        self.quality.setToolTip("Разрешение кадров для анализа")
        for w in (self.track_btn, self.retrack_btn, self.bad_btn, self.next_lost_btn):
            actions.addWidget(w)
        actions.addStretch(1)
        actions.addWidget(_label("Анализ:", "Dim"))
        actions.addWidget(self.quality)
        l.addLayout(actions)
        return p

    def _tool(self, name, tip, fn):
        b = QToolButton()
        b.setIcon(icon(name))
        b.setToolTip(tip)
        b.clicked.connect(fn)
        return b

    def _build_right(self):
        outer = _panel()
        outer.setFixedWidth(360)
        ol = QVBoxLayout(outer)
        ol.setContentsMargins(0, 0, 0, 10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.viewport().setObjectName("Inner")
        inner = QWidget()
        inner.setObjectName("Inner")
        l = QVBoxLayout(inner)
        l.setContentsMargins(12, 10, 12, 4)
        l.setSpacing(2)
        scroll.setWidget(inner)
        ol.addWidget(scroll, 1)

        l.addWidget(_label("Виртуальная камера", "Title"))
        grid = QGridLayout()
        grid.setSpacing(4)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons = {}
        for i, m in enumerate(MODES):
            b = QPushButton(MODE_TITLES[m])
            b.setObjectName("Mode")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, mm=m: self.set_mode(mm))
            self.mode_group.addButton(b)
            self.mode_buttons[m] = b
            grid.addWidget(b, i // 3, i % 3)
        l.addLayout(grid)
        self.mode_hint = _label("", "Dim")
        l.addWidget(self.mode_hint)

        prow = QHBoxLayout()
        self.preset_combo = QComboBox()
        self.preset_combo.activated.connect(self._preset_chosen)
        prow.addWidget(self.preset_combo, 1)
        prow.addWidget(self._tool("save", "Сохранить текущие настройки как пресет", self.save_preset))
        prow.addWidget(self._tool("trash", "Удалить пользовательский пресет", self.delete_preset))
        l.addLayout(prow)

        self.rows = {}

        def section(title):
            l.addWidget(_label(title, "Section"))

        def row(key, *a, **kw):
            r = SliderRow(*a, **kw)
            r.valueChanged.connect(lambda v, k=key: self._setting_changed(k, v))
            self.rows[key] = r
            l.addWidget(r)

        section("Масштаб")
        row("start_zoom", "Начальный масштаб", 0.5, 4.0, 0.05, suffix="×", tooltip="Масштаб в первом кадре клипа")
        row("max_zoom", "Максимальный масштаб", 1.0, 8.0, 0.05, suffix="×")
        row("min_zoom", "Минимальный масштаб", 0.5, 4.0, 0.05, suffix="×")
        row("zoom_in_speed", "Скорость приближения", 0.05, 4.0, 0.05, tooltip="Удвоений масштаба в секунду")
        row("zoom_out_speed", "Скорость отдаления", 0.05, 4.0, 0.05, tooltip="Удвоений масштаба в секунду")
        row("padding", "Запас вокруг объекта", 0.0, 3.0, 0.05, suffix="×",
            tooltip="Свободное место с каждой стороны, в размерах объекта")
        row("speed_zoom", "Отдаление на скорости", 0.0, 1.0, 0.01, percent=True, suffix="%",
            tooltip="Dynamic Zoom: камера отъезжает, когда объект движется быстро")
        section("Движение камеры")
        row("smoothness", "Плавность", 0.0, 1.0, 0.01, percent=True, suffix="%")
        row("inertia", "Инерция", 0.0, 1.0, 0.01, percent=True, suffix="%")
        row("stabilization", "Сглаживание дрожания", 0.0, 1.0, 0.01, percent=True, suffix="%")
        row("reaction", "Скорость реакции", 0.0, 1.0, 0.01, percent=True, suffix="%")
        row("dead_zone", "Мёртвая зона", 0.0, 0.4, 0.01, percent=True, suffix="%",
            tooltip="Камера стоит, пока объект внутри центральной зоны")
        self.zero_phase = QCheckBox("Без запаздывания (анализ всего клипа)")
        self.zero_phase.toggled.connect(lambda v: self._setting_changed("zero_phase", v))
        l.addWidget(self.zero_phase)
        section("Кадрирование")
        row("frame_x", "Положение по X", 0.0, 1.0, 0.01, percent=True, suffix="%")
        row("frame_y", "Положение по Y", 0.0, 1.0, 0.01, percent=True, suffix="%")
        self.keep_filled = QCheckBox("Не показывать чёрные края")
        self.keep_filled.toggled.connect(lambda v: self._setting_changed("keep_filled", v))
        l.addWidget(self.keep_filled)
        section("Поворот и наклон")
        row("follow_rotation", "Следовать повороту", 0.0, 1.0, 0.01, percent=True, suffix="%",
            tooltip="Компенсировать вращение объекта в кадре")
        row("tilt", "Наклон кадра", -30.0, 30.0, 0.5, decimals=1, suffix="°")
        section("Ключевые кадры")
        row("keyframe_tolerance", "Точность", 0.2, 6.0, 0.1, decimals=1, suffix=" px",
            tooltip="Допустимое отклонение от расчётной траектории; меньше — больше ключевых кадров")
        self.summary_label = _label("", "Dim")
        l.addWidget(self.summary_label)
        l.addStretch(1)

        btns = QVBoxLayout()
        btns.setContentsMargins(12, 0, 12, 0)
        self.apply_btn = QPushButton(icon("apply", "#0d1716"), "  Применить к клипу в CapCut")
        self.apply_btn.setObjectName("Primary")
        self.apply_btn.setMinimumHeight(34)
        self.apply_btn.clicked.connect(self.apply_to_capcut)
        btns.addWidget(self.apply_btn)
        r2 = QHBoxLayout()
        self.remove_btn = QPushButton(icon("trash", theme.ERROR, 14), " Убрать камеру")
        self.remove_btn.setObjectName("Danger")
        self.remove_btn.clicked.connect(self.remove_keyframes)
        self.restore_btn = QPushButton(icon("undo", size=14), " Откат из бэкапа")
        self.restore_btn.clicked.connect(self.restore)
        r2.addWidget(self.remove_btn)
        r2.addWidget(self.restore_btn)
        btns.addLayout(r2)
        ol.addLayout(btns)
        self._refresh_presets()
        return outer

    # ------------------------------------------------------------ messages
    def info(self, text, level="info"):
        col = {"info": theme.TEXT_DIM, "ok": theme.OK, "warn": theme.WARN, "error": theme.ERROR}[level]
        ic = {"info": "eye", "ok": "apply", "warn": "warning", "error": "lost"}[level]
        self.status_icon.setPixmap(icon(ic, col, 14).pixmap(14, 14))
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {col};")

    def _update_capcut_badge(self):
        r = capcut_running()
        if r is None:
            self.capcut_badge.setText("CapCut: статус неизвестен")
        elif r:
            self.capcut_badge.setText("● CapCut запущен — закройте перед применением")
            self.capcut_badge.setStyleSheet(f"color: {theme.WARN};")
        else:
            self.capcut_badge.setText("● CapCut закрыт")
            self.capcut_badge.setStyleSheet(f"color: {theme.OK};")

    # ------------------------------------------------------------ projects
    def choose_root(self):
        d = QFileDialog.getExistingDirectory(self, "Папка черновиков CapCut (com.lveditor.draft)", self.draft_root)
        if d:
            self.draft_root = d
            self.refresh_projects()

    def refresh_projects(self):
        self.root_label.setText(self.draft_root or "Папка черновиков не найдена — укажите вручную")
        self.root_label.setToolTip(self.draft_root)
        self.project_list.clear()
        for pr in list_projects(self.draft_root):
            it = QListWidgetItem(f"{pr.name}\n{time.strftime('%d.%m.%Y %H:%M', time.localtime(pr.modified))}")
            it.setData(Qt.UserRole, pr.path)
            self.project_list.addItem(it)
        if self.project_list.count() == 0:
            self.info("Проекты не найдены. В CapCut: Настройки → Черновики показывает путь к папке.", "warn")

    def _project_selected(self, item, _prev=None):
        self._close_clip()
        self.segment_list.clear()
        self.project = None
        if item is None:
            return
        try:
            self.project = DraftProject(item.data(Qt.UserRole))
        except EncryptedDraftError as e:
            self.info(str(e), "error")
            self.info_label.setText("Проект зашифрован этой сборкой CapCut — запись невозможна.")
            return
        except (DraftError, OSError, ValueError) as e:
            self.info(f"Не удалось прочитать проект: {e}", "error")
            return
        segs = self.project.video_segments()
        for s in segs:
            it = QListWidgetItem(s.label() + ("" if s.file_exists else "  (файл не найден)"))
            it.setData(Qt.UserRole, s)
            if s.existing_keyframes:
                it.setToolTip("Ключевые кадры: " + ", ".join(s.existing_keyframes))
            self.segment_list.addItem(it)
        w, h = self.project.canvas_size
        self.info_label.setText(f"Холст {w}×{h} · сохранено CapCut {self.project.app_version}\n"
                                f"Видеоклипов: {len(segs)}")
        self.info("Выберите видеоклип." if segs else "В проекте нет видеоклипов.", "info")

    def _close_clip(self):
        self.play_timer.stop()
        if self.source:
            self.source.close()
        self.source = self.track = self.geo = self.cam = self.keys = None
        self.segment = None
        self.pending_box = None
        self.preview.set_frame(None, (1, 1), False)
        self.strip.set_data(0, None)

    def _segment_selected(self, item, _prev=None):
        self._close_clip()
        if item is None or self.project is None:
            return
        seg: SegmentInfo = item.data(Qt.UserRole)
        if not seg.file_exists:
            self.info(f"Исходный файл клипа не найден: {seg.material_path}", "error")
            return
        try:
            self.source = FrameSource(seg.material_path)
        except VideoOpenError as e:
            self.info(str(e), "error")
            return
        self.segment = seg
        fps = self.source.fps
        first = min(self.source.time_to_frame(seg.source_start / 1e6), self.source.frame_count - 1)
        count = max(1, min(self.source.time_to_frame(seg.source_duration / 1e6), self.source.frame_count - first))
        cw, ch = self.project.canvas_size
        self.geo = ClipGeometry(self.source.width, self.source.height, cw, ch, seg.flip_h, seg.flip_v)
        self.track = TrackData(first, count, self.source.width, self.source.height, fps)
        warn = []
        if seg.has_crop:
            warn.append("у клипа есть обрезка (Crop) — расчёт кадра будет неточным, уберите её в CapCut")
        if abs(seg.speed - 1.0) > 1e-3:
            warn.append(f"скорость клипа {seg.speed:.2f}× — время ключевых кадров пересчитано, проверьте результат")
        if seg.material_w and seg.material_h and \
                abs(seg.material_w / seg.material_h - self.source.width / self.source.height) > 0.02:
            warn.append("пропорции файла и клипа в CapCut различаются (поворот видео?)")
        restored = storage.load_session(self.project.path, seg.segment_id)
        if restored and restored[0].count == count and restored[0].first_frame == first:
            self.track, settings, _ = restored
            self._apply_settings_to_ui(settings)
            self.info("Восстановлен сохранённый трекинг этого клипа." + (" Внимание: " + "; ".join(warn) if warn else ""),
                      "warn" if warn else "ok")
        else:
            prefix = ("Внимание: " + "; ".join(warn) + ". ") if warn else ""
            self.info(prefix + "Выделите объект рамкой на кадре и нажмите Start Tracking.",
                      "warn" if warn else "info")
        self.pos = 0
        self.recompute()
        self.view_tabs.setCurrentIndex(0)
        self.show_frame()

    # ------------------------------------------------------------ playback
    def seek(self, i):
        if not self.track:
            return
        self.pos = int(min(max(i, 0), self.track.count - 1))
        self.show_frame()

    def toggle_play(self):
        if not self.track:
            return
        if self.play_timer.isActive():
            self.play_timer.stop()
            self.play_btn.setIcon(icon("play"))
        else:
            if self.pos >= self.track.count - 1:
                self.pos = 0
            self.play_timer.start(int(1000 / self.track.fps))
            self.play_btn.setIcon(icon("pause"))

    def _play_tick(self):
        if self.pos >= self.track.count - 1:
            self.toggle_play()
            return
        self.pos += 1
        self.show_frame()

    def show_frame(self):
        if not (self.source and self.track):
            return
        frame = self.source.read(self.track.first_frame + self.pos)
        if frame is None:
            return
        result = self.view_tabs.currentIndex() == 1
        self.preview.select_enabled = not result and self.worker is None
        if result and self.cam is not None:
            i = self.pos
            out_w = min(1280, max(320, int(self.preview.width() * self.devicePixelRatioF())))
            img = render_canvas(frame, self.geo, self.cam.scale[i], self.cam.pos_x[i], self.cam.pos_y[i],
                                self.cam.rotation[i], out_w)
            self.preview.set_frame(img, (img.shape[1], img.shape[0]), True)
            self.preview.set_overlay(target_point=(self.settings.frame_x, self.settings.frame_y))
        else:
            if result:
                self.preview.message = "Сначала выполните трекинг"
            small, _ = resize_to_max(frame, 1280)
            self.preview.set_frame(small, (self.source.width, self.source.height), False)
            box, color, look = None, theme.OK, None
            st = int(self.track.status[self.pos])
            if self.pending_box is not None and self.pending_box[0] == self.pos:
                box, color = self.pending_box[1], theme.ACCENT
            elif st:
                box = tuple(self.track.boxes[self.pos])
                color = theme.STATUS_COLORS[st]
            if self.cam is not None:
                look = (self.cam.look_x[self.pos], self.cam.look_y[self.pos])
            self.preview.set_overlay(box, color, look)
        self.strip.set_position(self.pos)
        t = self.pos / self.track.fps
        self.time_label.setText(f"{t:6.2f} s  /  {self.track.count / self.track.fps:.2f} s")
        names = {0: "", 1: "отслежен", 2: "ПОТЕРЯН", 3: "опорная точка", 4: "найден снова"}
        st = names[int(self.track.status[self.pos])]
        self.frame_label.setText(f"кадр {self.pos + 1}/{self.track.count}" + (f" · {st}" if st else ""))

    # ------------------------------------------------------------ tracking
    def _box_drawn(self, box):
        if not self.track:
            return
        self.pending_box = (self.pos, box)
        has = self.track.has_track()
        self.info("Рамка задана. " + ("Нажмите «Исправить отсюда», чтобы поправить трек с этого кадра, или Start "
                                      "Tracking, чтобы отследить заново." if has else "Нажмите Start Tracking."), "info")
        self.show_frame()

    def auto_detect(self, kind):
        if not (self.source and self.track):
            return
        frame = self.source.read(self.track.first_frame + self.pos)
        box = detect_largest(frame, kind)
        if box is None:
            self.info("Ничего не найдено — выделите объект рамкой вручную.", "warn")
            return
        self.view_tabs.setCurrentIndex(0)
        self._box_drawn(box)

    def start_tracking(self, correction=False):
        if not (self.source and self.track) or self.worker is not None:
            return
        box = None
        if self.pending_box is not None and self.pending_box[0] == self.pos:
            box = self.pending_box[1]
        elif self.track.status[self.pos] == STATUS_MANUAL:
            box = tuple(float(v) for v in self.track.boxes[self.pos])
        if box is None:
            self.info("Сначала выделите объект рамкой на текущем кадре (вкладка «Исходник»).", "warn")
            self.view_tabs.setCurrentIndex(0)
            return
        if correction and not self.track.has_track():
            correction = False
        data = TrackData(self.track.first_frame, self.track.count, self.track.width, self.track.height, self.track.fps)
        if correction:
            data = TrackData.from_npz_dict(self.track.to_npz_dict())
        cfg = TrackerConfig(analysis_size=int(self.quality.currentData()))
        self.play_timer.stop()
        self.worker = TrackWorker(self.segment.material_path, data, self.track.first_frame + self.pos, box,
                                  True, not correction, cfg)
        self.worker.progress.connect(lambda f: self.progress.setValue(int(f * 1000)))
        self.worker.done.connect(self._tracking_done)
        self.progress.setValue(0)
        self.progress.show()
        self.cancel_btn.show()
        for w in (self.track_btn, self.retrack_btn, self.apply_btn):
            w.setEnabled(False)
        self.info("Анализ движения объекта…", "info")
        self.worker.start()

    def cancel_tracking(self):
        if self.worker:
            self.worker.cancel()

    def _tracking_done(self, data, err):
        self.worker.wait()
        self.worker = None
        self.progress.hide()
        self.cancel_btn.hide()
        for w in (self.track_btn, self.retrack_btn, self.apply_btn):
            w.setEnabled(True)
        if data is None:
            self.info(err, "warn" if "остановлен" in err else "error")
            return
        self.track = data
        self.pending_box = None
        self.recompute()
        storage.save_session(self.project.path, self.segment.segment_id, self.track, self.settings)
        spans = self.track.spans(STATUS_LOST)
        if spans:
            fps = self.track.fps
            txt = ", ".join(f"{a / fps:.1f}–{(b + 1) / fps:.1f} c" for a, b in spans[:4])
            more = f" и ещё {len(spans) - 4}" if len(spans) > 4 else ""
            self.info(f"Объект потерян на {len(spans)} участк(ах): {txt}{more}. Камера проведена через них по "
                      "интерполяции. Чтобы исправить — «К потере», выделите объект и «Исправить отсюда».", "warn")
        else:
            self.info("Трекинг завершён: объект отслежен во всех кадрах. Проверьте вкладку «Результат камеры».", "ok")
        self.view_tabs.setCurrentIndex(1)
        self.show_frame()

    def mark_bad(self):
        if not self.track or not self.track.has_track():
            return
        self.track.mark_lost(self.pos, self.pos)
        self.recompute()
        self.show_frame()

    def goto_next_lost(self):
        if not self.track:
            return
        spans = self.track.spans(STATUS_LOST)
        if not spans:
            self.info("Потерь объекта нет.", "ok")
            return
        nxt = next((a for a, _ in spans if a > self.pos), spans[0][0])
        self.view_tabs.setCurrentIndex(0)
        self.seek(nxt)
        self.info("Объект потерян здесь. Выделите его рамкой и нажмите «Исправить отсюда».", "warn")

    # ------------------------------------------------------------ camera
    def _apply_settings_to_ui(self, s: CameraSettings):
        self._loading_settings = True
        self.settings = CameraSettings.from_dict(s.to_dict())
        for k, r in self.rows.items():
            r.setValue(getattr(s, k), silent=True)
        self.zero_phase.setChecked(s.zero_phase)
        self.keep_filled.setChecked(s.keep_filled)
        self.mode_buttons.get(s.mode, self.mode_buttons["custom"]).setChecked(True)
        self.mode_hint.setText({
            "tight": "Камера держит объект вплотную и почти без задержки.",
            "smooth": "Сбалансированное плавное следование.",
            "cinematic": "Камера стоит, пока объект в центре, затем плавно догоняет. Медленный зум.",
            "dynamic": "Зум зависит от движения: приближение в покое, отъезд при быстром движении.",
            "custom": "Ручная настройка всех параметров.",
        }[s.mode])
        self._loading_settings = False

    def set_mode(self, mode):
        self._apply_settings_to_ui(settings_for_mode(mode, self.settings))
        self.recalc_timer.start()

    def _setting_changed(self, key, value):
        if self._loading_settings:
            return
        setattr(self.settings, key, value)
        if key in _MODE_KEYS and self.settings.mode != "custom":
            self.settings.mode = "custom"
            self.mode_buttons["custom"].setChecked(True)
            self.mode_hint.setText("Ручная настройка всех параметров.")
        self.recalc_timer.start()

    def recompute(self):
        self.cam = self.keys = None
        if self.track is None:
            return
        if self.track.has_track():
            try:
                self.cam = compute_camera(self.track, self.geo, self.settings)
                self.keys = build_keyframes(self.cam, self.geo, self.track.fps, self.segment.speed,
                                            self.settings.keyframe_tolerance, needs_rotation(self.cam))
                txt = summary(self.keys)
                z = self.cam.scale
                txt += f"\nМасштаб {z.min():.2f}× … {z.max():.2f}×"
                if self.cam.warnings:
                    txt += "\n⚠ " + "\n⚠ ".join(self.cam.warnings)
                self.summary_label.setText(txt)
            except ValueError as e:
                self.summary_label.setText(str(e))
        else:
            self.summary_label.setText("Нет трекинга — ключевые кадры не рассчитаны.")
        self.strip.set_data(self.track.count, self.track.status, self.keys.frames if self.keys else None)
        self.show_frame()

    # ------------------------------------------------------------ presets
    def _refresh_presets(self):
        self.preset_combo.clear()
        self.preset_combo.addItem("Пресеты…", None)
        for name, s in storage.builtin_presets().items():
            self.preset_combo.addItem(name, ("builtin", name))
        for name in storage.load_user_presets():
            self.preset_combo.addItem("★ " + name, ("user", name))

    def _preset_chosen(self, idx):
        d = self.preset_combo.itemData(idx)
        if not d:
            return
        kind, name = d
        s = (storage.builtin_presets() if kind == "builtin" else storage.load_user_presets()).get(name)
        if s:
            self._apply_settings_to_ui(s)
            self.recompute()
            self.info(f"Пресет «{name}» применён.", "ok")

    def save_preset(self):
        name, ok = QInputDialog.getText(self, "Сохранить пресет", "Название пресета:")
        if ok and name.strip():
            storage.save_user_preset(name.strip(), self.settings)
            self._refresh_presets()
            self.info(f"Пресет «{name.strip()}» сохранён.", "ok")

    def delete_preset(self):
        d = self.preset_combo.currentData()
        if not d or d[0] != "user":
            self.info("Выберите пользовательский пресет (★) в списке.", "warn")
            return
        storage.delete_user_preset(d[1])
        self._refresh_presets()

    # ------------------------------------------------------------ apply
    def _confirm_capcut_closed(self) -> bool:
        while capcut_running():
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("CapCut запущен")
            box.setText("CapCut сейчас открыт. Открытый проект CapCut держит в памяти и при закрытии может "
                        "перезаписать изменения.\n\nЗакройте CapCut (или как минимум этот проект) и нажмите "
                        "«Проверить снова».")
            retry = box.addButton("Проверить снова", QMessageBox.AcceptRole)
            force = box.addButton("Записать всё равно", QMessageBox.DestructiveRole)
            box.addButton("Отмена", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is retry:
                continue
            return box.clickedButton() is force
        return True

    def _reload_project(self) -> Optional[DraftProject]:
        try:
            d = DraftProject(self.project.path)
        except DraftError as e:
            self.info(str(e), "error")
            return None
        if d.find_segment(d.content, self.segment.segment_id) is None:
            self.info("Клип больше не найден в проекте (его удалили или изменили в CapCut). Обновите список.", "error")
            return None
        return d

    def apply_to_capcut(self):
        if not (self.keys and self.project and self.segment):
            self.info("Сначала выполните трекинг объекта.", "warn")
            return
        if not self._confirm_capcut_closed():
            return
        d = self._reload_project()
        if d is None:
            return
        try:
            res = d.apply_keyframes(self.segment.segment_id, self.keys, storage.backup_root())
        except (DraftError, OSError) as e:
            self.info(f"Не удалось записать проект: {e}", "error")
            return
        self.project = d
        storage.save_session(d.path, self.segment.segment_id, self.track, self.settings)
        props = ", ".join(self.keys.values)
        self.info(f"Готово: {len(self.keys.frames)} ключевых кадров ({props}) записаны в «{d.name}». "
                  f"Откройте проект в CapCut. Бэкап: {res['backup']}", "ok")

    def remove_keyframes(self):
        if not (self.project and self.segment):
            return
        if QMessageBox.question(self, "Убрать камеру", "Удалить ключевые кадры Position/Scale/Rotation с этого клипа "
                                "и сбросить масштаб и позицию?") != QMessageBox.Yes:
            return
        if not self._confirm_capcut_closed():
            return
        d = self._reload_project()
        if d is None:
            return
        res = d.remove_tracking_keyframes(self.segment.segment_id, storage.backup_root())
        self.project = d
        self.info(f"Ключевые кадры камеры удалены. Бэкап: {res['backup']}", "ok")

    def restore(self):
        if not self.project:
            return
        items = list_backups(storage.backup_root(), self.project.name)
        if not items:
            self.info("Для этого проекта бэкапов нет.", "warn")
            return
        names = [os.path.basename(x) for x in items]
        name, ok = QInputDialog.getItem(self, "Откат из бэкапа", "Восстановить состояние проекта на:", names, 0, False)
        if not ok:
            return
        if not self._confirm_capcut_closed():
            return
        restore_backup(items[names.index(name)], self.project.path)
        self.info(f"Проект восстановлен из бэкапа {name}.", "ok")
        cur = self.project_list.currentItem()
        self._project_selected(cur)

    def closeEvent(self, e):
        if self.worker:
            self.worker.cancel()
            self.worker.wait(3000)
        super().closeEvent(e)

