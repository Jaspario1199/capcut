"""Phase 3 with an LLM: ask Claude for a plan, validate, retry with errors.

The model sees a compact view of the manifest and footage index, thumbnails
when available, the brief, and up to k worked examples from the library with
the operator's corrections. It answers with JSON constrained to PLAN_SCHEMA.
Our validator, not the model, decides whether the plan is applicable; on
errors the model gets the list back and tries again.

The client is injected so tests run without credentials. Real use:

    from anthropic import Anthropic
    plan, attempts = plan_with_claude(manifest, index, brief, examples, client=Anthropic())
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from .footage import FootageIndex
from .library import JobRecord
from .manifest import Manifest
from .plan import Plan, validate_plan

MODEL = "claude-opus-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "job_name": {"type": "string"},
        "rationale": {"type": "string"},
        "media": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"slot_id": {"type": "string"}, "clip_id": {"type": "string"}, "scene_id": {"type": "string"}},
                "required": ["slot_id", "clip_id", "scene_id"],
                "additionalProperties": False,
            },
        },
        "text": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"slot_id": {"type": "string"}, "new_text": {"type": "string"}},
                "required": ["slot_id", "new_text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["job_name", "rationale", "media", "text"],
    "additionalProperties": False,
}

SYSTEM = """You fill slots in a CapCut video template with new footage and text.

The template's timeline is fixed: every slot keeps its position, duration, speed, keyframes, transitions and effects. You only decide which new clip and which scene start fills each replaceable media slot, and what each replaceable text slot says. Locked slots are shown for context and must not be referenced.

Rules the validator enforces, so obey them or the plan is rejected:
- Fill every replaceable media slot exactly once. Never reference a locked slot.
- scene start + slot source duration + transition pad must fit inside the clip. Prefer scenes with room to spare.
- Full-frame slots need a clip whose orientation matches the canvas.
- Text must stay within max_chars for its slot.
- Keep the meaning and rhythm of the original text: a hook stays a hook, a caption stays a caption.

Match the role of each slot: what the template showed at that point (thumbnail, keyframe motion, picture-in-picture vs full frame, whether its audio is audible) should be echoed by the clip you choose. When worked examples with operator corrections are given, treat the corrections as the operator's taste and follow them.

