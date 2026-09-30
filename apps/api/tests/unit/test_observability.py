"""Sentry must be the integration that can never take the API down.

THE INVARIANT
=============

``init_sentry`` runs during the FastAPI lifespan, which runs on every boot. If it
can raise, then a typo in one environment variable prevents the service from
starting - and the resulting outage is invisible to the very system that was
supposed to report it. So the three states worth testing are not "works" and
"doesn't work" but:

  1. no DSN      -> disabled, and the SDK is never touched
  2. good DSN    -> the SDK is initialised with the right arguments
  3. broken DSN  -> the failure is swallowed and the caller still gets a working app

The third is the one that matters and the one least likely to be written by hand.

MOCKING, AND WHY IT IS NOT OPTIONAL HERE
========================================

Every test that supplies a DSN patches ``sentry_sdk.init``. This is not defensive
style - the real ``sentry_sdk.init`` starts a background transport thread that
begins POSTing events to sentry.io. A test that called it with a syntactically
valid fake DSN would open a real outbound connection, and the suite would become
flaky and network-dependent for reasons that have nothing to do with the code
under test.

Patching the attribute on the ``sentry_sdk`` module object works because
``init_sentry`` does ``import sentry_sdk`` INSIDE the function body, resolving the
same object out of ``sys.modules`` on every call. A module-level import in
``observability.py`` would have forced import-time patching instead.
"""

from __future__ import annotations

import builtins
import logging

import pytest
import sentry_sdk

from app.core.config import Settings
from app.core.observability import dsn_host, init_sentry

#: Shaped like a real DSN so any validation inside the SDK would pass. The key is
#: fake and the host does not resolve - nothing is ever sent.
FAKE_DSN = "https://abc123def456@o123456.ingest.sentry.io/1234567"


@pytest.fixture
def sentry_init_calls(monkeypatch):
    """Record every sentry_sdk.init call instead of performing it.

    Returns the list that gets appended to, so a test can assert on the exact
    keyword arguments rather than on a looser "it was called" truthiness.
    """
    calls: list[dict] = []

    def _fake_init(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(sentry_sdk, "init", _fake_init)
    return calls


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


# ------------------------------------------------------- 1. disabled, no DNS


def test_sentry_disabled_without_dsn(sentry_init_calls, caplog) -> None:
    """No DSN means the SDK is never called at all.

    Asserted on the recorded calls rather than on the return value alone: a
    refactor that initialised the SDK and then ignored the DSN would still return
    False, and would start a transport thread in every local run and in CI.
    """
    with caplog.at_level(logging.WARNING, logger="app.core.observability"):
        result = init_sentry(_settings())

    assert result is False
    assert sentry_init_calls == [], "the SDK must not be touched with no DSN"
    assert "Sentry disabled" in caplog.text


def test_an_empty_string_dsn_is_also_disabled(sentry_init_calls) -> None:
    """`SENTRY_DSN=` in a .env file yields "", not None. Same outcome."""
    assert init_sentry(_settings(sentry_dsn="")) is False
    assert sentry_init_calls == []


# ---------------------------------------------------------- 2. enabled, DSN set


def test_sentry_enabled_with_dsn(sentry_init_calls) -> None:
    """The happy path, asserted on the real arguments.

    `traces_sample_rate` and `environment` are checked explicitly rather than
    "init was called": both are decisions with a cost attached, and an edit that
    set traces_sample_rate=1.0 would otherwise pass every test here while quietly
    multiplying the Sentry bill.
    """
    result = init_sentry(_settings(sentry_dsn=FAKE_DSN, environment="staging"))

    assert result is True
    assert len(sentry_init_calls) == 1

    kwargs = sentry_init_calls[0]
    assert kwargs["dsn"] == FAKE_DSN
    assert kwargs["traces_sample_rate"] == 0.1
    assert kwargs["environment"] == "staging"


def test_the_fastapi_and_starlette_integrations_are_registered(sentry_init_calls) -> None:
    """Without these, Sentry sees 500s but no route, no trace and no request data.

    Asserted on class names rather than instances so the test does not pin a
    particular constructor signature.
    """
    init_sentry(_settings(sentry_dsn=FAKE_DSN))

    names = {type(i).__name__ for i in sentry_init_calls[0]["integrations"]}
    assert "FastApiIntegration" in names
    assert "StarletteIntegration" in names


def test_the_environment_is_passed_through(sentry_init_calls) -> None:
    """`environment` should never reach Sentry as "" - it scopes every issue."""
    init_sentry(_settings(sentry_dsn=FAKE_DSN, environment="development"))
    assert sentry_init_calls[0]["environment"] == "development"


# ----------------------------------------------------- 3. broken, must not raise


def test_sentry_bad_dsn_does_not_crash(monkeypatch) -> None:
    """THE test. A DSN the SDK rejects must not stop the app booting.

    This is the whole reason init_sentry wraps its body: the failure mode being
    prevented is "a monitoring configuration error becomes a total outage".
    """

    def _boom(**kwargs):
        raise ValueError("malformed DSN")

    monkeypatch.setattr(sentry_sdk, "init", _boom)

    # No pytest.raises: reaching the next line IS the assertion.
    assert init_sentry(_settings(sentry_dsn="not-a-valid-dsn")) is False


def test_an_arbitrary_sdk_exception_is_also_swallowed(monkeypatch) -> None:
    """Broad except on purpose - a changed SDK signature must not be fatal.

    Catching only ValueError would pass the test above and still let an
    AttributeError or TypeError from a future SDK version take the process down.
    """

    def _boom(**kwargs):
        raise RuntimeError("SDK internals changed")

    monkeypatch.setattr(sentry_sdk, "init", _boom)

    assert init_sentry(_settings(sentry_dsn=FAKE_DSN)) is False


def test_the_failure_is_logged_with_a_reason(monkeypatch, caplog) -> None:
    """Silent degradation is the other failure mode: nobody knows it is broken."""

    def _boom(**kwargs):
        raise ValueError("malformed DSN")

    monkeypatch.setattr(sentry_sdk, "init", _boom)

    with caplog.at_level(logging.ERROR, logger="app.core.observability"):
        init_sentry(_settings(sentry_dsn="not-a-valid-dsn"))

    assert "Sentry initialisation failed" in caplog.text


def test_a_missing_sdk_does_not_crash(monkeypatch) -> None:
    """sentry-sdk is in requirements.txt but is still an optional import here.

    If the package is ever missing from a slim image, the API must serve traffic
    rather than fail to start.
    """
    real_import = builtins.__import__

    def _no_sentry(name, *args, **kwargs):
        if name == "sentry_sdk" or name.startswith("sentry_sdk."):
            raise ImportError("No module named 'sentry_sdk'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_sentry)

    assert init_sentry(_settings(sentry_dsn=FAKE_DSN)) is False


# ------------------------------------------------------ the DSN is not logged


def test_the_dsn_key_is_never_returned_by_the_log_helper() -> None:
    """A DSN embeds a public key. Logging it verbatim writes a credential to disk."""
    host = dsn_host(FAKE_DSN)

    assert "abc123def456" not in host
    assert host == "o123456.ingest.sentry.io"


def test_dsn_host_survives_garbage() -> None:
    """It is called on a startup path; it must not be the thing that crashes."""
    assert dsn_host("not-a-dsn") == "unknown"
    assert dsn_host("") == "unknown"
