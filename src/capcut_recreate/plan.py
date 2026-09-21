"""Phase 3: the plan schema and its validator.

A plan is the only thing a planner (human or LLM) produces:

    {
      "job_name": "beach-v1",
      "media": [{"slot_id": "<segment id>", "clip_id": "c00", "scene_id": "s0"}],
      "text":  [{"slot_id": "<segment id>", "new_text": "..."}]
    }

The in-point is the chosen scene's start. No free-float in-points: a planner
that cannot see the footage frame by frame has no basis for sub-scene precision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .draft import utf16_len
from .footage import FootageIndex
from .manifest import Manifest


@dataclass
class MediaChoice:
    slot_id: str
    clip_id: str
    scene_id: str


@dataclass
class TextChoice:
    slot_id: str
    new_text: str


@dataclass
class Plan:
    job_name: str
    media: list[MediaChoice] = field(default_factory=list)
    text: list[TextChoice] = field(default_factory=list)

    @staticmethod
    def from_json(data: dict[str, Any]) -> "Plan":
        return Plan(
            job_name=str(data.get("job_name", "")),
            media=[MediaChoice(**m) for m in data.get("media", [])],
            text=[TextChoice(**t) for t in data.get("text", [])],
        )


@dataclass
class ResolvedMedia:
    slot_id: str
    clip_path: str
    clip_md5: str
    in_point_us: int
    source_duration_us: int


def validate_plan(plan: Plan, manifest: Manifest, index: FootageIndex) -> tuple[list[str], list[ResolvedMedia]]:
    """Return (errors, resolved media). Empty errors means the plan is applicable."""
    errors: list[str] = []
    resolved: list[ResolvedMedia] = []
    media_slots = manifest.media_by_id()
    text_slots = manifest.text_by_id()
    clips = index.by_id()

    if not plan.job_name or "/" in plan.job_name or plan.job_name.startswith("."):
        errors.append("job_name must be a plain folder name")

    seen: set[str] = set()
    for m in plan.media:
        slot = media_slots.get(m.slot_id)
        if slot is None:
            errors.append(f"media slot {m.slot_id}: not in manifest")
            continue
        if m.slot_id in seen:
            errors.append(f"media slot {m.slot_id}: filled twice")
            continue
        seen.add(m.slot_id)
        if slot.status != "replaceable":
            errors.append(f"media slot {m.slot_id}: locked ({'; '.join(slot.lock_reasons)})")
            continue
        clip = clips.get(m.clip_id)
        if clip is None:
            errors.append(f"media slot {m.slot_id}: unknown clip {m.clip_id}")
            continue
        scene = next((s for s in clip.scenes if s.scene_id == m.scene_id), None)
        if scene is None:
            errors.append(f"media slot {m.slot_id}: clip {m.clip_id} has no scene {m.scene_id}")
            continue
        need = scene.start_us + slot.source_duration_us + slot.transition_pad_us
        if need > clip.duration_us:
            errors.append(
                f"media slot {m.slot_id}: needs {need / 1e6:.2f}s from scene {m.scene_id} start "
                f"({scene.start_us / 1e6:.2f}s) but clip {m.clip_id} is {clip.duration_us / 1e6:.2f}s")
            continue
        if slot.full_frame and clip.width and clip.height:
            cw, ch = manifest.canvas.get("width"), manifest.canvas.get("height")
            if cw and ch:
                canvas_portrait = ch > cw
                clip_portrait = clip.height > clip.width
                if canvas_portrait != clip_portrait:
                    errors.append(f"media slot {m.slot_id}: full-frame slot but clip {m.clip_id} orientation "
                                  f"({clip.width}x{clip.height}) does not match canvas ({cw}x{ch})")
                    continue
        resolved.append(ResolvedMedia(m.slot_id, clip.path, clip.md5, scene.start_us, slot.source_duration_us))

    missing = [s.slot_id for s in manifest.media if s.status == "replaceable" and s.slot_id not in seen]
    for sid in missing:
        errors.append(f"media slot {sid}: replaceable but not filled")

    seen_t: set[str] = set()
    for t in plan.text:
        slot = text_slots.get(t.slot_id)
        if slot is None:
            errors.append(f"text slot {t.slot_id}: not in manifest")
            continue
        if t.slot_id in seen_t:
            errors.append(f"text slot {t.slot_id}: filled twice")
            continue
        seen_t.add(t.slot_id)
        if slot.status != "replaceable":
            errors.append(f"text slot {t.slot_id}: locked ({'; '.join(slot.lock_reasons)})")
            continue
        if not t.new_text.strip():
            errors.append(f"text slot {t.slot_id}: empty text")
            continue
        n = utf16_len(t.new_text)
        if n > slot.max_chars:
            errors.append(f"text slot {t.slot_id}: {n} chars exceeds heuristic max {slot.max_chars}")

    return errors, resolved
