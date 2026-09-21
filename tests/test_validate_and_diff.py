import copy

from conftest import MEDIA_SLOT_A

from capcut_recreate.diffing import diff
from capcut_recreate.validate import validate_doc


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
