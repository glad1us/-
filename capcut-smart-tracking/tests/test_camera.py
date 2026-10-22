import math

import numpy as np
import pytest

from smart_tracking.camera import CameraSettings, compute_camera, settings_for_mode
from smart_tracking.capcut_transform import ClipGeometry, clamp_to_fill, position_for, video_to_canvas
from smart_tracking.keyframes import KF_POS_X, KF_SCALE_X, build_keyframes
from smart_tracking.tracking import STATUS_LOST, STATUS_TRACKED, TrackData

GEO = ClipGeometry(1920, 1080, 1920, 1080)


def synthetic_track(n=300, fps=30, fast_part=None, jitter=0.0, lost=None, seed=0):
    rng = np.random.default_rng(seed)
    t = TrackData(0, n, 1920, 1080, fps)
    for i in range(n):
        u = i / n
        cx = 960 + 500 * math.sin(2 * math.pi * u)
        cy = 540 + 200 * math.sin(4 * math.pi * u)
        if fast_part and fast_part[0] <= i <= fast_part[1]:
            cx += 25 * (i - fast_part[0])        # sudden fast move
        if fast_part and i > fast_part[1]:
            cx += 25 * (fast_part[1] - fast_part[0])
        cx += rng.normal(0, jitter)
        cy += rng.normal(0, jitter)
        t.boxes[i] = (cx - 80, cy - 140, 160, 280)
        t.status[i] = STATUS_TRACKED
    if lost:
        t.status[lost[0]:lost[1] + 1] = STATUS_LOST
        t.boxes[lost[0]:lost[1] + 1] = 0
    return t


def obj_canvas_pos(geo, path, i, track):
    b = track.boxes[i]
    m = video_to_canvas(geo, path.scale[i], path.pos_x[i], path.pos_y[i], path.rotation[i])
    return m[:, :2] @ np.array([b[0] + b[2] / 2, b[1] + b[3] / 2]) + m[:, 2]


def path_length(p):
    return float(np.sum(np.hypot(np.diff(p.pos_x), np.diff(p.pos_y))))


# -- transform ---------------------------------------------------------------

def test_identity_transform_maps_video_to_canvas():
    m = video_to_canvas(GEO, 1.0, 0, 0, 0)
    assert np.allclose(m, [[1, 0, 0], [0, 1, 0]])


def test_position_units_follow_capcut_convention():
    # +1.0 X moves by half a canvas width to the right, +1.0 Y moves UP by half a height
    m = video_to_canvas(GEO, 1.0, 1.0, 1.0, 0)
    c = m @ np.array([960, 540, 1])
    assert np.allclose(c, [960 + 960, 540 - 540])


@pytest.mark.parametrize("scale,rot", [(1.0, 0), (2.3, 0), (1.7, 12.5), (3.0, -30)])
def test_position_for_puts_point_on_target(scale, rot):
    geo = ClipGeometry(1280, 720, 1080, 1920)  # portrait canvas, landscape source
    p, tgt = (300.0, 200.0), (540.0, 700.0)
    x, y = position_for(geo, p, tgt, scale, rot)
    m = video_to_canvas(geo, scale, x, y, rot)
    assert np.allclose(m[:, :2] @ p + m[:, 2], tgt)


def test_flip_is_respected():
    geo = ClipGeometry(1920, 1080, 1920, 1080, flip_h=True)
    m = video_to_canvas(geo, 1.0, 0, 0, 0)
    assert np.allclose(m @ np.array([0, 0, 1]), [1920, 0])


def test_clamp_keeps_canvas_covered():
    for scale in (1.0, 1.5, 2.5):
        x, y = clamp_to_fill(GEO, scale, 5.0, -5.0)
        m = video_to_canvas(GEO, scale, x, y, 0)
        tl = m @ np.array([0, 0, 1])
        br = m @ np.array([1920, 1080, 1])
        assert tl[0] <= 1e-6 and tl[1] <= 1e-6 and br[0] >= 1920 - 1e-6 and br[1] >= 1080 - 1e-6


# -- camera ------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["tight", "smooth", "cinematic", "dynamic"])
def test_modes_keep_object_framed(mode):
    tr = synthetic_track()
    s = settings_for_mode(mode)
    s.keep_filled = False
    p = compute_camera(tr, GEO, s)
    errs = [np.linalg.norm(obj_canvas_pos(GEO, p, i, tr) - (960, 540)) for i in range(60, 300)]
    limit = {"tight": 40, "smooth": 120, "cinematic": 420, "dynamic": 150}[mode]
    assert np.median(errs) < limit, (mode, np.median(errs))
    assert p.scale.min() >= s.min_zoom - 1e-6 and p.scale.max() <= s.max_zoom + 1e-6


