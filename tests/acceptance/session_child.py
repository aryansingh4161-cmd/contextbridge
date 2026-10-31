"""A simulated working session for acceptance Test 24, run as a separate process and killed at random.

Prints `ACK <n> <kind> <marker>` only AFTER a write has returned (i.e. committed), and `START <n> handoff`
before a handoff, so the parent knows exactly what was durably saved before the kill."""

from __future__ import annotations

import sys

from contextbridge.core import knowledge as kn
from contextbridge.core import projects as proj
from contextbridge.core import state as st
from contextbridge.core.app import open_app
from contextbridge.core.checkpoints import CheckpointIn, checkpoint
from contextbridge.core.handoff import prepare_handoff
from contextbridge.core.knowledge import EntryIn


def main(home: str, steps: int) -> None:
    import os
    if os.environ.get("CB_TEST_SLOW_WRITES"):  # test-only: widen the window in which a handoff is half-written
        import time

        from contextbridge.core import handoff as h
        real = h._write

        def slow(path, text):  # type: ignore[no-untyped-def]
            real(path, text)
            time.sleep(0.03)
        h._write = slow
    app = open_app(home, log_to_stderr=False)
    c = app.conn
    proj.create_project(c, name="Kill Test", objective="survive abrupt ends", slug="kt")
    pid = proj.get_project_id(c, "kt")
    print("ACK 0 project -", flush=True)
    for n in range(1, steps + 1):
        m = f"M{n:03d}"
        kind = ("remember", "state", "remember", "checkpoint", "remember", "handoff")[n % 6]
        if kind == "remember":
            kn.remember(c, pid, [EntryIn(kind="fact", topic_key=f"t.k{n}", title=f"fact {m}", body="x " * 50,
                                         provenance="user_stated")], session_label="s")
        elif kind == "state":
            st.update_state(c, pid, {"in_progress": st.SectionIn(f"working on {m}")})
        elif kind == "checkpoint":
            checkpoint(c, pid, CheckpointIn(summary=f"summary {m}", where_stopped=f"stopped {m}",
                                            next_action=f"next {m}", session_label="s",
                                            items=[EntryIn(kind="lesson", topic_key=f"l.k{n}", title=f"lesson {m}",
                                                           provenance="user_stated")]))
        else:
            print(f"START {n} handoff", flush=True)
            prepare_handoff(c, app.paths, app.config, pid, mode="standard")
        print(f"ACK {n} {kind} {m}", flush=True)
    app.close()


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]))
