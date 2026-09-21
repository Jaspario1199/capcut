# Workflow v2: LLM recreates a complex CapCut project with new content

Goal: take an existing hand-made CapCut (international, desktop) project and produce a new project in the same style with different footage and text, driven by an LLM, with the app doing the final render.

## Design principles
1. Template cloning, not timeline authoring. The LLM never writes draft JSON. Deterministic code does every write.
2. Slot lengths are frozen. The LLM only chooses which clip and which in-point fills each media slot, and what each text slot says. Nothing that changes a target duration is in scope for v0.
3. Every write is followed by an invariant check over the raw JSON. capcut-cli lint is advisory, not the gate.
4. Fail closed. Any unverified precondition aborts the run before anything is written to the real store.

## Tool stack
- capcut-cli pinned to 0.25.0 in a lockfile. Re-run the fixture round-trip (below) before any bump.
- ffmpeg and ffprobe on PATH. `capcut doctor` must report ffprobe present or the run aborts.
- PySceneDetect, faster-whisper, and a multimodal LLM for the footage index.
- CapCut desktop: one exact build, validated end to end (open, edit, save, reopen) and recorded in the run config. Installer archived. `capcut version` and `doctor` run at the start of every job; any app-version drift aborts.

## Phase 0: one-time validation of the exact CapCut build
1. Create an empty project in the app, quit the app.
2. Run `capcut init probe --template <known-good-template> --drafts <store>`, then `sync-timelines --nested --apply`, `register --materials --apply`, `lint --fix`.
3. Open in the app, make a trivial edit, save, quit. Run `capcut diff` between what was written and what the app saved. Expected: only frame-grid rounding (±1 frame) and app bookkeeping fields differ. Record the allowlist of fields the app rewrites.
4. If the app rejects the draft or rewrites timing beyond one frame, this build is unsupported. Stop.

## Phase 1: template preparation (per template, once)
1. Preflight: `capcut diagnose <template>` and `sync-timelines <template> --nested` in plan mode. Require in-sync root, template-2.tmp, and nested Timelines documents. On drift, abort: the root file may be a stale mirror of what the editor shows.
2. Tar the whole template folder including `assets/` as the rollback snapshot.
3. Build the slot manifest with your own extractor over the raw timeline JSON (not `segments` or `export-timeline`, both too lossy). For each video/image segment on any track:
   - slot_id, track name and z-order, target_timerange, source_timerange, speed, and `curve_speed` presence
   - clip transform: scale, position, crop, rotation; full-frame vs PIP inference from scale and crop
   - volume and mute state
   - keyframe summary per property: from value, to value, timestamps relative to segment
   - intro, outro, and group animation ids and durations
   - transitions on each side with `is_overlap` and duration, so the extractor computes the extra source frames the transition consumes
   - mask presence, mask keyframe presence, `group_id`, `combination` membership
   - a thumbnail of the template clip at the slot's in-point and mid-point
   - transcript span of any voiceover audible under the slot
   For each text segment: slot_id, current text, style range count, font, size, bounding box, duration, animation ids.
