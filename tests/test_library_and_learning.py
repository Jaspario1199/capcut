"""Library retrieval, corrections capture, thumbnails, and the planner loop with a fake client."""

import copy
import json
import types
from pathlib import Path

import pytest
from conftest import MEDIA_SLOT_PHOTO, FOOTAGE, MEDIA_SLOT_A, MEDIA_SLOT_PIP, TEMPLATE, TEXT_SLOT_OK, requires_cli

from capcut_recreate.corrections import capture
from capcut_recreate.footage import stage_footage
from capcut_recreate.library import Correction, JobRecord, Library, template_fingerprint
from capcut_recreate.manifest import build_manifest
from capcut_recreate.plan import Plan, validate_plan
from capcut_recreate.planner import PLAN_SCHEMA, build_messages, plan_with_claude

GOOD_PLAN = {
    "job_name": "job",
    "media": [{"slot_id": MEDIA_SLOT_A, "clip_id": "c00", "scene_id": "s0"},
              {"slot_id": MEDIA_SLOT_PIP, "clip_id": "c02", "scene_id": "s0"},
              {"slot_id": MEDIA_SLOT_PHOTO, "clip_id": "c03", "scene_id": "s0"}],
    "text": [{"slot_id": TEXT_SLOT_OK, "new_text": "Welcome back"}],
}


def seg(doc, sid):
    for t in doc["tracks"]:
        for s in t["segments"]:
            if s["id"] == sid:
                return s
    raise KeyError(sid)


# ---------------------------------------------------------------- fingerprint / library

def test_fingerprint_ignores_ids_and_paths(template_doc):
    a = copy.deepcopy(template_doc)
    a["id"] = "other"
    a["name"] = "renamed"
    for m in a["materials"]["videos"]:
        m["path"] = "/elsewhere/" + m["path"]
    assert template_fingerprint(a) == template_fingerprint(template_doc)
    b = copy.deepcopy(template_doc)
    seg(b, MEDIA_SLOT_A)["target_timerange"]["duration"] += 1_000_000
    assert template_fingerprint(b) != template_fingerprint(template_doc)


def _record(job_id, template_doc, corrections=None, created=1.0, canvas=None):
    return JobRecord(job_id=job_id, created_at=created, template_fingerprint=template_fingerprint(template_doc),
                     template_dir="t", job_dir=f"/jobs/{job_id}", canvas=canvas or template_doc["canvas_config"],
                     slot_count=6, brief="b", manifest={}, footage_index={}, plan=GOOD_PLAN,
                     corrections=corrections or [])


def test_library_roundtrip_and_ranking(tmp_path, template_doc):
    lib = Library(tmp_path / "lib")
    other_doc = copy.deepcopy(template_doc)
    seg(other_doc, MEDIA_SLOT_A)["target_timerange"]["duration"] += 1_000_000
    lib.save(_record("same-plain", template_doc, created=3.0))
    lib.save(_record("same-corrected", template_doc, [Correction(MEDIA_SLOT_A, "media_clip", "a", "b")], created=1.0))
    lib.save(_record("other-tpl", other_doc, created=5.0))
    lib.save(_record("portrait", other_doc, created=6.0, canvas={"width": 1080, "height": 1920}))

    got = [r.job_id for r in lib.find_examples(template_doc, k=3)]
    assert got == ["same-corrected", "same-plain", "other-tpl"]
    assert lib.load("same-corrected").corrections[0].kind == "media_clip"
    assert lib.find_by_job_dir(Path("/jobs/portrait")).job_id == "portrait"


# ---------------------------------------------------------------- corrections

def _applied_record(template_doc, tmp_path):
    written = copy.deepcopy(template_doc)
    # simulate what apply wrote: new clip on slot A and new text
    seg(written, MEDIA_SLOT_A)["source_timerange"]["start"] = 0
    vm = next(m for m in written["materials"]["videos"] if m["id"] == "mat-video-01")
    vm["path"] = str(tmp_path / "job" / "assets" / "video" / "10ef_beach_wide.mp4")
    tm = next(m for m in written["materials"]["texts"] if m["id"] == "mat-text-01")
    c = json.loads(tm["content"])
    c["text"] = "Welcome back"
    c["styles"][0]["range"] = [0, 12]
    tm["content"] = json.dumps(c)
    rec = _record("job", template_doc)
    rec.written_doc = written
    rec.job_dir = str(tmp_path / "job")
    return rec, written


def _write_job(tmp_path, doc):
    d = tmp_path / "job"
    d.mkdir(exist_ok=True)
    (d / "draft_content.json").write_text(json.dumps(doc))
    return d


def test_capture_no_edit_yields_nothing(template_doc, tmp_path):
    rec, written = _applied_record(template_doc, tmp_path)
    job = _write_job(tmp_path, written)
    corrections, rewrites = capture(job, rec)
    assert corrections == [] and rewrites == []


