from conftest import MEDIA_SLOT_A, MEDIA_SLOT_MASK, MEDIA_SLOT_PIP, TEXT_SLOT_LEGACY, TEXT_SLOT_MULTI, TEXT_SLOT_OK

from capcut_recreate.manifest import Manifest, manifest_from_json


def test_counts(manifest: Manifest):
    assert manifest.counts() == {"media_replaceable": 3, "media_locked": 1, "text_replaceable": 1, "text_locked": 2}


def test_speed_slot_carries_timing_and_keyframes(manifest: Manifest):
    s = manifest.media_by_id()[MEDIA_SLOT_A]
    assert s.status == "replaceable"
    assert s.speed == 1.5
    assert s.target_duration_us == 5_000_000
    assert s.source_duration_us == 7_500_000
    assert [k.property for k in s.keyframes] == ["UNIFORM_SCALE"]
    assert s.keyframes[0].from_value == 1.0 and s.keyframes[0].to_value == 1.2
    assert s.transition_out is not None and s.transition_out.name == "Dissolve"
    assert s.transition_pad_us == 0  # non-overlapping transition consumes no extra source
    assert s.full_frame


def test_mask_locks_slot(manifest: Manifest):
    s = manifest.media_by_id()[MEDIA_SLOT_MASK]
    assert s.status == "locked"
    assert any("mask" in r for r in s.lock_reasons)
    assert s.transition_in is not None  # inherits the previous slot's outgoing transition


def test_pip_slot_not_full_frame(manifest: Manifest):
    s = manifest.media_by_id()[MEDIA_SLOT_PIP]
    assert s.status == "replaceable"
    assert not s.full_frame
    assert s.scale == (0.4, 0.4)


def test_text_classification(manifest: Manifest):
    t = manifest.text_by_id()
    assert t[TEXT_SLOT_OK].status == "replaceable" and t[TEXT_SLOT_OK].text == "Hello everyone"
    assert t[TEXT_SLOT_MULTI].status == "locked" and t[TEXT_SLOT_MULTI].style_count == 2
    assert t[TEXT_SLOT_LEGACY].status == "locked"


def test_shared_material_locks(template_doc, tmp_path):
    import json

    from capcut_recreate.manifest import build_manifest

    # Make the PIP segment share the masked segment's material.
    for track in template_doc["tracks"]:
        for seg in track["segments"]:
            if seg["id"] == MEDIA_SLOT_PIP:
                seg["material_id"] = "mat-video-02"
    d = tmp_path / "t"
    d.mkdir()
    (d / "draft_content.json").write_text(json.dumps(template_doc))
    m = build_manifest(d)
    assert m.media_by_id()[MEDIA_SLOT_PIP].status == "locked"
    assert any("shared" in r for r in m.media_by_id()[MEDIA_SLOT_PIP].lock_reasons)


def test_roundtrip_json(manifest: Manifest):
    again = manifest_from_json(manifest.to_json())
    assert again.to_json() == manifest.to_json()


def test_paths_are_absolute(tmp_path, monkeypatch):
    from pathlib import Path

    from capcut_recreate.footage import stage_footage
    from capcut_recreate.manifest import build_manifest
    from conftest import FOOTAGE, TEMPLATE

    monkeypatch.chdir(tmp_path)
    m = build_manifest(Path("../" * 0 + str(TEMPLATE)))
    assert Path(m.template_dir).is_absolute()
    idx = stage_footage(sorted(FOOTAGE.glob("*.mp4"))[:1], Path("rel_staging"), thumbnails=False)
    assert all(Path(c.path).is_absolute() for c in idx.clips)
