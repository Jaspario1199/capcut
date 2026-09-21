# Workflow v1: LLM recreates a complex CapCut project with new content

Goal: take an existing, hand-made CapCut (international, desktop) project with many tracks, keyframes, masks, effects, text, and produce a new project in the same style with different footage and text, driven by an LLM, with minimal manual touch-up.

## Design principle
Template cloning, not timeline authoring. The LLM never writes draft JSON. It chooses which new clip fills which existing slot and what the new text says. Deterministic tools do all writes.

## Tool stack
- capcut-cli 0.25+ (npm, MIT) for inspection, `replace-media`, `set-text`, `lint`, `register`, `sync-timelines`, `batch`.
- ffmpeg + ffprobe for probing and proxies. `capcut render` for low-res proxy.
- faster-whisper for transcripts. PySceneDetect for scene cuts.
- Any LLM with tool calling; multimodal for thumbnail review.
- CapCut desktop pinned to 9.x (10.x reported to reject tool-written drafts).

## Steps
1. Freeze the environment. Pin CapCut to a 9.x build, disable auto-update, back up the drafts store. Close CapCut before any write.
2. Snapshot the template. Copy the whole draft folder wholesale (keeps compound clips, mask keyframes, unknown fields). Delete `Timelines/` in the copy so the app rebuilds it. Keep version markers.
3. Extract a slot manifest. Run `capcut info`, `tracks`, `segments`, `texts`, `export-timeline` on the copy. Deterministic script converts this to a compact manifest: per media slot {slot_id, track, index, target duration, source duration used, speed, has_keyframes, has_mask, transition_in/out, label}; per text slot {slot_id, current text, max chars, duration}. This manifest is what the LLM sees. Never the raw JSON.
4. Index the new footage. For each new clip: ffprobe (duration, fps, resolution), PySceneDetect scenes, whisper transcript, one thumbnail per scene, optional VLM caption. Emit a footage index JSON.
5. LLM plan. Prompt = manifest + footage index + brief ("same style, new topic X"). Output must be a JSON plan: for each media slot {slot_id, clip, in_point_s} and for each text slot {slot_id, new_text}. Constraints given in prompt: in_point + slot duration must fit inside clip; text length within max chars; every slot filled.
6. Validate the plan. Deterministic script checks every constraint from step 5, rejects and reprompts with the error list. Max 3 retries.
7. Apply. Script converts plan to capcut-cli `batch` JSONL: `replace-media <seg> <file> --retime` per media slot, `set-text` per text slot, `rename`. Single transactional write.
8. Lint and register. `capcut lint` must exit 0. `capcut register --materials --apply` to populate draft_meta_info materials. `capcut sync-timelines --apply` if the version needs mirrors.
9. Proxy review. `capcut render` low-res; sample frames at each slot boundary; VLM checks: no black frames, text legible and inside safe area, no clip shorter than its slot. Failures loop back to step 5 with notes.
10. Open in CapCut, eyeball, export. Manual export (no reliable headless export exists for CapCut international).

## Known gaps
- Speed-ramped segments and combined in/out animations are not retimed on replacement.
- Effects and stickers must already exist in the template; no new resource IDs.
- Export is manual.
