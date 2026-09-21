"""Phase 1: extract a slot manifest from a template draft and classify slots.

A slot is one segment on a video or text track. The manifest is the only view
of the template the planner (human or LLM) ever sees. It is built from the raw
document because `capcut segments` and `export-timeline` drop the fields that
decide whether a slot is safe to fill.

Classification is deliberately conservative. We lock everything whose
behaviour under replacement is unknown or not retimeable:
  - any mask material (mask keyframe encoding is unknown, so presence is enough)
  - non-empty group_id or a combination reference (compound clip structure is undocumented)
  - speed curves (no tool handles them)
  - materials shared by more than one segment (replace-media swaps all users)
  - text with more than one style range (set-text only rewrites styles[0])
  - text whose content is not the JSON form
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .draft import US, SegRef, iter_segments, load_doc, material_index, material_users, parse_text_content, utf16_len

MEDIA_TRACKS = {"video", "image"}
TEXT_TRACKS = {"text"}


@dataclass
class Keyframe:
    property: str
    from_value: float | None
    to_value: float | None
    first_offset_us: int
    last_offset_us: int
    count: int


@dataclass
class Transition:
    material_id: str
    name: str
    duration_us: int
    is_overlap: bool


@dataclass
class MediaSlot:
    slot_id: str
    kind: str = "media"
    track_name: str = ""
    track_index: int = 0
    z_order: int = 0
    material_id: str = ""
    material_type: str = ""
    path: str = ""
    material_duration_us: int = 0
    target_start_us: int = 0
    target_duration_us: int = 0
    source_start_us: int = 0
    source_duration_us: int = 0
    speed: float = 1.0
    has_curve_speed: bool = False
    scale: tuple[float, float] = (1.0, 1.0)
    transform: tuple[float, float] = (0.0, 0.0)
    rotation: float = 0.0
    crop: dict[str, float] | None = None
    full_frame: bool = True
    volume: float = 1.0
    keyframes: list[Keyframe] = field(default_factory=list)
    animations: list[dict[str, Any]] = field(default_factory=list)
    transition_out: Transition | None = None
    transition_in: Transition | None = None
    # Extra source microseconds the outgoing transition consumes past the
    # nominal source range. Only overlapping transitions do this.
    transition_pad_us: int = 0
    has_mask: bool = False
    group_id: str = ""
    has_combination: bool = False
    shared_with: list[str] = field(default_factory=list)
    status: str = "replaceable"
    lock_reasons: list[str] = field(default_factory=list)


@dataclass
class TextSlot:
    slot_id: str
    kind: str = "text"
    track_name: str = ""
    track_index: int = 0
    material_id: str = ""
    text: str = ""
    style_count: int = 0
    font_size: float | None = None
    target_start_us: int = 0
    target_duration_us: int = 0
    animations: list[dict[str, Any]] = field(default_factory=list)
    # Heuristic. Derived from the current text length; the true limit depends
    # on font metrics and bounding box, which we do not compute.
    max_chars: int = 0
    status: str = "replaceable"
    lock_reasons: list[str] = field(default_factory=list)


@dataclass
class Manifest:
    template_dir: str
    fps: float
    duration_us: int
    canvas: dict[str, Any]
    media: list[MediaSlot]
    text: list[TextSlot]
    locked_other: list[dict[str, Any]]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    def counts(self) -> dict[str, int]:
        out = {"media_replaceable": 0, "media_locked": 0, "text_replaceable": 0, "text_locked": 0}
        for s in self.media:
            out["media_replaceable" if s.status == "replaceable" else "media_locked"] += 1
        for s in self.text:
            out["text_replaceable" if s.status == "replaceable" else "text_locked"] += 1
        return out

    def media_by_id(self) -> dict[str, MediaSlot]:
        return {s.slot_id: s for s in self.media}

    def text_by_id(self) -> dict[str, TextSlot]:
        return {s.slot_id: s for s in self.text}


def _kf_summary(seg: dict[str, Any]) -> list[Keyframe]:
    out = []
    for kf in seg.get("common_keyframes") or []:
        lst = sorted(kf.get("keyframe_list") or [], key=lambda k: k.get("time_offset", 0))
        if not lst:
            continue

        def first_val(k: dict[str, Any]) -> float | None:
            v = k.get("values")
            return float(v[0]) if isinstance(v, list) and v else None

        out.append(Keyframe(
            property=str(kf.get("property_type", "")),
            from_value=first_val(lst[0]),
            to_value=first_val(lst[-1]),
            first_offset_us=int(lst[0].get("time_offset", 0)),
            last_offset_us=int(lst[-1].get("time_offset", 0)),
            count=len(lst),
        ))
    return out


def _media_slot(ref: SegRef, mats: dict, users: dict, z: int) -> MediaSlot:
    seg = ref.segment
    mid = seg.get("material_id", "")
    cat, mat = mats.get(mid, ("", {}))
    tgt = seg.get("target_timerange") or {}
    src = seg.get("source_timerange") or {}
    clip = seg.get("clip") or {}
    scale = clip.get("scale") or {}
    xform = clip.get("transform") or {}

    slot = MediaSlot(
        slot_id=seg["id"],
        track_name=ref.track_name,
        track_index=ref.track_index,
        z_order=z,
        material_id=mid,
        material_type=str(mat.get("type", cat)),
        path=str(mat.get("path", "")),
        material_duration_us=int(mat.get("duration") or 0),
        target_start_us=int(tgt.get("start", 0)),
        target_duration_us=int(tgt.get("duration", 0)),
        source_start_us=int(src.get("start", 0)),
        source_duration_us=int(src.get("duration", 0)),
        speed=float(seg.get("speed") or 1.0),
        scale=(float(scale.get("x", 1.0)), float(scale.get("y", 1.0))),
        transform=(float(xform.get("x", 0.0)), float(xform.get("y", 0.0))),
        rotation=float(clip.get("rotation") or 0.0),
        crop=mat.get("crop") if isinstance(mat.get("crop"), dict) else None,
        volume=float(seg.get("volume") if seg.get("volume") is not None else 1.0),
        keyframes=_kf_summary(seg),
        group_id=str(seg.get("group_id") or ""),
        has_combination=bool(seg.get("combination")) or bool(seg.get("combination_id")),
        shared_with=[s for s in users.get(mid, []) if s != seg["id"]],
    )
    slot.full_frame = slot.scale == (1.0, 1.0) and slot.transform == (0.0, 0.0) and not _is_cropped(slot.crop)

    for ref_id in seg.get("extra_material_refs") or []:
        rcat, rmat = mats.get(ref_id, ("", {}))
        if rcat == "speeds":
            slot.has_curve_speed = rmat.get("curve_speed") not in (None, [], {})
        elif rcat == "transitions":
            t = Transition(ref_id, str(rmat.get("name", "")), int(rmat.get("duration") or 0), bool(rmat.get("is_overlap")))
            slot.transition_out = t
            if t.is_overlap:
                slot.transition_pad_us = int(round(t.duration_us * slot.speed))
        elif rcat in ("masks", "common_masks"):
            slot.has_mask = True
        elif rcat == "material_animations":
            for a in rmat.get("animations") or []:
                slot.animations.append({"type": a.get("type"), "name": a.get("name"),
                                        "duration_us": a.get("duration"), "start_us": a.get("start")})
    if seg.get("masks") or seg.get("common_masks"):
        slot.has_mask = True

    reasons = []
    if slot.has_mask:
        reasons.append("mask (keyframe encoding unknown)")
    if slot.group_id:
        reasons.append("group_id set")
    if slot.has_combination:
        reasons.append("compound clip")
    if slot.has_curve_speed:
        reasons.append("speed curve")
    if slot.shared_with:
        reasons.append(f"material shared with {len(slot.shared_with)} other segment(s)")
    if slot.material_type not in ("video", "photo", "image", "videos"):
        reasons.append(f"material type {slot.material_type!r}")
    if reasons:
        slot.status, slot.lock_reasons = "locked", reasons
    return slot


def _is_cropped(crop: dict[str, float] | None) -> bool:
    if not crop:
        return False
    default = {"upper_left_x": 0, "upper_left_y": 0, "upper_right_x": 1, "upper_right_y": 0,
               "lower_left_x": 0, "lower_left_y": 1, "lower_right_x": 1, "lower_right_y": 1}
    return any(abs(float(crop.get(k, v)) - v) > 1e-6 for k, v in default.items())


def _text_slot(ref: SegRef, mats: dict) -> TextSlot:
    seg = ref.segment
    mid = seg.get("material_id", "")
    _, mat = mats.get(mid, ("", {}))
    tgt = seg.get("target_timerange") or {}
    slot = TextSlot(
        slot_id=seg["id"], track_name=ref.track_name, track_index=ref.track_index, material_id=mid,
        font_size=mat.get("font_size"),
        target_start_us=int(tgt.get("start", 0)), target_duration_us=int(tgt.get("duration", 0)),
    )
    for ref_id in seg.get("extra_material_refs") or []:
        rcat, rmat = mats.get(ref_id, ("", {}))
        if rcat == "material_animations":
            for a in rmat.get("animations") or []:
                slot.animations.append({"type": a.get("type"), "name": a.get("name"), "duration_us": a.get("duration")})

    parsed = parse_text_content(mat)
    reasons = []
    if parsed is None:
        reasons.append("content is not the JSON form")
    else:
        slot.text = str(parsed.get("text", ""))
        slot.style_count = len(parsed.get("styles") or [])
        # Heuristic: allow modest growth over the template's own length.
        slot.max_chars = max(8, int(utf16_len(slot.text) * 1.3))
        if slot.style_count > 1:
            reasons.append(f"{slot.style_count} style ranges (set-text only rewrites styles[0])")
    if seg.get("group_id"):
        reasons.append("group_id set")
    if reasons:
        slot.status, slot.lock_reasons = "locked", reasons
    return slot


def build_manifest(template_dir: Path) -> Manifest:
    doc = load_doc(template_dir)
    mats = material_index(doc)
    users = material_users(doc)
    media: list[MediaSlot] = []
    text: list[TextSlot] = []
    other: list[dict[str, Any]] = []
    for ref in iter_segments(doc):
        if ref.track_type in MEDIA_TRACKS:
            media.append(_media_slot(ref, mats, users, z=ref.track_index))
        elif ref.track_type in TEXT_TRACKS:
            text.append(_text_slot(ref, mats))
        else:
            other.append({"slot_id": ref.id, "track_type": ref.track_type, "track_name": ref.track_name})

    # Incoming transitions: the previous segment on the same track owns the material.
    for track in doc.get("tracks", []):
        segs = track.get("segments", [])
        by_id = {s.slot_id: s for s in media}
        for prev, nxt in zip(segs, segs[1:]):
            p, n = by_id.get(prev["id"]), by_id.get(nxt["id"])
            if p and n and p.transition_out:
                n.transition_in = p.transition_out

    return Manifest(
        template_dir=str(template_dir), fps=float(doc.get("fps") or 30), duration_us=int(doc.get("duration") or 0),
        canvas=doc.get("canvas_config") or {}, media=media, text=text, locked_other=other,
    )


def manifest_from_json(data: dict[str, Any]) -> Manifest:
    media = []
    for m in data["media"]:
        m = dict(m)
        m["keyframes"] = [Keyframe(**k) for k in m.get("keyframes", [])]
        for key in ("transition_out", "transition_in"):
            m[key] = Transition(**m[key]) if m.get(key) else None
        m["scale"] = tuple(m["scale"])
        m["transform"] = tuple(m["transform"])
        media.append(MediaSlot(**m))
    text = [TextSlot(**t) for t in data["text"]]
    return Manifest(data["template_dir"], data["fps"], data["duration_us"], data["canvas"], media, text, data.get("locked_other", []))
