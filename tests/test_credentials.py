import pytest

from credentials import (
    CredentialConfigurationError, kraken_credentials, t212_credentials,
)


def test_kraken_cache_uses_complete_dedicated_pair():
    pair = kraken_credentials("cache", values={
        "KRAKEN_API_KEY_CACHE": "cache-key",
        "KRAKEN_API_SECRET_CACHE": "cache-secret",
        "KRAKEN_API_KEY_BOT": "bot-key",
        "KRAKEN_API_SECRET_BOT": "bot-secret",
    })
    assert (pair.key, pair.secret, pair.profile) == (
        "cache-key", "cache-secret", "Kraken CACHE",
    )


def test_kraken_cache_fallback_is_an_explicit_complete_pair():
    pair = kraken_credentials("cache", values={
        "KRAKEN_API_KEY_BOT": "bot-key",
        "KRAKEN_API_SECRET_BOT": "bot-secret",
    })
    assert pair.profile == "Kraken BOT fallback"


def test_partial_primary_profile_does_not_hide_behind_fallback():
    with pytest.raises(CredentialConfigurationError, match="CACHE"):
        kraken_credentials("cache", values={
            "KRAKEN_API_KEY_CACHE": "partial",
            "KRAKEN_API_KEY_BOT": "bot-key",
            "KRAKEN_API_SECRET_BOT": "bot-secret",
        })


def test_fleet_never_mixes_key_and_secret_from_different_profiles():
    with pytest.raises(CredentialConfigurationError, match="primary"):
        kraken_credentials("fleet", values={
            "KRAKEN_API_KEY": "primary-key",
            "KRAKEN_API_SECRET_SPARE": "spare-secret",
            "KRAKEN_API_KEY_BOT": "bot-key",
            "KRAKEN_API_SECRET_BOT": "bot-secret",
        })


def test_t212_requires_both_halves():
    with pytest.raises(CredentialConfigurationError, match="T212"):
        t212_credentials(values={"T212_API_KEY": "key-only"})
