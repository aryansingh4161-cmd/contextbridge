"""Attachments (screenshots, PDFs, documents) + word-for-word Claude export import (post-MVP, 2026-10-31)."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from compression import zstd

from contextbridge.core import attachments as att
from contextbridge.core import knowledge as kn
from contextbridge.core import projects as proj
from contextbridge.core.app import App, open_app
from contextbridge.core.errors import CBError
from contextbridge.core.forget import forget, purge
from contextbridge.core.handoff import prepare_handoff
from contextbridge.core.importer.staging import commit, stage
from contextbridge.core.knowledge import EntryIn
from contextbridge.core.transfer import export_full, import_full

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a49444154789c63000100"
                    "000500010d0a2db40000000049454e44ae426082")  # a real 1x1 PNG
FAKE_KEY = "sk-ant-" + "api03-" + "Qw" * 12 + "7x" * 10


def _pid(app: App, slug: str = "pp") -> int:
    proj.create_project(app.conn, name=slug.upper(), objective="o", slug=slug)
    return proj.get_project_id(app.conn, slug)


def _file(tmp: Path, name: str, data: bytes | str) -> str:
    p = tmp / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode())
    return str(p)


def test_attach_stores_private_copy_dedupes_and_survives_original(app: App, tmp_path: Path) -> None:
    pid = _pid(app)
    src = _file(tmp_path, "login-bug.png", PNG)
    out = att.add(app.conn, app.paths, app.config, pid, src, note="500 error on wrong password", restrict=False)
    assert out["stored"] and out["mime"] == "image/png" and out["searchable"] == "name and note"
    row = att.list_for(app.conn, app.paths, pid)[0]
    copy = Path(row["path"])
    assert copy.read_bytes() == PNG and copy.name == f"{hashlib.sha256(PNG).hexdigest()}.png"
    assert stat.S_IMODE(copy.stat().st_mode) == 0o600 and stat.S_IMODE(copy.parent.stat().st_mode) == 0o700
    again = att.add(app.conn, app.paths, app.config, pid, src, restrict=False)
    assert again["duplicate"] and again["attachment_id"] == out["attachment_id"]
    os.remove(src)
    assert copy.exists(), "the memory keeps its own copy"
    assert att.search(app.conn, pid, "password")[0]["filename"] == "login-bug.png"


def test_text_attachments_are_searchable_and_redacted_in_index(app: App, tmp_path: Path) -> None:
    pid = _pid(app)
    src = _file(tmp_path, "notes.md", f"The OCR vendor is Zebrafish.\nKEY={FAKE_KEY}\n")
    out = att.add(app.conn, app.paths, app.config, pid, src, restrict=False)
    assert out["searchable"] == "full text" and out["text_redactions"] >= 1 and "warning" in out
    assert att.search(app.conn, pid, "Zebrafish")
    idx = app.conn.execute("SELECT text FROM attachments_fts").fetchone()[0]
    assert FAKE_KEY not in idx


def test_entry_link_and_type_and_path_rules(home: Path, tmp_path: Path) -> None:
    home.mkdir(parents=True)
    ok = tmp_path / "allowed"
    ok.mkdir()
    (home / "config.toml").write_text(f"[import]\nallowed_dirs = ['{ok}']\n[attachments]\nmax_bytes = 1000\n")
    app = open_app(home, log_to_stderr=False)
    try:
        pid = _pid(app)
        with pytest.raises(CBError) as e:   # AI-chosen path outside the allowed folders
            att.add(app.conn, app.paths, app.config, pid, _file(tmp_path / "else", "a.png", PNG))
        assert "outside the folders" in e.value.message
        assert att.add(app.conn, app.paths, app.config, pid, _file(ok, "a.png", PNG))["stored"]
        for bad, why in ((_file(ok, "tool.exe", b"MZ"), "can't be used"), (_file(ok, "big.png", b"x" * 2000), "limit"),
                         (_file(tmp_path / ".secret", "k.png", PNG), "hidden")):
            with pytest.raises(CBError) as e:
                att.add(app.conn, app.paths, app.config, pid, bad, restrict=False)
            assert why in e.value.message + (e.value.hint or "")
        with pytest.raises(CBError):
            att.add(app.conn, app.paths, app.config, pid, _file(ok, "b.png", PNG + b"1"), entry_id=999, restrict=False)
    finally:
        app.close()


def test_handoff_lists_and_optionally_includes_attachments(app: App, tmp_path: Path) -> None:
    pid = _pid(app)
    e = kn.remember(app.conn, pid, [EntryIn(kind="bug", topic_key="auth.login", title="Login 500",
                                            provenance="user_stated")]).results[0].id
    att.add(app.conn, app.paths, app.config, pid, _file(tmp_path, "bug.png", PNG), note="the error page", entry_id=e,
            restrict=False)
    std = prepare_handoff(app.conn, app.paths, app.config, pid, mode="standard")
    text = Path(std["primary_file"]).read_text(encoding="utf-8")
    files = text.split("# 11. Files & repo")[1].split("# 12.")[0]
    assert "[A-0001 · user_stated" in files and "`bug.png`" in files and "the error page" in files
    assert "not included in this package" in files and not (Path(std["directory"]) / "attachments").exists()
    kp = prepare_handoff(app.conn, app.paths, app.config, pid, mode="knowledge_package")
    d = Path(kp["directory"])
    copied = d / "attachments" / "A-0001_bug.png"
    assert copied.read_bytes() == PNG and stat.S_IMODE(copied.stat().st_mode) == 0o600
    m = json.loads((d / "00_manifest.json").read_text())
    entry = next(f for f in m["files"] if f["role"] == "attachment")
    assert entry["sha256"] == hashlib.sha256(PNG).hexdigest() and entry["upload"] is True
    assert "attachments/A-0001_bug.png" in sum(m["upload_plan"], [])


def test_export_import_carries_files(app: App, tmp_path: Path) -> None:
    pid = _pid(app)
    att.add(app.conn, app.paths, app.config, pid, _file(tmp_path, "spec.md", "Zebrafish OCR spec"), restrict=False)
    att.add(app.conn, app.paths, app.config, pid, _file(tmp_path, "shot.png", PNG), restrict=False)
    arch = export_full(app.conn, app.paths)
    other = open_app(tmp_path / "newmac", log_to_stderr=False)
    try:
        proj.create_project(other.conn, name="Local", objective="o", slug="local")  # occupy id 1 → ids get remapped?
        res = import_full(other.conn, other.paths, arch["path"])["imported"][0]
        npid = proj.get_project_id(other.conn, "pp")
        rows = att.list_for(other.conn, other.paths, npid)
        assert {r["filename"] for r in rows} == {"spec.md", "shot.png"} and all(r["present"] for r in rows)
        assert Path(next(r["path"] for r in rows if r["ext"] == "png")).read_bytes() == PNG
        assert att.search(other.conn, npid, "Zebrafish"), "text index rebuilt on import"
        assert res["counts"]["attachments"] == 2
    finally:
        other.close()
    # a tampered file member is refused
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(arch["path"]) as zin, zipfile.ZipFile(bad, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item)
            zout.writestr(item, data + b"!" if item.filename.endswith(".png") else data)
    with pytest.raises(CBError):
        import_full(open_app(tmp_path / "third", log_to_stderr=False).conn, app.paths, bad)


def test_forget_and_purge_remove_files(app: App, tmp_path: Path) -> None:
    pid = _pid(app, name_slug := "pp")
    a = att.add(app.conn, app.paths, app.config, pid, _file(tmp_path, "a.png", PNG), restrict=False)["attachment_id"]
    path = Path(att.list_for(app.conn, app.paths, pid)[0]["path"])
    forget(app.conn, app.paths, pid, "attachment", a, confirm=True)
    assert not path.exists() and not att.list_for(app.conn, app.paths, pid) and not att.search(app.conn, pid, "a")
    att.add(app.conn, app.paths, app.config, pid, _file(tmp_path, "z.md", "QUOKKA-MARKER-991"), restrict=False)
    forget(app.conn, app.paths, pid, "project", None, confirm=True, confirm_project_name="PP")
    assert not (app.paths.attachments / name_slug).exists()
    z = next((app.paths.backups / "deleted-projects").glob("*.zip"))
    assert any(n.startswith("attachments/pp/") for n in zipfile.ZipFile(z).namelist()), "recoverable until purge"
    out = purge(app.conn, app.paths, "pp")
    assert out["recovery_archives"] == 1
    app.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert b"QUOKKA-MARKER-991" not in app.paths.db.read_bytes()


# ---------------------------------------------------------------- Claude export: word for word

def _export(tmp: Path) -> Path:
    convs = [
        {"uuid": "c1", "name": "Folio planning", "created_at": "2026-09-01",
         "chat_messages": [
             {"sender": "human", "text": "Here is my brief", "created_at": "2026-09-01T10:00:00Z",
              "attachments": [{"file_name": "brief.txt", "extracted_content": "Photographer portfolio, 40 galleries"}],
              "files": [{"file_name": "moodboard.jpg"}]},
             {"sender": "assistant", "content": [{"type": "text", "text": "Let's use Astro: zero JS by default."}]}]},
        {"uuid": "c2", "name": "Pasta recipes", "chat_messages": [{"sender": "human", "text": "carbonara?"}]},
        {"uuid": "c3", "name": "Folio deploy", "chat_messages": [{"sender": "human", "text": "Netlify or R2?"}]}]
    z = tmp / "data-export.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("conversations.json", json.dumps(convs))
    return z


def test_claude_export_keeps_attachment_text_word_for_word(app: App, tmp_path: Path) -> None:
    pid = _pid(app)
    s = stage(app.conn, app.paths, app.config, pid, "claude_export", path=str(_export(tmp_path)), restrict=False)
    commit(app.conn, pid, s["staging_id"], select=[0])
    text = zstd.decompress(app.conn.execute("SELECT content FROM raw_archive").fetchone()[0]).decode()
    assert "Here is my brief" in text and "Let's use Astro: zero JS by default." in text
    assert "[attached file: brief.txt]\nPhotographer portfolio, 40 galleries" in text
    assert "moodboard.jpg (file itself not in the export)" in text
    sess = app.conn.execute("SELECT label, capture, external_key FROM sessions").fetchone()
    assert tuple(sess) == ("Folio planning", "transcript", "c1")


def test_cli_import_commit_match_selects_by_name(home: Path, tmp_path: Path) -> None:
    app = open_app(home, log_to_stderr=False)
    _pid(app)
    app.close()
    env = {**os.environ, "CONTEXTBRIDGE_HOME": str(home)}
    ctxb = [sys.executable, "-m", "contextbridge.cli.main"]
    r = subprocess.run([*ctxb, "import", str(_export(tmp_path)), "--type", "claude_export", "--project", "pp"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0 and "Pasta recipes" in r.stdout
    sid = r.stdout.split("ctxb import-commit pp ")[1].split()[0]
    r = subprocess.run([*ctxb, "import-commit", "pp", sid, "--match", "folio"], capture_output=True, text=True,
                       env=env, timeout=60)
    assert r.returncode == 0 and "selected 2 item(s)" in r.stdout and "archived: 2" in r.stdout, r.stdout + r.stderr
    app = open_app(home, log_to_stderr=False)
    try:
        titles = {t for (t,) in app.conn.execute("SELECT title FROM raw_archive")}
        assert titles == {"Folio planning", "Folio deploy"}
    finally:
        app.close()


# ---------------------------------------------------------------- security regressions (review findings)

def test_crafted_archive_with_traversal_attachment_rows_is_refused(app: App, tmp_path: Path) -> None:
    pid = _pid(app)
    att.add(app.conn, app.paths, app.config, pid, _file(tmp_path, "a.png", PNG), restrict=False)
    arch = Path(export_full(app.conn, app.paths)["path"])
    victim = tmp_path / "victim.txt"
    victim.write_text("do not delete me")
    for bad in ({"sha256": "../../../" + str(victim.with_suffix("")).lstrip("/"), "ext": "txt"},
                {"ext": "png/../../x"}):
        evil = tmp_path / "evil.zip"
        with zipfile.ZipFile(arch) as zin, zipfile.ZipFile(evil, "w") as zout:
            manifest = json.loads(zin.read("manifest.json"))
            for item in zin.infolist():
                data = zin.read(item)
                if item.filename == "projects/pp.json":
                    dump = json.loads(data)
                    dump["tables"]["attachments"][0].update(bad)
                    data = json.dumps(dump, sort_keys=True).encode()
                    manifest["projects"][0]["sha256"] = hashlib.sha256(data).hexdigest()
                if item.filename != "manifest.json":
                    zout.writestr(item, data)
            zout.writestr("manifest.json", json.dumps(manifest))
        other = open_app(tmp_path / f"new{len(bad)}", log_to_stderr=False)
        try:
            with pytest.raises(CBError) as e:
                import_full(other.conn, other.paths, evil)
            assert "invalid sha256/extension" in e.value.message
            assert other.conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 0
        finally:
            other.close()
    assert victim.read_text() == "do not delete me"


def test_stored_path_rejects_bad_rows(app: App) -> None:
    for row in ({"sha256": "../x", "ext": "png"}, {"sha256": "a" * 64, "ext": "png/../../etc"},
                {"sha256": "a" * 64, "ext": "exe"}):
        with pytest.raises(CBError):
            att.stored_path(app.paths, "pp", row)
    assert att.stored_path(app.paths, "pp", {"sha256": "a" * 64, "ext": "png"}).name == "a" * 64 + ".png"


def test_private_keys_and_ai_chosen_secret_files_are_refused(home: Path, tmp_path: Path) -> None:
    home.mkdir(parents=True)
    ok = tmp_path / "ok"
    ok.mkdir()
    (home / "config.toml").write_text(f"[import]\nallowed_dirs = ['{ok}']\n")
    app = open_app(home, log_to_stderr=False)
    try:
        pid = _pid(app)
        pem = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n-----END OPENSSH PRIVATE KEY-----\n"
        for name in ("id_ed25519.txt", "deploy.pdf"):
            with pytest.raises(CBError) as e:
                att.add(app.conn, app.paths, app.config, pid, _file(ok, name, pem), restrict=False)
            assert "private key" in e.value.message
        with pytest.raises(CBError):
            att.add(app.conn, app.paths, app.config, pid, _file(ok, "keynote.key", b"PK\x03\x04"), restrict=False)
        env_file = _file(ok, "settings.json", json.dumps({"api_key": FAKE_KEY}))
        with pytest.raises(CBError) as e:  # the AI chose it → refused
            att.add(app.conn, app.paths, app.config, pid, env_file)
        assert "appears to contain secrets" in e.value.message
        out = att.add(app.conn, app.paths, app.config, pid, env_file, restrict=False)  # the user typed it → allowed
        assert out["stored"] and "warning" in out
        # … but a secret-bearing attachment is never copied into an upload package
        kp = prepare_handoff(app.conn, app.paths, app.config, pid, mode="knowledge_package")
        assert not (Path(kp["directory"]) / "attachments").exists() or not any(
            (Path(kp["directory"]) / "attachments").iterdir())
        assert any("NOT copied into the package" in w for w in kp["warnings"])
    finally:
        app.close()


def test_secret_scan_is_content_based_and_fails_closed() -> None:
    import io
    import zlib
    key = FAKE_KEY.encode()
    # one invalid byte no longer skips the scan
    assert att.has_secrets(b"\xff\xfe broken utf8 then " + key, "txt")
    # Office/iWork files are ZIPs: secrets inside their XML are found
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"<w:t>ANTHROPIC_API_KEY=" + key + b"</w:t>")
    assert att.has_secrets(buf.getvalue(), "docx")
    # PDF text in a Flate-compressed stream is found
    pdf = b"%PDF-1.4\n1 0 obj\n<< /Filter /FlateDecode >>\nstream\n" + zlib.compress(b"token " + key) \
        + b"\nendstream\nendobj\n%%EOF"
    assert att.has_secrets(pdf, "pdf")
    # a container that can't be inspected is risky only where we must fail closed
    assert att.has_secrets(b"PK\x03\x04garbage", "docx", fail_closed=True)
    assert not att.has_secrets(b"PK\x03\x04garbage", "docx", fail_closed=False)
    # ordinary files are not flagged (no false positive on image bytes)
    assert not att.has_secrets(PNG, "png") and not att.has_secrets(b"%PDF-1.4\n%%EOF", "pdf")
    clean = io.BytesIO()
    with zipfile.ZipFile(clean, "w") as z:
        z.writestr("word/document.xml", "<w:t>Quarterly plan</w:t>")
    assert not att.has_secrets(clean.getvalue(), "docx", fail_closed=True)