def test_capture_detects_inpoint_text_and_clip(template_doc, tmp_path):
    rec, written = _applied_record(template_doc, tmp_path)
    edited = copy.deepcopy(written)
    seg(edited, MEDIA_SLOT_A)["source_timerange"]["start"] = 400_000  # operator nudged in-point
    tm = next(m for m in edited["materials"]["texts"] if m["id"] == "mat-text-01")
    c = json.loads(tm["content"])
    c["text"] = "Welcome back!"
    c["styles"][0]["range"] = [0, 13]
    tm["content"] = json.dumps(c)
    vm = next(m for m in edited["materials"]["videos"] if m["id"] == "mat-video-01")
    vm["path"] = str(tmp_path / "job" / "assets" / "video" / "sunset.mp4")  # swapped clip
    edited["duration"] = 10_000_001  # app bookkeeping
    seg(edited, MEDIA_SLOT_PIP)["target_timerange"]["duration"] = 2_000_000 + 10  # frame-grid rounding

    job = _write_job(tmp_path, edited)
    corrections, rewrites = capture(job, rec)
    kinds = {(c.slot_id, c.kind) for c in corrections}
    assert (MEDIA_SLOT_A, "media_inpoint") in kinds
    assert (MEDIA_SLOT_A, "media_clip") in kinds
    assert (TEXT_SLOT_OK, "text") in kinds
    text_c = next(c for c in corrections if c.kind == "text")
    assert text_c.planned == "Welcome back" and text_c.actual == "Welcome back!"
    assert "duration" in rewrites
    assert any(MEDIA_SLOT_PIP in r and "target_timerange.duration" in r for r in rewrites)
    assert not any(c.slot_id == MEDIA_SLOT_PIP for c in corrections)


# ---------------------------------------------------------------- thumbnails

@requires_cli
def test_thumbnails_extracted(tmp_path):
    m = build_manifest(TEMPLATE, thumbs_dir=tmp_path / "thumbs")
    a = m.media_by_id()[MEDIA_SLOT_A]
    assert len(a.thumbnails) == 2 and all(Path(t).exists() and Path(t).stat().st_size > 0 for t in a.thumbnails)
    idx = stage_footage(sorted(FOOTAGE.glob("*.mp4")), tmp_path / "stage")
    assert all(c.scenes[0].thumbnail and Path(c.scenes[0].thumbnail).exists() for c in idx.clips)
    msgs = build_messages(m, idx, "brief", [])
    kinds = [b["type"] for b in msgs[0]["content"]]
    assert kinds.count("image") == sum(len(s.thumbnails) for s in m.media) + len(idx.clips)


# ---------------------------------------------------------------- planner with a fake client

class _Resp:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [types.SimpleNamespace(type="text", text=text)]
        self.stop_reason = stop_reason


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp(self.replies.pop(0))


def test_planner_retries_with_validator_errors(manifest, footage_index):
    bad = dict(GOOD_PLAN, rationale="", media=[{"slot_id": MEDIA_SLOT_A, "clip_id": "c01", "scene_id": "s0"}])  # too short + missing PIP
    good = dict(GOOD_PLAN, rationale="")
    client = FakeClient([json.dumps(bad), json.dumps(good)])
    plan, attempts = plan_with_claude(manifest, footage_index, "beach reel", [], client=client, job_name="job",
                                      include_images=False)
    assert plan is not None and len(attempts) == 2
    assert attempts[0]["errors"] and not attempts[1]["errors"]
    second = client.calls[1]["messages"]
    assert second[-2]["role"] == "assistant" and "rejected" in second[-1]["content"]
    assert client.calls[0]["output_config"]["format"]["schema"] == PLAN_SCHEMA
    assert client.calls[0]["model"] == "claude-opus-5" and client.calls[0]["fallbacks"] == "default"
    errors, _ = validate_plan(plan, manifest, footage_index)
    assert errors == []


def test_planner_gives_up_and_reports(manifest, footage_index):
    bad = json.dumps(dict(GOOD_PLAN, rationale="", media=[]))
    client = FakeClient([bad, bad, bad])
    plan, attempts = plan_with_claude(manifest, footage_index, "x", [], client=client, job_name="job", include_images=False)
    assert plan is None and len(attempts) == 3


def test_planner_prompt_carries_examples_and_job_name(manifest, footage_index, template_doc):
    rec = _record("prev", template_doc, [Correction(TEXT_SLOT_OK, "text", "Welcome back", "Welcome back, friends")])
    rec.brief = "previous beach reel"
    msgs = build_messages(manifest, footage_index, "new brief", [rec], include_images=False)
    text = "\n".join(b["text"] for b in msgs[0]["content"] if b["type"] == "text")
    assert "previous beach reel" in text and "Welcome back, friends" in text and "WORKED EXAMPLES" in text
    assert "locked" in text and MEDIA_SLOT_PIP in text


