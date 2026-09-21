"""capcut-recreate command line.

  capcut-recreate manifest  <template-dir> [-o manifest.json] [--thumbs DIR]
  capcut-recreate index     <staging-dir> <clip>... [-o footage.json]
  capcut-recreate prompt    <manifest.json> <footage.json> --job NAME --brief TEXT [-o prompt.json]   (no API: for Claude Code / any LLM)
  capcut-recreate plan      <manifest.json> <footage.json> --job NAME --brief TEXT [-o plan.json]     (Anthropic API)
  capcut-recreate check     <manifest.json> <footage.json> <plan.json>
  capcut-recreate apply     <manifest.json> <footage.json> <plan.json> --store <drafts-dir> [--brief TEXT]
  capcut-recreate learn     <job-dir>            capture operator corrections after editing in CapCut
  capcut-recreate feedback  <job-id> --rating up|down [--note TEXT]
  capcut-recreate library   [--limit N]
  capcut-recreate validate  <draft-dir>
  capcut-recreate diff      <draft-a> <draft-b> [--allow REGEX ...]

The library defaults to ~/.capcut-recreate/library or $CAPCUT_RECREATE_LIBRARY.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .apply import ApplyError, apply_plan, preflight
from .diffing import diff
from .draft import load_doc
from .footage import index_from_json, stage_footage
from .library import Library
from .manifest import build_manifest, manifest_from_json
from .plan import Plan, validate_plan
from .validate import validate_dir


def _dump(obj, out: str | None) -> None:
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    if out:
        Path(out).write_text(text + "\n")
        print(out)
    else:
        print(text)


def _lib(a) -> Library:
    return Library(Path(a.library)) if getattr(a, "library", None) else Library()


def cmd_manifest(a) -> int:
    m = build_manifest(Path(a.template), thumbs_dir=Path(a.thumbs) if a.thumbs else None)
    _dump(m.to_json(), a.out)
    print(json.dumps(m.counts()), file=sys.stderr)
    return 0


def cmd_index(a) -> int:
    idx = stage_footage([Path(p) for p in a.clips], Path(a.staging), scene_threshold=a.scene_threshold,
                        thumbnails=not a.no_thumbs)
    _dump(idx.to_json(), a.out)
    return 0


def _load_two(a):
    manifest = manifest_from_json(json.loads(Path(a.manifest).read_text()))
    index = index_from_json(json.loads(Path(a.footage).read_text()))
    return manifest, index


def _load_three(a):
    manifest, index = _load_two(a)
    plan = Plan.from_json(json.loads(Path(a.plan).read_text()))
    return manifest, index, plan


def cmd_plan(a) -> int:
    from .planner import plan_with_claude

    try:
        from anthropic import Anthropic
    except ImportError:
        print("pip install 'capcut-recreate[llm]' for the planner", file=sys.stderr)
        return 2
    manifest, index = _load_two(a)
    lib = _lib(a)
    examples = lib.find_examples(load_doc(Path(manifest.template_dir)), k=a.examples) if a.examples else []
    plan, attempts = plan_with_claude(manifest, index, a.brief, examples, client=Anthropic(), job_name=a.job,
                                      model=a.model, max_attempts=a.attempts, effort=a.effort,
                                      use_fallbacks=not a.no_fallbacks, include_images=not a.no_images)
    if plan is None:
        _dump({"ok": False, "attempts": attempts}, None)
        return 1
    out = {"job_name": plan.job_name, "media": [m.__dict__ for m in plan.media], "text": [t.__dict__ for t in plan.text]}
    _dump(out, a.out)
    print(json.dumps({"attempts": len(attempts), "examples_used": len(examples)}), file=sys.stderr)
    return 0


def cmd_prompt(a) -> int:
    """Write the planner input for an LLM that is not called through the API."""
    from .planner import export_prompt

    manifest, index = _load_two(a)
    lib = _lib(a)
    examples = lib.find_examples(load_doc(Path(manifest.template_dir)), k=a.examples) if a.examples else []
    out = export_prompt(manifest, index, a.brief, examples, a.job)
    _dump(out, a.out)
    print(json.dumps({"images": len(out["images"]), "examples_used": len(examples)}), file=sys.stderr)
    return 0


def cmd_check(a) -> int:
    manifest, index, plan = _load_three(a)
    errors, resolved = validate_plan(plan, manifest, index)
    _dump({"ok": not errors, "errors": errors, "resolved": [r.__dict__ for r in resolved]}, None)
    return 0 if not errors else 1


def cmd_apply(a) -> int:
    manifest, index, plan = _load_three(a)
    errors, resolved = validate_plan(plan, manifest, index)
    if errors:
        _dump({"ok": False, "stage": "plan", "errors": errors}, None)
        return 1
    template = Path(a.template or manifest.template_dir)
    try:
        pre = preflight(template, Path(a.store), force_write=a.force_write)
        report = apply_plan(plan, manifest, resolved, Path(a.store), template_dir=template, sync_nested=a.sync_nested,
                            library=None if a.no_library else _lib(a), footage_index=index.to_json(), brief=a.brief or "",
                            force_write=a.force_write)
    except ApplyError as e:
        _dump({"ok": False, "stage": "apply", "error": str(e)}, None)
        return 2
    _dump({"ok": report.ok, "job_dir": report.job_dir, "draft_id": report.draft_id, "library_job_id": report.library_job_id,
           "replaced": len(report.replaced), "batch": report.batch, "invariant_errors": report.invariant_errors,
           "diff_violations": report.diff_violations,
           "lint_summary": (report.lint_info or {}).get("summary") if isinstance(report.lint_info, dict) else None,
           "preflight": {k: bool(v) for k, v in pre.items()}}, None)
    return 0 if report.ok else 2


def cmd_learn(a) -> int:
    from .corrections import capture

    lib = _lib(a)
    rec = lib.find_by_job_dir(Path(a.job_dir))
    if rec is None:
        _dump({"ok": False, "error": f"no library record for {a.job_dir}; apply with the library enabled first"}, None)
        return 1
    corrections, rewrites = capture(Path(a.job_dir), rec)
    rec.corrections = corrections
    rec.app_rewrite_paths = rewrites
    rec.learned_at = time.time()
    lib.save(rec)
    _dump({"ok": True, "job_id": rec.job_id, "corrections": [c.__dict__ for c in corrections],
           "app_rewrite_paths": rewrites}, None)
    return 0


def cmd_feedback(a) -> int:
    rec = _lib(a).record_feedback(a.job_id, a.rating, a.note or "")
    _dump({"ok": True, "job_id": rec.job_id, "feedback": rec.feedback}, None)
    return 0


def cmd_library(a) -> int:
    recs = _lib(a).all()
    recs.sort(key=lambda r: -r.created_at)
    _dump([{"job_id": r.job_id, "job_dir": r.job_dir, "template": r.template_fingerprint, "brief": r.brief,
            "corrections": len(r.corrections), "feedback": r.feedback.get("rating"), "learned": bool(r.learned_at)}
           for r in recs[: a.limit]], None)
    return 0


def cmd_validate(a) -> int:
    errors = validate_dir(Path(a.draft))
    _dump({"ok": not errors, "errors": errors}, None)
    return 0 if not errors else 1


def cmd_diff(a) -> int:
    d = diff(load_doc(Path(a.a)), load_doc(Path(a.b)))
    changes = d.filtered(a.allow or [])
    _dump({"changes": [str(c) for c in changes], "count": len(changes)}, None)
    return 0 if not changes else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="capcut-recreate")
    sub = p.add_subparsers(dest="cmd", required=True)

    def lib_arg(sp):
        sp.add_argument("--library", help="library root (default ~/.capcut-recreate/library)")

    s = sub.add_parser("manifest"); s.add_argument("template"); s.add_argument("-o", "--out"); s.add_argument("--thumbs")
    s.set_defaults(fn=cmd_manifest)

    s = sub.add_parser("index"); s.add_argument("staging"); s.add_argument("clips", nargs="+"); s.add_argument("-o", "--out")
    s.add_argument("--scene-threshold", type=float, default=27.0); s.add_argument("--no-thumbs", action="store_true")
    s.set_defaults(fn=cmd_index)

    s = sub.add_parser("plan"); s.add_argument("manifest"); s.add_argument("footage"); s.add_argument("--job", required=True)
    s.add_argument("--brief", required=True); s.add_argument("-o", "--out"); s.add_argument("--examples", type=int, default=3)
    s.add_argument("--model", default="claude-opus-5"); s.add_argument("--attempts", type=int, default=3)
    s.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    s.add_argument("--no-fallbacks", action="store_true"); s.add_argument("--no-images", action="store_true"); lib_arg(s)
    s.set_defaults(fn=cmd_plan)

    s = sub.add_parser("prompt"); s.add_argument("manifest"); s.add_argument("footage"); s.add_argument("--job", required=True)
    s.add_argument("--brief", required=True); s.add_argument("-o", "--out"); s.add_argument("--examples", type=int, default=3); lib_arg(s)
    s.set_defaults(fn=cmd_prompt)

    s = sub.add_parser("check"); s.add_argument("manifest"); s.add_argument("footage"); s.add_argument("plan"); s.set_defaults(fn=cmd_check)

    s = sub.add_parser("apply"); s.add_argument("manifest"); s.add_argument("footage"); s.add_argument("plan")
    s.add_argument("--store", required=True); s.add_argument("--template"); s.add_argument("--sync-nested", action="store_true")
    s.add_argument("--brief"); s.add_argument("--no-library", action="store_true"); lib_arg(s)
    s.add_argument("--force-write", action="store_true",
                   help="proceed while CapCut is running (only for scratch stores the app does not read)")
    s.set_defaults(fn=cmd_apply)

    s = sub.add_parser("learn"); s.add_argument("job_dir"); lib_arg(s); s.set_defaults(fn=cmd_learn)
    s = sub.add_parser("feedback"); s.add_argument("job_id"); s.add_argument("--rating", required=True, choices=["up", "down"])
    s.add_argument("--note"); lib_arg(s); s.set_defaults(fn=cmd_feedback)
    s = sub.add_parser("library"); s.add_argument("--limit", type=int, default=20); lib_arg(s); s.set_defaults(fn=cmd_library)

    s = sub.add_parser("validate"); s.add_argument("draft"); s.set_defaults(fn=cmd_validate)
    s = sub.add_parser("diff"); s.add_argument("a"); s.add_argument("b"); s.add_argument("--allow", action="append"); s.set_defaults(fn=cmd_diff)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
