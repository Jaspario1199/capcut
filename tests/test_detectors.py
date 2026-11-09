"""Shot detectors on a synthetic clip with known cuts: hard cut at 2.0 s, a 0.5 s
dissolve at 4.0-4.5 s, a hard cut at 6.5 s, then 3 s of heavy motion with no cut."""

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

from capcut_recreate.footage import FootageError, detect_scenes

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
TRUTH = [2.0, 4.25, 6.5]


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("shots")
    srcs = {
        "a": "testsrc2=s=320x180:r=30:d=2",
        "b": "mandelbrot=s=320x180:r=30",
        "c": "smptehdbars=s=320x180:r=30:d=2.5",
        "d": "life=s=320x180:r=30:mold=10:ratio=0.3:life_color=#00ff00:death_color=#aa0000",
    }
    dur = {"a": 2, "b": 2.5, "c": 2.5, "d": 3}
    for k, src in srcs.items():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-t", str(dur[k]), str(d / f"{k}.mp4")],
                       check=True)
    out = d / "test.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *sum((["-i", str(d / f"{k}.mp4")] for k in "abcd"), []),
                    "-filter_complex",
                    "[1][2]xfade=transition=fade:duration=0.5:offset=2.0[bc];[0][bc][3]concat=n=3:v=1:a=0,format=yuv420p",
                    "-r", "30", str(out)], check=True)
    return out


def cuts(scenes):
    return [s.start_us / 1e6 for s in scenes[1:]]


@pytest.mark.parametrize("detector", ["content", "adaptive"])
def test_pyscenedetect_detectors_find_every_cut_and_no_false_ones(clip, detector):
    pytest.importorskip("scenedetect")
    found = cuts(detect_scenes(clip, 9_500_000, detector=detector))
    assert len(found) == len(TRUTH)
    # a threshold detector marks a dissolve where it ends; allow 0.35 s
    assert all(abs(f - t) <= 0.35 for f, t in zip(found, TRUTH)), found


def test_transnet_finds_every_cut(clip):
    if importlib.util.find_spec("transnetv2_pytorch") is None:
        pytest.skip("shots-nn extra not installed")
    found = cuts(detect_scenes(clip, 9_500_000, detector="transnet"))
    assert len(found) == len(TRUTH)
    assert all(abs(f - t) <= 0.2 for f, t in zip(found, TRUTH)), found


def test_unknown_detector_is_an_error(clip):
    with pytest.raises(FootageError, match="unknown detector"):
        detect_scenes(clip, 9_500_000, detector="nope")
