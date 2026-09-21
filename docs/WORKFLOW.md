# LLM-driven recreation of complex CapCut projects

**Status:** v3, after two rounds of adversarial review with every tool claim tested against capcut-cli 0.25.0 on a scratch drafts store.
**Date:** 2026-09-21

## 1. What this is

A pipeline where an LLM takes an existing, hand-made CapCut (international, desktop) project and produces a new project in the same style with different footage and text. CapCut itself does the final render.

## 2. What the research established

**Tool landscape**

| Tool | Role | Verdict |
|---|---|---|
| capcut-cli 0.25.0 (MIT, npm) | Inspect, clone, replace media, trim, set text, register, lint | Primary tool. Actively maintained, weekly releases, single maintainer. |
| pyCapCut 0.0.3 (PyPI) | Template mode: replace material by name, replace text with style-range rescaling | Frozen since Sept 2025. Does not write `draft_meta_info.json` materials, ignores `Timelines/` and `template-2.tmp`, export automation targets the Chinese app window. Use only its `replace_text` range logic as a reference. |
| pyJianYingDraft 0.3.0 | Parent of pyCapCut, JianYing only | Not applicable to CapCut international. |
| CapCutAPI / VectCutAPI (Apache 2.0) | HTTP + MCP, builds drafts from scratch | No import-existing-draft path. Wrong tool for recreation. |
| JmsLdrn/capcut-mcp (MIT) | MCP that clones an existing draft | One commit. Watch, do not depend on. |
| Remotion, Diffusion Studio, OpenCut | Headless render alternatives | Not CapCut-compatible. OpenCut's MCP and headless mode are unshipped. |

**Format facts that shape the design**

- CapCut international drafts are plain JSON on every version. Encryption applies to JianYing 6.0+ only.
- The draft folder holds several documents that must agree: root `draft_content.json` (Windows) or `draft_info.json` (macOS), `template-2.tmp` on 8.7+, nested `Timelines/<id>/draft_info.json` on 7.7 to 9.2.8 macOS, and `draft_meta_info.json` whose `draft_materials` list decides on 9.1+ whether clips show as available.
- The app lists drafts from `root_meta_info.json`, not by scanning folders.
- Only CapCut 6.2.8 is fixture-tested by capcut-cli. 9.3.0 round-tripped once from a user report. 10.x is reported to reject tool-written drafts. No documented way to permanently disable auto-update on Windows.
- Mask keyframe encoding is unknown. Compound clip structure is undocumented. Speed curves are not handled by any tool.
- Text content is JSON-in-JSON with UTF-16 style ranges. capcut-cli `set-text` only resets `styles[0]`, so multi-style text is corrupted by it.
- Nobody has published a working "ingest arbitrary complex project, LLM reproduces style on new footage" system. The pattern that works in the wild is template cloning with slot filling. LLM-authored timelines are confined to research systems that limit themselves to hard cuts.

## 3. Design principles

1. **Template cloning, not timeline authoring.** The LLM never writes draft JSON. Deterministic code does every write.
2. **Slot lengths are frozen.** The LLM chooses which clip and which scene start fills each media slot, and what each text slot says. Nothing that changes a target duration is in scope.
3. **Own the validation.** capcut-cli `lint` is advisory. capcut-cli `diff` is blind to source ranges, keyframes, transforms, and companion refs, so it is not used as a gate at all. A custom recursive JSON diff and a custom invariant validator are the gates.
4. **Fail closed.** Any unverified precondition aborts before the real store is touched.
5. **Lock what you cannot see.** Any slot with a mask, a group id, a combination reference, a speed curve, or multi-style text is locked. No attempt is made to detect mask keyframes because the encoding is unknown.

## 4. Tool stack

- capcut-cli pinned to 0.25.0 in a lockfile. Fixture round-trip re-run before any bump. Note `render --all-video-tracks` is undocumented in help text and must be covered by a test.
- ffmpeg and ffprobe on PATH. `capcut doctor` must report ffprobe or the run aborts.
- PySceneDetect, faster-whisper, a multimodal LLM for the footage index and the plan.
- CapCut desktop: one exact build validated by Phase 0, installer archived, version recorded. `capcut version` and `doctor` run at the start of every job and abort on drift.

## 5. Phase 0: validate the exact CapCut build (once per build)

