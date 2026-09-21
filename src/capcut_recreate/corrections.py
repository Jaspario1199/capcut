"""Capture what the operator changed after opening a generated job in CapCut.

Diffs the job's current document against the snapshot written at apply time
and turns the differences into labelled corrections:

  media_clip     the operator swapped the clip we chose for a slot
  media_inpoint  the operator moved the in-point inside the clip
  text           the operator rewrote our text
  other          anything else on a planned slot (transform, volume, ...)

Everything outside planned slots is recorded as app_rewrite_paths: these are
the fields CapCut itself rewrites on open (frame-grid rounding, bookkeeping)
and feed the Phase 0 allowlist.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .diffing import diff
from .draft import frame_us, iter_segments, load_doc, material_index, parse_text_content
from .library import Correction, JobRecord

_SEG = re.compile(r"^tracks\[([^\]]+)\]\.segments\[([^\]]+)\]\.(.+)$")
_MAT = re.compile(r"^materials\.(videos|audios|texts)\[([^\]]+)\]\.(.+)$")


def capture(job_dir: Path, rec: JobRecord) -> tuple[list[Correction], list[str]]:
    if not rec.written_doc:
        raise ValueError(f"job {rec.job_id} has no written snapshot; was it applied through this tool?")
    before = rec.written_doc
    after = load_doc(job_dir)
    tol = frame_us(after) + 1

    planned_media = {m["slot_id"]: m for m in rec.plan.get("media", [])}
    planned_text = {t["slot_id"]: t for t in rec.plan.get("text", [])}
    seg_to_mat_before = {r.id: r.segment.get("material_id") for r in iter_segments(before)}
    mats_before, mats_after = material_index(before), material_index(after)
    mat_to_seg = {}
    for r in iter_segments(after):
        mat_to_seg.setdefault(r.segment.get("material_id"), r.id)

    corrections: list[Correction] = []
    rewrites: list[str] = []
    seen_clip: set[str] = set()

    for ch in diff(before, after).changes:
        m = _SEG.match(ch.path)
        if m:
            _, seg_id, rest = m.groups()
            if seg_id in planned_media:
                if rest == "source_timerange.start":
                    if abs(int(ch.after or 0) - int(ch.before or 0)) > tol:
                        corrections.append(Correction(seg_id, "media_inpoint", ch.before, ch.after))
                    else:
                        rewrites.append(ch.path)
                elif rest.startswith(("target_timerange", "source_timerange")):
                    delta = abs(int(ch.after or 0) - int(ch.before or 0)) if isinstance(ch.after, int) and isinstance(ch.before, int) else None
                    (rewrites if delta is not None and delta <= tol else corrections).append(
                        ch.path if delta is not None and delta <= tol else Correction(seg_id, "other", ch.before, ch.after, rest))
                elif rest == "material_id":
                    corrections.append(Correction(seg_id, "media_clip", _mat_name(mats_before, ch.before), _mat_name(mats_after, ch.after)))
                    seen_clip.add(seg_id)
                else:
                    corrections.append(Correction(seg_id, "other", ch.before, ch.after, rest))
            elif seg_id in planned_text and rest not in ("material_id",):
                corrections.append(Correction(seg_id, "other", ch.before, ch.after, rest))
            else:
                rewrites.append(ch.path)
            continue

        m = _MAT.match(ch.path)
        if m:
            cat, mat_id, rest = m.groups()
            seg_id = mat_to_seg.get(mat_id) or next((s for s, mm in seg_to_mat_before.items() if mm == mat_id), None)
            if cat in ("videos", "audios") and seg_id in planned_media and rest in ("path", "material_name", "name"):
                if seg_id not in seen_clip and rest == "path" and Path(str(ch.before)).name != Path(str(ch.after)).name:
                    corrections.append(Correction(seg_id, "media_clip", Path(str(ch.before)).name, Path(str(ch.after)).name))
                    seen_clip.add(seg_id)
                continue
            if cat == "texts" and seg_id in planned_text and rest == "content":
                tb = parse_text_content({"content": ch.before}) or {}
                ta = parse_text_content({"content": ch.after}) or {}
                if tb.get("text") != ta.get("text"):
                    corrections.append(Correction(seg_id, "text", tb.get("text"), ta.get("text")))
                else:
                    rewrites.append(ch.path)
                continue
            rewrites.append(ch.path)
            continue

        rewrites.append(ch.path)

    return corrections, sorted(set(rewrites))


def _mat_name(mats: dict, mat_id: Any) -> str:
    _, m = mats.get(str(mat_id), ("", {}))
    p = m.get("path") or m.get("material_name") or m.get("name") or str(mat_id)
    return Path(str(p)).name
