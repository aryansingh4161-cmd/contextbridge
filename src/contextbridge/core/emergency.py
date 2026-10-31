"""Emergency path (spec §10.5): when the model may be unavailable, save what we can WITHOUT a model.

1. Archive the raw transcript if one is available (Claude Code gives its path to hooks).
2. Write a compact handoff from the database, clearly labelled as not AI-synthesized after the last
   checkpoint, pointing at the archived transcript. It never pretends a summary was produced.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .budget import assess, latest
from .config import Config
from .errors import CBError
from .handoff import prepare_handoff
from .importer.staging import commit, stage
from .logging_setup import get_logger
from .paths import DataPaths
from .status import checkpoint_status

log = get_logger("emergency")


def emergency(conn: sqlite3.Connection, paths: DataPaths, config: Config, project_id: int, *,
              transcript: str | None = None, reason: str = "manual", only_if_unsaved: bool = False,
              session_key: str | None = None, restrict: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {"reason": reason, "archived": None, "handoff": None}
    archive_ref = None
    if transcript:
        try:
            s = stage(conn, paths, config, project_id, "transcript_claude_code", path=transcript, restrict=restrict)
            c = commit(conn, project_id, int(s["staging_id"]), select=[])
            a = c["archived"][0] if c["archived"] else None
            out["archived"] = a
            if a:
                archive_ref = (f"archive #{a['archive_id']} ('{a['title']}')" if a.get("stored")
                               else f"'{a['title']}' (already archived earlier)")
        except CBError as exc:
            out["archive_error"] = exc.message
            log.warning("event=emergency_archive_failed code=%s", exc.code)
    status = checkpoint_status(conn, project_id)
    level = assess(latest(conn, session_key=session_key) if session_key else None)["level"]
    if only_if_unsaved and not status["writes_since_checkpoint"] and level < 90:
        out["skipped_handoff"] = "nothing unsaved since the last checkpoint and no budget pressure"
        return out
    banner = (
        "# ⚠ EMERGENCY HANDOFF — read this first\n\n"
        f"This file was written automatically WITHOUT an AI summary (reason: {reason}). Everything below comes "
        f"from the project memory as saved up to the last checkpoint ({status['last_checkpoint_at'] or 'none yet'})"
        + (f" plus {status['writes_since_checkpoint']} later save(s)" if status["writes_since_checkpoint"] else "")
        + ". Anything said after that and not saved as an item is NOT in this file"
        + (f"; the raw conversation was archived as {archive_ref}. A future assistant should review it "
           "(search_memory with include_archive, or import_context) and save what matters."
           if archive_ref else "; no transcript was available to archive.")
        + "\n\n")
    res = prepare_handoff(conn, paths, config, project_id, mode="quick", emergency_banner=banner,
                          emergency_meta={"reason": reason, "archive": archive_ref,
                                          "last_checkpoint_at": status["last_checkpoint_at"]})
    out["handoff"] = res["primary_file"]
    out["directory"] = res["directory"]
    log.info("event=emergency_handoff reason=%s archived=%s", reason.split(":")[0], bool(archive_ref))
    return out