1. Build a small representative test template by hand in the app: at least one keyframed slot, one transition, one speed not equal to 1, one single-style text, one masked slot. Quit the app.
2. Run the full v0 apply sequence from Phase 4 against it with a fixed plan.
3. Open the result in the app, make a trivial edit, save, quit.
4. Deep-diff the saved draft against what was written using the custom path-level diff. Record every path the app rewrites (frame-grid rounding, bookkeeping fields). This becomes the app-rewrite allowlist.
5. If the app rejects the draft, or rewrites timing by more than one frame, or drops keyframes, masks, or text, the build is unsupported. Stop.

## 6. Phase 1: template preparation (once per template)

1. **Preflight.** Run `capcut diagnose`. With custom code, compare the hash of the root timeline document against `Timelines/<main_timeline_id>/draft_info.json` when that exists. Abort on mismatch, since `init --template` clones the root document and a stale root means cloning something other than what the editor shows. A binary `template-2.tmp` is expected on 8.7+ and is not drift.
2. **Self-contain.** Run `lint --fix media-outside-draft` so all template media lives in `assets/`. Otherwise every clone inherits absolute paths that break on other machines.
3. **Snapshot.** Tar the whole template folder including `assets/`.
4. **Extract the slot manifest** with custom code over the raw timeline JSON. `segments` and `export-timeline` are too lossy. Per video or image segment:
   - slot id, track name and z-order, target and source timeranges, speed, `curve_speed` on the linked speed material
   - transform: scale, position, crop, rotation. Full-frame vs picture-in-picture inferred from these.
   - volume and mute state
   - keyframe summary per property: from, to, offsets relative to segment
   - intro, outro, and group animation ids and durations
   - transitions on each side with `is_overlap` and duration, and the computed extra source frames the transition consumes
   - mask material present yes/no, `group_id`, any `combination` reference
   - thumbnail of the template clip at in-point and mid-point
   - transcript span of any voiceover audible under the slot
   Per text segment: slot id, current text, count of `styles[]`, font, size, bounding box, duration, animation ids.
5. **Classify each slot.**
   - **replaceable**: linear speed, no mask material, empty `group_id`, no combination reference, and for text exactly one style range.
   - **locked**: everything else. Shown to the LLM as context, never filled.
   Report the counts. If most slots are locked, tell the operator before spending LLM budget.

## 7. Phase 2: footage index (per job)

1. Rename every new clip to `<slotsafe-hash>_<original>.<ext>` so basenames are unique. `replace-media` skips the copy silently on basename collision and two slots end up pointing at the same wrong file. Record md5 per clip.
2. ffprobe each clip: duration, fps, resolution, orientation, audio presence. Reject variable frame rate.
3. PySceneDetect scene list per clip. One thumbnail per scene. Whisper transcript with word timestamps.
4. Optional VLM caption per scene. Emit `footage_index.json`.

## 8. Phase 3: LLM plan

Prompt: slot manifest with thumbnails, footage index with thumbnails, the brief, the constraints. Output is JSON validated against a schema.

- media: `{slot_id, clip_id, scene_id}`. `scene_id` is an enum from the index. The in-point is that scene's start. No free-float in-points.
- text: `{slot_id, new_text}` for replaceable text slots only.

Deterministic validator, applied before anything is written:

- scene start + slot source duration + transition overlap pad <= clip duration
- clip orientation and aspect compatible with the slot's crop and scale
- every replaceable slot filled exactly once, no locked slot referenced
- text length within a max derived from bounding box and font size, labelled as heuristic

Reject and reprompt with the error list, at most three times, then hand to the operator.

## 9. Phase 4: apply (inside the real drafts store)

1. **Clone:** `capcut init <job> --template <template-dir> --drafts <store>`. Verified: accepts a template outside the store, mints a fresh draft id, skips `Timelines/`, registers the draft. Never copy the folder by hand, which produces duplicate draft ids in `root_meta_info.json`.
2. **Per media slot, sequentially:** `capcut replace-media <seg> <renamed-clip>` with no `--retime` flag. Assert `new_duration_us` is not null and the md5 of `assets/<kind>/<basename>` equals the source. Accept exactly one known warning string ("New clip is Xs but the segment uses up to Ys of source") and only when a trim for that segment follows. Any other warning or failure: restore from tar, abort.
3. **One `capcut batch`** containing per media slot `{"cmd":"trim","id":<seg>,"start":"<scene_start>s","duration":"<original_source_duration>s"}` and per text slot a `set-text`. All times as strings: the batch parser rejects numeric `0`. Verified: trim recomputes target from source and speed, so target duration is preserved within frame rounding.
4. `register --materials --apply`, then `lint --fix` to populate `local_material_id` links, then `sync-timelines --nested --apply` only if Phase 0 showed the build needs it. Do not gate on lint's exit code: pre-existing template issues will fail it.
5. **Custom invariant validator** over the raw JSON:
   - every source_timerange inside its material duration
   - source duration equals target duration × speed within one frame
   - keyframe, intro, outro, and animation offsets <= target duration
   - text style ranges <= text length
   - every locked slot identical to the template except allowlisted app-rewrite paths
