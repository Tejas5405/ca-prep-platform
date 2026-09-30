"""Error reporting to Sentry. Optional, and never allowed to break startup.

WHY A MODULE AT ALL
===================

Sentry is the only integration in this codebase that touches every request, so it
is also the only one whose failure would be catastrophic in a boring way: a typo in
a DSN, a bad extra, or an incompatible SDK would raise during import of
``app/main.py`` and take the whole API down. Error reporting that can cause the
outage it exists to diagnose is worse than no error reporting.

So the contract here is narrow and absolute:

* **No DSN means disabled.** Not an error, not a warning-level event. A local
  developer and CI both run with no DSN and must be completely unaffected.
* **A bad DSN never raises.** Every call into the SDK is wrapped. A malformed DSN
  degrades to "Sentry is off", which is the state we were in a moment earlier
  anyway.
* **It is called once, from the lifespan**, after logging is configured, so a
  Sentry event about a startup failure can itself be reported.

TRACES ARE 10%
==============

``traces_sample_rate=0.1`` is deliberate and is the one number worth arguing
about. At 100% every request becomes a performance transaction: for a
question-bank API where most traffic is short JSON reads, that is a large bill for
data nobody acts on. At 0 there is no performance regression signal at all, which
means the first time the API gets slow there is nothing to compare against. 10%
is the compromise - enough transactions to see a trend, cheap enough to leave on
in production permanently.

Only *errors* are sent in full. Successful requests are sampled, not reported, so
PII in a response body is not shipped off-box by default.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def init_sentry(settings: Any) -> bool:
    """Initialise Sentry from settings. Returns True if it was enabled.

    The return value exists for the tests and for the startup log line. Nothing
    branches on it at runtime: a False here is a normal, fully supported state.

    Takes the settings object rather than reading ``get_settings()`` itself so
    that a test can pass a purpose-built Settings without mutating the process
    environment, and so the dependency is visible in the signature.
    """
    dsn = getattr(settings, "sentry_dsn", None)

    if not dsn:
        # WARNING, not INFO: an operator who believes they have error reporting
        # and does not is worse off than one who never configured it. This line
        # is the only signal they will get, and it is emitted on every boot.
        logger.warning(
            "Sentry disabled: no DSN configured. Set SENTRY_DSN to enable error "
            "reporting. The API is unaffected either way."
        )
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        sentry_sdk.init(
            dsn=dsn,
            environment=getattr(settings, "environment", "development"),
            traces_sample_rate=0.1,
            integrations=[FastApiIntegration(), StarletteIntegration()],
            # Release tagging is what makes a Sentry issue attributable to a
            # deploy rather than to "sometime this week". No CI/CD here publishes
            # a release, so the commit SHA is the strongest available signal.
            release=_git_release(),
        )
    except Exception:
        # Deliberately broad. The alternatives are worse: catching only
        # ValueError misses a changed SDK signature, and letting it propagate
        # means a monitoring dependency can prevent the service from starting.
        # The reason is logged at full detail for a human; the operator gets a
        # working API without error reporting, which is the pre-Sentry status quo.
        logger.exception(
            "Sentry initialisation failed; continuing without error reporting. "
            "Check SENTRY_DSN is a valid DSN from sentry.io project settings."
        )
        return False

    logger.info("Sentry enabled (env=%s, traces_sample_rate=0.1)", dsn_host(dsn))
    return True


def _git_release() -> str | None:
    """The commit SHA, or None. Best-effort and never raises.

    Deliberately does NOT shell out to git: a source tarball or a Docker layer
    has no .git, the subprocess would add a startup dependency for a cosmetic
    label, and ``S608``-class shell use at import time is not worth it.
    """
    import os

    return os.environ.get("GIT_COMMIT_SHA") or os.environ.get("RENDER_GIT_COMMIT")


def dsn_host(dsn: str) -> str:
    """The host part of a DSN, for logging. Never the key.

    A DSN embeds a secret: ``https://<public-key>@o123.ingest.sentry.io/456``.
    Logging the DSN string would therefore write a credential to the log file, so
    only the host - the part that identifies which Sentry project - is ever
    printed. The secret is not recoverable from what gets logged.
    """
    try:
        from urllib.parse import urlparse

        return urlparse(dsn).netloc.rsplit("@", 1)[-1] or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"
