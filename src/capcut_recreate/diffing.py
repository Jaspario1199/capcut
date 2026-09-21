"""Path-level JSON diff with an allowlist.

capcut-cli's own `diff` compares six segment fields and reports material
changes by id only, so it cannot tell us whether a keyframe, transform,
source range or companion ref changed. This one walks the whole document.

Arrays whose items carry an `id` are keyed by that id so a reordered array
does not show as a full rewrite. Paths look like:

    tracks[track-video-01].segments[aaaaaa01-...].source_timerange.start
    materials.videos[mat-video-01].path
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Change:
    path: str
    before: Any
    after: Any
    kind: str  # changed | added | removed

    def __str__(self) -> str:
        return f"{self.kind:7} {self.path}: {self.before!r} -> {self.after!r}"


@dataclass
class DiffResult:
    changes: list[Change] = field(default_factory=list)

    def filtered(self, allow: list[str]) -> list[Change]:
        pats = [re.compile(p) for p in allow]
        return [c for c in self.changes if not any(p.search(c.path) for p in pats)]


def _keyed(arr: list[Any]) -> dict[str, Any] | None:
    if not arr or not all(isinstance(x, dict) and "id" in x for x in arr):
        return None
    keys = [str(x["id"]) for x in arr]
    if len(set(keys)) != len(keys):
        return None
    return dict(zip(keys, arr))


def diff(a: Any, b: Any, path: str = "", out: DiffResult | None = None) -> DiffResult:
    out = out or DiffResult()
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            p = f"{path}.{k}" if path else k
            if k not in a:
                out.changes.append(Change(p, None, b[k], "added"))
            elif k not in b:
                out.changes.append(Change(p, a[k], None, "removed"))
            else:
                diff(a[k], b[k], p, out)
        return out
    if isinstance(a, list) and isinstance(b, list):
        ka, kb = _keyed(a), _keyed(b)
        if ka is not None and kb is not None:
            for k in list(ka) + [k for k in kb if k not in ka]:
                p = f"{path}[{k}]"
                if k not in kb:
                    out.changes.append(Change(p, ka[k], None, "removed"))
                elif k not in ka:
                    out.changes.append(Change(p, None, kb[k], "added"))
                else:
                    diff(ka[k], kb[k], p, out)
            return out
        n = max(len(a), len(b))
        for i in range(n):
            p = f"{path}[{i}]"
            if i >= len(a):
                out.changes.append(Change(p, None, b[i], "added"))
            elif i >= len(b):
                out.changes.append(Change(p, a[i], None, "removed"))
            else:
                diff(a[i], b[i], p, out)
        return out
    if a != b:
        out.changes.append(Change(path, a, b, "changed"))
    return out


def seg_path(track_id: str, seg_id: str) -> str:
    return f"tracks[{track_id}].segments[{seg_id}]"


def apply_allowlist(manifest_media_ids: list[str], manifest_text_ids: list[str],
                    replaced_material_ids: list[str], text_material_ids: list[str],
                    seg_track: dict[str, str], extra: list[str] | None = None) -> list[str]:
    """Regexes for every path the apply step is allowed to change."""
    allow = [r"^id$", r"^name$", r"^duration$", r"^tm_", r"^last_modified_platform", r"^update_time$", r"^create_time$"]
    for mid in replaced_material_ids:
        m = re.escape(mid)
        allow += [rf"^materials\.videos\[{m}\]\.(path|material_name|name|duration|width|height|local_material_id)$",
                  rf"^materials\.audios\[{m}\]\.(path|name|duration|local_material_id)$"]
    for mid in text_material_ids:
        allow.append(rf"^materials\.texts\[{re.escape(mid)}\]\.content$")
    for sid in manifest_media_ids:
        t = re.escape(seg_track[sid])
        s = re.escape(sid)
        allow.append(rf"^tracks\[{t}\]\.segments\[{s}\]\.source_timerange\.(start|duration)$")
        allow.append(rf"^tracks\[{t}\]\.segments\[{s}\]\.target_timerange\.duration$")
    # lint --fix links every local media to its draft_materials entry; relink
    # rewrites every media path to the clone's assets/ (basename checked in apply).
    allow.append(r"^materials\.(videos|audios)\[[^\]]+\]\.(local_material_id|path)$")
    return allow + list(extra or [])
