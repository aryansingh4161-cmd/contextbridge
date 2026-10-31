from __future__ import annotations

import pytest

from contextbridge.core import projects as proj
from contextbridge.core.app import App
from contextbridge.core.errors import CBError
from contextbridge.core.slug import MAX_LEN, is_valid_slug, make_slug


# --- make_slug contract (implementation choices are yours; these are the invariants) ---

def test_slug_basic() -> None:
    assert make_slug("SIH Document Verification System", set()) == "sih-document-verification-system"


def test_slug_punctuation_collapses() -> None:
    s = make_slug("Portfolio v2 (Next.js)!!", set())
    assert is_valid_slug(s) and "--" not in s and not s.endswith("-")


def test_slug_collision_gets_suffix() -> None:
    first = make_slug("My App", set())
    second = make_slug("My App", {first})
    assert second != first and is_valid_slug(second)
    third = make_slug("My App", {first, second})
    assert third not in {first, second}


def test_slug_length_cap_including_suffix() -> None:
    name = "x" * 200
    a = make_slug(name, set())
    b = make_slug(name, {a})
    for s in (a, b):
        assert len(s) <= MAX_LEN and is_valid_slug(s)


@pytest.mark.parametrize("bad", ["", "   ", "!!!", "a"])
def test_slug_unusable_raises(bad: str) -> None:
    with pytest.raises(ValueError):
        make_slug(bad, set())


def test_slug_non_ascii_is_valid_or_refused() -> None:
    for name in ["Café Résumé Tool", "डॉक्यूमेंट सत्यापन"]:
        try:
            assert is_valid_slug(make_slug(name, set()))
        except ValueError:
            pass  # refusing is an acceptable policy; producing an invalid slug is not


# --- repository ---

def test_create_and_list(app: App) -> None:
    slug, created = proj.create_project(app.conn, name="SIH Document Verification System",
                                        objective="Verify documents", slug="sih-docverify")
    assert (slug, created) == ("sih-docverify", True)
    rows = proj.list_projects(app.conn)
    assert [r.slug for r in rows] == ["sih-docverify"]
    assert rows[0].last_checkpoint_age_min is None and rows[0].open_conflicts == 0


def test_create_is_idempotent_on_name(app: App) -> None:
    proj.create_project(app.conn, name="Portfolio", objective="o", slug="portfolio")
    assert proj.create_project(app.conn, name="portfolio ", objective="o2") == ("portfolio", False)
    assert len(proj.list_projects(app.conn)) == 1


def test_derived_slug(app: App) -> None:
    slug, _ = proj.create_project(app.conn, name="Side Project", objective="o")
    assert is_valid_slug(slug)


@pytest.mark.parametrize("bad", ["UPPER?", "-lead", "a", "has space"])
def test_explicit_slug_validation(app: App, bad: str) -> None:
    with pytest.raises(CBError) as e:
        proj.create_project(app.conn, name=f"n{bad}", objective="o", slug=bad)
    assert e.value.code == "invalid_input"


def test_duplicate_explicit_slug(app: App) -> None:
    proj.create_project(app.conn, name="A", objective="o", slug="same")
    with pytest.raises(CBError) as e:
        proj.create_project(app.conn, name="B", objective="o", slug="same")
    assert e.value.code == "conflict"


def test_required_fields(app: App) -> None:
    with pytest.raises(CBError):
        proj.create_project(app.conn, name="  ", objective="o", slug="xx")
    with pytest.raises(CBError):
        proj.create_project(app.conn, name="N", objective=" ", slug="xx")


def test_not_found_suggests(app: App) -> None:
    proj.create_project(app.conn, name="SIH", objective="o", slug="sih-docverify")
    with pytest.raises(CBError) as e:
        proj.get_project_id(app.conn, "sih")
    assert e.value.code == "not_found" and "sih-docverify" in (e.value.hint or "")
