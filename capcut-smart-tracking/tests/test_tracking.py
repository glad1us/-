import numpy as np
import pytest

from smart_tracking.tracking import (STATUS_LOST, STATUS_MANUAL, ObjectTracker, TrackData,
                                     TrackerConfig)
from smart_tracking.video_io import FrameSource
from tests.synth import make_video


def _center_err(box, gt):
    return np.hypot(box[0] + box[2] / 2 - (gt[0] + gt[2] / 2), box[1] + box[3] / 2 - (gt[1] + gt[3] / 2))


@pytest.fixture(scope="module")
def clean_video(tmp_path_factory):
    p = str(tmp_path_factory.mktemp("v") / "phone.mp4")
    return p, make_video(p, n=150)


@pytest.fixture(scope="module")
def occluded_video(tmp_path_factory):
    p = str(tmp_path_factory.mktemp("v") / "phone_hidden.mp4")
    return p, make_video(p, n=150, hide=(60, 80))


@pytest.mark.parametrize("size", [720, 480])
def test_follows_moving_scaling_object(clean_video, size):
    path, gts = clean_video
    src = FrameSource(path)
    data = TrackData(0, src.frame_count, src.width, src.height, src.fps)
    ObjectTracker(TrackerConfig(analysis_size=size)).track(src, data, 0, gts[0][:4])
    errs = np.array([_center_err(data.boxes[i], gts[i]) for i in range(data.count)])
    valid = data.valid()
    assert valid.mean() > 0.95, valid.mean()
    assert np.median(errs) < 6, np.median(errs)
    # CSRT also follows size (the phone grows/shrinks by +-25 %)
    gw = np.array([g[2] for g in gts])
    assert np.corrcoef(data.boxes[:, 2], gw)[0, 1] > 0.8


def test_backward_and_forward_from_middle(clean_video):
    path, gts = clean_video
    src = FrameSource(path)
    data = TrackData(0, src.frame_count, src.width, src.height, src.fps)
    ObjectTracker().track(src, data, 75, gts[75][:4])
    errs = np.array([_center_err(data.boxes[i], gts[i]) for i in range(data.count)])
    assert data.status[75] == STATUS_MANUAL
    assert np.median(errs[:75]) < 6 and np.median(errs[76:]) < 6


def test_loss_is_reported_and_object_reacquired(occluded_video):
    path, gts = occluded_video
    src = FrameSource(path)
    data = TrackData(0, src.frame_count, src.width, src.height, src.fps)
    ObjectTracker().track(src, data, 0, gts[0][:4])
    hidden = data.status[60:81]
    assert (hidden == STATUS_LOST).mean() > 0.7, hidden
    assert len(data.spans(STATUS_LOST)) >= 1
    after = [i for i in range(95, 150)]
    assert data.valid()[after].mean() > 0.9
    errs = np.array([_center_err(data.boxes[i], gts[i]) for i in after])
    assert np.median(errs) < 8


def test_manual_anchor_is_preserved(clean_video):
    path, gts = clean_video
    src = FrameSource(path)
    data = TrackData(0, src.frame_count, src.width, src.height, src.fps)
    tr = ObjectTracker()
    tr.track(src, data, 0, gts[0][:4])
    fixed = (100.0, 100.0, 50.0, 50.0)
    data.boxes[100] = fixed
    data.status[100] = STATUS_MANUAL
    # correction at frame 40 re-tracks forward but must stop before frame 100
    tr.track(src, data, 40, gts[40][:4], backward=False)
    assert tuple(data.boxes[100]) == fixed


def test_cancel(clean_video):
    from smart_tracking.tracking import TrackingCancelled
    path, gts = clean_video
    src = FrameSource(path)
    data = TrackData(0, src.frame_count, src.width, src.height, src.fps)
    with pytest.raises(TrackingCancelled):
        ObjectTracker().track(src, data, 0, gts[0][:4], cancelled=lambda: True)
