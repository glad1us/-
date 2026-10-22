"""End-to-end UI run (offscreen): project -> clip -> box -> tracking -> modes -> apply to draft."""

import json
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pycapcut = pytest.importorskip("pycapcut")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.synth import make_video  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SMART_TRACKING_HOME", str(tmp_path / "home"))
    video = str(tmp_path / "media" / "phone.mp4")
    gts = make_video(video, n=150, w=960, h=540, hide=(95, 110))
    root = tmp_path / "drafts"
    root.mkdir()
    script = pycapcut.DraftFolder(str(root)).create_draft("Phone review", 1080, 1920, allow_replace=True)
    script.add_track(pycapcut.TrackType.video)
    script.add_segment(pycapcut.VideoSegment(video, pycapcut.trange("0s", "5s")))
    script.save()
    return str(root), gts


def _wait(app, cond, timeout=120):
    t0 = time.time()
    while not cond():
        app.processEvents()
        time.sleep(0.01)
        assert time.time() - t0 < timeout, "timeout"


def test_full_workflow(env, tmp_path):
    from smart_tracking.ui.main_window import MainWindow
    from smart_tracking.ui.theme import QSS

    root, gts = env
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    w = MainWindow(root)
    w.resize(1440, 860)
    w.show()
    app.processEvents()

    assert w.project_list.count() == 1
    w.project_list.setCurrentRow(0)
    assert w.segment_list.count() == 1
    w.segment_list.setCurrentRow(0)
    assert w.track is not None and w.track.count == 150

    w._box_drawn(gts[0][:4])
    w.start_tracking()
    _wait(app, lambda: w.worker is None)
    assert w.track.valid().mean() > 0.8
    assert w.track.spans(2), "occlusion should be reported as lost"
    assert "потерян" in w.status_label.text()
    assert w.keys is not None and len(w.keys.frames) > 2

    shots = os.environ.get("SMART_TRACKING_SHOTS")
    for mode in ("tight", "cinematic", "dynamic", "smooth"):
        w.set_mode(mode)
        w.recompute()
        assert w.cam is not None
    w.seek(60)
    w.view_tabs.setCurrentIndex(1)
    app.processEvents()
    if shots:
        os.makedirs(shots, exist_ok=True)
        w.grab().save(os.path.join(shots, "result_view.png"))
    w.view_tabs.setCurrentIndex(0)
    app.processEvents()
    if shots:
        w.grab().save(os.path.join(shots, "source_view.png"))

    # a slider edit switches to Custom
    w.rows["inertia"].setValue(0.9)
    assert w.settings.mode == "custom"

    # correction workflow: jump to the loss, re-select, retrack forward
    w.goto_next_lost()
    i = w.pos
    w._box_drawn(gts[i][:4] if gts[i][4] else gts[111][:4])
    w.start_tracking(correction=True)
    _wait(app, lambda: w.worker is None)
    assert w.track.status[i] == 3

    w.apply_to_capcut()
    assert "Готово" in w.status_label.text(), w.status_label.text()
    with open(w.project.timeline_file, encoding="utf-8") as f:
        content = json.load(f)
    seg = content["tracks"][0]["segments"][0]
    props = {k["property_type"] for k in seg["common_keyframes"]}
    assert {"KFTypePositionX", "KFTypePositionY", "KFTypeScaleX"} <= props

    # reopening the clip restores the saved session without re-tracking
    w.segment_list.setCurrentRow(-1)
    w.segment_list.setCurrentRow(0)
    assert w.track.has_track() and w.settings.inertia == pytest.approx(0.9)
    w.close()
