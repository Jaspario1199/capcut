"""Read-only helpers over a CapCut draft document.

Nothing in this module writes. Writes go through capcut-cli (see runner.py).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

US = 1_000_000

# Order matters: on Windows the root document is draft_content.json, on macOS it
# is draft_info.json. capcut-cli prefers template-2.tmp on 8.7+, but that file is
# a string-JSON envelope we do not parse here; the runner keeps mirrors in sync.
DOC_NAMES = ("draft_content.json", "draft_info.json")


class DraftError(Exception):
    pass


def find_doc(draft_dir: Path) -> Path:
    for name in DOC_NAMES:
        p = draft_dir / name
        if p.exists():
            return p
    raise DraftError(f"no draft document in {draft_dir} (looked for {', '.join(DOC_NAMES)})")


def load_doc(draft_dir: Path) -> dict[str, Any]:
    p = find_doc(draft_dir)
    raw = p.read_bytes()
    if not raw.lstrip().startswith(b"{"):
        raise DraftError(f"{p} is not plain JSON (encrypted JianYing draft?)")
    return json.loads(raw)


def frame_us(doc: dict[str, Any]) -> float:
    fps = doc.get("fps") or 30
    return US / float(fps)


@dataclass(frozen=True)
class SegRef:
    track_index: int
    track_id: str
    track_type: str
    track_name: str
    segment: dict[str, Any]

    @property
    def id(self) -> str:
        return self.segment["id"]


def iter_segments(doc: dict[str, Any]) -> Iterator[SegRef]:
    for ti, track in enumerate(doc.get("tracks", [])):
        for seg in track.get("segments", []):
            yield SegRef(ti, track.get("id", ""), track.get("type", ""), track.get("name", ""), seg)


def material_index(doc: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    """id -> (category, material) across every materials.* array."""
    out: dict[str, tuple[str, dict[str, Any]]] = {}
    for cat, items in (doc.get("materials") or {}).items():
        if not isinstance(items, list):
            continue
        for m in items:
            if isinstance(m, dict) and "id" in m:
                out[m["id"]] = (cat, m)
    return out


def material_users(doc: dict[str, Any]) -> dict[str, list[str]]:
    """material id -> segment ids whose material_id is that material."""
    users: dict[str, list[str]] = {}
    for ref in iter_segments(doc):
        mid = ref.segment.get("material_id")
        if mid:
            users.setdefault(mid, []).append(ref.id)
    return users


def parse_text_content(material: dict[str, Any]) -> dict[str, Any] | None:
    """Decode materials.texts[].content. None when it is not the JSON form."""
    raw = material.get("content")
    if not isinstance(raw, str) or not raw.lstrip().startswith("{"):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict) or "text" not in parsed:
        return None
    return parsed


# Modern CapCut (9.x) stores media paths relative to the draft folder behind a
# placeholder token instead of an absolute path. The app resolves it on open.
PLACEHOLDER_RE = re.compile(r"^##_draftpath_placeholder_[0-9A-Za-z-]+_##[\\/]?")


def is_placeholder_path(path: str) -> bool:
    return bool(PLACEHOLDER_RE.match(path or ""))


def resolve_media_path(path: str, draft_dir: Path) -> Path:
    """Where a material's `path` points on disk for a draft living in draft_dir."""
    if is_placeholder_path(path):
        rest = PLACEHOLDER_RE.sub("", path).replace("\\", "/")
        return Path(draft_dir) / rest
    p = Path(path)
    return p if p.is_absolute() else Path(draft_dir) / p


def utf16_len(s: str) -> int:
    """Length in UTF-16 code units, the unit CapCut's style ranges use."""
    return len(s.encode("utf-16-le")) // 2