def test_modes_differ_in_behaviour():
    tr = synthetic_track(jitter=6)
    paths = {m: compute_camera(tr, GEO, settings_for_mode(m)) for m in ("tight", "smooth", "cinematic")}
    # cinematic moves the least, tight the most (it chases the jitter more)
    assert path_length(paths["cinematic"]) < path_length(paths["smooth"]) < path_length(paths["tight"])


def test_dynamic_zoom_pulls_out_on_fast_motion():
    tr = synthetic_track(fast_part=(150, 170))
    still = compute_camera(tr, GEO, settings_for_mode("smooth"))
    dyn = compute_camera(tr, GEO, settings_for_mode("dynamic"))
    rel_dyn = dyn.scale[150:175].mean() / dyn.scale[60:120].mean()
    rel_smooth = still.scale[150:175].mean() / still.scale[60:120].mean()
    assert rel_dyn < rel_smooth - 0.05


def test_zoom_starts_at_start_zoom_and_respects_rate_limits():
    tr = synthetic_track()
    s = settings_for_mode("custom")
    s.start_zoom, s.max_zoom, s.zoom_in_speed, s.smoothness = 1.0, 3.0, 0.5, 0.0
    p = compute_camera(tr, GEO, s)
    assert abs(p.scale[0] - 1.0) < 0.05
    # 0.5 doublings/s -> after 1 s at most ~1.41x (+ easing tolerance)
    assert p.scale[30] < 1.5


def test_stabilization_removes_jitter():
    noisy, clean = synthetic_track(jitter=10), synthetic_track()

    def residual(stab):
        s = settings_for_mode("tight")
        s.stabilization = stab
        a, b = compute_camera(noisy, GEO, s), compute_camera(clean, GEO, s)
        return np.sqrt(np.mean((a.pos_x - b.pos_x) ** 2 + (a.pos_y - b.pos_y) ** 2))

    # the spring already filters some noise; stabilization must remove a clear share of the rest
    assert residual(1.0) < residual(0.0) * 0.7


def test_lost_frames_are_interpolated_and_reported():
    tr = synthetic_track(lost=(100, 220))
    p = compute_camera(tr, GEO, settings_for_mode("smooth"))
    assert np.isfinite(p.pos_x).all() and np.isfinite(p.scale).all()
    assert p.lost_fraction > 0.3 and p.warnings


def test_object_placement():
    tr = synthetic_track()
    s = settings_for_mode("tight")
    s.frame_x, s.frame_y, s.keep_filled = 0.33, 0.33, False
    p = compute_camera(tr, GEO, s)
    pos = np.array([obj_canvas_pos(GEO, p, i, tr) for i in range(60, 300)])
    assert np.allclose(np.median(pos, axis=0), (0.33 * 1920, 0.33 * 1080), atol=40)


def test_follow_rotation_counter_rotates():
    tr = synthetic_track()
    tr.angle[:] = np.linspace(0, 30, tr.count)
    s = settings_for_mode("smooth")
    s.follow_rotation = 1.0
    p = compute_camera(tr, GEO, s)
    assert p.rotation[-1] < -20


def test_settings_roundtrip():
    s = settings_for_mode("cinematic")
    assert CameraSettings.from_dict(s.to_dict()) == s


# -- keyframes ---------------------------------------------------------------

def test_keyframe_reduction_within_tolerance():
    tr = synthetic_track()
    p = compute_camera(tr, GEO, settings_for_mode("smooth"))
    ks = build_keyframes(p, GEO, tr.fps, tolerance_px=1.0)
    assert 2 < len(ks.frames) < tr.count / 3
    rec = np.interp(np.arange(tr.count), ks.frames, ks.values[KF_POS_X])
    assert np.abs(rec - p.pos_x).max() * 960 <= 1.0 + 1e-6
    rec_s = np.interp(np.arange(tr.count), ks.frames, ks.values[KF_SCALE_X])
    assert np.abs(rec_s - p.scale).max() * 960 <= 1.0 + 1e-6
    assert ks.offsets_us[0] == 0
    assert ks.offsets_us[-1] == round((tr.count - 1) / tr.fps * 1e6)


def test_keyframe_offsets_follow_segment_speed():
    tr = synthetic_track()
    p = compute_camera(tr, GEO, settings_for_mode("smooth"))
    ks = build_keyframes(p, GEO, tr.fps, speed=2.0)
    assert ks.offsets_us[-1] == round((tr.count - 1) / tr.fps / 2.0 * 1e6)
