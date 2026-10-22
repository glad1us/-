"""Presets and per-clip tracking sessions, stored in the user's app-data folder."""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Dict, Optional, Tuple

import numpy as np

from .camera import CameraSettings, MODE_TITLES, settings_for_mode
from .tracking import TrackData


def app_dir() -> str:
    base = os.environ.get("SMART_TRACKING_HOME")
    if not base:
        if sys.platform == "win32":
            base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "CapCutSmartTracking")
        else:
            base = os.path.join(os.path.expanduser("~"), ".config", "capcut-smart-tracking")
    os.makedirs(base, exist_ok=True)
    return base


def backup_root() -> str:
    d = os.path.join(app_dir(), "backups")
    os.makedirs(d, exist_ok=True)
    return d


# -- presets -------------------------------------------------------------------

def _presets_file() -> str:
    return os.path.join(app_dir(), "presets.json")


def builtin_presets() -> Dict[str, CameraSettings]:
    return {MODE_TITLES[m]: settings_for_mode(m) for m in ("tight", "smooth", "cinematic", "dynamic")}


def load_user_presets() -> Dict[str, CameraSettings]:
    try:
        with open(_presets_file(), "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {k: CameraSettings.from_dict(v) for k, v in raw.items()}
    except (OSError, ValueError):
        return {}


def save_user_preset(name: str, s: CameraSettings):
    p = load_user_presets()
    p[name] = s
    _write_presets(p)


def delete_user_preset(name: str):
    p = load_user_presets()
    p.pop(name, None)
    _write_presets(p)


def _write_presets(p: Dict[str, CameraSettings]):
    with open(_presets_file(), "w", encoding="utf-8") as f:
        json.dump({k: v.to_dict() for k, v in p.items()}, f, ensure_ascii=False, indent=2)


# -- sessions ------------------------------------------------------------------

def _session_base(project_path: str, segment_id: str) -> str:
    key = re.sub(r"[^A-Za-z0-9_-]+", "_", f"{os.path.basename(project_path)}__{segment_id}")
    d = os.path.join(app_dir(), "sessions")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, key)


def save_session(project_path: str, segment_id: str, track: TrackData, settings: CameraSettings,
                 extra: Optional[Dict] = None):
    base = _session_base(project_path, segment_id)
    np.savez_compressed(base + ".npz", **track.to_npz_dict())
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"settings": settings.to_dict(), "extra": extra or {}}, f, ensure_ascii=False, indent=2)


def load_session(project_path: str, segment_id: str) -> Optional[Tuple[TrackData, CameraSettings, Dict]]:
    base = _session_base(project_path, segment_id)
    if not (os.path.isfile(base + ".npz") and os.path.isfile(base + ".json")):
        return None
    try:
        with np.load(base + ".npz") as d:
            track = TrackData.from_npz_dict(d)
        with open(base + ".json", "r", encoding="utf-8") as f:
            meta = json.load(f)
        return track, CameraSettings.from_dict(meta.get("settings", {})), meta.get("extra", {})
    except (OSError, ValueError, KeyError):
        return None
