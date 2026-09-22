---
name: capcut-plan
description: Plan a CapCut recreation job in this session instead of through the Anthropic API. Use when the user asks to plan, fill, or recreate a CapCut template with new footage, or invokes /capcut-plan. Reads the manifest and footage index, looks at thumbnails, writes plan.json (chunk by chunk for big templates), validates it, and optionally applies it.
---

# capcut-plan

You are the planner for `capcut-recreate`. The pipeline never lets a model
write draft JSON; you only choose which clip and scene fills each replaceable
slot, and what each replaceable text slot says. Deterministic code does the rest.

## Inputs

Arguments: `<manifest.json> <footage.json> --job <name> --brief "<text>"`.
If the manifest or footage index does not exist yet, build them first:

```bash
capcut-recreate manifest <template-dir> -o manifest.json --thumbs ./thumbs
capcut-recreate index ./staging <clips and images...> -o footage.json
```

`index` takes mp4/mov and png/jpg alike; images become `kind: "image"` clips
with one scene and are the only thing a photo slot accepts.

Read the manifest's slot counts (printed on stderr). Fewer than about 15
replaceable media slots: plan in one pass. More: plan in chunks.

## One pass (small templates)

1. `capcut-recreate prompt manifest.json footage.json --job <name> --brief "<brief>" -o prompt.json`
2. Read `prompt.json`. Follow its `system` text. Open every path in `images`
   with the Read tool and look at it.
3. Write `plan.json` matching `schema`: `job_name` exactly as given, one entry
   per replaceable media slot, `new_text` for replaceable text slots only.
   Never reference a locked slot. Keep text within each slot's `max_chars`.
   For a slot whose template media is structural (a background plate that
   spans the whole video, a white or black flash, a texture or gradient
   layer), write `{"slot_id": "...", "clip_id": "", "scene_id": "", "keep": true}`
   to leave it as the template had it. Only put footage where the template
   showed footage. Slots whose template media lives in CapCut's online
   material cache (`Cache/onlineMaterial`, the white and black flash frames)
   must be kept: the app reverts them on save.
   A slot with `media_type: "photo"` showed a still image (poster, title art,
   logo). It takes an image clip only. Make one per entry with
   `capcut-recreate titlecard "FILM|Person" -o cards/01.png --like <template png>`
   (or `--size WxH` matching `template_media_size`), index the cards with the
   footage, and fill each photo slot with its card. Never put a video or a
   black placeholder in a photo slot.
   One slot, one shot: the slot's source length must fit inside the chosen
   scene, not just the clip. `check` rejects a choice that crosses a detected
   cut, because that adds a cut the template never had, off the beat.
4. `capcut-recreate check manifest.json footage.json plan.json`; fix every
   error and re-check until `ok` is true. At most three rounds; then show the
   user the remaining errors and stop.

## Chunked (large templates)

1. `capcut-recreate prompt manifest.json footage.json --job <name> --brief "<brief>" --chunk 12 -o chunks`
   This writes `chunks/chunk_XX.prompt.json` and `chunks/chunk_XX.manifest.json`
   for consecutive time windows. The output lists each chunk's window and counts.
2. For each chunk in order: read its prompt, open its images, write
   `chunks/chunk_XX.plan.json` covering only that chunk's slots, then
   `capcut-recreate check chunks/chunk_XX.manifest.json footage.json chunks/chunk_XX.plan.json`
   and fix until ok. Carry the story forward: text in later chunks should
   continue what earlier chunks set up, and the same clip may be reused in
   different chunks at different scene starts.
3. `capcut-recreate merge-plans chunks/*.plan.json --job <name> -o plan.json`
4. `capcut-recreate check manifest.json footage.json plan.json` on the merged
   plan. It must be ok; if not, fix the offending chunk and re-merge.

## Review and apply

Show the user the plan with one line of reasoning per media slot (group text
slots by chunk). Do not apply unless they ask. When they do, CapCut must be
closed:

```bash
capcut-recreate apply manifest.json footage.json plan.json --store <drafts-dir> --brief "<brief>" [--allow-untested-version]
```

`--allow-untested-version` is needed for templates made on CapCut mobile
(version stamp 14.x or 15.x). Tell the user to back up the drafts folder first.

## After the user edits the result in CapCut

```bash
capcut-recreate learn <drafts-dir>/<job-name>
capcut-recreate feedback <job-id> --rating up|down --note "..."
```

Corrections feed the next plan's worked examples. Treat them as the operator's
taste when planning for the same template again.

## Working through the repo (no local Claude Code)

If the person is running the CLI on their machine but planning happens in a
Claude Code session elsewhere, they commit the job's inputs to a branch:

```bash
git checkout -b work/<name>
mkdir -p work/<name>
cp manifest.json footage.json work/<name>/ ; cp -r chunks thumbs staging/thumbs work/<name>/
git add work/<name> && git commit -m "work: <name> inputs" && git push -u origin work/<name>
```

The session plans from `work/<name>/`, commits the plan files, and pushes. The
person pulls and runs `apply` locally. Thumbnail paths inside the JSON files
are absolute to their machine; when they do not exist in the session, look for
the same basenames under `work/<name>/`.