4. Classify every slot into one of three bins:
   - **replaceable**: linear speed, no `curve_speed`, no mask keyframes, not in a `combination`, single-style text. The LLM may fill it.
   - **locked**: anything else. Kept as is from the template. The manifest still shows it to the LLM as context.
   - **text-locked**: multi-style text. Kept unless a range-rescaling replacement is implemented (pyJianYingDraft's `replace_text` logic is the reference).
   Report the counts. If most slots are locked, the template is a poor fit and the operator should know before spending LLM budget.

## Phase 2: footage index (per job)
1. Rename every new clip to `<slot-safe-hash>_<original>.ext` so basenames are unique. Record md5.
2. ffprobe each clip: duration, fps, resolution, orientation, audio presence. Reject variable frame rate.
3. PySceneDetect scene list per clip. One thumbnail per scene. Whisper transcript with word timestamps.
4. Optional VLM caption per scene. Emit `footage_index.json`.

## Phase 3: LLM plan
Prompt contains: the slot manifest with thumbnails, the footage index with thumbnails, the brief, and the hard constraints. Output is JSON validated against a schema:
- media: `{slot_id, clip_id, scene_id}` where scene_id is an enum from the index. In-point is the scene start. No free-float in-points.
- text: `{slot_id, new_text}` for replaceable text slots only.
Constraints enforced by a deterministic validator, not by the prompt alone:
- scene start + slot source duration + transition overlap pad <= clip duration
- orientation and aspect of the clip compatible with the slot's crop and scale
- every replaceable slot filled exactly once, no locked slot referenced
- text length within the heuristic max derived from bounding box and font size, and admitted as heuristic
Reject and reprompt with the error list. Maximum 3 retries, then hand to the operator.

## Phase 4: apply (inside the real drafts store)
1. Clone: `capcut init <job-name> --template <template-dir> --drafts <store>`. This mints a fresh draft id, skips `Timelines/`, and registers the draft. Never copy the folder by hand.
2. Per media slot, in sequence: `capcut replace-media <seg> <renamed-clip>` with no `--retime`. Assert `new_duration_us` is not null and `assets/<kind>/<basename>` md5 equals the source md5. Any failure: restore from the tar and abort.
3. One `capcut batch` containing, per media slot, `trim <seg> <scene_start> <original_source_duration>` (preserves target duration because trim recomputes target from source and speed), and per text slot `set-text`.
4. `register --materials --apply`, then `lint --fix` to repair `local_material_id` links, then `sync-timelines --nested --apply` if the validated build needs it.
5. Own invariant validator over the raw JSON:
   - source_timerange within material duration
   - source duration == target duration × speed within one frame
   - keyframe, intro, outro, and animation offsets <= target duration
   - text style ranges <= text length
   - locked slots byte-identical to the template
6. `capcut diff <template> <new>` with an allowlist: only material paths, material durations, source ranges, text content, draft id, and the fields recorded in Phase 0 may differ. Anything else fails the job.
7. `capcut lint` for information only, with an allowlist of accepted codes (line-too-long, cue-too-long on stylised titles).

## Phase 5: review and export
1. `capcut render --all-video-tracks` as a gross-error proxy only: wrong clip, black frames, obviously wrong layer. It does not render CapCut effects, fonts, or masks, so no timing or legibility verdicts come from it.
2. Open in the app. Operator reviews. Export manually. There is no reliable headless export for CapCut international.
3. After the app saves, run `capcut diff` again against the pre-open state to confirm the app only applied the Phase 0 allowlisted rewrites.

## Testing strategy
- Fixture round-trip per capcut-cli version bump and per CapCut build.
- Golden test: one template, one fixed plan, expected output JSON. Diff on every pipeline change.
- Collision test: two clips with the same basename must produce two distinct assets.
- Negative tests: clip too short for slot, VFR clip, multi-style text targeted, locked slot referenced. All must abort before Phase 4 step 2.

## Known limits, stated plainly
- Slots with curve speed, mask keyframes, or group membership are never filled. Complex templates may have many.
- Keyframed moves designed for the old footage's composition are reapplied to new footage blindly. Thumbnails in the manifest are the only mitigation.
- Audio ducking and beat sync are inherited from the template and only stay correct because slot lengths are frozen.
- Effects, stickers, fonts must already be in the template and cached on the machine.
- Single-maintainer dependency releasing weekly. CapCut 10.x acceptance of tool-written drafts is unverified.
- Export is manual.

## What v0 actually is
`init --template` clone on one validated build, per-slot `replace-media` with unique filenames and ffprobe asserted, one batched `trim` plus `set-text` on single-style texts, `register --materials --apply`, `lint --fix`, own invariant validator, `capcut diff` with an allowlist, open in the app. Ship that before adding the LLM.
