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

# 6. After you fix things by hand in CapCut and save, capture what you changed.
capcut-recreate learn <drafts-dir>/beach-v1
capcut-recreate feedback <job-id> --rating up --note "hook clip was perfect"
```

## Letting an LLM plan, and letting it learn

### No API key: Claude Code (or any LLM) as the planner

Open this repo in Claude Code and run `/capcut-plan manifest.json footage.json --job beach-v2 --brief "..."`.
The skill in `.claude/skills/capcut-plan/` does the loop below. It uses the
subscription you already have and nothing else.

```bash
capcut-recreate manifest <template> -o manifest.json --thumbs ./thumbs   # thumbnails for the planner
capcut-recreate index ./staging clipA.mp4 clipB.mov -o footage.json      # scene thumbnails too
capcut-recreate prompt manifest.json footage.json --job beach-v2 --brief "..." -o prompt.json
# an LLM with file access reads prompt.json, opens the listed thumbnails, writes plan.json
capcut-recreate check manifest.json footage.json plan.json               # repeat until ok
capcut-recreate apply manifest.json footage.json plan.json --store <drafts-dir> --brief "..."
```

`prompt.json` holds the system instructions, the brief, worked examples with
corrections, the slot manifest, the footage index, the JSON schema the plan
must match, and the path of every thumbnail. Any model that can read files
can plan from it.

Big templates (tens of slots) are planned in time windows: `prompt ... --chunk 12 -o chunks`
writes one prompt and one sub-manifest per window, each chunk is planned and
checked on its own, and `merge-plans chunks/*.plan.json --job NAME -o plan.json`
stitches them for the final check and apply.

### With an API key: `plan`

```bash
pip install -e ".[llm]"          # anthropic SDK; needs ANTHROPIC_API_KEY or `ant auth login`
capcut-recreate plan manifest.json footage.json --job beach-v2 --brief "..." -o plan.json
```

`plan` sends Claude the same material with thumbnails inline, runs our
validator on the answer, and sends the error list back for another attempt, at
most three times. Either way the model never sees or writes draft JSON.

**Learning** is a retrieval loop, not model training. Every `apply` stores the
job in the library (`~/.capcut-recreate/library`). After you open the result in
CapCut, fix it by hand, and save, `learn` diffs the saved draft against what we
wrote and records labelled corrections: which slot got a different clip, which
in-point you nudged, which text you rewrote. `feedback` records a rating. The
next `plan` for the same template (or the closest one) shows those examples and
corrections to the model as the operator's taste. Fields CapCut itself rewrote
on open are stored separately as app-rewrite paths, which is the Phase 0
allowlist described in `docs/WORKFLOW.md`.

Planner defaults: `claude-opus-5`, adaptive thinking, effort `high`, structured
JSON output, and server-side refusal fallbacks (`--no-fallbacks` to disable).

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
- [x] Thumbnails in the manifest and footage index
- [x] Example library, corrections capture, feedback
- [x] Planner without an API key: `prompt` export plus the `/capcut-plan` Claude Code skill (exercised end to end)
- [x] API planner with validator feedback loop (tested with a fake client; live calls need credentials)
- [x] Phase 0 verified on CapCut 9.5.0 Windows with a mobile-made template (WORKFLOW.md section 5a). The app rewrote none of the fields the pipeline wrote.
- [ ] Transcript spans in the manifest
- [ ] Multi-style text replacement with range rescaling

Not affiliated with ByteDance or CapCut.
