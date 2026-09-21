"""Frame thumbnails via ffmpeg, for the planner's eyes.

Small JPEGs (320px wide) at given timestamps. Used for template slots (at the
slot's in-point and mid-point) and for footage scenes (at each scene start).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .draft import US


class ThumbError(Exception):
    pass


def ffmpeg_available(cmd: str = "ffmpeg") -> bool:
    return shutil.which(cmd) is not None


def extract_frame(video: Path, at_us: int, out: Path, width: int = 320, ffmpeg_cmd: str = "ffmpeg") -> Path:
    if not ffmpeg_available(ffmpeg_cmd):
        raise ThumbError("ffmpeg not on PATH")
    out.parent.mkdir(parents=True, exist_ok=True)
    secs = max(0.0, at_us / US)
    cmd = [ffmpeg_cmd, "-v", "error", "-y", "-ss", f"{secs:.3f}", "-i", str(video),
           "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not out.exists():
        raise ThumbError(f"ffmpeg failed on {video} at {secs:.3f}s: {proc.stderr.strip()}")
    return out


def thumb_name(prefix: str, at_us: int) -> str:
    return f"{prefix}_{at_us // 1000:08d}ms.jpg"
