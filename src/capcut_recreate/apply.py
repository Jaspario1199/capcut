"""Phase 4: apply a validated plan inside a drafts store.

Sequence (each step verified by the adversarial review against capcut-cli 0.25.0):

  1. capcut init <job> --template <template> --drafts <store>
  2. per media slot: capcut replace-media <seg> <clip>   (no --retime)
       assert new_duration_us != null, assert staged asset md5 == source md5
  3. one capcut batch: trim per media slot (string times), set-text per text slot
  4. capcut register --materials --apply ; capcut lint --fix
  4b. refresh material.unique_id on every replaced video material (see _refresh_unique_ids)
  5. own invariant validator
  6. own path-level diff against the template with an allowlist
  7. capcut lint for information only

Any failure after step 1 removes the job folder and its root_meta_info entry
is left for `capcut register` to reconcile; the store is never half-written
because every capcut-cli write is atomic and we abort on the first error.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import runner
from .diffing import apply_allowlist, diff
from .draft import US, find_doc, is_placeholder_path, iter_segments, load_doc, resolve_media_path, external_media_ids
from .footage import md5_file
from .library import Library
from .manifest import Manifest
from .plan import Plan, ResolvedMedia

LINT_NO_CAP_SECS = 100_000
LINT_NO_CAP_CHARS = 100_000
KNOWN_WARNING = re.compile(r"^New clip is [\d.]+s but the segment uses up to [\d.]+s of source")


class ApplyError(Exception):
    pass


@dataclass
class ApplyReport:
    job_dir: str
    draft_id: str
    replaced: list[dict[str, Any]] = field(default_factory=list)
    batch: dict[str, Any] | None = None
    register: dict[str, Any] | None = None
    lint_fix: dict[str, Any] | None = None
    invariant_errors: list[str] = field(default_factory=list)
    diff_violations: list[str] = field(default_factory=list)
    lint_info: dict[str, Any] | None = None
    unique_ids: dict[str, str] = field(default_factory=dict)
    library_job_id: str | None = None
    ok: bool = False


def _us_to_s(us: int) -> str:
    # capcut-cli's batch parser rejects a numeric 0; always pass "<seconds>s".
    return f"{us / US:.6f}s"


def capcut_running() -> bool:
    """True when the CapCut desktop app has a live process (Windows or macOS)."""
    import platform
    import subprocess

    try:
        if platform.system() == "Windows":
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq CapCut.exe", "/NH"], capture_output=True, text=True, check=False)
            return "CapCut.exe" in out.stdout
        out = subprocess.run(["pgrep", "-x", "CapCut"], capture_output=True, text=True, check=False)
        return out.returncode == 0
    except OSError:
        return False


def template_support(template_dir: Path) -> dict[str, Any]:
    """capcut-cli's own verdict on the draft's version stamp."""
    data = runner.run_raw("version", str(template_dir)).data or {}
    support = data.get("support") or {}
    return {"app_version": data.get("app_version"), "write_guard": support.get("write_guard"),
            "status": support.get("status"), "notes": support.get("notes") or []}


def preflight(template_dir: Path, store: Path, force_write: bool = False,
              allow_untested_version: bool = False) -> dict[str, Any]:
    runner.check_version()
    if capcut_running() and not force_write:
        raise ApplyError("CapCut is running. Close it before applying; the app rewrites the project index "
                         "and can overwrite or lose the new draft. (Scratch stores may pass force_write.)")
    support = template_support(template_dir)
    if support.get("write_guard") == "refuse" and not (allow_untested_version or force_write):
        raise ApplyError(
            f"capcut-cli has no evidence it can round-trip drafts stamped CapCut {support.get('app_version')} "
            "(mobile-made projects carry the mobile version). Pass allow_untested_version after backing up the "
            "drafts folder, then verify the result opens in the app (Phase 0 in docs/WORKFLOW.md).")
    doctor = runner.run_raw("doctor", drafts=str(store)).data or {}
    tools = doctor.get("tools") or doctor
    if isinstance(tools, dict) and tools.get("ffprobe") in (False, None) and "ffprobe" in json.dumps(doctor):
        # Only trust an explicit negative. Shapes differ across versions.
        if tools.get("ffprobe") is False:
            raise ApplyError("capcut doctor reports ffprobe missing")
    if shutil.which("ffprobe") is None:
        raise ApplyError("ffprobe not on PATH")
    diag = runner.run_raw("diagnose", str(template_dir)).data or {}
    nested = template_dir / "Timelines"
    if nested.exists():
        proj = nested / "project.json"
        main_id = None
        if proj.exists():
            try:
                main_id = json.loads(proj.read_text()).get("main_timeline_id")
            except json.JSONDecodeError:
                main_id = None
        if main_id:
            nested_doc = nested / main_id / "draft_info.json"
            if nested_doc.exists():
                root = load_doc(template_dir)
                inner = json.loads(nested_doc.read_text())
                if json.dumps(root.get("tracks"), sort_keys=True) != json.dumps(inner.get("tracks"), sort_keys=True):
                    raise ApplyError("template root document differs from nested Timelines document; "
                                     "the root may be a stale mirror. Refusing to clone it.")
    return {"doctor": doctor, "diagnose": diag, "support": support}


def apply_plan(plan: Plan, manifest: Manifest, resolved: list[ResolvedMedia], store: Path,
               template_dir: Path | None = None, sync_nested: bool = False,
               library: "Library | None" = None, footage_index: dict[str, Any] | None = None,
               brief: str = "", force_write: bool = False, allow_untested_version: bool = False) -> ApplyReport:
    template_dir = Path(template_dir or manifest.template_dir)
    store = Path(store)
    job_dir = store / plan.job_name
    if job_dir.exists():
        raise ApplyError(f"{job_dir} already exists")
    # One capcut-cli flag covers both overrides; our preflight keeps them distinct.
    fw = force_write or allow_untested_version

    init = runner.run("init", plan.job_name, template=str(template_dir), drafts=str(store), force_write=fw)
    job_dir = Path(init.get("draft_path") or job_dir)
    if not job_dir.exists():
        raise ApplyError(f"init reported {job_dir} but it does not exist")
    doc_path = find_doc(job_dir)
    template_doc = load_doc(template_dir)
    report = ApplyReport(job_dir=str(job_dir), draft_id=str(load_doc(job_dir).get("id", "")))

    try:
        # init copies assets/ but the cloned document still points at the
        # template's files (absolute, as CapCut writes them) or at relative
        # paths lint and the app resolve against the wrong base. Relink every
        # material to the clone's own assets/ so the job is self-contained.
        runner.run("relink", str(doc_path), **{"from": str(template_dir), "to": str(job_dir)}, force_write=fw)
        for kind in ("video", "audio", "image"):
            adir = job_dir / "assets" / kind
            if adir.is_dir():
                runner.run("relink", str(doc_path), dir=str(adir), force_write=fw)
        _check_relink(template_doc, load_doc(job_dir), job_dir)

        media_slots = manifest.media_by_id()
        for r in resolved:
            res = runner.run("replace-media", str(doc_path), r.slot_id, r.clip_path, force_write=fw)
            # A still has no duration; replace-media then leaves the photo material's nominal
            # 3 h duration alone, which is what CapCut wants. Videos must probe.
            if r.kind != "image" and res.get("new_duration_us") in (None, 0):
                raise ApplyError(f"{r.slot_id}: replace-media returned no duration (ffprobe?)")
            new_path = Path(res["new_path"])
            if not new_path.is_absolute():
                new_path = job_dir / new_path
            if md5_file(new_path) != r.clip_md5:
                raise ApplyError(f"{r.slot_id}: staged asset {new_path} md5 differs from source (basename collision?)")
            w = res.get("warning")
            if w and not KNOWN_WARNING.match(w):
                raise ApplyError(f"{r.slot_id}: unexpected replace-media warning: {w}")
            report.replaced.append(res)

        ops = []
        for r in resolved:
            if r.kind == "image":
                continue  # a photo segment's source range is not an in-point into a file
            ops.append({"cmd": "trim", "id": r.slot_id, "start": _us_to_s(r.in_point_us),
                        "duration": _us_to_s(r.source_duration_us)})
        for t in plan.text:
            ops.append({"cmd": "set-text", "id": t.slot_id, "text": t.new_text})
        if ops:
            stdin = "\n".join(json.dumps(o, ensure_ascii=False) for o in ops) + "\n"
            report.batch = runner.run("batch", str(doc_path), stdin=stdin, force_write=fw)

        # capcut-cli refuses index writes while the app runs; only scratch stores pass force_write.
        report.register = runner.run("register", str(job_dir), apply=True, materials=True, drafts=str(store),
                                     force_write=fw)
        # lint --fix is run for its media-outside-draft staging and draft_materials linking only.
        # Its caption rules would otherwise shorten or re-wrap template text (the 0805 titles run
        # 14 s and lint capped them at 7 s), so push those thresholds out of reach.
        fix = runner.run_raw("lint", str(doc_path), fix=True, force_write=fw,
                             max_cue_secs=LINT_NO_CAP_SECS, max_chars=LINT_NO_CAP_CHARS, min_gap_ms=0)
        report.lint_fix = fix.data
        if fix.status not in (0, 1, 2):
            raise ApplyError(f"lint --fix crashed: {fix.stderr}")
        if sync_nested:
            runner.run("sync-timelines", str(job_dir), nested=True, apply=True, force_write=fw)
        # Last write: nothing from capcut-cli runs after this, so its changed-on-disk guard never trips.
        report.unique_ids = _refresh_unique_ids(job_dir, [media_slots[r.slot_id].material_id for r in resolved],
                                                nested=sync_nested)

        from .validate import new_invariant_errors
        new_doc = load_doc(job_dir)
        report.invariant_errors = new_invariant_errors(template_doc, new_doc)

        seg_track = {ref.id: ref.track_id for ref in iter_segments(template_doc)}
        replaced_mats = [media_slots[r.slot_id].material_id for r in resolved]
        text_mats = [manifest.text_by_id()[t.slot_id].material_id for t in plan.text]
        # lint --fix stages media that lives outside the template (CapCut's online-material and
        # music caches) into assets/ and renames the material after the file; CapCut restores the
        # cache path on save. Allow that bookkeeping on exactly those materials.
        external = external_media_ids(template_doc, template_dir)
        extra = [rf"^materials\.(videos|audios)\[{re.escape(mid)}\]\.(name|material_name)$" for mid in external]
        allow = apply_allowlist([r.slot_id for r in resolved], [t.slot_id for t in plan.text],
                                replaced_mats, text_mats, seg_track, extra=extra)
        d = diff(template_doc, new_doc)
        report.diff_violations = [str(c) for c in d.filtered(allow)]

        report.lint_info = runner.run_raw("lint", str(doc_path)).data
        report.ok = not report.invariant_errors and not report.diff_violations
        if not report.ok:
            raise ApplyError("validation failed: " + "; ".join(report.invariant_errors + report.diff_violations))
        if library is not None:
            rec = library.record_apply(
                job_id=report.draft_id or plan.job_name, template_doc=template_doc, template_dir=str(template_dir),
                job_dir=str(job_dir), manifest=manifest.to_json(), footage_index=footage_index or {},
                plan={"job_name": plan.job_name, "media": [m.__dict__ for m in plan.media], "text": [t.__dict__ for t in plan.text]},
                written_doc=new_doc, brief=brief,
            )
            report.library_job_id = rec.job_id
        return report
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        _drop_root_entry(store, str(job_dir))
        raise


def _check_relink(template_doc: dict[str, Any], new_doc: dict[str, Any], job_dir: Path) -> None:
    """Every media material must keep its basename and resolve to a real file inside the job.

    Accepts CapCut's draft-folder placeholder token (9.x) as well as absolute paths.
    """
    old = {m["id"]: m for cat in ("videos", "audios") for m in template_doc["materials"].get(cat, [])}
    for cat in ("videos", "audios"):
        for m in new_doc["materials"].get(cat, []):
            path = m.get("path")
            if not isinstance(path, str) or not path or path.startswith("http"):
                continue
            before = old.get(m["id"], {}).get("path", "")
            if Path(path.replace("\\", "/")).name != Path(before.replace("\\", "/")).name:
                raise ApplyError(f"relink changed basename of {m['id']}: {before} -> {path}")
            resolved = resolve_media_path(path, job_dir)
            if not resolved.exists():
                raise ApplyError(f"{m['id']}: media {path} does not resolve to a file in the job ({resolved})")
            if not is_placeholder_path(path) and not Path(path).is_absolute():
                raise ApplyError(f"{m['id']}: path {path} is neither absolute nor a draft placeholder after relink")


def unique_id_for(path: str) -> str:
    """CapCut desktop's material.unique_id: MD5 of the absolute path with forward slashes.

    Verified on CapCut 9.5.0 (2026-09-21): the app keys its per-file probe cache
    (User Data/Cache/importcache3/mediainfo/<unique_id>.json) by this value.
    """
    return hashlib.md5(path.replace("\\", "/").encode("utf-8")).hexdigest()


def _refresh_unique_ids(job_dir: Path, material_ids: list[str], nested: bool = False) -> dict[str, str]:
    """Recompute unique_id for replaced video materials.

    replace-media keeps the template's unique_id, so CapCut reads the template
    file's cached probe (duration, dimensions, stream layout) for the new file
    and the clip never finishes loading: striped track, black preview. Seen on
    biopics-v1, every replaced slot; the same-file phase-0 test could not show it.
    """
    docs = [find_doc(job_dir)]
    if nested:
        proj = job_dir / "Timelines" / "project.json"
        if proj.exists():
            main_id = json.loads(proj.read_text()).get("main_timeline_id")
            for name in ("draft_content.json", "draft_info.json"):
                inner = job_dir / "Timelines" / str(main_id) / name
                if main_id and inner.exists():
                    docs.append(inner)
    wanted = set(material_ids)
    out: dict[str, str] = {}
    for doc_path in docs:
        doc = json.loads(doc_path.read_bytes())
        changed = False
        for m in doc["materials"].get("videos", []):
            if m.get("id") in wanted and m.get("path"):
                uid = unique_id_for(m["path"])
                if m.get("unique_id") != uid:
                    m["unique_id"] = uid
                    changed = True
                out[m["id"]] = uid
        if changed:
            doc_path.write_bytes(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    missing = wanted - set(out)
    if missing:
        raise ApplyError(f"unique_id refresh: replaced materials not found in document: {sorted(missing)}")
    return out


def _drop_root_entry(store: Path, job_dir: str) -> None:
    p = store / "root_meta_info.json"
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError:
        return
    entries = data.get("all_draft_store") or []
    kept = [e for e in entries if e.get("draft_fold_path") != job_dir]
    if len(kept) != len(entries):
        data["all_draft_store"] = kept
        p.write_text(json.dumps(data, ensure_ascii=False))