6. **Custom path-level diff** template vs new draft, keyed by segment and material id. Allowlist: material path, material duration, source_timerange on replaced slots, text content on replaced text slots, draft id, and the Phase 0 app-rewrite paths. Any other changed path fails the job.
7. `capcut lint` for information only, with an allowlist of accepted codes such as `line-too-long` and `cue-too-long` on stylised titles.
8. Delete the template's replaced original media from `assets/` so per-job disk growth is bounded. `prune` removes materials, not files.

## 10. Phase 5: review and export

1. `capcut render --all-video-tracks` as a gross-error proxy only: wrong clip, black frames, wrong layer. It does not render CapCut effects, fonts, masks, or transitions, so no timing or legibility verdicts come from it.
2. Open in the app. Operator reviews. Export manually. There is no reliable headless export for CapCut international.
3. After the app saves, run the custom diff against the pre-open state to confirm only Phase 0 allowlisted paths changed.

## 11. Testing strategy

- Fixture round-trip per capcut-cli bump and per CapCut build.
- Golden test: one template, one fixed plan, expected output JSON, diffed on every pipeline change.
- Collision test: two clips with the same original basename produce two distinct assets with correct md5.
- Numeric-zero test: a scene start of 0 serialises as "0s" and the batch succeeds.
- Negative tests that must abort before Phase 4 step 2: clip too short, VFR clip, multi-style text targeted, locked slot referenced, ffprobe missing, app version drift.

## 12. Known limits

- Slots with masks, groups, combinations, or speed curves are never filled. In a genuinely complex template this may be most of them. The most likely silent failure that remains is a masked or grouped slot misclassified as replaceable, because mask keyframes and compound membership are not fully visible in the JSON.
- Keyframed moves designed for the old footage's composition are reapplied to new footage blindly. Thumbnails in the manifest are the only mitigation.
- Audio ducking and beat sync stay correct only because slot lengths are frozen.
- Effects, stickers, and fonts must already be in the template and cached on the machine. Cloud-removed effects stop loading.
- Single-maintainer dependency with weekly behaviour-changing releases. CapCut 10.x acceptance is unverified.
- Export is manual.

## 13. Build order

Ship v0 with no LLM: Phase 0, Phase 1, a hand-written plan, Phase 4, Phase 5. Only when a hand plan round-trips cleanly through the pinned app add Phase 2 and Phase 3. The LLM is the last thing to add, not the first.

## 14. Sources

- capcut-cli: https://github.com/renezander030/capcut-cli (docs/version-support.md, docs/draft-schema/, skills/capcut-edit/SKILL.md, src/replace.ts, src/index.ts, src/factory.ts, src/store.ts, src/lint.ts)
- Schema cheat sheet: https://gist.github.com/renezander030/80823f1d47081c312d2c1f9edd20dc22
- Encryption guide: https://gist.github.com/renezander030/521e6c6e8590a2a6e917009d9313bc55
- Agent pipeline notes: https://gist.github.com/renezander030/866bd85789c5902471f8f5fc86d09342
- pyCapCut: https://github.com/GuanYixuan/pyCapCut (issue #13 draft_materials, #10 timeline scrambled, #8 animations lost)
- pyJianYingDraft: https://github.com/GuanYixuan/pyJianYingDraft
- CapCutAPI: https://github.com/sun-guannan/CapCutAPI
- JmsLdrn/capcut-mcp: https://github.com/JmsLdrn/capcut-mcp
- capcut-shell-inject 9.3.0 recipe: https://github.com/zxypro1/capcut-shell-inject
- EditDuet (Editor + Critic agents): https://arxiv.org/html/2509.10761v1
- premiere-pro-mcp sub-frame drift issue: https://github.com/leancoderkavy/premiere-pro-mcp/issues/553
- otio-diff: https://github.com/chaoz23/otio-diff
- Diffusion Studio editor: https://github.com/diffusionstudio/editor
- OpenCut: https://github.com/OpenCut-app/OpenCut
