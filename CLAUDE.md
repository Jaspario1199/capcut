# capcut-recreate: project brief for any Claude session

Read this first. It is the state of the project and what the operator wants.

## Goal

Take an existing CapCut desktop project (the **template**), keep its whole
layout (tracks, timing, keyframes, transitions, effects, text styling), and
produce a new project that plays the same way with **different footage and
different text**. The operator wants to hand a template to an LLM and get a
recreation back, and wants the system to learn from the edits they make
afterwards in CapCut.

The LLM never writes CapCut JSON. It only writes a **plan**: which new clip
and which scene start fills each replaceable media slot, and what each
replaceable text slot should say. Deterministic code (this repo, on top of
`capcut-cli`) does every write and validates every result. See
`docs/WORKFLOW.md` for the design and the research behind it.

## Current job

- Template: `0805 Copy Copy Copy` in the operator's CapCut drafts folder
  (`%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft\0805 Copy Copy Copy`).
  14 tracks, 247 segments, 61 s, portrait 3:4. Made on CapCut mobile, so it
  carries version stamp 14.8.0; the desktop app is CapCut 9.5.0 on Windows.
  Manifest: 93 replaceable video slots, 150 replaceable text slots, none locked.
- New content: **"the greatest biopic movies of all time"**, a ranked countdown
  with a hype tone and punchy captions; each entry names the film and the
  person it is about.
- New footage: clips in `C:\Users\jaspe\Videos\4K Video Downloader+`
  (landscape downloads; that is fine, orientation mismatch is only a warning).
- Job name: `biopics-v1`.

The animated or other templates are not the current job unless the operator
says so.

## What has been verified on the operator's machine

- The full pipeline round-trips on CapCut 9.5.0: `apply` wrote a draft, CapCut
  listed it, opened it, played it, the operator edited and saved, and `learn`
  captured the edit. CapCut rewrote none of the fields the pipeline wrote.
- Mobile-stamped templates need `--allow-untested-version` on `apply`.
- CapCut must be **closed** during `apply`; the pipeline refuses otherwise.
- CapCut 9.x stores media paths as `##_draftpath_placeholder_<id>_##/...`;
  the pipeline resolves that token. Do not rewrite those paths.
- CapCut 9.x keys its per-file probe cache
  (`User Data\Cache\importcache3\mediainfo\<unique_id>.json`) by the video
  material's `unique_id` = MD5 of the absolute forward-slash path. capcut-cli's
  `replace-media` keeps the template's value, so a replaced clip reads the old
  file's probe and never finishes loading (striped track, black preview; this
  was biopics-v1). `apply` now recomputes `unique_id` for every replaced video
  material as its last write. The file format was never the problem: the
  downloads are 1080p H.264, the same shape as the template's own footage.
- Slots whose template media is an online library asset (the white and black
  flash frames, path under `Cache/onlineMaterial`) must be `keep: true`; CapCut
  reverts their path on save even when replaced.

## How to run the current job

Use the `/capcut-plan` skill (`.claude/skills/capcut-plan/SKILL.md`). In short:

```powershell
$p = "$env:LOCALAPPDATA\CapCut\User Data\Projects\com.lveditor.draft\0805 Copy Copy Copy"
$store = "$env:LOCALAPPDATA\CapCut\User Data\Projects\com.lveditor.draft"
$clips = (Get-ChildItem "C:\Users\jaspe\Videos\4K Video Downloader+" -Recurse -Include *.mp4,*.mov).FullName
python -m capcut_recreate.cli manifest $p -o real.json --thumbs realthumbs
python -m capcut_recreate.cli index staging $clips -o footage.json
python -m capcut_recreate.cli prompt real.json footage.json --job biopics-v1 --brief "<brief above>" --chunk 12 -o chunks
# plan each chunks/chunk_XX.prompt.json -> chunks/chunk_XX.plan.json, check each against its chunk manifest
python -m capcut_recreate.cli merge-plans chunks/*.plan.json --job biopics-v1 -o plan.json
python -m capcut_recreate.cli check real.json footage.json plan.json
# only with CapCut closed and after the operator says go:
python -m capcut_recreate.cli apply real.json footage.json plan.json --store $store --brief "<brief>" --allow-untested-version
```

After the operator edits the result in CapCut and saves:

```powershell
python -m capcut_recreate.cli learn "$store\biopics-v1"
```

`python -m capcut_recreate.cli` and `capcut-recreate` are the same thing.
`capcut` is the capcut-cli binary (0.25.0, pinned); on this machine `capcut.cmd`
also works.

## Rules

- Never edit CapCut draft JSON by hand. Go through the CLI.
- Never apply into the real drafts store while CapCut is running.
- Slot lengths are frozen; a plan only chooses clips, scene starts, and text.
- Locked slots are never filled. The manifest says why each one is locked.
- Run `python -m pytest -q` before pushing code changes. Tests need
  `capcut-cli` and `ffprobe` on PATH for the end-to-end cases.
- Keep GitHub in sync while working: commit job inputs and plans under
  `work/<job>/` on a `work/<job>` branch and push after each milestone
  (index built, plan checked, apply done, learn captured). Open a pull request
  with `gh pr create` when the job is done. Never force-push; never push
  footage files, thumbnails are fine.
- Nothing here calls a paid API by default. `capcut-recreate plan` is the only
  command that does, and the operator has chosen not to use it.

## Layout

- `src/capcut_recreate/` pipeline: `manifest`, `footage`, `plan`, `apply`,
  `validate`, `diffing`, `library`, `corrections`, `planner`, `thumbs`, `cli`.
- `tests/` with a bundled fixture template and footage.
- `docs/WORKFLOW.md` design, research, Phase 0 record. `docs/reviews/` history.
- `.claude/skills/capcut-plan/` the planning procedure for a session.
- `.claude/settings.json` pre-approved commands for local sessions.
