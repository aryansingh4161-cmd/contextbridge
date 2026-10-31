"""Client installers (spec §11, M7). Every change to a client config file is:

- merged (other servers, preferences and hooks are kept), never a rewrite of the user's file;
- preceded by a timestamped backup in ~/.contextbridge/backups/client-configs/ (mode 600);
- written atomically (temp file + rename), keeping the original file's permissions;
- refused if the existing file isn't valid JSON (we never "fix" a file we can't parse);
- reversible (`--uninstall`) and previewable (`--dry-run`).

Verified formats (2026-10-31): Claude Desktop `~/Library/Application Support/Claude/
claude_desktop_config.json` → `mcpServers.<name> = {command, args, env}`; Claude Code
`claude mcp add --transport stdio --scope user <name> -- <cmd>` (stored in ~/.claude.json);
hooks in `~/.claude/settings.json` → `hooks.SessionStart[] = {matcher?, hooks: [{type: "command",
command, timeout}]}`, plain stdout becomes context (≤ 10,000 chars).
"""

from __future__ import annotations

import json
import re
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.errors import CBError
from ..core.paths import DataPaths, ensure_private_dir, ensure_private_file

SERVER_NAME = "contextbridge"
HOOK_MARK = "hook session-start"
SYNCED_PARTS = ("Desktop", "Documents", "Mobile Documents", "iCloud")


def desktop_config_path() -> Path:
    return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"


def claude_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def find_binary(name: str) -> Path:
    """The installed entry point next to the running interpreter, else on PATH."""
    here = Path(sys.executable).parent / name
    if here.exists():
        return here
    found = shutil.which(name)
    if found:
        return Path(found)
    raise CBError("not_found", f"'{name}' not found", "install with: uv tool install <path-to-contextbridge-repo>")


def binary_warnings(cmd: Path) -> list[str]:
    w = []
    home = Path.home()
    try:
        rel = cmd.resolve().relative_to(home)
        if rel.parts and rel.parts[0] in SYNCED_PARTS or any(p in SYNCED_PARTS for p in rel.parts[:3]):
            w.append(f"{cmd} is inside an iCloud-synced folder; macOS may hide files the venv needs and the "
                     "server can fail to start. Prefer `uv tool install` (installs under ~/.local).")
    except ValueError:
        pass
    return w


# ---------------------------------------------------------------- JSON file merge helpers

def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except ValueError as exc:
        raise CBError("invalid_input", f"{path} is not valid JSON; refusing to modify it",
                      "fix or move the file, then run the installer again") from exc
    if not isinstance(data, dict):
        raise CBError("invalid_input", f"{path} does not contain a JSON object; refusing to modify it")
    return data


def _backup(path: Path, paths: DataPaths) -> Path | None:
    if not path.exists():
        return None
    d = paths.backups / "client-configs"
    ensure_private_dir(paths.backups)
    ensure_private_dir(d)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    dest = d / f"{path.name}.{stamp}.bak"
    shutil.copy2(path, dest)
    ensure_private_file(dest)
    return dest


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------- Claude Desktop

def install_desktop(paths: DataPaths, *, uninstall: bool = False, dry_run: bool = False,
                    command: Path | None = None) -> dict[str, Any]:
    cfg_path = desktop_config_path()
    data = _load_json(cfg_path)
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise CBError("invalid_input", f"'mcpServers' in {cfg_path} is not an object; refusing to modify it")
    before = servers.get(SERVER_NAME)
    warnings: list[str] = []
    entry: dict[str, Any] | None
    if uninstall:
        if before is None:
            return {"changed": False, "config": str(cfg_path), "message": "ContextBridge was not configured"}
        new_servers = {k: v for k, v in servers.items() if k != SERVER_NAME}
        entry = None
    else:
        cmd = command or find_binary("contextbridge-mcp")
        warnings += binary_warnings(cmd)
        entry = {"command": str(cmd), "args": []}
        if paths.root != Path.home() / ".contextbridge":
            entry = entry | {"env": {"CONTEXTBRIDGE_HOME": str(paths.root)}}
        if before == entry:
            return {"changed": False, "config": str(cfg_path), "entry": entry, "warnings": warnings,
                    "message": "already configured"}
        new_servers = {**servers, SERVER_NAME: entry}
    result: dict[str, Any] = {"config": str(cfg_path), "before": before, "after": entry,
                              "other_servers_kept": sorted(k for k in servers if k != SERVER_NAME),
                              "other_keys_kept": sorted(k for k in data if k != "mcpServers"), "warnings": warnings}
    if dry_run:
        return result | {"changed": False, "dry_run": True}
    backup = _backup(cfg_path, paths)
    new = dict(data)
    if new_servers:
        new["mcpServers"] = new_servers
    else:
        new.pop("mcpServers", None)
    _write_json_atomic(cfg_path, new)
    return result | {"changed": True, "backup": str(backup) if backup else None,
                     "next": "Quit Claude Desktop completely (Claude menu → Quit, or ⌘Q) and open it again."}


# ---------------------------------------------------------------- Claude Code

def _claude(*args: str) -> subprocess.CompletedProcess[str]:
    exe = shutil.which("claude")
    if exe is None:
        raise CBError("not_found", "the `claude` command (Claude Code) is not on PATH",
                      "install Claude Code first, or add its folder to PATH")
    return subprocess.run([exe, *args], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)


