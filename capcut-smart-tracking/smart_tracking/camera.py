"""Virtual camera: turns an object track into scale/position/rotation curves.

Pipeline (all offline, the whole clip is known in advance):

1. fill lost stretches by interpolation, hold the ends;
2. jitter removal on the raw track (median + Gaussian);
3. zoom target from object size and required free space, optional speed-based
   zoom (Dynamic Zoom), clamped to [min, max] and rate-limited with separate
   zoom-in / zoom-out speeds, starting from the configured start zoom;
4. look-at point: optional dead zone (Cinematic), Gaussian smoothing, then a
   damped spring (reaction speed = stiffness, inertia = mass/damping);
   zero-phase modes run the spring forward and backward so there is no lag;
5. convert look-at + zoom into CapCut position so the object sits at the chosen
   point of the frame, optionally clamped so no black borders appear.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field, fields
from typing import Dict, Optional

import numpy as np

from .capcut_transform import ClipGeometry, clamp_to_fill, position_for
from .tracking import TrackData

MODES = ("tight", "smooth", "cinematic", "dynamic", "custom")
MODE_TITLES = {
    "tight": "Tight Follow",
    "smooth": "Smooth Follow",
    "cinematic": "Cinematic Follow",
    "dynamic": "Dynamic Zoom",
    "custom": "Custom",
}


@dataclass
class CameraSettings:
    mode: str = "smooth"
    start_zoom: float = 1.0       # scale at the first frame of the clip
    min_zoom: float = 1.0
    max_zoom: float = 2.5
    zoom_in_speed: float = 0.8    # max zoom-in rate, doublings per second
    zoom_out_speed: float = 1.2   # max zoom-out rate, doublings per second
    smoothness: float = 0.5       # 0..1 low-pass on the camera target
    inertia: float = 0.4          # 0..1 camera mass (lag + slight overshoot)
    stabilization: float = 0.5    # 0..1 jitter removal on the raw track
    reaction: float = 0.6         # 0..1 how fast the camera catches up
    frame_x: float = 0.5          # where the object sits in frame, 0..1
    frame_y: float = 0.5
    padding: float = 0.8          # free space around object, x object size per side
    dead_zone: float = 0.0        # 0..0.4 fraction of the visible frame
    zero_phase: bool = True       # non-causal smoothing (no lag)
    speed_zoom: float = 0.0       # 0..1 zoom out while the object moves fast
    keep_filled: bool = True      # never reveal canvas borders
    follow_rotation: float = 0.0  # 0..1 counter-rotate with the object
    tilt: float = 0.0             # constant roll, degrees
    keyframe_tolerance: float = 1.0  # max deviation (canvas px) when thinning keyframes

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "CameraSettings":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


_MODE_PRESETS: Dict[str, Dict] = {
    # follows hard, almost no lag, frames the object closely
    "tight": dict(smoothness=0.1, inertia=0.05, stabilization=0.35, reaction=0.95, padding=0.45,
                  dead_zone=0.0, zero_phase=True, speed_zoom=0.0, zoom_in_speed=2.0, zoom_out_speed=3.0),
    # balanced default
    "smooth": dict(smoothness=0.5, inertia=0.4, stabilization=0.5, reaction=0.6, padding=0.8,
                   dead_zone=0.0, zero_phase=True, speed_zoom=0.0, zoom_in_speed=0.8, zoom_out_speed=1.2),
    # camera rests until the object leaves a central zone, then glides; slow zoom
    "cinematic": dict(smoothness=0.85, inertia=0.7, stabilization=0.7, reaction=0.35, padding=1.1,
                      dead_zone=0.18, zero_phase=True, speed_zoom=0.0, zoom_in_speed=0.3, zoom_out_speed=0.6),
    # zoom reacts to motion: punch in when still, pull out on fast movement
    "dynamic": dict(smoothness=0.45, inertia=0.3, stabilization=0.5, reaction=0.7, padding=0.6,
                    dead_zone=0.0, zero_phase=True, speed_zoom=0.8, zoom_in_speed=1.0, zoom_out_speed=2.5),
}


def settings_for_mode(mode: str, base: Optional[CameraSettings] = None) -> CameraSettings:
    s = CameraSettings.from_dict((base or CameraSettings()).to_dict())
    s.mode = mode
    for k, v in _MODE_PRESETS.get(mode, {}).items():
        setattr(s, k, v)
    return s


@dataclass
class CameraPath:
    """Per-frame CapCut clip values; index 0 == TrackData.first_frame."""
    scale: np.ndarray
    pos_x: np.ndarray
    pos_y: np.ndarray
    rotation: np.ndarray
    look_x: np.ndarray            # smoothed look-at point, video px (for overlays)
    look_y: np.ndarray
    lost_fraction: float = 0.0
    warnings: list = field(default_factory=list)


# -- signal helpers ------------------------------------------------------------

def _interp_gaps(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    idx = np.arange(len(values))
    if valid.sum() == 0:
        return values.copy()
    return np.interp(idx, idx[valid], values[valid])


def _gaussian(x: np.ndarray, sigma: float) -> np.ndarray:
    if sigma < 0.3 or len(x) < 3:
        return x.copy()
    r = int(math.ceil(3 * sigma))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    padded = np.pad(x, r, mode="edge")
    return np.convolve(padded, k, mode="valid")


def _median(x: np.ndarray, win: int) -> np.ndarray:
    if win < 3 or len(x) < win:
        return x.copy()
    r = win // 2
    p = np.pad(x, r, mode="edge")
    strided = np.lib.stride_tricks.sliding_window_view(p, win)
    return np.median(strided, axis=1)


def _spring(target: np.ndarray, dt: float, omega: float, zeta: float) -> np.ndarray:
    """Damped spring follower (semi-implicit Euler, sub-stepped for stability)."""
    out = np.empty_like(target)
    x, v = float(target[0]), 0.0
    sub = max(1, int(math.ceil(dt * omega * 4)))
    h = dt / sub
    for i, t in enumerate(target):
        for _ in range(sub):
            a = omega * omega * (t - x) - 2 * zeta * omega * v
            v += a * h
            x += v * h
        out[i] = x
    return out


def _dead_zone(target: np.ndarray, zone: np.ndarray) -> np.ndarray:
    out = np.empty_like(target)
    cam = float(target[0])
    for i, (t, z) in enumerate(zip(target, zone)):
        d = t - cam
        if d > z:
            cam = t - z
        elif d < -z:
            cam = t + z
        out[i] = cam
    return out


def _rate_limit_log(target: np.ndarray, start: float, up: float, down: float, dt: float) -> np.ndarray:
    """Limit zoom change in log2 space: ``up``/``down`` doublings per second."""
    out = np.empty_like(target)
    cur = math.log2(start)
    lt = np.log2(target)
    for i, t in enumerate(lt):
        d = t - cur
        cur += min(d, up * dt) if d > 0 else max(d, -down * dt)
        out[i] = cur
    return np.power(2.0, out)


# -- main ------------------------------------------------------------------------

def compute_camera(track: TrackData, geo: ClipGeometry, s: CameraSettings) -> CameraPath:
    n = track.count
    fps = track.fps
    dt = 1.0 / fps
    valid = track.valid()
    warnings = []
    if valid.sum() == 0:
        raise ValueError("Нет данных трекинга: выделите объект и запустите трекинг")
    lost_fraction = 1.0 - valid.mean()
    if lost_fraction > 0.3:
        warnings.append(f"Объект не найден в {lost_fraction:.0%} кадров — траектория в этих местах интерполирована")

    b = track.boxes.astype(np.float64)
    cx = _interp_gaps(b[:, 0] + b[:, 2] / 2, valid)
    cy = _interp_gaps(b[:, 1] + b[:, 3] / 2, valid)
    bw = _interp_gaps(b[:, 2], valid)
    bh = _interp_gaps(b[:, 3], valid)
    ang = _interp_gaps(track.angle.astype(np.float64), valid)

    # 2. jitter removal: scale windows with fps so behaviour is frame-rate independent
    fs = fps / 30.0
    med = int(1 + 2 * round(s.stabilization * 4 * fs))
    sig_j = s.stabilization * 6.0 * fs
    cx, cy = _gaussian(_median(cx, med), sig_j), _gaussian(_median(cy, med), sig_j)
    # object size is noisier than position and should change slowly
    bw = _gaussian(_median(bw, med + 4), sig_j * 2 + 2 * fs)
    bh = _gaussian(_median(bh, med + 4), sig_j * 2 + 2 * fs)

    # 3. zoom
    fit = geo.fit
    pad = 1 + 2 * s.padding
    z_fit = np.minimum(geo.canvas_w / (fit * bw * pad), geo.canvas_h / (fit * bh * pad))
    if s.speed_zoom > 0:
        speed = np.hypot(np.gradient(cx), np.gradient(cy)) * fps          # video px / s
        speed = _gaussian(speed, 6 * fs)
        visible_w = geo.canvas_w / (fit * z_fit)                         # video px across the view
        rel = speed / np.maximum(visible_w, 1)                            # views per second
        z_fit = z_fit / (1 + 2.5 * s.speed_zoom * rel)
    z_target = np.clip(z_fit, s.min_zoom, s.max_zoom)
    z_target = np.power(2.0, _gaussian(np.log2(z_target), s.smoothness * 12 * fs + 1))
    zoom = _rate_limit_log(z_target, max(s.start_zoom, 0.05), s.zoom_in_speed, s.zoom_out_speed, dt)
    # ease the corners the rate limiter leaves
    zoom = np.power(2.0, _gaussian(np.log2(zoom), 4 * fs))
    zoom = np.maximum(zoom, 0.05)

    # 4. look-at point
    lx, ly = cx.copy(), cy.copy()
    if s.dead_zone > 0:
        vis_w = geo.canvas_w / (fit * zoom)
        vis_h = geo.canvas_h / (fit * zoom)
        lx = _dead_zone(lx, s.dead_zone * vis_w / 2)
        ly = _dead_zone(ly, s.dead_zone * vis_h / 2)
    sig = s.smoothness * 10 * fs
    lx, ly = _gaussian(lx, sig), _gaussian(ly, sig)
    omega = 2.0 + 18.0 * s.reaction
    omega *= 1.0 - 0.6 * s.inertia
    zeta = 1.0 - 0.45 * s.inertia
    lx, ly = _spring(lx, dt, omega, zeta), _spring(ly, dt, omega, zeta)
    if s.zero_phase:
        lx = _spring(lx[::-1], dt, omega, zeta)[::-1]
        ly = _spring(ly[::-1], dt, omega, zeta)[::-1]

    # rotation (extra feature)
    rot = np.full(n, float(s.tilt))
    if s.follow_rotation > 0:
        a = _gaussian(ang - ang[0], s.smoothness * 10 * fs + 2)
        rot -= s.follow_rotation * a

    # 5. to CapCut position
    tgt = (s.frame_x * geo.canvas_w, s.frame_y * geo.canvas_h)
    px = np.empty(n)
    py = np.empty(n)
    for i in range(n):
        x, y = position_for(geo, (lx[i], ly[i]), tgt, zoom[i], rot[i])
        if s.keep_filled:
            x, y = clamp_to_fill(geo, zoom[i], x, y, rot[i])
        px[i], py[i] = x, y
    cover = max(geo.canvas_w / (fit * geo.video_w), geo.canvas_h / (fit * geo.video_h))
    if zoom.min() < cover - 1e-3:
        warnings.append(f"При масштабе меньше {cover:.2f}× видео не закрывает холст целиком "
                        f"(видны поля). Поднимите минимальный и начальный масштаб до {cover:.2f}×")
    return CameraPath(zoom, px, py, rot, lx, ly, float(lost_fraction), warnings)
