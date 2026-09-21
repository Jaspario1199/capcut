# capcut-recreate

Take an existing, hand-made CapCut desktop project and produce a new project
in the same style with different footage and text. Deterministic slot filling
on top of [capcut-cli](https://github.com/renezander030/capcut-cli); the LLM,
when you add one, only writes a plan.

Read [`docs/WORKFLOW.md`](docs/WORKFLOW.md) first. It is the design, the
research behind it, and the two rounds of adversarial review that shaped it.
The reviews that produced it are in [`docs/reviews/`](docs/reviews/).

## What it does today (v0, no LLM)

```
template draft ──manifest──▶ slots (replaceable / locked)
new clips     ──index────▶ footage index (md5, ffprobe, scenes)
you / an LLM  ──plan─────▶ {slot → clip+scene, slot → text}
                 check ───▶ every constraint enforced before any write
                 apply ───▶ init --template, relink, replace-media, batch trim + set-text,
                            register --materials, lint --fix,
                            own invariant validator, own path-level diff
```

Slot lengths are frozen. Any slot with a mask, group, compound clip, speed
curve, shared material, or multi-style text is locked and never filled. That
is the honest ceiling: the encodings for those are undocumented and no tool
can retime them safely.

## Install

```bash
npm install -g capcut-cli@0.25.0     # pinned; re-run the tests before bumping
# ffmpeg + ffprobe on PATH
pip install -e ".[dev]"              # add [scenes] for PySceneDetect scene cuts
```

## Use

```bash
# 1. Describe the template. Prints slot counts to stderr.
capcut-recreate manifest ~/Movies/CapCut/User\ Data/Projects/com.lveditor.draft/<template> -o manifest.json

# 2. Stage and index the new footage (unique filenames, md5, scenes).
capcut-recreate index ./staging clipA.mp4 clipB.mov -o footage.json

# 3. Write a plan (see examples/plan.json), then check it.
capcut-recreate check manifest.json footage.json plan.json

# 4. Apply into the real drafts store. Aborts and cleans up on any failure.
capcut-recreate apply manifest.json footage.json plan.json --store ~/Movies/CapCut/User\ Data/Projects/com.lveditor.draft

# 5. Open CapCut, review, export. There is no headless export.
```

Close CapCut before `apply`. Validate one exact CapCut build with
`docs/WORKFLOW.md` Phase 0 before trusting any of this on a real template.

## Plan format

```json
{
  "job_name": "beach-v1",
  "media": [{"slot_id": "<segment id from manifest>", "clip_id": "c00", "scene_id": "s0"}],
  "text":  [{"slot_id": "<segment id from manifest>", "new_text": "Welcome back"}]
}
```

The in-point is the chosen scene's start. Every replaceable media slot must
be filled exactly once. Locked slots cannot be referenced.

## Tests

```bash
pytest
```

Unit tests cover manifest extraction, plan validation, the invariant
validator, and the diff. `tests/test_apply_e2e.py` runs the real
capcut-cli against a bundled 6.2.8-shaped fixture and is skipped when the
binary or ffprobe is missing. The fixture is built by
`tests/fixtures/build_template.py` and carries one of each thing the pipeline
must handle: a speed-changed slot with keyframes and a transition, a masked
slot, a picture-in-picture slot, and single-style, multi-style, and legacy
text.

## Status

- [x] Phase 1 manifest and classification
- [x] Phase 2 footage index (ffprobe, optional PySceneDetect; no transcripts yet)
- [x] Phase 3 plan schema and validator
- [x] Phase 4 apply with own validator and diff
- [ ] Phase 0 validation against a real CapCut build (needs the app; see WORKFLOW.md)
- [ ] Thumbnails and transcript spans in the manifest
- [ ] LLM planner
- [ ] Multi-style text replacement with range rescaling

Not affiliated with ByteDance or CapCut.
