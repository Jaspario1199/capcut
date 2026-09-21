"""Phase 2: index new footage.

Produces a footage index the planner chooses from. Every clip gets a unique,
slot-safe filename (replace-media silently reuses an existing asset on
basename collision), an md5, ffprobe metadata, and a scene list. Scene
detection uses PySceneDetect when installed and otherwise falls back to one
scene per clip, which is still a valid (if coarse) enum for the planner.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .draft import US


class FootageError(Exception):
    pass


@dataclass
class Scene:
    scene_id: str
    start_us: int
    end_us: int
    thumbnail: str | None = None


@dataclass
class Clip:
    clip_id: str
    path: str
    original_name: str
    md5: str
    duration_us: int
    fps: float
    width: int
    height: int
    has_audio: bool
    vfr: bool
    scenes: list[Scene] = field(default_factory=list)


@dataclass
class FootageIndex:
    clips: list[Clip]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    def by_id(self) -> dict[str, Clip]:
        return {c.clip_id: c for c in self.clips}


def md5_file(p: Path) -> str:
    h = hashlib.md5()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ffprobe(path: Path, ffprobe_cmd: str = "ffprobe") -> dict[str, Any]:
    if shutil.which(ffprobe_cmd) is None:
        raise FootageError(f"{ffprobe_cmd} not on PATH; replace-media would silently keep stale durations")
    cmd = [ffprobe_cmd, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise FootageError(f"ffprobe failed on {path}: {out.stderr.strip()}")
    return json.loads(out.stdout)


def _fraction(s: str | None) -> float:
    if not s or "/" not in s:
        return float(s) if s else 0.0
    n, d = s.split("/")
    return float(n) / float(d) if float(d) else 0.0


def probe_clip(path: Path, clip_id: str, ffprobe_cmd: str = "ffprobe") -> Clip:
    info = ffprobe(path, ffprobe_cmd)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if v is None:
        raise FootageError(f"{path}: no video stream")
    a = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    dur = float(info.get("format", {}).get("duration") or v.get("duration") or 0)
    r = _fraction(v.get("r_frame_rate"))
    avg = _fraction(v.get("avg_frame_rate"))
    vfr = bool(r and avg and abs(r - avg) / r > 0.01)
    return Clip(
        clip_id=clip_id, path=str(path), original_name=path.name, md5=md5_file(path),
        duration_us=int(round(dur * US)), fps=avg or r, width=int(v.get("width", 0)), height=int(v.get("height", 0)),
        has_audio=a, vfr=vfr,
    )


def detect_scenes(path: Path, duration_us: int, threshold: float = 27.0) -> list[Scene]:
    try:
        from scenedetect import ContentDetector, detect  # type: ignore
    except Exception:
        return [Scene("s0", 0, duration_us)]
    try:
        cuts = detect(str(path), ContentDetector(threshold=threshold))
    except Exception:
        return [Scene("s0", 0, duration_us)]
    if not cuts:
        return [Scene("s0", 0, duration_us)]
    scenes = []
    for i, (start, end) in enumerate(cuts):
        scenes.append(Scene(f"s{i}", int(start.get_seconds() * US), int(end.get_seconds() * US)))
    return scenes


def stage_footage(sources: list[Path], staging_dir: Path, ffprobe_cmd: str = "ffprobe",
                  scene_threshold: float = 27.0, thumbnails: bool = True) -> FootageIndex:
    """Copy clips into staging_dir under unique names and index them."""
    from .thumbs import ThumbError, extract_frame, ffmpeg_available, thumb_name

    staging_dir.mkdir(parents=True, exist_ok=True)
    thumbs_dir = staging_dir / "thumbs"
    clips: list[Clip] = []
    seen: set[str] = set()
    for src in sources:
        src = Path(src)
        if not src.exists():
            raise FootageError(f"missing footage: {src}")
        digest = md5_file(src)
        clip_id = f"c{len(clips):02d}"
        unique = f"{digest[:10]}_{src.name}"
        if unique in seen:
            # identical bytes and name: keep one copy
            continue
        seen.add(unique)
        dest = staging_dir / unique
        if not dest.exists():
            shutil.copyfile(src, dest)
        clip = probe_clip(dest, clip_id, ffprobe_cmd)
        if clip.md5 != digest:
            raise FootageError(f"staged copy of {src} does not match source md5")
        if clip.vfr:
            raise FootageError(f"{src}: variable frame rate; transcode to CFR first")
        clip.original_name = src.name
        clip.scenes = detect_scenes(dest, clip.duration_us, scene_threshold)
        if thumbnails and ffmpeg_available():
            for sc in clip.scenes:
                out = thumbs_dir / thumb_name(f"{clip_id}_{sc.scene_id}", sc.start_us)
                try:
                    if not out.exists():
                        extract_frame(dest, sc.start_us, out)
                    sc.thumbnail = str(out)
                except ThumbError:
                    sc.thumbnail = None
        clips.append(clip)
    return FootageIndex(clips)


def index_from_json(data: dict[str, Any]) -> FootageIndex:
    clips = []
    for c in data["clips"]:
        c = dict(c)
        c["scenes"] = [Scene(**s) for s in c.get("scenes", [])]
        clips.append(Clip(**c))
    return FootageIndex(clips)
