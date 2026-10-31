"""`ctxb` command-line interface.

Rule: this module and everything it imports at top level are stdlib-only, so latency-critical
commands (statusline, hooks — M8) start fast. Heavy imports happen inside command functions.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from ..core.errors import CBError

if TYPE_CHECKING:  # type-only: keeps the CLI import path light
    from ..core.app import App

EXIT_OK, EXIT_ERR, EXIT_WARN = 0, 1, 2


def _print_json(obj: object) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def cmd_init(args: argparse.Namespace) -> int:
    from ..core.app import open_app

    app = open_app(args.home)
    try:
        print(f"ContextBridge data directory ready: {app.paths.root}")
        print(f"  database: {app.paths.db.name}   config: {app.paths.config.name}")
        print("Next: `ctxb doctor` to check health, or connect a client (see docs/INSTALL.md).")
    finally:
        app.close()
    return EXIT_OK


def cmd_projects(args: argparse.Namespace) -> int:
    from ..core.app import open_app
    from ..core.projects import list_projects

    app = open_app(args.home)
    try:
        rows = list_projects(app.conn, include_archived=args.all)
    finally:
        app.close()
    if args.json:
        _print_json([r.to_dict() for r in rows])
        return EXIT_OK
    if not rows:
        print("No projects yet. Create one from your AI assistant (create_project).")
        return EXIT_OK
    print(f"{'SLUG':<28} {'UPDATED':<26} {'CKPT AGE':>9} {'CONFL':>5}  NAME")
    for r in rows:
        age = f"{r.last_checkpoint_age_min}m" if r.last_checkpoint_age_min is not None else "never"
        print(f"{r.slug:<28} {r.updated_at:<26} {age:>9} {r.open_conflicts:>5}  {r.name}")
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    from .doctor import run_doctor

    report = run_doctor(args.home)
    if args.json:
        _print_json(report.to_dict())
    else:
        for c in report.checks:
            mark = {"ok": "✓", "warn": "!", "fail": "✗", "info": "·"}[c.status]
            print(f" {mark} {c.name}: {c.detail}")
        print(f"\nOverall: {report.overall}")
    return {"ok": EXIT_OK, "warn": EXIT_WARN, "fail": EXIT_ERR}[report.overall]


def cmd_search(args: argparse.Namespace) -> int:
    from ..core.app import open_app
    from ..core.knowledge import search
    from ..core.projects import get_project_id

    app = open_app(args.home)
    try:
        pid = get_project_id(app.conn, args.project)
        out = search(app.conn, pid, " ".join(args.query), kinds=args.kind or None,
                     include_superseded=args.all, include_archive=args.archive, limit=args.limit)
    finally:
        app.close()
    if args.json:
        _print_json(out)
        return EXIT_OK
    if not out["hits"]:
        print("No matches.")
    for h in out["hits"]:
        if h["source"] == "knowledge":
            print(f"[{h['id']}] {h['kind']:<17} {h['status']:<10} {h['topic_key']}: {h['title']}")
        else:
            print(f"[archive {h['id']}] {h['title']}")
        print(f"      {h['snippet']}")
    return EXIT_OK


def cmd_conflicts(args: argparse.Namespace) -> int:
    from ..core.app import open_app
    from ..core.knowledge import list_conflicts
    from ..core.projects import get_project_id

    app = open_app(args.home)
    try:
        rows = list_conflicts(app.conn, get_project_id(app.conn, args.project),
                              "resolved" if args.resolved else "open")
    finally:
        app.close()
    if args.json:
        _print_json(rows)
        return EXIT_OK
    if not rows:
        print("No open conflicts." if not args.resolved else "No resolved conflicts.")
    for r in rows:
        print(f"#{r['id']} {r['kind']} '{r['topic_key']}'  A=[{r['entry_a']}] {r['a_title']}  vs  B=[{r['entry_b']}] {r['b_title']}")
        if r["resolution"]:
            print(f"      resolved: {r['resolution']} ({r['note']})")
    return EXIT_WARN if rows and not args.resolved else EXIT_OK


def _project_app(args: argparse.Namespace) -> tuple[App, int]:
    from ..core.app import open_app
    from ..core.projects import get_project_id

    app = open_app(args.home)
    try:
        return app, get_project_id(app.conn, args.project)
    except BaseException:
        app.close()
        raise


def cmd_checkpoint(args: argparse.Namespace) -> int:
    from ..core.checkpoints import CheckpointIn, checkpoint

    app, pid = _project_app(args)
    try:
        out = checkpoint(app.conn, pid, CheckpointIn(summary=args.summary, where_stopped=args.where,
                                                     next_action=args.next, session_label=args.session,
                                                     client="cli"))
    finally:
        app.close()
    print(f"Checkpoint {out['checkpoint_id']} saved (session {out['session_id']}).")
    return EXIT_OK


def cmd_snapshots(args: argparse.Namespace) -> int:
    from ..core.snapshots import create_snapshot, list_snapshots

    app, pid = _project_app(args)
    try:
        if args.create:
            for comp in ("knowledge", "state"):
                sid, new = create_snapshot(app.conn, pid, comp, label=args.label or "manual", reason="ctxb snapshots --create")
                print(f"{comp}: snapshot {sid}" + ("" if new else " (unchanged, existing snapshot reused)"))
            return EXIT_OK
        rows = list_snapshots(app.conn, pid, args.component)
    finally:
        app.close()
    if args.json:
        _print_json(rows)
        return EXIT_OK
    if not rows:
        print("No snapshots yet (they are taken at each checkpoint).")
    print(f"{'ID':>5}  {'COMPONENT':<10} {'VER':>4}  {'CREATED':<26} {'BYTES':>7}  LABEL")
    for r in rows:
        print(f"{r['id']:>5}  {r['component']:<10} {r['version']:>4}  {r['created_at']:<26} {r['bytes']:>7}  {r['label'] or ''}")
    return EXIT_OK


def cmd_diff(args: argparse.Namespace) -> int:
    from ..core.snapshots import diff

    app, pid = _project_app(args)
    try:
        out = diff(app.conn, pid, args.a, args.b)
    finally:
        app.close()
    if args.json:
        _print_json(out)
        return EXIT_OK
    against = f"snapshot {args.b}" if args.b is not None else "current"
    print(f"{out['component']}: snapshot {args.a} → {against}")
    if out["component"] == "knowledge":
        e = out["entries"]
        print(f"  entries added: {e['added']}  removed: {e['removed']}")
        for c in e["changed"]:
            print(f"  entry {c['id']}: " + ", ".join(f"{k}: {v[0]!r} → {v[1]!r}" for k, v in c["fields"].items()))
    elif out["component"] == "state":
        for name, lines in out["sections"].items():
            print(f"  [{name}]")
            for ln in lines[2:]:
                print(f"    {ln}")
        t = out["tasks"]
        print(f"  tasks added: {t['added']}  removed: {t['removed']}  changed: {[c['id'] for c in t['changed']]}")
    return EXIT_OK


def cmd_restore(args: argparse.Namespace) -> int:
    from ..core.snapshots import restore

    app, pid = _project_app(args)
    try:
        if not args.yes:
            print(f"This restores snapshot {args.snapshot} over the current {args.project} data.\n"
                  "The current version is snapshotted first, so it can be undone. Re-run with --yes.")
            return EXIT_WARN
        out = restore(app.conn, pid, args.snapshot)
    finally:
        app.close()
    print(f"Restored {out['component']} from snapshot {out['restored']}: {out['counts']}")
    print(f"Undo with: ctxb restore {args.project} {out['pre_restore_snapshot']} --yes")
    return EXIT_OK


def cmd_backup(args: argparse.Namespace) -> int:
    from ..core.app import open_app
    from ..core.backup import list_backups, restore_backup
    from ..core.db.migrate import backup_db, prune_backups

    app = open_app(args.home)
    try:
        if args.list:
            rows = list_backups(app.paths)
            if args.json:
                _print_json(rows)
            for r in rows:
                print(f"{r['file']:<60} {r['bytes']:>9}  {r['modified']}")
            if not rows:
                print("No backups yet.")
            return EXIT_OK
        if args.restore:
            if not args.yes:
                print(f"This replaces the WHOLE database with backups/{args.restore}.\n"
                      "A safety backup of the current DB is taken first. Close other clients, then re-run with --yes.")
                return EXIT_WARN
            out = restore_backup(app.conn, app.paths, args.restore, app.config.backups_keep)
            print(f"Database restored from {out['restored_from']}. Safety copy: backups/{out['safety_backup']}")
            return EXIT_OK
        dest = backup_db(app.conn, app.paths, "manual")
        prune_backups(app.paths, app.config.backups_keep)
        print(f"Backup written: {dest}")
        return EXIT_OK
    finally:
        app.close()


def cmd_handoff(args: argparse.Namespace) -> int:
    from ..core.handoff import prepare_handoff

    app, pid = _project_app(args)
    try:
        out = prepare_handoff(app.conn, app.paths, app.config, pid, mode=args.mode, profile=args.profile,
                              fmt=args.format)
    finally:
        app.close()
    if args.json:
        _print_json(out)
        return EXIT_OK
    print(f"Handoff written: {out['primary_file']}")
    for f in out["files"]:
        t = f["est_tokens_range"]
        print(f"  {f['bytes']:>9} B  ~{t['low']}-{t['high']} tokens  {f['path'].rsplit('/', 1)[-1]}")
    for line in out["summary_3_lines"]:
        print(f"  {line}")
    for m in out["report"]["missing"]:
        print(f"  ! {m}")
    for w in out["warnings"]:
        print(f"  ! {w}")
    return EXIT_OK


def cmd_context(args: argparse.Namespace) -> int:
    from ..core.context import get_context

    app, pid = _project_app(args)
    try:
        out = get_context(app.conn, app.paths, app.config, pid, scope=args.scope, budget_tokens=args.budget,
                          topics=args.topic)
    finally:
        app.close()
    if args.json:
        _print_json(out)
    else:
        print(out["content"])
        if "file_path" in out:
            print(f"\n(written to {out['file_path']})", file=sys.stderr)
    return EXIT_OK


def cmd_link(args: argparse.Namespace) -> int:
    from ..core.repo import link

    if not args.unlink and not args.directory:
        raise CBError("invalid_input", "give a directory to link, or --unlink")
    app, pid = _project_app(args)
    try:
        value = link(app.conn, pid, None if args.unlink else args.directory)
    finally:
        app.close()
    if value is None:
        print(f"Unlinked {args.project}; handoffs will no longer include repo details.")
    else:
        print(f"Linked {args.project} -> {value}. Handoffs will include branch, last commit and changed paths "
              "(read with git; file contents are never exported).")
    return EXIT_OK


def cmd_merge(args: argparse.Namespace) -> int:
    """Reassemble split package parts byte for byte and verify against the manifest."""
    import hashlib
    from pathlib import Path

    d = Path(args.package)
    manifest = json.loads((d / "00_manifest.json").read_text(encoding="utf-8"))
    groups: dict[str, list[dict[str, object]]] = {}
    for f in manifest["files"]:
        if f.get("upload") and int(f.get("of", 1)) > 1:
            groups.setdefault(str(f["source"]), []).append(f)
    if not groups:
        print("Nothing to merge: no file in this package was split.")
        return EXIT_OK
    out_dir = (Path(args.output) if args.output else d).resolve()
    root = d.resolve()

    def inside(base: Path, name: str) -> Path:
        # Manifest names are untrusted: plain file names only, resolved inside the package.
        if not name or "/" in name or "\\" in name or name in (".", "..") or Path(name).is_absolute():
            raise CBError("invalid_input", f"unsafe file name in manifest: {name!r}")
        p = (base / name).resolve()
        if not p.is_relative_to(base):
            raise CBError("invalid_input", f"manifest entry escapes the package folder: {name!r}")
        return p

    bad = 0
    for source, parts in groups.items():
        parts.sort(key=lambda f: int(f["part"]))  # type: ignore[call-overload]
        data = b"".join(inside(root, str(f["name"])).read_bytes() for f in parts)
        ext = str(parts[0]["name"]).rsplit(".", 1)[-1]
        ok = hashlib.sha256(data).hexdigest() == parts[0].get("source_sha256")
        bad += not ok
        if not ok:
            print(f"✗ {source}: {len(parts)} parts, {len(data)} bytes, HASH MISMATCH (nothing written)")
            continue
        target = inside(out_dir, f"{source}.merged.{ext}")
        if target.exists():
            raise CBError("conflict", f"{target} already exists", "choose another --output directory")
        target.write_bytes(data)
        print(f"✓ {target} ({len(parts)} parts, {len(data)} bytes)")
    return EXIT_ERR if bad else EXIT_OK


def cmd_export(args: argparse.Namespace) -> int:
    from ..core.app import open_app
    from ..core.transfer import export_full

    if not args.full:
        raise CBError("invalid_input", "only full exports exist: use `ctxb export --full`",
                      "for a package to give to an AI, use `ctxb handoff`")
    app = open_app(args.home, log_to_stderr=False)
    try:
        out = export_full(app.conn, app.paths, args.project or None, args.output)
    finally:
        app.close()
    if args.json:
        _print_json(out)
        return EXIT_OK
    print(f"Exported {len(out['projects'])} project(s) to {out['path']} ({out['bytes']} bytes)")
    print("This file holds your private project memory. Move it to the new Mac and run:")
    print(f"  ctxb import --full {out['path'].rsplit('/', 1)[-1]}")
    return EXIT_OK


def cmd_import(args: argparse.Namespace) -> int:
    from ..core.app import open_app

    if args.full:
        from ..core.transfer import import_full

        app = open_app(args.home, log_to_stderr=False)
        try:
            out = import_full(app.conn, app.paths, args.source, only=args.project or None, as_slug=args.as_slug,
                              dry_run=args.dry_run)
        finally:
            app.close()
        if args.json:
            _print_json(out)
            return EXIT_OK
        if out.get("dry_run"):
            for p in out["would_import"]:
                print(f"would import {p['slug']}: {p['rows']} rows")
            return EXIT_OK
        for r in out["imported"]:
            ids = "ids preserved" if r["ids_preserved"] else f"ids remapped ({', '.join(r['id_map'])}; see --json)"
            print(f"✓ {r['slug']}: {sum(r['counts'].values())} rows, {ids}"
                  + (f", {r['redactions']} secret(s) redacted" if r["redactions"] else ""))
        print(f"Backup taken first: {out['backup']}")
        return EXIT_OK

    from pathlib import Path

    from ..core.importer import staging as imp
    from ..core.projects import get_project_id

    if not args.project or len(args.project) != 1:
        raise CBError("invalid_input", "say which project to import into: --project SLUG")
    if not args.type:
        raise CBError("invalid_input", "say what the source is: --type package|markdown|transcript_claude_code|"
                                       "claude_export|text")
    app = open_app(args.home, log_to_stderr=False)
    try:
        pid = get_project_id(app.conn, args.project[0])
        src = str(Path(args.source).expanduser().resolve()) if args.source != "-" else None
        text = sys.stdin.read() if args.source == "-" else None
        staged = imp.stage(app.conn, app.paths, app.config, pid, args.type, path=src, text=text, restrict=False)
        if not args.yes:
            print(staged["preview"])
            for n in staged["notes"]:
                print(f"note: {n}")
            if staged["redactions"]:
                print(f"redacted: {staged['redactions']}")
            print(f"\nNothing saved yet. Commit with: ctxb import-commit {args.project[0]} {staged['staging_id']}"
                  "   (or re-run with --yes; --discard to drop it)")
            return EXIT_OK
        out = imp.commit(app.conn, pid, staged["staging_id"])
    finally:
        app.close()
    return _print_commit(out, args.json)


def _print_commit(out: dict[str, Any], as_json: bool) -> int:
    if as_json:
        _print_json(out)
        return EXIT_OK
    results = out.get("item_results") or []
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print(f"Committed: {counts or 'no knowledge items'}; archived: "
          f"{sum(1 for a in out.get('archived', []) if a['stored'])}")
    for sk in out.get("skipped", []):
        print(f"  skipped [{sk['index']}]: {sk['reason']}")
    return EXIT_OK


def cmd_import_commit(args: argparse.Namespace) -> int:
    from ..core.importer import staging as imp

    app, pid = _project_app(args)
    try:
        out = imp.discard(app.conn, pid, args.staging_id) if args.discard else imp.commit(app.conn, pid, args.staging_id)
    finally:
        app.close()
    if args.discard:
        print(f"Discarded staging {args.staging_id}; nothing was saved.")
        return EXIT_OK
    return _print_commit(out, False)


def cmd_ingest(args: argparse.Namespace) -> int:
    """Archive a Claude Code transcript into a project (no knowledge items): for hooks (M8) and by hand."""
    from pathlib import Path

    from ..core.importer import staging as imp

    app, pid = _project_app(args)
    try:
        staged = imp.stage(app.conn, app.paths, app.config, pid, "transcript_claude_code",
                           path=str(Path(args.transcript).expanduser().resolve()), restrict=False)
        out = imp.commit(app.conn, pid, staged["staging_id"], select=[])
    finally:
        app.close()
    stored = [a for a in out["archived"] if a["stored"]]
    print(f"Archived {len(stored)} transcript(s)" + ("" if stored else " (already archived)"))
    return EXIT_OK


def _print_install(out: dict[str, Any], as_json: bool) -> None:
    if as_json:
        _print_json(out)
        return
    for k, v in out.items():
        if k == "warnings":
            for w in v:
                print(f"! {w}")
        elif isinstance(v, dict):
            print(f"{k}: " + ", ".join(f"{a}={b}" for a, b in v.items()))
        elif v is not None and v != []:
            print(f"{k}: {v}")


def cmd_install_desktop(args: argparse.Namespace) -> int:
    from ..adapters.install import install_desktop
    from ..core.paths import ensure_layout, resolve_paths

    paths = resolve_paths(args.home)
    ensure_layout(paths)
    _print_install(install_desktop(paths, uninstall=args.uninstall, dry_run=args.dry_run), args.json)
    return EXIT_OK


def cmd_install_code(args: argparse.Namespace) -> int:
    from ..adapters.install import install_code
    from ..core.paths import ensure_layout, resolve_paths

    paths = resolve_paths(args.home)
    ensure_layout(paths)
    _print_install(install_code(paths, uninstall=args.uninstall, dry_run=args.dry_run, hook=not args.no_hook,
                                statusline=not args.no_statusline, force_statusline=args.force_statusline),
                   args.json)
    return EXIT_OK


def cmd_hook(args: argparse.Namespace) -> int:
    """Claude Code hook entry point. Never fails the session: errors print nothing and exit 0."""
    try:
        from ..adapters.claude_code import hooks as h

        data = sys.stdin.read()
        if args.event == "session-start":
            text = h.session_start(data, args.home)
        elif args.event == "stop":
            text = h.stop(data, args.home)
        elif args.event == "pre-compact":
            text = h.pre_compact(data, args.home)
        else:
            text = h.spawn_emergency(data, args.home, args.event)
        if text:
            sys.stdout.write(text + "\n")
    except Exception as exc:  # noqa: BLE001 (a hook must never break the session)
        print(f"contextbridge hook skipped: {type(exc).__name__}", file=sys.stderr)
    return EXIT_OK


def cmd_statusline(args: argparse.Namespace) -> int:
    """Claude Code status line: record a reading and print one line. Never fails loudly."""
    try:
        from ..adapters.claude_code.hooks import statusline

        line = statusline(sys.stdin.read(), args.home)
    except Exception as exc:  # noqa: BLE001
        line = f"ContextBridge: – ({type(exc).__name__})"
    print(line)
    return EXIT_OK


def cmd_emergency(args: argparse.Namespace) -> int:
    from ..core.emergency import emergency

    app, pid = _project_app(args)
    try:
        out = emergency(app.conn, app.paths, app.config, pid, transcript=args.transcript, reason=args.reason,
                        only_if_unsaved=args.only_if_unsaved, session_key=args.session, restrict=False)
    finally:
        app.close()
    if out.get("handoff"):
        print(f"Emergency handoff written: {out['handoff']}")
    else:
        print(f"No handoff written: {out.get('skipped_handoff', 'see log')}")
    if out.get("archived"):
        print(f"Transcript: {'archived' if out['archived'].get('stored') else 'already archived'}")
    if out.get("archive_error"):
        print(f"! transcript not archived: {out['archive_error']}")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    from ..core.app import open_app
    from ..core.budget import budget_status
    from ..core.projects import get_project_id

    app = open_app(args.home, log_to_stderr=False)
    try:
        pid = get_project_id(app.conn, args.project) if args.project else None
        out = budget_status(app.conn, pid, args.session,
                            stale_after_s=int(app.config.get("telemetry.stale_after_seconds", 300)))
    finally:
        app.close()
    if args.json:
        _print_json(out)
        return EXIT_OK

    def show(v: Any) -> str:
        return "unavailable" if v == "unavailable" else f"{v['used_pct']:.0f}%" + (
            f" (resets {v['resets_at']})" if v.get("resets_at") else "")

    print(f"context:   {show(out['context'])}")
    print(f"5-hour:    {show(out['five_hour'])}")
    print(f"weekly:    {show(out['seven_day'])}")
    if out.get("reading_age_s") is not None:
        print(f"reading:   {out['reading_age_s']} s old" + (" (STALE)" if out["stale"] else ""))
    c = out["est_handoff_cost_range"]
    print(f"handoff:   needs ~{c['low']}-{c['high']} tokens; feasible: "
          f"{ {True: 'yes', False: 'NO', None: 'unknown'}[out['feasible']] }")
    print(f"action:    {out['recommendation']} (handoff mode: {out['recommended_mode']})")
    if "context_health" in out:
        h = out["context_health"]
        print(f"project:   last checkpoint {h['minutes_since_checkpoint'] if h['minutes_since_checkpoint'] is not None else 'never'}"
              f" min ago, {h['writes_since_checkpoint']} unsaved write(s), {h['open_conflicts']} open conflict(s), "
              f"{h['uncaptured_sessions']} uncaptured session(s)")
    if out.get("note"):
        print(f"note:      {out['note']}")
    return EXIT_OK


def cmd_telemetry(args: argparse.Namespace) -> int:
    """Generic adapter: any client can push a reading (spec §10.2)."""
    from ..core.app import open_app
    from ..core.budget import Reading, record
    from ..core.db.connection import now_iso
    from ..core.projects import get_project_id

    app = open_app(args.home, log_to_stderr=False)
    try:
        r = Reading(source=f"push:{args.source}", session_key=args.session, ts=now_iso(),
                    context_used_pct=args.context_pct, context_window_tokens=args.context_window,
                    five_hour_pct=args.five_hour_pct, seven_day_pct=args.seven_day_pct,
                    project_id=get_project_id(app.conn, args.project) if args.project else None)
        stored = record(app.conn, r)
    finally:
        app.close()
    print("reading stored" if stored else "reading skipped (throttled: same values within 15 s)")
    return EXIT_OK


def cmd_snippet(args: argparse.Namespace) -> int:
    from ..adapters.snippets import claude_md, desktop_instructions

    print(claude_md(args.project) if args.kind == "claude-md" else desktop_instructions(args.project), end="")
    return EXIT_OK


def cmd_version(_args: argparse.Namespace) -> int:
    from importlib.metadata import version

    print(f"ctxb {version('contextbridge')}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ctxb", description="ContextBridge: persistent, portable project memory.")
    p.add_argument("--home", default=None, help="data directory (default: $CONTEXTBRIDGE_HOME or ~/.contextbridge)")
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def add(name: str, fn: Callable[[argparse.Namespace], int], help_: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_, description=help_)
        sp.set_defaults(func=fn)
        return sp

    add("init", cmd_init, "create the data directory, config and database")
    sp = add("projects", cmd_projects, "list projects")
    sp.add_argument("--all", action="store_true", help="include archived projects")
    sp.add_argument("--json", action="store_true")
    sp = add("doctor", cmd_doctor, "check database, migrations, permissions and client setup")
    sp.add_argument("--json", action="store_true")
    sp = add("search", cmd_search, "full-text search a project's knowledge")
    sp.add_argument("project")
    sp.add_argument("query", nargs="+")
    sp.add_argument("--kind", action="append", help="filter by kind (repeatable)")
    sp.add_argument("--all", action="store_true", help="include superseded/outdated entries")
    sp.add_argument("--archive", action="store_true", help="also search the raw archive")
    sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--json", action="store_true")
    sp = add("conflicts", cmd_conflicts, "list a project's conflicts")
    sp.add_argument("project")
    sp.add_argument("--resolved", action="store_true", help="show resolved instead of open")
    sp.add_argument("--json", action="store_true")
    sp = add("checkpoint", cmd_checkpoint, "save a checkpoint by hand")
    sp.add_argument("project")
    sp.add_argument("--summary", required=True)
    sp.add_argument("--where", required=True, help="where work stopped")
    sp.add_argument("--next", required=True, help="the next action")
    sp.add_argument("--session", help="session label")
    sp = add("snapshots", cmd_snapshots, "list (or --create) snapshots of a project")
    sp.add_argument("project")
    sp.add_argument("--component", choices=["knowledge", "state", "handoff"])
    sp.add_argument("--create", action="store_true", help="snapshot knowledge and state now")
    sp.add_argument("--label")
    sp.add_argument("--json", action="store_true")
    sp = add("diff", cmd_diff, "diff snapshot A against snapshot B (or the current version)")
    sp.add_argument("project")
    sp.add_argument("a", type=int)
    sp.add_argument("b", type=int, nargs="?")
    sp.add_argument("--json", action="store_true")
    sp = add("restore", cmd_restore, "restore a project component from a snapshot (reversible)")
    sp.add_argument("project")
    sp.add_argument("snapshot", type=int)
    sp.add_argument("--yes", action="store_true")
    sp = add("backup", cmd_backup, "back up the whole database (or --list / --restore FILE)")
    sp.add_argument("--list", action="store_true")
    sp.add_argument("--restore", metavar="FILE")
    sp.add_argument("--yes", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp = add("handoff", cmd_handoff, "write a handoff package from stored memory (no AI needed)")
    sp.add_argument("project")
    sp.add_argument("--mode", default="standard", choices=["quick", "standard", "detailed", "knowledge_package"])
    sp.add_argument("--profile", help="upload profile from config (default: defaults.profile)")
    sp.add_argument("--format", choices=["md", "txt"])
    sp.add_argument("--json", action="store_true")
    sp = add("context", cmd_context, "print a project's context (what get_context returns)")
    sp.add_argument("project")
    sp.add_argument("--scope", default="brief", choices=["brief", "state", "knowledge", "handoff", "full"])
    sp.add_argument("--budget", type=int, help="token budget (500-8000)")
    sp.add_argument("--topic", action="append", help="topic_key prefix (repeatable)")
    sp.add_argument("--json", action="store_true")
    sp = add("link", cmd_link, "link a project to a local repo directory (or --unlink)")
    sp.add_argument("project")
    sp.add_argument("directory", nargs="?")
    sp.add_argument("--unlink", action="store_true")
    sp = add("merge", cmd_merge, "reassemble split package parts and verify them against the manifest")
    sp.add_argument("package", help="package directory (contains 00_manifest.json)")
    sp.add_argument("-o", "--output", help="output directory (default: the package directory)")
    sp = add("export", cmd_export, "export projects to one local archive, for moving to another Mac")
    sp.add_argument("--full", action="store_true", help="everything: knowledge, history, state, snapshots, archive")
    sp.add_argument("--project", action="append", help="only this project (repeatable; default: all)")
    sp.add_argument("-o", "--output", help="file or directory (default: ~/.contextbridge/exports/)")
    sp.add_argument("--json", action="store_true")
    sp = add("import", cmd_import, "import context (staged, untrusted) or, with --full, a migration archive")
    sp.add_argument("source", help="file or package folder ('-' = stdin text); with --full: an export ZIP or package")
    sp.add_argument("--full", action="store_true", help="rebuild projects exactly (ids and provenance kept)")
    sp.add_argument("--type", choices=["package", "markdown", "transcript_claude_code", "claude_export", "text"])
    sp.add_argument("--yes", action="store_true", help="commit everything staged without review")
    sp.add_argument("--project", action="append", help="only this project from the archive (repeatable)")
    sp.add_argument("--as", dest="as_slug", help="import under a different slug (one project)")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp = add("import-commit", cmd_import_commit, "commit (or --discard) a staged import")
    sp.add_argument("project")
    sp.add_argument("staging_id", type=int)
    sp.add_argument("--discard", action="store_true")
    sp = add("ingest-transcript", cmd_ingest, "archive a Claude Code transcript (.jsonl) into a project")
    sp.add_argument("project")
    sp.add_argument("transcript")
    for name, fn, what in (("install-claude-desktop", cmd_install_desktop, "Claude Desktop's config"),
                           ("install-claude-code", cmd_install_code, "Claude Code (user scope)")):
        sp = add(name, fn, f"add ContextBridge to {what}; merges, backs up first (or --uninstall)")
        sp.add_argument("--uninstall", action="store_true")
        sp.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
        sp.add_argument("--json", action="store_true")
        if name == "install-claude-code":
            sp.add_argument("--no-hook", action="store_true", help="don't add hooks or the status line")
            sp.add_argument("--no-statusline", action="store_true", help="don't set the status line")
            sp.add_argument("--force-statusline", action="store_true", help="replace an existing status line")
    sp = add("hook", cmd_hook, "Claude Code hook entry point (reads the event JSON on stdin)")
    sp.add_argument("event", choices=["session-start", "stop", "pre-compact", "session-end", "stop-failure"])
    add("statusline", cmd_statusline, "Claude Code status line (reads session JSON on stdin)")
    sp = add("emergency", cmd_emergency, "write a compact handoff WITHOUT a model; archive a transcript")
    sp.add_argument("project")
    sp.add_argument("--transcript", help="Claude Code transcript (.jsonl) to archive first")
    sp.add_argument("--reason", default="manual")
    sp.add_argument("--session", help="Claude Code session id (for budget level)")
    sp.add_argument("--only-if-unsaved", action="store_true",
                    help="skip the handoff when nothing changed since the last checkpoint")
    sp = add("status", cmd_status, "budget and context health (telemetry where the client provides it)")
    sp.add_argument("project", nargs="?")
    sp.add_argument("--session")
    sp.add_argument("--json", action="store_true")
    sp = add("telemetry", cmd_telemetry, "push a budget reading (generic adapter for other clients)")
    sp.add_argument("action", choices=["push"])
    sp.add_argument("--session", required=True)
    sp.add_argument("--source", default="manual")
    sp.add_argument("--project")
    sp.add_argument("--context-pct", type=float)
    sp.add_argument("--context-window", type=int)
    sp.add_argument("--five-hour-pct", type=float)
    sp.add_argument("--seven-day-pct", type=float)
    sp = add("snippet", cmd_snippet, "print paste-ready instructions (CLAUDE.md or Desktop project instructions)")
    sp.add_argument("kind", choices=["claude-md", "desktop"])
    sp.add_argument("project", help="project slug")
    add("version", cmd_version, "print version")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        code: int = args.func(args)
        return code
    except CBError as exc:
        print(f"error: {exc.message}" + (f"\nhint: {exc.hint}" if exc.hint else ""), file=sys.stderr)
        return EXIT_ERR


if __name__ == "__main__":
    sys.exit(main())