@requires_cli
def test_apply_records_to_library(manifest, footage_index, store, template_dir, tmp_path):
    from capcut_recreate.apply import apply_plan

    lib = Library(tmp_path / "lib")
    plan = Plan.from_json(GOOD_PLAN)
    _, resolved = validate_plan(plan, manifest, footage_index)
    report = apply_plan(plan, manifest, resolved, store, template_dir=template_dir, library=lib,
                        footage_index=footage_index.to_json(), brief="beach", force_write=True)
    assert report.ok and report.library_job_id
    rec = lib.find_by_job_dir(Path(report.job_dir))
    assert rec is not None and rec.written_doc and rec.brief == "beach"
    corrections, rewrites = capture(Path(report.job_dir), rec)
    assert corrections == [] and rewrites == []


def test_export_prompt_lists_images_and_slots(manifest, footage_index, template_doc):
    from capcut_recreate.planner import export_prompt

    rec = _record("prev", template_doc, [Correction(TEXT_SLOT_OK, "text", "a", "b")])
    out = export_prompt(manifest, footage_index, "brief here", [rec], job_name="jobx")
    assert out["job_name"] == "jobx" and out["schema"] == PLAN_SCHEMA
    assert out["prompt"].startswith("job_name must be exactly: jobx")
    assert "brief here" in out["prompt"] and MEDIA_SLOT_A in out["prompt"] and "WORKED EXAMPLES" in out["prompt"]
    assert all(set(i) == {"for", "path"} for i in out["images"])
    assert len(out["images"]) == sum(len(s.thumbnails) for s in manifest.media) + sum(
        1 for c in footage_index.clips for s in c.scenes if s.thumbnail)


def test_capture_labels_split_and_trim(template_doc, tmp_path):
    rec, written = _applied_record(template_doc, tmp_path)
    edited = copy.deepcopy(written)
    a = seg(edited, MEDIA_SLOT_A)
    a["target_timerange"]["duration"] = 2_000_000
    a["source_timerange"]["duration"] = 3_000_000
    second = copy.deepcopy(a)
    second["id"] = "eeeeeeee-0000-0000-0000-000000000001"
    second["target_timerange"] = {"start": 2_000_000, "duration": 3_000_000}
    second["source_timerange"] = {"start": 3_000_000, "duration": 4_500_000}
    edited["tracks"][0]["segments"].insert(1, second)
    job = _write_job(tmp_path, edited)
    corrections, rewrites = capture(job, rec)
    kinds = sorted((c.slot_id[:8], c.kind) for c in corrections)
    assert ("aaaaaa01", "media_trim") in kinds
    assert ("eeeeeeee", "split") in kinds
    assert not any("eeeeeeee" in r for r in rewrites)


def test_chunking_and_merge(manifest, footage_index):
    from capcut_recreate.planner import chunk_manifest, merge_plans

    chunks = chunk_manifest(manifest, max_media=1)
    assert len(chunks) == 3  # replaceable media slots at 0s, 3s (photo) and 7s
    all_media = sorted(s.slot_id for c in chunks for s in c.media)
    all_text = sorted(s.slot_id for c in chunks for s in c.text)
    assert all_media == sorted(s.slot_id for s in manifest.media)
    assert all_text == sorted(s.slot_id for s in manifest.text)
    assert chunks[0].media_by_id()[MEDIA_SLOT_A] and chunks[2].media_by_id()[MEDIA_SLOT_PIP]
    assert chunks[1].media_by_id()[MEDIA_SLOT_PHOTO]
    # locked mask slot at 5s rides with the window that starts at 3s
    from conftest import MEDIA_SLOT_MASK
    assert MEDIA_SLOT_MASK in chunks[1].media_by_id()

    p0 = Plan.from_json({"job_name": "j", "media": [{"slot_id": MEDIA_SLOT_A, "clip_id": "c00", "scene_id": "s0"}],
                         "text": [{"slot_id": TEXT_SLOT_OK, "new_text": "Hi there"}]})
    pp = Plan.from_json({"job_name": "j", "media": [{"slot_id": MEDIA_SLOT_PHOTO, "clip_id": "c03", "scene_id": "s0"}],
                         "text": []})
    p1 = Plan.from_json({"job_name": "j", "media": [{"slot_id": MEDIA_SLOT_PIP, "clip_id": "c02", "scene_id": "s0"}],
                         "text": []})
    assert validate_plan(p0, chunks[0], footage_index)[0] == []
    assert validate_plan(pp, chunks[1], footage_index)[0] == []
    assert validate_plan(p1, chunks[2], footage_index)[0] == []
    merged = merge_plans([p0, pp, p1], "j")
    assert validate_plan(merged, manifest, footage_index)[0] == []
    assert len(merged.media) == 3 and len(merged.text) == 1