def install_code(paths: DataPaths, *, uninstall: bool = False, dry_run: bool = False, hook: bool = True,
                 command: Path | None = None, ctxb: Path | None = None, statusline: bool = True,
                 force_statusline: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"warnings": []}
    exists = _claude("mcp", "get", SERVER_NAME).returncode == 0
    claude_json = Path.home() / ".claude.json"
    if uninstall:
        out["mcp"] = "not configured"
        if exists and not dry_run:
            out["backup_claude_json"] = str(_backup(claude_json, paths))
            r = _claude("mcp", "remove", SERVER_NAME, "--scope", "user")
            if r.returncode != 0:
                raise CBError("internal", f"claude mcp remove failed: {r.stderr.strip()[:300]}")
            out["mcp"] = "removed"
        elif exists:
            out["mcp"] = "would remove"
        out["hooks"] = _hook(paths, None, remove=True, dry_run=dry_run)
        return out
    cmd = command or find_binary("contextbridge-mcp")
    out["warnings"] += binary_warnings(cmd)
    add = ["mcp", "add", "--transport", "stdio", "--scope", "user"]
    if paths.root != Path.home() / ".contextbridge":
        add += ["--env", f"CONTEXTBRIDGE_HOME={paths.root}"]
    add += [SERVER_NAME, "--", str(cmd)]
    out["command"] = "claude " + " ".join(add)
    if exists:
        out["mcp"] = "already configured (run with --uninstall first to change it)"
    elif dry_run:
        out["mcp"] = "would add"
    else:
        out["backup_claude_json"] = str(_backup(claude_json, paths)) if claude_json.exists() else None
        r = _claude(*add)
        if r.returncode != 0:
            raise CBError("internal", f"claude mcp add failed: {r.stderr.strip()[:300]}")
        out["mcp"] = "added (user scope: every project)"
    if hook:
        ctxb_bin = ctxb or find_binary("ctxb")
        base = shlex.quote(str(ctxb_bin))
        if paths.root != Path.home() / ".contextbridge":
            base = f"CONTEXTBRIDGE_HOME={shlex.quote(str(paths.root))} {base}"
        out["hooks"] = _hook(paths, base, remove=False, dry_run=dry_run, statusline=statusline,
                             force_statusline=force_statusline)
    return out


# event -> (ctxb hook subcommand, timeout seconds). SessionEnd's default budget is 1.5 s; our handler only
# spawns a detached child, but we still ask for 5 s (docs: per-hook timeout raises the budget, max 60 s).
HOOK_EVENTS = {"SessionStart": ("session-start", 10), "Stop": ("stop", 10), "PreCompact": ("pre-compact", 30),
               "SessionEnd": ("session-end", 5), "StopFailure": ("stop-failure", 5)}
_OURS = re.compile(r"ctxb'? hook (session-start|stop|pre-compact|session-end|stop-failure)$")


def _is_ours(g: Any) -> bool:
    return isinstance(g, dict) and any(isinstance(h, dict) and _OURS.search(str(h.get("command", "")).strip())
                                       for h in g.get("hooks", []))


def _hook(paths: DataPaths, ctxb_cmd: str | None, *, remove: bool, dry_run: bool,
          statusline: bool = True, force_statusline: bool = False) -> dict[str, Any]:
    """Merge our hooks (and status line) into ~/.claude/settings.json; other entries are kept."""
    sp = claude_settings_path()
    data = _load_json(sp)
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        raise CBError("invalid_input", f"'hooks' in {sp} is not an object; refusing to modify it")
    new_hooks: dict[str, Any] = dict(hooks)
    report: dict[str, Any] = {"settings": str(sp)}
    for event, (sub, timeout) in HOOK_EVENTS.items():
        groups = hooks.get(event, [])
        if not isinstance(groups, list):
            raise CBError("invalid_input", f"'hooks.{event}' in {sp} is not a list; refusing to modify it")
        kept = [g for g in groups if not _is_ours(g)]
        if remove:
            new = kept
        else:
            new = [*kept, {"hooks": [{"type": "command", "command": f"{ctxb_cmd} hook {sub}", "timeout": timeout}]}]
        if new:
            new_hooks[event] = new
        else:
            new_hooks.pop(event, None)
    sl = data.get("statusLine")
    sl_ours = isinstance(sl, dict) and str(sl.get("command", "")).strip().endswith("statusline") and "ctxb" in str(
        sl.get("command", ""))
    new_data = {k: v for k, v in data.items() if k != "hooks"} | ({"hooks": new_hooks} if new_hooks else {})
    if remove:
        if sl_ours:
            new_data.pop("statusLine", None)
            report["statusline"] = "removed"
    elif statusline:
        if sl is None or sl_ours or force_statusline:
            new_data["statusLine"] = {"type": "command", "command": f"{ctxb_cmd} statusline", "padding": 0}
            report["statusline"] = "set" if sl is None or sl_ours else "replaced (--force-statusline)"
        else:
            report["statusline"] = "kept yours (you already have a status line; --force-statusline to replace it)"
    if new_data == data:
        return report | {"status": "already configured" if not remove else "not configured"}
    if dry_run:
        return report | {"status": "would remove" if remove else "would update"}
    backup = _backup(sp, paths)
    _write_json_atomic(sp, new_data)
    return report | {"status": "removed" if remove else "updated", "events": list(HOOK_EVENTS),
                     "backup": str(backup) if backup else None}
