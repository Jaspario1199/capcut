"""The example library: what the planner learns from.

There is no model training here. Every job stores its manifest, footage
index, plan, and later the operator's corrections and feedback. When a new
job is planned, the most similar past jobs are shown to the model as worked
examples. Similarity is exact-template first (same fingerprint), then same
canvas ratio and closest slot count.

Layout: <root>/jobs/<job_id>.json, one file per job, rewritten on update.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .draft import iter_segments

DEFAULT_ROOT = Path(os.environ.get("CAPCUT_RECREATE_LIBRARY") or Path.home() / ".capcut-recreate" / "library")


def template_fingerprint(doc: dict[str, Any]) -> str:
    """Stable id for a template's structure, independent of draft id, name, or media paths."""
    parts = []
    for ref in iter_segments(doc):
        tgt = ref.segment.get("target_timerange") or {}
        parts.append((ref.track_type, ref.track_name, int(tgt.get("start", 0)), int(tgt.get("duration", 0)),
                      len(ref.segment.get("common_keyframes") or []), len(ref.segment.get("extra_material_refs") or [])))
    canvas = doc.get("canvas_config") or {}
    payload = json.dumps({"canvas": [canvas.get("width"), canvas.get("height")], "fps": doc.get("fps"), "segments": parts}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass
class Correction:
    slot_id: str
    kind: str  # media_clip | media_inpoint | text | other
    planned: Any
    actual: Any
    note: str = ""


@dataclass
class JobRecord:
    job_id: str
    created_at: float
    template_fingerprint: str
    template_dir: str
    job_dir: str
    canvas: dict[str, Any]
    slot_count: int
    brief: str
    manifest: dict[str, Any]
    footage_index: dict[str, Any]
    plan: dict[str, Any]
    written_doc: dict[str, Any] | None = None
    corrections: list[Correction] = field(default_factory=list)
    app_rewrite_paths: list[str] = field(default_factory=list)
    feedback: dict[str, Any] = field(default_factory=dict)
    learned_at: float | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_json(d: dict[str, Any]) -> "JobRecord":
        d = dict(d)
        d["corrections"] = [Correction(**c) for c in d.get("corrections", [])]
        return JobRecord(**d)


class Library:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or DEFAULT_ROOT)
        self.jobs_dir = self.root / "jobs"

    def _path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def save(self, rec: JobRecord) -> Path:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        p = self._path(rec.job_id)
        p.write_text(json.dumps(rec.to_json(), ensure_ascii=False, indent=1))
        return p

    def load(self, job_id: str) -> JobRecord:
        return JobRecord.from_json(json.loads(self._path(job_id).read_text()))

    def all(self) -> list[JobRecord]:
        if not self.jobs_dir.exists():
            return []
        out = []
        for p in sorted(self.jobs_dir.glob("*.json")):
            try:
                out.append(JobRecord.from_json(json.loads(p.read_text())))
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    def find_by_job_dir(self, job_dir: Path) -> JobRecord | None:
        target = str(Path(job_dir).resolve())
        for rec in self.all():
            if str(Path(rec.job_dir).resolve()) == target:
                return rec
        return None

    def record_apply(self, *, job_id: str, template_doc: dict[str, Any], template_dir: str, job_dir: str,
                     manifest: dict[str, Any], footage_index: dict[str, Any], plan: dict[str, Any],
                     written_doc: dict[str, Any], brief: str = "") -> JobRecord:
        canvas = template_doc.get("canvas_config") or {}
        rec = JobRecord(
            job_id=job_id, created_at=time.time(), template_fingerprint=template_fingerprint(template_doc),
            template_dir=template_dir, job_dir=job_dir, canvas=canvas,
            slot_count=len(manifest.get("media", [])) + len(manifest.get("text", [])),
            brief=brief, manifest=manifest, footage_index=footage_index, plan=plan, written_doc=written_doc,
        )
        self.save(rec)
        return rec

    def record_feedback(self, job_id: str, rating: str, note: str = "") -> JobRecord:
        rec = self.load(job_id)
        rec.feedback = {"rating": rating, "note": note, "at": time.time()}
        self.save(rec)
        return rec

    def find_examples(self, template_doc: dict[str, Any], k: int = 3, exclude_job_id: str | None = None) -> list[JobRecord]:
        fp = template_fingerprint(template_doc)
        canvas = template_doc.get("canvas_config") or {}
        ratio = _ratio(canvas)
        n_slots = sum(1 for _ in iter_segments(template_doc))

        def score(rec: JobRecord) -> tuple:
            same_tpl = rec.template_fingerprint == fp
            same_ratio = _ratio(rec.canvas) == ratio
            informative = bool(rec.corrections) or bool(rec.feedback)
            return (not same_tpl, not informative, not same_ratio, abs(rec.slot_count - n_slots), -rec.created_at)

        cands = [r for r in self.all() if r.job_id != exclude_job_id and r.plan]
        cands.sort(key=score)
        return cands[:k]


def _ratio(canvas: dict[str, Any]) -> str:
    w, h = canvas.get("width"), canvas.get("height")
    if not w or not h:
        return "?"
    return "portrait" if h > w else ("square" if h == w else "landscape")
