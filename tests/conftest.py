from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATE = FIXTURES / "template"
FOOTAGE = FIXTURES / "footage"

MEDIA_SLOT_A = "aaaaaa01-0000-0000-0000-000000000001"  # speed 1.5, keyframes, transition -> replaceable
MEDIA_SLOT_MASK = "aaaaaa02-0000-0000-0000-000000000002"  # mask -> locked
MEDIA_SLOT_PIP = "dddddd01-0000-0000-0000-000000000001"  # PIP -> replaceable
MEDIA_SLOT_PHOTO = "eeeeee01-0000-0000-0000-000000000001"  # still image -> replaceable, images only
TEXT_SLOT_OK = "cccccc01-0000-0000-0000-000000000001"
TEXT_SLOT_MULTI = "cccccc02-0000-0000-0000-000000000002"
TEXT_SLOT_LEGACY = "cccccc03-0000-0000-0000-000000000003"


def capcut_available() -> bool:
    return shutil.which("capcut") is not None and shutil.which("ffprobe") is not None


requires_cli = pytest.mark.skipif(not capcut_available(), reason="capcut-cli and ffprobe required")


@pytest.fixture
def template_dir() -> Path:
    return TEMPLATE


@pytest.fixture
def template_doc() -> dict:
    return json.loads((TEMPLATE / "draft_content.json").read_text())


@pytest.fixture
def manifest():
    from capcut_recreate.manifest import build_manifest
    return build_manifest(TEMPLATE)


@pytest.fixture
def footage_index(tmp_path):
    from capcut_recreate.footage import stage_footage
    # sorted by name: beach_wide (c00), city_night (c01), other_short2s (c02), poster.png (c03, image)
    return stage_footage(sorted(FOOTAGE.glob("*.mp4")) + sorted(FOOTAGE.glob("*.png")), tmp_path / "stage")


@pytest.fixture
def store(tmp_path) -> Path:
    s = tmp_path / "store"
    s.mkdir()
    return s
