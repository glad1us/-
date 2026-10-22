"""Turn per-frame camera curves into a small set of CapCut keyframes.

CapCut interpolates keyframes linearly, so we keep the minimum set of frames
whose piecewise-linear reconstruction stays within a tolerance of the dense
curve on *every* channel at once (multi-channel Ramer-Douglas-Peucker).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import numpy as np

from .camera import CameraPath
from .capcut_transform import ClipGeometry

# property names as stored in CapCut drafts (segment.common_keyframes[].property_type)
KF_POS_X = "KFTypePositionX"
KF_POS_Y = "KFTypePositionY"
KF_SCALE_X = "KFTypeScaleX"   # with uniform_scale.on this drives both axes
KF_ROTATION = "KFTypeRotation"


@dataclass
class KeyframeSet:
    frames: List[int]                     # indices into the camera path
    offsets_us: List[int]                 # time offset from the segment start, microseconds
    values: Dict[str, List[float]]        # property -> values at ``frames``
    dense_count: int

    def as_pairs(self, prop: str):
        return list(zip(self.offsets_us, self.values[prop]))


def _rdp_multi(y: np.ndarray, tol: float) -> List[int]:
    """y: (n, c) curves already in a common unit; returns kept indices."""
    n = len(y)
    if n <= 2:
        return list(range(n))
    keep = np.zeros(n, bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        t = (np.arange(a + 1, b) - a) / (b - a)
        lin = y[a] + (y[b] - y[a]) * t[:, None]
        err = np.abs(y[a + 1:b] - lin).max(axis=1)
        j = int(np.argmax(err))
        if err[j] > tol:
            m = a + 1 + j
            keep[m] = True
            stack.append((a, m))
            stack.append((m, b))
    return [int(i) for i in np.nonzero(keep)[0]]


def build_keyframes(path: CameraPath, geo: ClipGeometry, fps: float, speed: float = 1.0,
                    tolerance_px: float = 1.0, include_rotation: bool = False) -> KeyframeSet:
    """``speed``: segment playback speed; offsets are on the timeline (target) clock."""
    half_w, half_h = geo.canvas_w / 2, geo.canvas_h / 2
    chans = [path.pos_x * half_w, path.pos_y * half_h,
             # a scale change moves the canvas edge by delta * half width
             path.scale * half_w]
    if include_rotation:
        chans.append(np.radians(path.rotation) * half_w)
    y = np.stack(chans, axis=1)
    frames = _rdp_multi(y, max(tolerance_px, 0.05))
    offsets = [int(round(f / fps / speed * 1_000_000)) for f in frames]
    values = {
        KF_POS_X: [float(path.pos_x[f]) for f in frames],
        KF_POS_Y: [float(path.pos_y[f]) for f in frames],
        KF_SCALE_X: [float(path.scale[f]) for f in frames],
    }
    if include_rotation:
        values[KF_ROTATION] = [float(path.rotation[f]) for f in frames]
    return KeyframeSet(frames, offsets, values, len(path.scale))


def needs_rotation(path: CameraPath) -> bool:
    return bool(np.abs(path.rotation).max() > 1e-3) if len(path.rotation) else False


def summary(ks: KeyframeSet) -> str:
    return f"{len(ks.frames)} ключевых кадров (из {ks.dense_count} кадров клипа)"

