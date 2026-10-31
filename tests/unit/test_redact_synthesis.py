from __future__ import annotations

import pytest

from contextbridge.core.security.redact import redact, shannon_entropy
from contextbridge.core.synthesis import (
    normalize_topic_key,
    similar_topic_keys,
    similarity,
)

# Fake credentials built at runtime so no scanner flags this file as containing real ones.
FAKE = {
    "anthropic_key": "sk-ant-" + "api03-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4",
    "openai_key": "sk-" + "proj-" + "Zz9Yy8Xx7Ww6Vv5Uu4Tt3Ss2Rr1",
    "github_token": "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "slack_token": "xoxb-" + "1234567890-abcdefghij",
    "aws_access_key": "AKIA" + "IOSFODNN7EXAMPLE",
    "jwt": "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0" + ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
}


@pytest.mark.parametrize("rtype", list(FAKE))
def test_provider_patterns(rtype: str) -> None:
    text, found = redact(f"use {FAKE[rtype]} here")
    assert FAKE[rtype] not in text
    assert any(f.type == rtype for f in found), found


def test_private_key_block() -> None:
    block = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow" + "IBAAKCAQEA\n-----END RSA PRIVATE KEY-----"
    text, found = redact(f"key:\n{block}\nend")
    assert "MIIEow" not in text and found[0].type == "private_key"


def test_assignment_keeps_key_name() -> None:
    text, _ = redact('DB_PASSWORD="hunter2hunter2"\napi_key: abcd1234efgh')
    assert "hunter2" not in text and "abcd1234" not in text
    assert 'DB_PASSWORD="[REDACTED:secret_assignment]"' in text


def test_url_credentials() -> None:
    text, found = redact("postgres://admin:s3cretpw@db.local:5432/app")
    assert "s3cretpw" not in text and "db.local:5432/app" in text
    assert found[0].type == "url_credentials"


def test_hashes_and_normal_text_untouched() -> None:
    sample = ("commit 9fceb02d0ae598e95dc970b74767f19372d61af8 sha256 "
              "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 "
              "port 8000, FastAPI 0.115, run `uvicorn app.main:app --reload`")
    text, found = redact(sample)
    assert text == sample and found == []


def test_entropy_skips_code_fences_but_patterns_do_not() -> None:
    rnd = "Q7xK9pL2mN8vR4tY6wZ1aB3cD5eF0gH"
    assert shannon_entropy(rnd) > 4.2
    text, _ = redact(f"token {rnd}\n```\nx = '{rnd}'\nk = '{FAKE['github_token']}'\n```")
    assert text.count(rnd) == 1               # redacted outside the fence, kept inside
    assert FAKE["github_token"] not in text   # pattern rule applies inside fences too


def test_redact_is_idempotent() -> None:
    once, _ = redact(f"a {FAKE['openai_key']} b")
    twice, found = redact(once)
    assert once == twice and found == []


@pytest.mark.parametrize(("raw", "norm"), [
    ("Backend.Framework", "backend.framework"),
    ("auth / token storage", "auth_token_storage"),
    ("  DB..Engine ", "db.engine"),
    ("Café.Résumé", "cafe.resume"),
])
def test_topic_key_normalization(raw: str, norm: str) -> None:
    assert normalize_topic_key(raw) == norm


def test_similar_topic_keys() -> None:
    existing = {"backend.framework", "frontend.framework", "db.engine"}
    assert "backend.framework" in similar_topic_keys("backend.framwork", existing)
    assert set(similar_topic_keys("api.framework", existing)) >= {"backend.framework", "frontend.framework"}
    assert similar_topic_keys("backend.framework", existing) == []


def test_similarity() -> None:
    assert similarity("Use FastAPI", "for the backend API", "Use FastAPI", "for the backend API.") == 1.0
    assert similarity("Use FastAPI", "", "Use Flask", "") < 0.5
