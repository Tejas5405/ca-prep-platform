"""The AI fallback is configuration, and it only fires when it can differ.

THE DEFECT THIS PINS: the service hardcoded a "fallback" model equal to the
default model. `if text is None and model != _FALLBACK_MODEL` could therefore
never be true for a default-configured deployment - a failed primary call was
never retried against a different model, and nothing anywhere said so. The fix
moves the fallback into configuration (`AI_PROVIDER_FALLBACK_MODEL`), passes it
through the assistant route, and refuses an identical pair at Settings
validation time.

These tests are offline: the provider call is monkeypatched, so the assertions
are about WHICH models were asked for, not about any account or quota.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services import gemini

EXCERPTS = ["GSTR-3B is due on the 20th of the month following the tax period."]


@pytest.fixture()
def provider(monkeypatch):
    """Record requested models and answer per model, with no network call."""
    asked: list[str] = []
    answers: dict[str, str | None] = {}

    async def fake_call(api_key: str, model: str, prompt: str) -> str | None:
        asked.append(model)
        return answers.get(model)

    monkeypatch.setattr(gemini, "_call", fake_call)
    return asked, answers


async def test_primary_failure_uses_the_configured_fallback(provider):
    asked, answers = provider
    answers["primary-model"] = None
    answers["fallback-model"] = "Ground answer from the fallback."

    result = await gemini.write_suggestion(
        api_key="test-key",
        model="primary-model",
        query="When is GSTR-3B due?",
        excerpts=EXCERPTS,
        fallback_model="fallback-model",
    )

    assert asked == ["primary-model", "fallback-model"]
    assert result is not None
    assert result.startswith("Study suggestion:")


async def test_primary_success_does_not_call_the_fallback(provider):
    asked, answers = provider
    answers["primary-model"] = "Study suggestion: primary answer."

    result = await gemini.write_suggestion(
        api_key="test-key",
        model="primary-model",
        query="When is GSTR-3B due?",
        excerpts=EXCERPTS,
        fallback_model="fallback-model",
    )

    assert asked == ["primary-model"], "the fallback must not be called on success"
    assert result == "Study suggestion: primary answer."


async def test_unset_fallback_means_exactly_one_attempt(provider):
    asked, answers = provider
    answers["primary-model"] = None

    result = await gemini.write_suggestion(
        api_key="test-key",
        model="primary-model",
        query="When is GSTR-3B due?",
        excerpts=EXCERPTS,
        fallback_model=None,
    )

    assert asked == ["primary-model"]
    assert result is None, "no suggestion is invented when the only attempt fails"


async def test_an_identical_fallback_is_never_used_as_a_retry(provider):
    """Defence in depth: even if a caller passes the same model, it is ignored."""
    asked, answers = provider
    answers["same-model"] = None

    result = await gemini.write_suggestion(
        api_key="test-key",
        model="same-model",
        query="When is GSTR-3B due?",
        excerpts=EXCERPTS,
        fallback_model="same-model",
    )

    assert asked == ["same-model"], "retrying the same model is the same outage"
    assert result is None


def test_config_rejects_identical_primary_and_fallback_models():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            ai_provider_model="gemini-3.8-flash",
            ai_provider_fallback_model="gemini-3.8-flash",
        )


def test_config_accepts_a_genuinely_different_fallback():
    settings = Settings(
        _env_file=None,
        ai_provider_model="gemini-3.8-flash",
        ai_provider_fallback_model="gemini-3.1-flash-lite-preview",
    )
    assert settings.ai_provider_fallback_model == "gemini-3.1-flash-lite-preview"


def test_config_allows_an_unset_fallback():
    settings = Settings(_env_file=None, ai_provider_model="gemini-3.8-flash")
    assert settings.ai_provider_fallback_model is None
