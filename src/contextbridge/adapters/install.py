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
                 command: Path | None = None, ctxb: Path | None = None) -> dict[str, Any]:
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
        out["hook"] = _hook(paths, None, remove=True, dry_run=dry_run)
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
        hook_cmd = f"{shlex.quote(str(ctxb_bin))} {HOOK_MARK}"
        if paths.root != Path.home() / ".contextbridge":
            hook_cmd = f"CONTEXTBRIDGE_HOME={shlex.quote(str(paths.root))} {hook_cmd}"
        out["hook"] = _hook(paths, hook_cmd, remove=False, dry_run=dry_run)
    return out


def _hook(paths: DataPaths, hook_cmd: str | None, *, remove: bool, dry_run: bool) -> dict[str, Any]:
    sp = claude_settings_path()
    data = _load_json(sp)
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        raise CBError("invalid_input", f"'hooks' in {sp} is not an object; refusing to modify it")
    groups = hooks.get("SessionStart", [])
    if not isinstance(groups, list):
        raise CBError("invalid_input", f"'hooks.SessionStart' in {sp} is not a list; refusing to modify it")

    def ours(g: Any) -> bool:
        return isinstance(g, dict) and any(isinstance(h, dict) and HOOK_MARK in str(h.get("command", ""))
                                           for h in g.get("hooks", []))

    kept = [g for g in groups if not ours(g)]
    present = len(kept) != len(groups)
    if remove:
        if not present:
            return {"settings": str(sp), "status": "not configured"}
        new_groups = kept
    else:
        entry = {"hooks": [{"type": "command", "command": hook_cmd, "timeout": 10}]}
        if present and [g for g in groups if ours(g)] == [entry]:
            return {"settings": str(sp), "status": "already configured"}
        new_groups = [*kept, entry]
    if dry_run:
        return {"settings": str(sp), "status": "would remove" if remove else "would add"}
    backup = _backup(sp, paths)
    new_hooks = {**hooks, "SessionStart": new_groups} if new_groups else {k: v for k, v in hooks.items()
                                                                           if k != "SessionStart"}
    new = {**data, "hooks": new_hooks} if new_hooks else {k: v for k, v in data.items() if k != "hooks"}
    _write_json_atomic(sp, new)
    return {"settings": str(sp), "status": "removed" if remove else "added", "backup": str(backup) if backup else None,
            "other_session_start_hooks_kept": len(kept)}
