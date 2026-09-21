"""Invariant validator over a written draft.

capcut-cli lint treats source-range overruns as info and has no checks for
keyframe or animation offsets, so these are ours. Frame tolerance is one
frame at the project's fps because CapCut re-quantises durations on open.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .draft import frame_us, iter_segments, load_doc, material_index, parse_text_content, utf16_len


def validate_doc(doc: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    mats = material_index(doc)
    tol = frame_us(doc) + 1  # one frame plus rounding slack, in microseconds

    for ref in iter_segments(doc):
        seg = ref.segment
        sid = seg.get("id", "?")
        tgt = seg.get("target_timerange") or {}
        src = seg.get("source_timerange") or {}
        tdur = int(tgt.get("duration", 0))
        sdur = int(src.get("duration", 0))
        sstart = int(src.get("start", 0))
        speed = float(seg.get("speed") or 1.0)
        cat, mat = mats.get(seg.get("material_id", ""), ("", {}))

        if tdur <= 0:
            errors.append(f"{sid}: target duration {tdur} <= 0")

        if cat in ("videos", "audios"):
            mdur = int(mat.get("duration") or 0)
            if mat.get("type") != "photo" and mdur and sstart + sdur > mdur + tol:
                errors.append(f"{sid}: source range {sstart}+{sdur} exceeds material {mat.get('id')} duration {mdur}")
            if ref.track_type != "text" and abs(sdur - tdur * speed) > tol:
                errors.append(f"{sid}: source {sdur} != target {tdur} x speed {speed} (delta {sdur - tdur * speed:.0f}us)")
            path = mat.get("path")
            if isinstance(path, str) and path and not Path(path).is_absolute():
                pass  # relative to draft; existence checked by lint

        for kf in seg.get("common_keyframes") or []:
            for k in kf.get("keyframe_list") or []:
                off = int(k.get("time_offset", 0))
                if off > tdur + tol:
                    errors.append(f"{sid}: keyframe {kf.get('property_type')} at {off}us past segment end {tdur}us")

        for ref_id in seg.get("extra_material_refs") or []:
            rcat, rmat = mats.get(ref_id, ("", {}))
            if rcat == "":
                errors.append(f"{sid}: dangling companion ref {ref_id}")
                continue
            if rcat == "material_animations":
                for a in rmat.get("animations") or []:
                    start = int(a.get("start") or 0)
                    dur = int(a.get("duration") or 0)
                    if start + dur > tdur + tol:
                        errors.append(f"{sid}: animation {a.get('name')} ends at {start + dur}us past segment end {tdur}us")
            if rcat == "transitions":
                if int(rmat.get("duration") or 0) > tdur + tol:
                    errors.append(f"{sid}: transition longer than segment")

        if cat == "texts":
            parsed = parse_text_content(mat)
            if parsed is not None:
                n = utf16_len(str(parsed.get("text", "")))
                for i, st in enumerate(parsed.get("styles") or []):
                    r = st.get("range") or [0, 0]
                    if len(r) != 2 or r[0] < 0 or r[1] > n or r[0] > r[1]:
                        errors.append(f"{sid}: style range {i} {r} outside text length {n}")

    # timeline duration must cover every segment
    end = max((int((s.segment.get("target_timerange") or {}).get("start", 0)) +
               int((s.segment.get("target_timerange") or {}).get("duration", 0)) for s in iter_segments(doc)), default=0)
    if int(doc.get("duration") or 0) + tol < end:
        errors.append(f"draft duration {doc.get('duration')} shorter than last segment end {end}")
    return errors


def validate_dir(draft_dir: Path) -> list[str]:
    return validate_doc(load_doc(draft_dir))
