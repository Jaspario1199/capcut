"""capcut-recreate command line.

  capcut-recreate manifest  <template-dir> [-o manifest.json]
  capcut-recreate index     <staging-dir> <clip>... [-o footage.json]
  capcut-recreate check     <manifest.json> <footage.json> <plan.json>
  capcut-recreate apply     <manifest.json> <footage.json> <plan.json> --store <drafts-dir>
  capcut-recreate validate  <draft-dir>
  capcut-recreate diff      <draft-a> <draft-b> [--allow REGEX ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .apply import ApplyError, apply_plan, preflight
from .diffing import diff
from .draft import load_doc
from .footage import index_from_json, stage_footage
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


def cmd_manifest(a) -> int:
    m = build_manifest(Path(a.template))
    _dump(m.to_json(), a.out)
    print(json.dumps(m.counts()), file=sys.stderr)
    return 0


def cmd_index(a) -> int:
    idx = stage_footage([Path(p) for p in a.clips], Path(a.staging), scene_threshold=a.scene_threshold)
    _dump(idx.to_json(), a.out)
    return 0


def _load_three(a):
    manifest = manifest_from_json(json.loads(Path(a.manifest).read_text()))
    index = index_from_json(json.loads(Path(a.footage).read_text()))
    plan = Plan.from_json(json.loads(Path(a.plan).read_text()))
    return manifest, index, plan


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
        pre = preflight(template, Path(a.store))
        report = apply_plan(plan, manifest, resolved, Path(a.store), template_dir=template, sync_nested=a.sync_nested)
    except ApplyError as e:
        _dump({"ok": False, "stage": "apply", "error": str(e)}, None)
        return 2
    _dump({"ok": report.ok, "job_dir": report.job_dir, "draft_id": report.draft_id,
           "replaced": len(report.replaced), "batch": report.batch, "invariant_errors": report.invariant_errors,
           "diff_violations": report.diff_violations,
           "lint_summary": (report.lint_info or {}).get("summary") if isinstance(report.lint_info, dict) else None,
           "preflight": {k: bool(v) for k, v in pre.items()}}, None)
    return 0 if report.ok else 2


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

    s = sub.add_parser("manifest"); s.add_argument("template"); s.add_argument("-o", "--out"); s.set_defaults(fn=cmd_manifest)
    s = sub.add_parser("index"); s.add_argument("staging"); s.add_argument("clips", nargs="+"); s.add_argument("-o", "--out")
    s.add_argument("--scene-threshold", type=float, default=27.0); s.set_defaults(fn=cmd_index)
    for name, fn in (("check", cmd_check), ("apply", cmd_apply)):
        s = sub.add_parser(name); s.add_argument("manifest"); s.add_argument("footage"); s.add_argument("plan")
        if name == "apply":
            s.add_argument("--store", required=True); s.add_argument("--template")
            s.add_argument("--sync-nested", action="store_true")
        s.set_defaults(fn=fn)
    s = sub.add_parser("validate"); s.add_argument("draft"); s.set_defaults(fn=cmd_validate)
    s = sub.add_parser("diff"); s.add_argument("a"); s.add_argument("b"); s.add_argument("--allow", action="append"); s.set_defaults(fn=cmd_diff)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