Answer with the JSON plan only."""


def _compact_manifest(m: Manifest) -> dict[str, Any]:
    media = []
    for s in m.media:
        media.append({
            "slot_id": s.slot_id, "status": s.status, "lock_reasons": s.lock_reasons, "track": s.track_name,
            "at_s": round(s.target_start_us / 1e6, 2), "duration_s": round(s.target_duration_us / 1e6, 2),
            "source_needed_s": round((s.source_duration_us + s.transition_pad_us) / 1e6, 2), "speed": s.speed,
            "full_frame": s.full_frame, "scale": s.scale, "position": s.transform, "volume": s.volume,
            "keyframes": [{"property": k.property, "from": k.from_value, "to": k.to_value} for k in s.keyframes],
            "animations": [a.get("name") for a in s.animations if a.get("name")],
            "transition_out": s.transition_out.name if s.transition_out else None,
            "thumbnails": getattr(s, "thumbnails", None) or [],
        })
    text = [{"slot_id": t.slot_id, "status": t.status, "lock_reasons": t.lock_reasons, "current_text": t.text,
             "max_chars": t.max_chars, "at_s": round(t.target_start_us / 1e6, 2), "duration_s": round(t.target_duration_us / 1e6, 2)}
            for t in m.text]
    return {"canvas": m.canvas, "fps": m.fps, "duration_s": round(m.duration_us / 1e6, 2), "media": media, "text": text}


def _compact_index(idx: FootageIndex) -> dict[str, Any]:
    return {"clips": [{
        "clip_id": c.clip_id, "name": c.original_name, "duration_s": round(c.duration_us / 1e6, 2),
        "size": f"{c.width}x{c.height}", "has_audio": c.has_audio,
        "scenes": [{"scene_id": s.scene_id, "start_s": round(s.start_us / 1e6, 2), "end_s": round(s.end_us / 1e6, 2),
                    "thumbnail": getattr(s, "thumbnail", None)} for s in c.scenes],
    } for c in idx.clips]}


def _example_block(rec: JobRecord) -> dict[str, Any]:
    return {
        "brief": rec.brief,
        "plan": {"media": rec.plan.get("media", []), "text": rec.plan.get("text", [])},
        "operator_corrections": [{"slot_id": c.slot_id, "kind": c.kind, "planned": c.planned, "actual": c.actual, "note": c.note}
                                 for c in rec.corrections],
        "feedback": rec.feedback,
    }


def _image_block(path: str) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    data = base64.standard_b64encode(p.read_bytes()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def build_messages(manifest: Manifest, index: FootageIndex, brief: str, examples: list[JobRecord],
                   include_images: bool = True) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    cm, ci = _compact_manifest(manifest), _compact_index(index)
    content.append({"type": "text", "text": "BRIEF:\n" + (brief or "(none)")})
    if examples:
        content.append({"type": "text", "text": "WORKED EXAMPLES (same or similar template; corrections are the operator's taste):\n" +
                        json.dumps([_example_block(e) for e in examples], ensure_ascii=False)})
    content.append({"type": "text", "text": "TEMPLATE SLOTS:\n" + json.dumps(cm, ensure_ascii=False)})
    if include_images:
        for s in cm["media"]:
            for t in s["thumbnails"]:
                img = _image_block(t)
                if img:
                    content.append({"type": "text", "text": f"template slot {s['slot_id']} thumbnail {Path(t).name}"})
                    content.append(img)
    content.append({"type": "text", "text": "NEW FOOTAGE:\n" + json.dumps(ci, ensure_ascii=False)})
    if include_images:
        for c in ci["clips"]:
            for s in c["scenes"]:
                if s.get("thumbnail"):
                    img = _image_block(s["thumbnail"])
                    if img:
                        content.append({"type": "text", "text": f"clip {c['clip_id']} scene {s['scene_id']} thumbnail"})
                        content.append(img)
    content.append({"type": "text", "text": "Produce the plan now."})
    return [{"role": "user", "content": content}]


def export_prompt(manifest: Manifest, index: FootageIndex, brief: str, examples: list[JobRecord],
                  job_name: str) -> dict[str, Any]:
    """Everything a planner needs, as plain text plus image paths, for use without the API.

    A Claude Code session (or any LLM with file access) reads `prompt` and opens
    the listed thumbnails, then writes a plan.json matching `schema`.
    """
    messages = build_messages(manifest, index, brief, examples, include_images=False)
    text_parts = [f"job_name must be exactly: {job_name}"]
    text_parts += [b["text"] for b in messages[0]["content"] if b["type"] == "text"]
    images = []
    for s in manifest.media:
        for t in s.thumbnails:
            images.append({"for": f"template slot {s.slot_id}", "path": t})
    for c in index.clips:
        for sc in c.scenes:
            if sc.thumbnail:
                images.append({"for": f"clip {c.clip_id} scene {sc.scene_id}", "path": sc.thumbnail})
    return {"system": SYSTEM, "prompt": "\n\n".join(text_parts), "images": images, "schema": PLAN_SCHEMA,
            "job_name": job_name}


def _extract_text(response: Any) -> str:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise ValueError("no text block in response")


def plan_with_claude(manifest: Manifest, index: FootageIndex, brief: str, examples: list[JobRecord], *,
                     client: Any, job_name: str, model: str = MODEL, max_attempts: int = 3,
                     effort: str = "high", use_fallbacks: bool = True,
                     include_images: bool = True) -> tuple[Plan | None, list[dict[str, Any]]]:
    """Returns (plan or None, attempt log). A None plan means every attempt failed validation."""
    messages = build_messages(manifest, index, brief, examples, include_images=include_images)
    messages[0]["content"].insert(0, {"type": "text", "text": f"job_name must be exactly: {job_name}"})
    attempts: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = dict(
        model=model, max_tokens=16000, system=SYSTEM, messages=messages,
        output_config={"format": {"type": "json_schema", "schema": PLAN_SCHEMA}, "effort": effort},
    )
    if use_fallbacks:
        kwargs["betas"] = [FALLBACK_BETA]
        kwargs["fallbacks"] = "default"

    for i in range(max_attempts):
        response = client.beta.messages.create(**kwargs)
        if getattr(response, "stop_reason", None) == "refusal":
            attempts.append({"attempt": i + 1, "refusal": True})
            return None, attempts
        text = _extract_text(response)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            errors = [f"response was not valid JSON: {e}"]
            data = None
        if data is not None:
            data["job_name"] = job_name
            plan = Plan.from_json({k: v for k, v in data.items() if k in ("job_name", "media", "text")})
            errors, _ = validate_plan(plan, manifest, index)
        attempts.append({"attempt": i + 1, "response": text, "errors": errors})
        if not errors:
            return plan, attempts
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": "The validator rejected that plan:\n- " + "\n- ".join(errors) +
                         "\nFix every error and answer with the corrected JSON plan only."})
    return None, attempts
