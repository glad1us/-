"""Synthetic test footage with known ground truth: a 'phone' moving over a textured scene."""

import math
import os

import cv2
import numpy as np


def phone_texture(w, h, seed=3):
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), 25, np.uint8)                      # dark bezel
    screen = rng.integers(0, 255, (h - 16, w - 12, 3), np.uint8)
    screen = cv2.GaussianBlur(screen, (5, 5), 0)
    cv2.putText(screen, "12:45", (6, (h - 16) // 3), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    for k in range(4):
        cv2.rectangle(screen, (8 + k * 22, h // 2), (24 + k * 22, h // 2 + 16), (40 * k, 200, 255 - 50 * k), -1)
    img[8:h - 8, 6:w - 6] = screen
    return img


def background(w, h, seed=11):
    rng = np.random.default_rng(seed)
    bg = rng.integers(60, 140, (h // 8, w // 8, 3), np.uint8)
    bg = cv2.resize(bg, (w, h), interpolation=cv2.INTER_CUBIC)
    for _ in range(40):
        c = tuple(int(v) for v in rng.integers(0, 255, 3))
        p = (int(rng.integers(0, w)), int(rng.integers(0, h)))
        cv2.circle(bg, p, int(rng.integers(5, 30)), c, -1)
    return bg


def ground_truth(i, n, w, h, size=(90, 160)):
    t = i / n
    cx = w * (0.5 + 0.32 * math.sin(2 * math.pi * t))
    cy = h * (0.5 + 0.25 * math.sin(4 * math.pi * t + 0.6))
    s = 1.0 + 0.25 * math.sin(2 * math.pi * t)  # comes closer / goes away
    bw, bh = size[0] * s, size[1] * s
    return cx - bw / 2, cy - bh / 2, bw, bh


def make_video(path, n=150, w=960, h=540, fps=30, hide=None, noise=4):
    """hide: (start, end) frame range during which the phone leaves the frame (occlusion)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    bg = background(w, h)
    tex = phone_texture(90, 160)
    rng = np.random.default_rng(1)
    gts = []
    for i in range(n):
        f = bg.copy()
        x, y, bw, bh = ground_truth(i, n, w, h)
        visible = not (hide and hide[0] <= i <= hide[1])
        if visible:
            obj = cv2.resize(tex, (int(round(bw)), int(round(bh))))
            x0, y0 = int(round(x)), int(round(y))
            f[y0:y0 + obj.shape[0], x0:x0 + obj.shape[1]] = obj
        if noise:
            f = cv2.add(f, rng.integers(0, noise, f.shape, np.uint8))
        vw.write(f)
        gts.append((x, y, bw, bh, visible))
    vw.release()
    return gts
