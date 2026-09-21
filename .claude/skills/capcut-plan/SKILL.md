---
name: capcut-plan
description: Plan a CapCut recreation job in this session instead of through the Anthropic API. Use when the user asks to plan, fill, or recreate a CapCut template with new footage, or invokes /capcut-plan. Reads the manifest and footage index, looks at thumbnails, writes plan.json, validates it, and optionally applies it.
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
capcut-recreate index ./staging <clips...> -o footage.json
```

## Procedure

1. Export the planner input:
   ```bash
   capcut-recreate prompt manifest.json footage.json --job <name> --brief "<brief>" -o prompt.json
   ```
2. Read `prompt.json`. Follow its `system` text as your instructions. The
   `prompt` field holds the brief, worked examples with operator corrections,
   the template slots, and the new footage.
3. Open every path in `images` with the Read tool and look at it. Template
   slot thumbnails show what the original edit put in each slot; scene
   thumbnails show what each candidate clip offers. Match roles: hook to hook,
   full-frame to full-frame, motion to motion.
4. Write `plan.json` matching `schema`: `job_name` exactly as given, one
   entry per replaceable media slot with `clip_id` and `scene_id` from the
   footage index, and `new_text` for replaceable text slots only. Never
   reference a locked slot. Keep text within each slot's `max_chars`.
5. Validate:
   ```bash
   capcut-recreate check manifest.json footage.json plan.json
   ```
   Fix every reported error and re-check until `ok` is true. At most three
   rounds; then show the user the remaining errors and stop.
6. Show the user the plan with one line of reasoning per slot. Do not apply
   unless they ask. When they do:
   ```bash
   capcut-recreate apply manifest.json footage.json plan.json --store <drafts-dir> --brief "<brief>"
   ```
   CapCut must be closed during apply.

## After the user edits the result in CapCut

```bash
capcut-recreate learn <drafts-dir>/<job-name>
capcut-recreate feedback <job-id> --rating up|down --note "..."
```

Corrections feed the next plan's worked examples. Treat them as the operator's
taste when planning for the same template again.
