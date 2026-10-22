"""CapCut clip-transform model.

CapCut places a video segment on the canvas like this (values as stored in the
draft, see ``capcut_draft``):

* the video is first fitted inside the canvas ("contain"), factor ``fit``;
* it is then scaled by ``scale`` and rotated clockwise by ``rotation`` degrees
  around its own center;
* finally it is moved by ``position``; X is in units of half the canvas width
  (right positive), Y in units of half the canvas height (UP positive).

All camera math in the plugin goes through this module, and the preview is
rendered with the same matrix, so what you see is what gets written.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple

import cv2
import numpy as np


@dataclass
class ClipGeometry:
    video_w: int
    video_h: int
    canvas_w: int
    canvas_h: int
    flip_h: bool = False
    flip_v: bool = False

    @property
    def fit(self) -> float:
        return min(self.canvas_w / self.video_w, self.canvas_h / self.video_h)


def _rot(deg: float) -> np.ndarray:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s], [s, c]])  # clockwise on a y-down screen


def video_to_canvas(geo: ClipGeometry, scale: float, pos_x: float, pos_y: float, rotation: float) -> np.ndarray:
    """2x3 affine matrix mapping source-video pixels to canvas pixels."""
    flip = np.diag([-1.0 if geo.flip_h else 1.0, -1.0 if geo.flip_v else 1.0])
    m = _rot(rotation) @ (scale * geo.fit * flip)
    t = np.array([pos_x * geo.canvas_w / 2, -pos_y * geo.canvas_h / 2])
    c = np.array([geo.canvas_w / 2, geo.canvas_h / 2])
    vc = np.array([geo.video_w / 2, geo.video_h / 2])
    b = c + t - m @ vc
    return np.hstack([m, b[:, None]])


def position_for(geo: ClipGeometry, point: Tuple[float, float], target: Tuple[float, float],
                 scale: float, rotation: float) -> Tuple[float, float]:
    """CapCut position that puts video pixel ``point`` at canvas pixel ``target``."""
    m0 = video_to_canvas(geo, scale, 0.0, 0.0, rotation)
    here = m0[:, :2] @ np.asarray(point, float) + m0[:, 2]
    d = np.asarray(target, float) - here
    return d[0] / (geo.canvas_w / 2), -d[1] / (geo.canvas_h / 2)


def clamp_to_fill(geo: ClipGeometry, scale: float, pos_x: float, pos_y: float, rotation: float = 0.0):
    """Limit the position so the video still covers the whole canvas.

    Exact for rotation 0; with rotation the covered area is approximated by the
    rotated video's inscribed axis-aligned rectangle.
    """
    vw = geo.video_w * geo.fit * scale
    vh = geo.video_h * geo.fit * scale
    if rotation:
        a = abs(math.radians(rotation)) % math.pi
        a = min(a, math.pi - a)
        # largest axis-aligned rectangle with the canvas aspect inside the rotated video
        c, s = math.cos(a), math.sin(a)
        ar = geo.canvas_w / geo.canvas_h
        h_in = min(vw / (ar * c + s), vh / (ar * s + c))
        vw, vh = h_in * ar, h_in
    lim_x = max(0.0, (vw - geo.canvas_w) / 2) / (geo.canvas_w / 2)
    lim_y = max(0.0, (vh - geo.canvas_h) / 2) / (geo.canvas_h / 2)
    return float(np.clip(pos_x, -lim_x, lim_x)), float(np.clip(pos_y, -lim_y, lim_y))


def render_canvas(frame: np.ndarray, geo: ClipGeometry, scale: float, pos_x: float, pos_y: float,
                  rotation: float, out_w: int) -> np.ndarray:
    """Render the frame the way CapCut composes it, at ``out_w`` preview width."""
    k = out_w / geo.canvas_w
    out_h = max(1, int(round(geo.canvas_h * k)))
    m = video_to_canvas(geo, scale, pos_x, pos_y, rotation) * k
    # frame may be a downscaled copy of the source video
    fk = geo.video_w / frame.shape[1]
    m[:, :2] *= fk
    return cv2.warpAffine(frame, m, (out_w, out_h), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
