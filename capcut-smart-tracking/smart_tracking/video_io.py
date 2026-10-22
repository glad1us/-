"""Frame access for the clip under analysis (OpenCV / FFmpeg backend)."""

from __future__ import annotations

import os
from typing import Iterator, Optional, Tuple

import cv2
import numpy as np


class VideoOpenError(RuntimeError):
    pass


class FrameSource:
    """Random and sequential access to a video file.

    Sequential reads are cheap; a seek happens only when the requested frame is
    not the next one.  ``scale`` lets callers get downscaled frames for analysis.
    """

    def __init__(self, path: str):
        if not os.path.isfile(path):
            raise VideoOpenError(f"Файл видео не найден: {path}")
        self.path = path
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            raise VideoOpenError(f"Не удалось открыть видео: {path}")
        self.fps = float(self.cap.get(cv2.CAP_PROP_FPS)) or 30.0
        self.frame_count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        ok, frame = self.cap.read()
        if not ok:
            raise VideoOpenError(f"Видео не содержит кадров: {path}")
        # Decoded size (OpenCV applies orientation metadata automatically).
        self.height, self.width = frame.shape[:2]
        self._next_index = 1
        self._last: Optional[Tuple[int, np.ndarray]] = (0, frame)
        if self.frame_count <= 0:
            self.frame_count = self._count_frames()

    def _count_frames(self) -> int:
        n = 1
        while self.cap.grab():
            n += 1
        self._seek(0)
        return n

    def _seek(self, index: int):
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        self._next_index = index

    def time_to_frame(self, seconds: float) -> int:
        return int(round(seconds * self.fps))

    def frame_to_time(self, index: int) -> float:
        return index / self.fps

    def read(self, index: int) -> Optional[np.ndarray]:
        """Return frame ``index`` as BGR, or None past the end of the file."""
        index = max(0, min(index, self.frame_count - 1))
        if self._last is not None and self._last[0] == index:
            return self._last[1]
        if index != self._next_index:
            self._seek(index)
        ok, frame = self.cap.read()
        if not ok:
            return None
        self._next_index = index + 1
        self._last = (index, frame)
        return frame

    def iter_range(self, start: int, end: int, step: int = 1) -> Iterator[Tuple[int, np.ndarray]]:
        """Yield (index, frame) for start..end inclusive; step may be negative.

        Reverse iteration decodes blocks forward and replays them backwards so
        we never seek once per frame.
        """
        if step > 0:
            for i in range(start, end + 1, step):
                f = self.read(i)
                if f is None:
                    return
                yield i, f
            return
        block = 48
        hi = start
        while hi >= end:
            lo = max(end, hi - block + 1)
            frames = []
            for i in range(lo, hi + 1):
                f = self.read(i)
                if f is None:
                    break
                frames.append((i, f.copy()))
            for item in reversed(frames):
                yield item
            hi = lo - 1

    def close(self):
        self.cap.release()


def resize_to_max(frame: np.ndarray, max_dim: int) -> Tuple[np.ndarray, float]:
    """Downscale so the longest side is at most ``max_dim``; returns (img, factor)."""
    h, w = frame.shape[:2]
    m = max(h, w)
    if m <= max_dim:
        return frame, 1.0
    k = max_dim / m
    return cv2.resize(frame, (int(round(w * k)), int(round(h * k))), interpolation=cv2.INTER_AREA), k
