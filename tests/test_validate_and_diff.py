import pytest
import copy

from conftest import MEDIA_SLOT_A

from capcut_recreate.diffing import diff
from capcut_recreate.validate import new_invariant_errors, validate_doc


def seg(doc, sid):
    for t in doc["tracks"]:
        for s in t["segments"]:
            if s["id"] == sid:
                return s
    raise KeyError(sid)


def test_template_is_valid(template_doc):
    assert validate_doc(template_doc) == []


def test_source_overrun_detected(template_doc):
    d = copy.deepcopy(template_doc)
    seg(d, MEDIA_SLOT_A)["source_timerange"]["start"] = 2_000_000  # 2 + 7.5 > 8
    errs = validate_doc(d)
    assert any("exceeds material" in e for e in errs)


def test_speed_invariant_detected(template_doc):
    d = copy.deepcopy(template_doc)
    seg(d, MEDIA_SLOT_A)["source_timerange"]["duration"] = 6_000_000  # target 5s x 1.5 = 7.5s
    assert any("x speed" in e for e in validate_doc(d))


def test_one_frame_tolerance(template_doc):
    d = copy.deepcopy(template_doc)
    seg(d, MEDIA_SLOT_A)["source_timerange"]["duration"] = 7_500_000 + 30_000  # < one frame at 30fps
    assert validate_doc(d) == []


def test_keyframe_past_end(template_doc):
    d = copy.deepcopy(template_doc)
    seg(d, MEDIA_SLOT_A)["common_keyframes"][0]["keyframe_list"][-1]["time_offset"] = 9_000_000
    assert any("keyframe" in e for e in validate_doc(d))


def test_style_range_outside_text(template_doc):
    import json

    d = copy.deepcopy(template_doc)
    m = next(m for m in d["materials"]["texts"] if m["id"] == "mat-text-01")
    c = json.loads(m["content"])
    c["styles"][0]["range"] = [0, 99]
    m["content"] = json.dumps(c)
    assert any("style range" in e for e in validate_doc(d))


def test_dangling_ref(template_doc):
    d = copy.deepcopy(template_doc)
    seg(d, MEDIA_SLOT_A)["extra_material_refs"].append("nope")
    assert any("dangling" in e for e in validate_doc(d))


def test_diff_keys_by_id_and_sees_deep_fields(template_doc):
    d = copy.deepcopy(template_doc)
    seg(d, MEDIA_SLOT_A)["source_timerange"]["start"] = 500_000
    seg(d, MEDIA_SLOT_A)["clip"]["scale"]["x"] = 2.0
    d["tracks"].reverse()  # reorder must not produce a full rewrite
    changes = diff(template_doc, d).changes
    paths = {c.path for c in changes}
    assert f"tracks[track-video-01].segments[{MEDIA_SLOT_A}].source_timerange.start" in paths
    assert f"tracks[track-video-01].segments[{MEDIA_SLOT_A}].clip.scale.x" in paths
    assert len(changes) == 2


def test_diff_allowlist(template_doc):
    d = copy.deepcopy(template_doc)
    d["id"] = "other"
    seg(d, MEDIA_SLOT_A)["clip"]["scale"]["x"] = 2.0
    res = diff(template_doc, d)
    left = res.filtered([r"^id$"])
    assert len(left) == 1 and left[0].path.endswith("clip.scale.x")


def test_placeholder_media_paths(tmp_path):
    from pathlib import Path

    from capcut_recreate.apply import ApplyError, _check_relink
    from capcut_recreate.draft import is_placeholder_path, resolve_media_path

    token = "##_draftpath_placeholder_0E685133-18CE-45ED-8CB8-2904A212EC80_##"
    assert is_placeholder_path(token + "/video/a.mov")
    assert is_placeholder_path(token + "\\video\\a.mov")
    assert not is_placeholder_path("/abs/a.mov") and not is_placeholder_path("assets/video/a.mov")
    assert resolve_media_path(token + "/video/a.mov", tmp_path) == tmp_path / "video" / "a.mov"
    assert resolve_media_path("assets/video/a.mov", tmp_path) == tmp_path / "assets" / "video" / "a.mov"

    (tmp_path / "video").mkdir()
    (tmp_path / "video" / "a.mov").write_bytes(b"x")
    doc = {"materials": {"videos": [{"id": "m1", "path": token + "/video/a.mov"}], "audios": []}}
    _check_relink(doc, doc, tmp_path)  # placeholder resolving inside the job is fine
    missing = {"materials": {"videos": [{"id": "m1", "path": token + "/video/b.mov"}], "audios": []}}
    with pytest.raises(ApplyError, match="does not resolve"):
        _check_relink(missing, missing, tmp_path)


def test_template_errors_are_not_blamed_on_apply(template_doc):
    broken = copy.deepcopy(template_doc)
    seg = next(s for t in broken["tracks"] for s in t["segments"] if s["id"] == MEDIA_SLOT_A)
    seg["common_keyframes"] = [{"property_type": "KFTypeVolume", "keyframe_list": [{"time_offset": 10**9}]}]
    assert validate_doc(broken)
    # same defect copied verbatim into the new draft: not an apply error
    assert new_invariant_errors(broken, copy.deepcopy(broken)) == []
    # a defect that only the new draft has still fails
    worse = copy.deepcopy(broken)
    seg2 = next(s for t in worse["tracks"] for s in t["segments"] if s["id"] == MEDIA_SLOT_A)
    seg2["source_timerange"]["duration"] += 10**7
    assert new_invariant_errors(broken, worse)
