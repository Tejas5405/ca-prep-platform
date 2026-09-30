"""Settings.debug must never be the reason the app fails to boot.

THE BUG THIS EXISTS TO PIN DOWN
===============================

``debug: bool`` with no validator is STRICT. Pydantic raises ``ValidationError``
for anything it cannot parse, and that raise happens inside ``Settings()`` in
``app/main.py`` - at import time, before the app has served a request, before a
health check, before anything can report the problem. One ambient shell variable
takes the whole service down:

    $ DEBUG=release pytest
    E   pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
    E   debug
    E     Input should be a valid boolean, unable to interpret input
    E     [type=bool_parsing, input_value='release']

That is not a hypothetical. It is a real crash encountered in this repo, and it
is why ``coerce_debug`` exists. The rule it encodes: ``true``/``1``/``yes``/``on``
mean True, everything else means False, and NOTHING raises.

ISOLATION, AND WHY IT IS NOT PARANOIA
=====================================

Every test here clears ``DEBUG`` first. The developer who finds this bug by
running the suite with ``DEBUG=release`` exported is, by definition, the person
whose shell has ``DEBUG`` exported - so an un-isolated version of this file
cannot reproduce its own subject. ``_env_file=None`` stops the repo's real ``.env``
from supplying a value either, for the same reason.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _isolate_debug(monkeypatch, tmp_path):
    """Start every test with no DEBUG in the environment and no env file.

    Also runs from a tmp directory and clears the ``get_settings`` cache, because
    a value resolved by an earlier test would otherwise be reused here and the
    test would pass or fail for the wrong reason.
    """
    monkeypatch.delenv("DEBUG", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("ENV_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _debug_from_env(value: str) -> bool:
    """Load Settings with DEBUG set to `value`, reading no env file."""
    import os

    os.environ["DEBUG"] = value
    try:
        return Settings(_env_file=None).debug
    finally:
        del os.environ["DEBUG"]


# ---------------------------------------------------------------- the headline


def test_debug_release_does_not_crash_the_app():
    """THE regression. `DEBUG=release` used to make Settings() raise.

    Asserted on the value rather than merely "does not raise", because a
    validator that returned a non-bool would also not raise here and would then
    fail somewhere else, further from the cause.
    """
    assert _debug_from_env("release") is False


@pytest.mark.parametrize(
    "raw",
    ["true", "1", "yes", "on"],
    ids=["true", "one", "yes", "on"],
)
def test_truthy_spellings_are_true(raw: str) -> None:
    """Every accepted affirmative spelling, in the case it is written in."""
    assert _debug_from_env(raw) is True


@pytest.mark.parametrize(
    "raw",
    ["TRUE", "True", "YES", "ON", "1"],
    ids=["TRUE", "True", "YES", "ON", "digit-1"],
)
def test_coercion_is_case_insensitive(raw: str) -> None:
    """`DEBUG=True` and `DEBUG=TRUE` are what people actually type."""
    assert _debug_from_env(raw) is True


@pytest.mark.parametrize(
    "raw",
    ["false", "0", "no", "off", "", "release", "null", "none", "2", "-1", "  "],
    ids=[
        "false",
        "zero",
        "no",
        "off",
        "empty",
        "release",
        "null",
        "none",
        "two",
        "negative",
        "whitespace",
    ],
)
def test_everything_else_is_false_and_nothing_raises(raw: str) -> None:
    """Fails safe, and never raises.

    The junk values are the point, not padding: `release` and `off` are the two
    that have actually bitten. `2` and `-1` are here because "not a recognised
    affirmative" is the rule - a validator that treated any non-zero number as
    True would be inventing a fourth spelling nobody agreed to.
    """
    assert _debug_from_env(raw) is False


def test_surrounding_whitespace_is_ignored() -> None:
    """`DEBUG=' true '` arrives with padding from copy-paste and .env quoting."""
    assert _debug_from_env("  true  ") is True


def test_debug_unset_falls_back_to_the_default() -> None:
    """Unset is not "unset" - it is the field default, False."""
    assert Settings(_env_file=None).debug is False


def test_a_real_bool_passes_through_unchanged() -> None:
    """Tests construct `Settings(debug=True)`; that must not be stringified away."""
    assert Settings(_env_file=None, debug=True).debug is True
    assert Settings(_env_file=None, debug=False).debug is False


def test_the_validator_never_raises_for_any_string() -> None:
    """The property, asserted directly rather than by example.

    Every value above is a case someone thought of. This one is the general
    claim, so a future truthy spelling cannot slip through untested.
    """
    import os

    for raw in ("release", "prod", "verbose", "t", "y", "TRUE", "ON", "0", "", " ", "3.14"):
        os.environ["DEBUG"] = raw
        try:
            assert isinstance(Settings(_env_file=None).debug, bool), raw
        finally:
            del os.environ["DEBUG"]
