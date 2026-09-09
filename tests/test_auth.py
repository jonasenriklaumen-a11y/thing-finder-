"""Konten, Sitzungen und Kontingente."""

from __future__ import annotations

import pytest

from aquaticy.auth import (
    NORMAL_TOKEN_LIMIT,
    Account,
    AuthStore,
    RateLimiter,
    new_pro_code,
    pro_code_for,
)
from aquaticy.web import ChatSession

TERMS = {"terms_accepted": True, "terms_version": "test"}


@pytest.fixture
def store(tmp_path):
    return AuthStore(tmp_path, "PRO123456")


def test_normal_account_and_login(store: AuthStore) -> None:
    account = store.register(
        "Mensch@Example.org", "eine sehr lange Passphrase", "normal", **TERMS
    )
    assert account.email == "mensch@example.org"
    assert account.plan == "normal"
    assert store.authenticate(account.email, "eine sehr lange Passphrase") == account
    assert store.authenticate(account.email, "falsch und trotzdem lang genug") is None


def test_registration_keeps_the_chosen_username_and_accepts_seven_characters(
    store: AuthStore,
) -> None:
    account = store.register(
        "jonas@example.org", "1234567", "normal", username="Jonas", **TERMS
    )
    assert account.username == "Jonas"
    assert store.authenticate(account.email, "1234567") == account


def test_free_accounts_receive_four_hundred_thousand_tokens() -> None:
    assert NORMAL_TOKEN_LIMIT == 400_000


def test_registration_requires_and_records_explicit_terms(store: AuthStore) -> None:
    with pytest.raises(ValueError, match="ausdrücklich"):
        store.register("nein@example.org", "eine sehr lange Passphrase", "normal")
    account = store.register(
        "ja@example.org", "eine sehr lange Passphrase", "normal", **TERMS
    )
    with store._connect() as conn:
        row = conn.execute(
            "SELECT terms_version, terms_accepted_at FROM users WHERE id=?", (account.id,)
        ).fetchone()
    assert row["terms_version"] == "test"
    assert row["terms_accepted_at"] > 0


def test_pro_needs_the_secret_code(store: AuthStore) -> None:
    with pytest.raises(ValueError, match="Pro-Code"):
        store.register(
            "a@example.org", "eine sehr lange Passphrase", "pro", "FALSCH123", **TERMS
        )
    account = store.register(
        "a@example.org", "eine sehr lange Passphrase", "pro", "pro123456", **TERMS
    )
    assert account.pro


def test_pro_accepts_the_terminal_label_when_copied(store: AuthStore) -> None:
    account = store.register(
        "copy@example.org",
        "eine sehr lange Passphrase",
        "pro",
        f"Pro-Code: {store.pro_code} (9 Zeichen, geheim halten)",
        **TERMS,
    )
    assert account.pro


def test_duplicate_email_and_short_password_are_rejected(store: AuthStore) -> None:
    with pytest.raises(ValueError, match="7 Zeichen"):
        store.register("a@example.org", "kurz", "normal", **TERMS)
    store.register("a@example.org", "eine sehr lange Passphrase", "normal", **TERMS)
    with pytest.raises(ValueError, match="bereits"):
        store.register("A@example.org", "noch eine lange Passphrase", "normal", **TERMS)


def test_session_is_random_device_bound_and_revocable(store: AuthStore) -> None:
    account = store.register(
        "a@example.org", "eine sehr lange Passphrase", "normal", **TERMS
    )
    token = store.create_session(account, "Firefox|de", "192.168.1.4")
    assert token != store.create_session(account, "Firefox|de", "192.168.1.4")
    assert store.session_account(token, "Firefox|de") == account
    assert store.session_account(token, "anderes Geraet") is None
    store.logout(token)
    assert store.session_account(token, "Firefox|de") is None


def test_pro_code_is_nine_characters_and_persists(tmp_path) -> None:
    generated = pro_code_for(tmp_path)
    assert len(generated) == 9 and generated.isalnum()
    assert pro_code_for(tmp_path) == generated
    assert len(new_pro_code()) == 9


def test_rate_limiter_opens_again_after_window(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [10.0]
    monkeypatch.setattr("aquaticy.auth.time.monotonic", lambda: now[0])
    limiter = RateLimiter(attempts=2, window_seconds=10)
    assert limiter.allow("client")
    assert limiter.allow("client")
    assert not limiter.allow("client")
    now[0] = 21.0
    assert limiter.allow("client")


def test_normal_profile_cannot_enable_pro_integrations(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AQUATICY_DATA_DIR", str(tmp_path / "base"))
    profile = tmp_path / "normal"
    profile.mkdir()
    (profile / ".env").write_text(
        "AQUATICY_LAN_ENABLED=true\n"
        "AQUATICY_HA_URL=http://homeassistant:8123\n"
        "HA_TOKEN=secret\n"
        "AQUATICY_STORAGE_URL=http://lager:3000\n"
        "AQUATICY_STORAGE_ACCESS=write\n"
        "AQUATICY_VM_SIZE=plus\n",
        encoding="utf-8",
    )
    account = Account("u1", "a@example.org", "normal", 0)
    settings = ChatSession(account, profile).settings()
    assert settings.lan_enabled is False
    assert settings.ha_url == "" and settings.ha_token == ""
    assert settings.storage_url == "" and settings.storage_access == "off"
    assert settings.vm_size == "normal"
    assert (settings.vm_cpus, settings.vm_memory_mb, settings.vm_disk_gb) == (1, 1024, 4)
