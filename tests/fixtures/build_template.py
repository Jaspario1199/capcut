"""Turn the capcut-cli 6.2.8 fixture into a self-contained template with one of
each thing the pipeline must handle.

Run once; the output is committed. Re-run only when the fixture shape changes.

    python tests/fixtures/build_template.py

Slots produced:
  seg aaaaaa01  video, speed 1.5, uniform_scale keyframes, outgoing transition  -> replaceable
  seg aaaaaa02  video, static mask                                              -> locked (mask)
  seg dddddd01  video on PIP track, scaled and offset                            -> replaceable
  seg cccccc01  text, single style                                              -> replaceable
  seg cccccc02  text, two style ranges                                          -> locked (multi-style)
  seg cccccc03  text, legacy non-JSON content                                   -> locked (unparseable)
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
TPL = HERE / "template"
DOC = TPL / "draft_content.json"

US = 1_000_000


def main() -> None:
    d = json.loads(DOC.read_text())
    mats = d["materials"]

    # --- media: point at bundled assets with true durations -----------------
    vids = {m["id"]: m for m in mats["videos"]}
    vids["mat-video-01"].update(path="assets/video/long8s.mp4", material_name="long8s.mp4", duration=8 * US, width=320, height=240)
    vids["mat-video-02"].update(path="assets/video/short2s.mp4", material_name="short2s.mp4", duration=2 * US, width=320, height=240)
    pip = json.loads(json.dumps(vids["mat-video-02"]))
    pip.update(id="mat-video-03")
    mats["videos"].append(pip)

    (TPL / "assets" / "audio").mkdir(parents=True, exist_ok=True)
    music = TPL / "assets" / "audio" / "music.mp3"
    if not music.exists():
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=10",
             "-filter:a", "volume=0.1", "-c:a", "libmp3lame", "-b:a", "64k", str(music)],
            check=True,
        )
    aud = mats["audios"][0]
    aud.update(path="assets/audio/music.mp3", name="music.mp3", duration=10 * US)

    # --- companion materials --------------------------------------------------
    mats.setdefault("transitions", []).append({
        "id": "mat-trans-01", "type": "transition", "name": "Dissolve", "effect_id": "10000",
        "resource_id": "", "duration": 500_000, "is_overlap": False,
        "category_id": "", "category_name": "", "platform": "cc",
    })
    mats.setdefault("masks", []).append({
        "id": "mat-mask-01", "type": "mask", "resource_type": "circle", "name": "Circle", "platform": "cc",
        "config": {"rotation": 0, "centerX": 0, "centerY": 0, "feather": 0.1, "height": 0.6, "width": 0.6,
                    "invert": False, "roundCorner": 0},
    })

    # --- segments ---------------------------------------------------------------
    tracks = {t["id"]: t for t in d["tracks"]}
    v1, v2 = tracks["track-video-01"]["segments"]
    v1["extra_material_refs"] = ["mat-speed-01", "mat-trans-01"]
    v1["common_keyframes"] = [{
        "id": "kf-scale-01", "property_type": "UNIFORM_SCALE",
        "keyframe_list": [
            {"id": "kf-01a", "time_offset": 0, "values": [1.0], "curveType": "Line"},
            {"id": "kf-01b", "time_offset": 5 * US, "values": [1.2], "curveType": "Line"},
        ],
    }]
    v2["target_timerange"] = {"start": 5 * US, "duration": 2 * US}
    v2["source_timerange"] = {"start": 0, "duration": 2 * US}
    v2["extra_material_refs"] = ["mat-mask-01"]

    pip_seg = json.loads(json.dumps(v2))
    pip_seg.update(id="dddddd01-0000-0000-0000-000000000001", material_id="mat-video-03", extra_material_refs=[],
                   target_timerange={"start": 7 * US, "duration": 2 * US}, source_timerange={"start": 0, "duration": 2 * US},
                   render_index=1, track_render_index=1)
    pip_seg["clip"] = {"alpha": 1, "rotation": 0, "scale": {"x": 0.4, "y": 0.4}, "transform": {"x": 0.5, "y": 0.5},
                       "flip": {"horizontal": False, "vertical": False}}
    d["tracks"].insert(1, {"id": "track-video-02", "type": "video", "name": "PIP", "is_default_name": False,
                           "attribute": 0, "flag": 0, "segments": [pip_seg]})

    # --- texts ------------------------------------------------------------------
    texts = {m["id"]: m for m in mats["texts"]}
    t2 = json.loads(texts["mat-text-02"]["content"])
    base = t2["styles"][0]
    t2["text"] = "Watch this part closely"
    hi = json.loads(json.dumps(base))
    hi["fill"] = {"alpha": 1, "content": {"render_type": "solid", "solid": {"alpha": 1, "color": [1, 0.84, 0]}}}
    hi["bold"] = True
    base["range"] = [0, 6]
    hi["range"] = [6, len(t2["text"])]
    t2["styles"] = [base, hi]
    texts["mat-text-02"]["content"] = json.dumps(t2, separators=(",", ":"))

    d["duration"] = 10 * US
    DOC.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
    print("wrote", DOC)


if __name__ == "__main__":
    main()
