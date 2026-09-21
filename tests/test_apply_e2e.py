"""End-to-end against the real capcut-cli binary. Skipped when it is absent."""

import json
import shutil
from pathlib import Path

import pytest
from conftest import MEDIA_SLOT_A, MEDIA_SLOT_MASK, MEDIA_SLOT_PIP, TEXT_SLOT_OK, requires_cli

from capcut_recreate.apply import ApplyError, apply_plan
from capcut_recreate.draft import load_doc
from capcut_recreate.footage import md5_file
from capcut_recreate.plan import Plan, validate_plan

pytestmark = requires_cli


def plan(job="job", clip_a="c00", clip_pip="c02", scene="s0"):
    return Plan.from_json({
        "job_name": job,
        "media": [{"slot_id": MEDIA_SLOT_A, "clip_id": clip_a, "scene_id": scene},
                  {"slot_id": MEDIA_SLOT_PIP, "clip_id": clip_pip, "scene_id": scene}],
        "text": [{"slot_id": TEXT_SLOT_OK, "new_text": "Welcome back"}],
    })


def seg(doc, sid):
    for t in doc["tracks"]:
        for s in t["segments"]:
            if s["id"] == sid:
                return s
    raise KeyError(sid)


def test_apply_roundtrip(manifest, footage_index, store, template_dir):
    p = plan()
    errors, resolved = validate_plan(p, manifest, footage_index)
    assert errors == []
    report = apply_plan(p, manifest, resolved, store, template_dir=template_dir)
    assert report.ok, (report.invariant_errors, report.diff_violations)
    assert report.batch["succeeded"] == 3

    job = Path(report.job_dir)
    doc = load_doc(job)
    tpl = load_doc(template_dir)
    assert doc["id"] != tpl["id"] and report.draft_id == doc["id"]

    # replaced slot keeps its timeline placement and speed invariant
    a = seg(doc, MEDIA_SLOT_A)
    assert a["target_timerange"] == {"start": 0, "duration": 5_000_000}
    assert a["source_timerange"] == {"start": 0, "duration": 7_500_000}
    assert a["speed"] == 1.5
    assert a["common_keyframes"] == seg(tpl, MEDIA_SLOT_A)["common_keyframes"]
    mat = next(m for m in doc["materials"]["videos"] if m["id"] == a["material_id"])
    assert Path(mat["path"]).name.endswith("beach_wide.mp4") and mat["duration"] == 8_000_000
    assert md5_file(Path(mat["path"])) == footage_index.by_id()["c00"].md5

    # locked slot byte-identical apart from nothing
    assert seg(doc, MEDIA_SLOT_MASK) == seg(tpl, MEDIA_SLOT_MASK)

    # text replaced with a full-span style range
    t = next(m for m in doc["materials"]["texts"] if m["id"] == "mat-text-01")
    c = json.loads(t["content"])
    assert c["text"] == "Welcome back" and c["styles"][0]["range"] == [0, 12]

    # every media path is absolute, inside the job, and registered
    for cat in ("videos", "audios"):
        for m in doc["materials"][cat]:
            assert Path(m["path"]).is_absolute() and Path(m["path"]).exists()
            assert str(job) in m["path"]
            assert m.get("local_material_id")
    meta = json.loads((job / "draft_meta_info.json").read_text())
    registered = {Path(v["file_Path"]).name for g in meta["draft_materials"] for v in g["value"]}
    assert Path(mat["path"]).name in registered

    root = json.loads((store / "root_meta_info.json").read_text())
    assert [e["draft_name"] for e in root["all_draft_store"]] == ["job"]


def test_basename_collision_is_safe(manifest, footage_index, store, template_dir, tmp_path):
    """Two different clips with the same original basename must both land intact."""
    from capcut_recreate.footage import stage_footage

    src_a = tmp_path / "a" / "clip.mp4"
    src_b = tmp_path / "b" / "clip.mp4"
    src_a.parent.mkdir()
    src_b.parent.mkdir()
    shutil.copyfile(Path(manifest.template_dir) / "assets/video/long8s.mp4", src_a)
    shutil.copyfile(next(c.path for c in footage_index.clips if c.original_name == "other_short2s.mp4"), src_b)
    idx = stage_footage([src_a, src_b], tmp_path / "stage2")
    assert len({c.md5 for c in idx.clips}) == 2
    p = plan(job="collide", clip_a=idx.clips[0].clip_id, clip_pip=idx.clips[1].clip_id)
    errors, resolved = validate_plan(p, manifest, idx)
    assert errors == []
    report = apply_plan(p, manifest, resolved, store, template_dir=template_dir)
    assert report.ok
    doc = load_doc(Path(report.job_dir))
    paths = {m["id"]: Path(m["path"]) for m in doc["materials"]["videos"]}
    assert md5_file(paths["mat-video-01"]) == idx.clips[0].md5
    assert md5_file(paths["mat-video-03"]) == idx.clips[1].md5


def test_failed_apply_cleans_up(manifest, footage_index, store, template_dir, monkeypatch):
    from capcut_recreate import apply as apply_mod

    real = apply_mod.runner.run

    def broken(*args, **kw):
        if args and args[0] == "batch":
            raise ApplyError("simulated batch failure")
        return real(*args, **kw)

    monkeypatch.setattr(apply_mod.runner, "run", broken)
    p = plan(job="doomed")
    _, resolved = validate_plan(p, manifest, footage_index)
    with pytest.raises(ApplyError):
        apply_plan(p, manifest, resolved, store, template_dir=template_dir)
    assert not (store / "doomed").exists()
    root = json.loads((store / "root_meta_info.json").read_text())
    assert all(e["draft_name"] != "doomed" for e in root["all_draft_store"])


def test_zero_in_point_serialises_as_string():
    from capcut_recreate.apply import _us_to_s

    assert _us_to_s(0) == "0.000000s"
