import pytest
from conftest import MEDIA_SLOT_A, MEDIA_SLOT_MASK, MEDIA_SLOT_PIP, TEXT_SLOT_MULTI, TEXT_SLOT_OK

from capcut_recreate.plan import Plan, validate_plan


def good_plan() -> Plan:
    return Plan.from_json({
        "job_name": "job",
        "media": [{"slot_id": MEDIA_SLOT_A, "clip_id": "c00", "scene_id": "s0"},
                  {"slot_id": MEDIA_SLOT_PIP, "clip_id": "c02", "scene_id": "s0"}],
        "text": [{"slot_id": TEXT_SLOT_OK, "new_text": "Welcome back"}],
    })


def clip_ids(index):
    # stage_footage sorts by filename: beach_wide (8s), city_night (2s), other_short2s (3s)
    return {c.original_name: c.clip_id for c in index.clips}


def test_good_plan_resolves(manifest, footage_index):
    errors, resolved = validate_plan(good_plan(), manifest, footage_index)
    assert errors == []
    assert {r.slot_id for r in resolved} == {MEDIA_SLOT_A, MEDIA_SLOT_PIP}
    a = next(r for r in resolved if r.slot_id == MEDIA_SLOT_A)
    assert a.source_duration_us == 7_500_000 and a.in_point_us == 0


def test_clip_too_short(manifest, footage_index):
    p = good_plan()
    p.media[0].clip_id = "c01"  # 2s clip into a slot that needs 7.5s of source
    errors, _ = validate_plan(p, manifest, footage_index)
    assert any("needs 7.50s" in e for e in errors)


def test_locked_slot_rejected(manifest, footage_index):
    p = good_plan()
    p.media.append(type(p.media[0])(MEDIA_SLOT_MASK, "c01", "s0"))
    errors, _ = validate_plan(p, manifest, footage_index)
    assert any(MEDIA_SLOT_MASK in e and "locked" in e for e in errors)


def test_unfilled_replaceable_slot(manifest, footage_index):
    p = good_plan()
    p.media = p.media[:1]
    errors, _ = validate_plan(p, manifest, footage_index)
    assert any(MEDIA_SLOT_PIP in e and "not filled" in e for e in errors)


def test_multi_style_text_rejected(manifest, footage_index):
    p = good_plan()
    p.text.append(type(p.text[0])(TEXT_SLOT_MULTI, "x"))
    errors, _ = validate_plan(p, manifest, footage_index)
    assert any(TEXT_SLOT_MULTI in e and "locked" in e for e in errors)


def test_text_too_long(manifest, footage_index):
    p = good_plan()
    p.text[0].new_text = "x" * 200
    errors, _ = validate_plan(p, manifest, footage_index)
    assert any("exceeds heuristic max" in e for e in errors)


@pytest.mark.parametrize("bad", ["", "../x", ".hidden", "a/b"])
def test_bad_job_name(manifest, footage_index, bad):
    p = good_plan()
    p.job_name = bad
    errors, _ = validate_plan(p, manifest, footage_index)
    assert any("job_name" in e for e in errors)


def test_orientation_mismatch_is_a_warning(manifest, footage_index):
    from capcut_recreate.plan import validate_plan_full

    manifest.canvas = {"width": 1080, "height": 1920}  # portrait canvas, fixture clips are landscape
    errors, warnings, resolved = validate_plan_full(good_plan(), manifest, footage_index)
    assert errors == []
    assert any("orientation" in w for w in warnings)
    assert len(resolved) == 2
