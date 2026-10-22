"""Object tracking for an arbitrary user-selected region.

The core tracker is OpenCV CSRT (discriminative correlation filter with channel
and spatial reliability): it follows any textured region, not only faces, and
adapts to scale changes.  On top of it we add:

* appearance check (normalized cross-correlation against the reference patch)
  to notice when the tracker silently drifts onto background;
* automatic re-acquisition by multi-scale template search around the last
  confident position;
* in-plane rotation estimate from sparse optical flow inside the box
  (used by the optional "follow rotation" camera feature);
* manual anchors: a box drawn by the user is never overwritten by tracking.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

from .video_io import FrameSource, resize_to_max

STATUS_NONE = 0
STATUS_TRACKED = 1
STATUS_LOST = 2
STATUS_MANUAL = 3
STATUS_REACQUIRED = 4

VALID_STATUSES = (STATUS_TRACKED, STATUS_MANUAL, STATUS_REACQUIRED)

Box = Tuple[float, float, float, float]  # x, y, w, h in source-video pixels


class TrackingCancelled(Exception):
    pass


@dataclass
class TrackerConfig:
    algorithm: str = "csrt"          # csrt (default); kcf/mil only as fallbacks
    analysis_size: int = 720         # longest side of frames fed to the tracker
    lost_threshold: float = 0.30     # appearance score below this counts as a miss
    lost_patience: int = 4           # consecutive misses before declaring "lost"
    reacquire_threshold: float = 0.60
    max_area_change: float = 4.0     # box area vs reference: beyond this is a miss
    estimate_rotation: bool = True

    def to_dict(self):
        return asdict(self)


class TrackData:
    """Per-frame track for one clip.  Index 0 == absolute frame ``first_frame``."""

    def __init__(self, first_frame: int, count: int, width: int, height: int, fps: float):
        self.first_frame = int(first_frame)
        self.width, self.height, self.fps = int(width), int(height), float(fps)
        self.boxes = np.zeros((count, 4), np.float32)
        self.status = np.zeros(count, np.int8)
        self.score = np.zeros(count, np.float32)
        self.angle = np.zeros(count, np.float32)

    @property
    def count(self) -> int:
        return len(self.status)

    @property
    def last_frame(self) -> int:
        return self.first_frame + self.count - 1

    def index(self, abs_frame: int) -> int:
        return abs_frame - self.first_frame

    def valid(self) -> np.ndarray:
        return np.isin(self.status, VALID_STATUSES)

    def has_track(self) -> bool:
        return bool(self.valid().any())

    def spans(self, status: int) -> List[Tuple[int, int]]:
        """Contiguous index spans (inclusive) having the given status."""
        out, start = [], None
        for i, s in enumerate(self.status):
            if s == status and start is None:
                start = i
            elif s != status and start is not None:
                out.append((start, i - 1))
                start = None
        if start is not None:
            out.append((start, self.count - 1))
        return out

    def manual_frames(self) -> List[int]:
        return [int(i) for i in np.nonzero(self.status == STATUS_MANUAL)[0]]

    def mark_lost(self, i0: int, i1: int):
        """User marks a bad stretch; the camera interpolates through it."""
        self.status[i0:i1 + 1] = STATUS_LOST

    def to_npz_dict(self):
        return dict(first_frame=self.first_frame, width=self.width, height=self.height, fps=self.fps,
                    boxes=self.boxes, status=self.status, score=self.score, angle=self.angle)

    @classmethod
    def from_npz_dict(cls, d) -> "TrackData":
        t = cls(int(d["first_frame"]), len(d["status"]), int(d["width"]), int(d["height"]), float(d["fps"]))
        t.boxes[:] = d["boxes"]
        t.status[:] = d["status"]
        t.score[:] = d["score"]
        t.angle[:] = d["angle"]
        return t


def create_cv_tracker(algorithm: str):
    """Instantiate an OpenCV tracker, falling back if contrib modules are absent."""
    names = {"csrt": ["TrackerCSRT"], "kcf": ["TrackerKCF"], "mil": ["TrackerMIL"]}
    order = [algorithm] + [a for a in ("csrt", "kcf", "mil") if a != algorithm]
    for algo in order:
        for name in names.get(algo, []):
            for holder in (cv2, getattr(cv2, "legacy", None)):
                if holder is None:
                    continue
                cls = getattr(holder, name, None)
                if cls is not None and hasattr(cls, "create"):
                    return cls.create(), algo
                fn = getattr(holder, name + "_create", None)
                if fn is not None:
                    return fn(), algo
    raise RuntimeError("В OpenCV нет ни одного трекера (установите opencv-contrib-python)")


def _clip_box(b, w, h):
    x, y, bw, bh = b
    x0, y0 = max(0, int(round(x))), max(0, int(round(y)))
    x1, y1 = min(w, int(round(x + bw))), min(h, int(round(y + bh)))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return x0, y0, x1 - x0, y1 - y0


_SIG = (32, 32)


def _signature(gray, box):
    cb = _clip_box(box, gray.shape[1], gray.shape[0])
    if cb is None:
        return None
    x, y, w, h = cb
    return cv2.resize(gray[y:y + h, x:x + w], _SIG, interpolation=cv2.INTER_AREA).astype(np.float32)


def _ncc(a, b) -> float:
    if a is None or b is None:
        return 0.0
    return float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)[0, 0])


def _rotation_step(prev_gray, gray, prev_box) -> Optional[float]:
    """Degrees (clockwise on screen) the content inside ``prev_box`` rotated."""
    cb = _clip_box(prev_box, gray.shape[1], gray.shape[0])
    if cb is None:
        return None
    x, y, w, h = cb
    mask = np.zeros_like(prev_gray)
    mask[y:y + h, x:x + w] = 255
    pts = cv2.goodFeaturesToTrack(prev_gray, 80, 0.01, 4, mask=mask)
    if pts is None or len(pts) < 8:
        return None
    nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev_gray, gray, pts, None, winSize=(21, 21), maxLevel=3)
    good = st.reshape(-1) == 1
    if good.sum() < 8:
        return None
    m, inliers = cv2.estimateAffinePartial2D(pts[good], nxt[good], method=cv2.RANSAC, ransacReprojThreshold=2.0)
    if m is None or inliers is None or inliers.sum() < 6:
        return None
    return math.degrees(math.atan2(m[1, 0], m[0, 0]))


class ObjectTracker:
    def __init__(self, config: Optional[TrackerConfig] = None):
        self.cfg = config or TrackerConfig()
        self.used_algorithm = None

    # -- public -----------------------------------------------------------
    def track(self, source: FrameSource, data: TrackData, anchor_frame: int, box: Box,
              forward: bool = True, backward: bool = True,
              progress: Optional[Callable[[float], None]] = None,
              cancelled: Optional[Callable[[], bool]] = None):
        """Track from a user box at ``anchor_frame`` (absolute index).

        Writes results into ``data`` in place.  Stops at the next manual anchor
        in each direction so user corrections are preserved.
        """
        ai = data.index(anchor_frame)
        if not 0 <= ai < data.count:
            raise ValueError("Кадр выделения вне диапазона клипа")
        data.boxes[ai] = box
        data.status[ai] = STATUS_MANUAL
        data.score[ai] = 1.0
        total = (data.last_frame - anchor_frame if forward else 0) + (anchor_frame - data.first_frame if backward else 0)
        total = max(total, 1)
        done = [0]

        def tick():
            done[0] += 1
            if progress and done[0] % 5 == 0:
                progress(min(1.0, done[0] / total))
            if cancelled and cancelled():
                raise TrackingCancelled()

        if forward and anchor_frame < data.last_frame:
            self._pass(source, data, anchor_frame, box, data.last_frame, +1, tick)
        if backward and anchor_frame > data.first_frame:
            self._pass(source, data, anchor_frame, box, data.first_frame, -1, tick)
        if progress:
            progress(1.0)

    # -- internals --------------------------------------------------------
    def _pass(self, source, data, anchor, box, end, step, tick):
        cfg = self.cfg
        tracker = None
        k = 1.0
        base_sig = adapt_sig = None
        base_patch = None
        ref_area = box[2] * box[3]
        last_good = np.array(box, np.float32)
        prev_gray = prev_box_small = None
        misses = 0
        lost_since = None
        pending: List[int] = []   # indices of tentative misses before "lost" is declared
        angle = float(data.angle[data.index(anchor)])

        for idx, frame in source.iter_range(anchor, end, step):
            small, k = resize_to_max(frame, cfg.analysis_size)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            i = data.index(idx)

            if idx == anchor:
                sb = tuple(float(v) * k for v in box)
                tracker, self.used_algorithm = create_cv_tracker(cfg.algorithm)
                tracker.init(small, tuple(int(round(v)) for v in sb))
                base_sig = adapt_sig = _signature(gray, sb)
                cb = _clip_box(sb, gray.shape[1], gray.shape[0])
                base_patch = gray[cb[1]:cb[1] + cb[3], cb[0]:cb[0] + cb[2]].copy() if cb else None
                prev_gray, prev_box_small = gray, sb
                continue

            tick()
            if data.status[i] == STATUS_MANUAL:
                break  # respect a later user correction

            status, out_box, score = STATUS_LOST, last_good.copy(), 0.0
            if lost_since is None:
                ok, sb = tracker.update(small)
                sb = tuple(float(v) for v in sb)
                sig = _signature(gray, sb) if ok else None
                score = max(_ncc(sig, base_sig), _ncc(sig, adapt_sig)) if sig is not None else 0.0
                area_ratio = (sb[2] * sb[3]) / max(ref_area * k * k, 1e-6)
                sane = ok and sig is not None and 1 / cfg.max_area_change < area_ratio < cfg.max_area_change
                if sane and score >= cfg.lost_threshold:
                    misses = 0
                    pending.clear()
                    status, out_box = STATUS_TRACKED, np.array(sb, np.float32) / k
                    last_good = out_box.copy()
                    if score > 0.7:
                        adapt_sig = 0.9 * adapt_sig + 0.1 * sig
                else:
                    misses += 1
                    if misses < cfg.lost_patience:
                        status = STATUS_TRACKED   # tentative; revisited if loss is confirmed
                        out_box = np.array(sb, np.float32) / k if sig is not None else last_good.copy()
                        pending.append(i)
                    else:
                        for j in pending:
                            data.status[j] = STATUS_LOST
                            data.boxes[j] = last_good
                        pending.clear()
                        lost_since = idx
            if lost_since is not None and status == STATUS_LOST:
                found = self._reacquire(gray, base_patch, last_good * k, abs(idx - lost_since))
                if found is not None:
                    sb, score = found
                    tracker, _ = create_cv_tracker(cfg.algorithm)
                    tracker.init(small, tuple(int(round(v)) for v in sb))
                    adapt_sig = _signature(gray, sb)
                    status, out_box = STATUS_REACQUIRED, np.array(sb, np.float32) / k
                    last_good = out_box.copy()
                    lost_since, misses = None, 0

            if cfg.estimate_rotation and status != STATUS_LOST and prev_gray is not None:
                d = _rotation_step(prev_gray, gray, prev_box_small)
                if d is not None and abs(d) < 20:
                    angle += d
            data.boxes[i] = out_box
            data.status[i] = status
            data.score[i] = score
            data.angle[i] = angle
            if status != STATUS_LOST:
                prev_box_small = tuple(float(v) * k for v in out_box)
            prev_gray = gray

    def _reacquire(self, gray, patch, last_box_small, frames_lost):
        if patch is None or patch.size == 0:
            return None
        x, y, w, h = last_box_small
        cx, cy = x + w / 2, y + h / 2
        reach = (1.5 + 0.1 * min(frames_lost, 60)) * max(w, h)
        H, W = gray.shape
        x0, y0 = int(max(0, cx - reach)), int(max(0, cy - reach))
        x1, y1 = int(min(W, cx + reach)), int(min(H, cy + reach))
        region = gray[y0:y1, x0:x1]
        best = None
        for s in (0.75, 0.9, 1.0, 1.12, 1.3):
            tw, th = int(patch.shape[1] * s), int(patch.shape[0] * s)
            if tw < 8 or th < 8 or tw >= region.shape[1] or th >= region.shape[0]:
                continue
            t = cv2.resize(patch, (tw, th), interpolation=cv2.INTER_AREA)
            res = cv2.matchTemplate(region, t, cv2.TM_CCOEFF_NORMED)
            _, mv, _, ml = cv2.minMaxLoc(res)
            if best is None or mv > best[1]:
                best = ((x0 + ml[0], y0 + ml[1], tw, th), mv)
        if best and best[1] >= self.cfg.reacquire_threshold:
            return best
        return None


# -- assisted selection ------------------------------------------------------

def detect_largest(frame: np.ndarray, kind: str) -> Optional[Box]:
    """Suggest a starting box: 'face' (Haar cascade) or 'person' (HOG)."""
    small, k = resize_to_max(frame, 960)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    if kind == "face":
        casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        found = casc.detectMultiScale(gray, 1.1, 5, minSize=(24, 24))
    elif kind == "person":
        hog = cv2.HOGDescriptor()
        hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        found, _ = hog.detectMultiScale(small, winStride=(8, 8), padding=(8, 8), scale=1.05)
    else:
        raise ValueError(kind)
    if found is None or len(found) == 0:
        return None
    x, y, w, h = max(found, key=lambda b: b[2] * b[3])
    return (x / k, y / k, w / k, h / k)
