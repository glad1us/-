"""Reading CapCut Desktop projects and writing camera keyframes into them.

CapCut Desktop has no public plugin API.  What it does have is a documented-by-
practice project format: every project ("draft") is a folder with a JSON
timeline (``draft_content.json``; some builds also keep ``draft_info.json``
and ``Timelines/<id>/draft_content.json``).  Keyframes live in each segment's
``common_keyframes`` list.  This module edits exactly those fields — the same
fields CapCut itself writes when you add Position/Scale/Rotation keyframes by
hand — and leaves everything else untouched.

Safety rules implemented here:
* the full project folder JSON files are backed up before every write;
* writes are atomic (temp file + rename);
* if the timeline file is not plaintext JSON (encrypted builds) we refuse
  instead of guessing.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .keyframes import KF_POS_X, KF_POS_Y, KF_ROTATION, KF_SCALE_X, KeyframeSet

TIMELINE_NAMES = ("draft_content.json", "draft_info.json")
MANAGED_PROPS = (KF_POS_X, KF_POS_Y, KF_SCALE_X, "KFTypeScaleY", KF_ROTATION)
CAPCUT_PROCESSES = ("CapCut.exe", "JianyingPro.exe")


class DraftError(RuntimeError):
    pass


class EncryptedDraftError(DraftError):
    pass


def default_draft_roots() -> List[str]:
    roots = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(os.path.join(local, "CapCut", "User Data", "Projects", "com.lveditor.draft"))
    home = os.path.expanduser("~")
    roots.append(os.path.join(home, "Movies", "CapCut", "User Data", "Projects", "com.lveditor.draft"))
    seen, out = set(), []
    for r in roots:
        r = os.path.normpath(r)
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def find_draft_root() -> Optional[str]:
    for r in default_draft_roots():
        if os.path.isdir(r):
            return r
    return None


def capcut_running() -> Optional[bool]:
    """True/False on Windows; None where we cannot tell."""
    if sys.platform != "win32":
        return None
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                             timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return None
    low = out.lower()
    return any(f'"{p.lower()}"' in low for p in CAPCUT_PROCESSES)


@dataclass
class ProjectInfo:
    path: str
    name: str
    modified: float


def list_projects(root: str) -> List[ProjectInfo]:
    out = []
    if not root or not os.path.isdir(root):
        return out
    for entry in os.scandir(root):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if not any(os.path.isfile(os.path.join(entry.path, n)) for n in TIMELINE_NAMES):
            continue
        name, mtime = entry.name, entry.stat().st_mtime
        meta = os.path.join(entry.path, "draft_meta_info.json")
        try:
            with open(meta, "r", encoding="utf-8") as f:
                m = json.load(f)
            name = m.get("draft_name") or name
            t = m.get("tm_draft_modified")
            if isinstance(t, (int, float)) and t > 0:
                mtime = t / 1e6 if t > 1e12 else t
        except Exception:
            pass
        out.append(ProjectInfo(entry.path, name, mtime))
    out.sort(key=lambda p: p.modified, reverse=True)
    return out


@dataclass
class SegmentInfo:
    segment_id: str
    track_index: int
    track_name: str
    material_id: str
    material_path: str
    material_name: str
    material_w: int
    material_h: int
    target_start: int        # microseconds
    target_duration: int
    source_start: int
    source_duration: int
    speed: float
    has_crop: bool
    flip_h: bool
    flip_v: bool
    existing_keyframes: List[str]

    @property
    def file_exists(self) -> bool:
        return os.path.isfile(self.material_path)

    def label(self) -> str:
        t0 = self.target_start / 1e6
        return f"V{self.track_index + 1} · {t0:6.2f}s · {self.target_duration / 1e6:.2f}s · {self.material_name}"


def _load_json_file(path: str) -> dict:
    with open(path, "rb") as f:
        raw = f.read()
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EncryptedDraftError(
            f"Файл {os.path.basename(path)} не является открытым JSON (вероятно, зашифрован "
            "этой версией CapCut/JianYing). Запись ключевых кадров невозможна без взлома "
            "шифрования, а этого плагин делать не будет.")


_PLACEHOLDER = re.compile(r"^##_draftpath_placeholder_[^#]*_##")


class DraftProject:
    def __init__(self, path: str):
        self.path = os.path.normpath(path)
        self.timeline_file = None
        for n in TIMELINE_NAMES:
            p = os.path.join(self.path, n)
            if os.path.isfile(p):
                self.timeline_file = p
                break
        if self.timeline_file is None:
            raise DraftError("В папке нет draft_content.json / draft_info.json — это не проект CapCut")
        self.content = _load_json_file(self.timeline_file)
        if "tracks" not in self.content or "materials" not in self.content:
            raise DraftError("Неизвестная структура проекта: нет tracks/materials")

    # -- reading ----------------------------------------------------------
    @property
    def canvas_size(self) -> Tuple[int, int]:
        c = self.content.get("canvas_config") or {}
        return int(c.get("width") or 1920), int(c.get("height") or 1080)

    @property
    def app_version(self) -> str:
        p = self.content.get("last_modified_platform") or {}
        return str(p.get("app_version") or "неизвестно")

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    def _resolve(self, p: str) -> str:
        if not p:
            return ""
        p = _PLACEHOLDER.sub(self.path.replace("\\", "/"), p)
        return os.path.normpath(p)

    def video_segments(self) -> List[SegmentInfo]:
        mats = {m.get("id"): m for m in self.content["materials"].get("videos", [])}
        out = []
        vi = 0
        for track in self.content["tracks"]:
            if track.get("type") != "video":
                continue
            for seg in track.get("segments", []):
                mat = mats.get(seg.get("material_id"))
                if not mat or mat.get("type", "video") != "video":
                    continue  # photos, generated text-to-video etc.
                src = seg.get("source_timerange") or {}
                tgt = seg.get("target_timerange") or {}
                sd, td = int(src.get("duration") or 0), int(tgt.get("duration") or 0)
                speed = float(seg.get("speed") or (sd / td if sd and td else 1.0))
                crop = mat.get("crop") or {}
                has_crop = any(abs(float(crop.get(k, d)) - d) > 1e-6 for k, d in
                               (("upper_left_x", 0), ("upper_left_y", 0), ("lower_right_x", 1), ("lower_right_y", 1)))
                flip = (seg.get("clip") or {}).get("flip") or {}
                out.append(SegmentInfo(
                    segment_id=seg["id"], track_index=vi, track_name=track.get("name", ""),
                    material_id=mat["id"], material_path=self._resolve(mat.get("path", "")),
                    material_name=mat.get("material_name") or os.path.basename(mat.get("path", "")),
                    material_w=int(mat.get("width") or 0), material_h=int(mat.get("height") or 0),
                    target_start=int(tgt.get("start") or 0), target_duration=td,
                    source_start=int(src.get("start") or 0), source_duration=sd, speed=speed,
                    has_crop=has_crop, flip_h=bool(flip.get("horizontal")), flip_v=bool(flip.get("vertical")),
                    existing_keyframes=sorted({k.get("property_type", "") for k in seg.get("common_keyframes", [])}),
                ))
            vi += 1
        return out

    def find_segment(self, content: dict, segment_id: str) -> Optional[dict]:
        for track in content.get("tracks", []):
            for seg in track.get("segments", []):
                if seg.get("id") == segment_id:
                    return seg
        return None

    # -- writing ----------------------------------------------------------
    def sync_files(self) -> List[str]:
        """All plaintext timeline copies inside the project folder."""
        files = [os.path.join(self.path, n) for n in TIMELINE_NAMES]
        files += glob.glob(os.path.join(self.path, "Timelines", "*", "draft_content.json"))
        files += glob.glob(os.path.join(self.path, "Timelines", "*", "draft_info.json"))
        return [f for f in files if os.path.isfile(f)]

    def backup(self, backup_root: str) -> str:
        dest = os.path.join(backup_root, self.name, time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(dest, exist_ok=True)
        for f in self.sync_files() + [os.path.join(self.path, "draft_meta_info.json")]:
            if os.path.isfile(f):
                rel = os.path.relpath(f, self.path)
                os.makedirs(os.path.dirname(os.path.join(dest, rel)), exist_ok=True)
                shutil.copy2(f, os.path.join(dest, rel))
        return dest

    @staticmethod
    def _apply_to_segment(seg: dict, ks: KeyframeSet):
        props = set(ks.values)
        kept = [k for k in seg.get("common_keyframes", []) if k.get("property_type") not in MANAGED_PROPS
                or (k.get("property_type") == KF_ROTATION and KF_ROTATION not in props)]
        for prop, vals in ks.values.items():
            kept.append({
                "id": str(uuid.uuid4()).upper(),
                "keyframe_list": [{
                    "curveType": "Line",
                    "graphID": "",
                    "left_control": {"x": 0.0, "y": 0.0},
                    "right_control": {"x": 0.0, "y": 0.0},
                    "id": str(uuid.uuid4()).upper(),
                    "time_offset": int(off),
                    "values": [float(v)],
                } for off, v in zip(ks.offsets_us, vals)],
                "material_id": "",
                "property_type": prop,
            })
        seg["common_keyframes"] = kept
        clip = seg.setdefault("clip", {})
        sc = float(ks.values[KF_SCALE_X][0])
        clip["scale"] = {"x": sc, "y": sc}
        clip["transform"] = {"x": float(ks.values[KF_POS_X][0]), "y": float(ks.values[KF_POS_Y][0])}
        if KF_ROTATION in ks.values:
            clip["rotation"] = float(ks.values[KF_ROTATION][0])
        seg["uniform_scale"] = {"on": True, "value": 1.0}

    def apply_keyframes(self, segment_id: str, ks: KeyframeSet, backup_root: str) -> Dict:
        if not ks.frames:
            raise DraftError("Нет ключевых кадров для записи")
        backup_dir = self.backup(backup_root)
        written = []
        for f in self.sync_files():
            try:
                content = self.content if f == self.timeline_file else _load_json_file(f)
            except EncryptedDraftError:
                continue  # an encrypted sidecar copy: leave it, CapCut regenerates it
            seg = self.find_segment(content, segment_id)
            if seg is None:
                continue
            self._apply_to_segment(seg, ks)
            _atomic_write_json(f, content)
            written.append(f)
        if self.timeline_file not in written:
            raise DraftError("Сегмент не найден в основном файле проекта")
        return {"backup": backup_dir, "files": written}

    def remove_tracking_keyframes(self, segment_id: str, backup_root: str) -> Dict:
        backup_dir = self.backup(backup_root)
        written = []
        for f in self.sync_files():
            try:
                content = self.content if f == self.timeline_file else _load_json_file(f)
            except EncryptedDraftError:
                continue
            seg = self.find_segment(content, segment_id)
            if seg is None:
                continue
            seg["common_keyframes"] = [k for k in seg.get("common_keyframes", [])
                                       if k.get("property_type") not in MANAGED_PROPS]
            clip = seg.setdefault("clip", {})
            clip["scale"] = {"x": 1.0, "y": 1.0}
            clip["transform"] = {"x": 0.0, "y": 0.0}
            _atomic_write_json(f, content)
            written.append(f)
        return {"backup": backup_dir, "files": written}


def list_backups(backup_root: str, project_name: str) -> List[str]:
    d = os.path.join(backup_root, project_name)
    if not os.path.isdir(d):
        return []
    return sorted((os.path.join(d, x) for x in os.listdir(d)), reverse=True)


def restore_backup(backup_dir: str, project_path: str) -> List[str]:
    restored = []
    for f in glob.glob(os.path.join(backup_dir, "**", "*.json"), recursive=True):
        rel = os.path.relpath(f, backup_dir)
        dst = os.path.join(project_path, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(f, dst)
        restored.append(dst)
    return restored


def _atomic_write_json(path: str, content: dict):
    tmp = path + ".smarttracking.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(content, f, ensure_ascii=False)
    os.replace(tmp, path)
