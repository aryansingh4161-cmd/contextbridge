"""Acceptance Test 11: network off → store, search, checkpoint, handoff, import/export all work.
Every way to open a network connection is made to fail for the duration of the test."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from contextbridge.core import knowledge as kn
from contextbridge.core.app import App, open_app
from contextbridge.core.checkpoints import CheckpointIn, checkpoint
from contextbridge.core.context import get_context
from contextbridge.core.handoff import prepare_handoff
from contextbridge.core.importer.staging import commit, stage
from contextbridge.core.transfer import export_full, import_full

from fixtures.five_sessions import five_sessions


class NetworkUsed(AssertionError):
    pass


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_a: object, **_k: object) -> None:
        raise NetworkUsed("network access attempted while offline")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


def test_everything_works_offline(no_network: None, app: App, tmp_path: Path) -> None:
    with pytest.raises(NetworkUsed):  # the block itself works
        socket.create_connection(("example.com", 443), timeout=1)
    pid, _ = five_sessions(app)                                                   # store
    assert kn.search(app.conn, pid, "FastAPI")["hits"]                           # search
    checkpoint(app.conn, pid, CheckpointIn(summary="s", where_stopped="w", next_action="n"))  # checkpoint
    assert "FastAPI" in get_context(app.conn, app.paths, app.config, pid)["content"]
    out = prepare_handoff(app.conn, app.paths, app.config, pid, mode="knowledge_package")      # handoff
    assert Path(out["primary_file"]).exists()
    s = stage(app.conn, app.paths, app.config, pid, "markdown", text="## Note\nworks offline")  # import
    commit(app.conn, pid, s["staging_id"])
    arch = export_full(app.conn, app.paths)                                     # export
    other = open_app(tmp_path / "other", log_to_stderr=False)
    try:
        import_full(other.conn, other.paths, arch["path"])
    finally:
        other.close()
