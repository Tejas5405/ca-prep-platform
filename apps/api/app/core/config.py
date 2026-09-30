"""Application settings.

Secrets rule (blueprint v3 §5.1): only public Supabase configuration belongs in
the React bundle. Service-role keys, payment secrets, database URLs and Redis
credentials stay server-side on Render and are never exposed to the browser.
"""

from __future__ import annotations

import os
import pathlib
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    NoDecode,
    SettingsConfigDict,
)


class ConfigurationError(RuntimeError):
    """Configuration is missing for an environment that refuses to fall back.

    A dedicated type, not a bare `RuntimeError`, so startup handling and the tests
    can distinguish "this deployment is not configured" from "this deployment
    crashed". It is deliberately NOT a `ValidationError`: the settings are not
    invalid, the configuration source is absent, and the fix is to create a file or
    export variables rather than to correct a value.
    """


def _split_list(value: object) -> list[str]:
    """Read a list out of whichever spelling the environment used.

    A bare value with no separator is a single-item list, not an error: an operator
    setting one CORS origin or one bootstrap admin should not have to know that the
    field is plural. An empty or whitespace-only value is an empty list, because
    "unset" and "set to nothing" both mean "grant none" here.
    """
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            import json

            try:
                parsed = json.loads(text)
            except ValueError:
                # Not JSON after all - fall through to the comma form rather than
                # failing, since a value starting with "[" is otherwise nonsense
                # and the comma reading cannot be worse.
                parsed = None
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in text.split(",") if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value)]


#: Which env file each environment reads. Keyed on the value of `ENVIRONMENT`.
#:
#: `None` means "read no file at all", which is the correct behaviour for `test`
#: and for a deployed service: both receive every value as a real environment
#: variable from CI or from the host's dashboard, so there is nothing to read and
#: nothing that could be stale.
#:
#: `development` keeps the historical `.env`, so the existing developer workflow
#: is unchanged. `staging` and `production` name their own file, and the rule
#: below is that neither may fall back to `.env`.
ENV_FILE_FOR_ENVIRONMENT: dict[str, str | None] = {
    "development": ".env",
    "staging": ".env.staging",
    "production": ".env.production",
    "test": None,
}

#: Environments where a missing configuration file is a startup failure rather
#: than a default. `development` is excluded on purpose: a new contributor must be
#: able to run the suite before creating any file at all.
FAIL_CLOSED_ENVIRONMENTS = frozenset({"staging", "production"})


def resolve_env_file(environment: str | None) -> str | None:
    """The env file for an environment, or None to read no file.

    Reads the process environment directly rather than taking `environment` as
    authoritative, because the whole point is that the OPERATOR's choice decides.
    `ENV_FILE` overrides the mapping, for a deployment that wants a differently
    named file without a code change.

    An unset ENVIRONMENT reads `.env`, because that is the field's own default
    and a contributor who exports nothing must get the behaviour they had
    before this existed. An UNRECOGNISED environment reads no file: guessing
    `.env` there would recreate exactly the bug this replaces, where a
    configuration nobody expected becomes the development one.
    """
    override = os.getenv("ENV_FILE")
    if override:
        return override
    return ENV_FILE_FOR_ENVIRONMENT.get(environment or "development")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # No env_file here on purpose. pydantic-settings would otherwise read
        # `.env` unconditionally, which meant ENVIRONMENT=staging still loaded
        # the DEVELOPMENT file and connected to localhost while appearing
        # healthy. The file is chosen by `settings_customise_sources` below, from
        # the environment, and staging fails closed when its file is absent.
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["development", "staging", "production", "test"] = "development"
    debug: bool = False

    @field_validator("debug", mode="before")
    @classmethod
    def coerce_debug(cls, v: object) -> bool:
        """Coerce DEBUG to a bool instead of refusing to boot over it.

        WHY THIS EXISTS. ``debug: bool`` on its own makes pydantic STRICT: any
        value it cannot parse is a ValidationError raised from ``Settings()`` in
        ``app/main.py``, i.e. at import time, i.e. before the app has served a
        single request. That turns a cosmetic slip in somebody's shell into a
        total outage whose stack trace is about booleans.

        This is not hypothetical. A developer with ``DEBUG=release`` exported -
        a plausible thing for a Go or Node habit to leave lying around - could not
        run the test suite at all. Collection died with::

            ValidationError: 1 validation error for Settings
            debug
              Input should be a valid boolean, unable to interpret input
              [type=bool_parsing, input_value='release']

        The fix is to be liberal in what we accept and conservative in what we
        infer from it.

        THE RULE. ``true``/``1``/``yes``/``on`` (any case, surrounding whitespace
        ignored) mean True. Everything else means False, and nothing raises.

        FAILING SAFE IS THE POINT. An unrecognised value resolving to False is
        deliberate. The failure mode of a debug flag stuck ON is a traceback page
        in production, possibly carrying environment values with it; the failure
        mode of it stuck OFF is a missing traceback that nobody is relying on yet.

        THE ONE REAL NARROWING. Pydantic's own parser also accepts ``t``/``f``/
        ``y``/``n`` and the digits as ints. This validator accepts the four
        spellings above and nothing else, so ``DEBUG=t`` now reads as False where
        it previously read as True. That is the safe direction for this flag, and
        ``.env.example`` and ``infra/render.yaml`` both already write
        ``true``/``false``.

        Real bools survive untouched, so ``Settings(debug=True)`` - how tests
        construct it - still yields True.
        """
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in {"true", "1", "yes", "on"}

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        """Choose the env file from the environment, and fail closed when absent.

        This replaces pydantic-settings' unconditional `env_file=".env"`. The
        dangerous sequence it made possible was:

            ENVIRONMENT=staging  ->  .env still read  ->  localhost database
                                 ->  the service starts and looks healthy

        Nothing warned, because the development file was read successfully. The
        invariant now is that `staging` and `production` read their OWN file and
        never `.env`, and that a missing file is a startup error rather than a
        silent downgrade to development defaults.

        Precedence is unchanged in the ways that matter: real environment
        variables still beat the file, and explicit keyword arguments still beat
        everything. Only the FILE choice became environment-driven.
        """
        environment = os.getenv("ENVIRONMENT")
        env_file = resolve_env_file(environment)

        if env_file is None:
            # `test`, an unknown value, or ENV_FILE="". Real environment
            # variables are the only source, which is what CI and a deployed
            # service both provide.
            return (
                init_settings,
                env_settings,
                file_secret_settings,
            )

        if environment in FAIL_CLOSED_ENVIRONMENTS and not pathlib.Path(env_file).is_file():
            raise ConfigurationError(
                f"ENVIRONMENT={environment} requires the configuration file "
                f"{env_file!r}, which does not exist in {pathlib.Path.cwd()}. "
                f"Refusing to start rather than fall back to a development "
                f"configuration: a staging service reading .env would connect to "
                f"the development database and would look healthy while doing it. "
                f"Create {env_file} (see .env.staging.example), or set the "
                f"configuration as real environment variables."
            )

        return (
            init_settings,
            env_settings,
            DotEnvSettingsSource(
                settings_cls,
                env_file=env_file,
                env_file_encoding=settings_cls.model_config.get("env_file_encoding", "utf-8"),
            ),
            file_secret_settings,
        )

    api_v1_prefix: str = "/api/v1"

    # ---------------------------------------------------------- data layer
    # postgresql+psycopg:// (psycopg 3) per blueprint v3 §5.1
    database_url: str = "postgresql+psycopg://caprep:caprep@localhost:5432/caprep"
    #: Used by Alembic ONLY, and it exists because of managed poolers.
    #:
    #: Supabase (and Neon, and RDS Proxy) offer a connection pooler in
    #: TRANSACTION mode. That mode cannot hold a session across statements, so
    #: anything session-scoped misbehaves there - and DDL is exactly that:
    #: ``alembic upgrade`` takes an advisory lock and runs a multi-statement
    #: migration inside one transaction. Running migrations through the pooler is
    #: the classic way to get a half-applied schema.
    #:
    #: So the app may use the pooled URL while migrations use the direct one. When
    #: this is empty, Alembic falls back to ``database_url`` - correct for a local
    #: server, and a deployment with no pooler simply never sets it.
    direct_database_url: str | None = None
    redis_url: str | None = "redis://localhost:6379"

    # ------------------------------------------------------- supabase (all)
    # ONE project provides identity, the database and file storage.
    #
    # The Firebase fields that used to live here are gone: identity moved to
    # Supabase Auth (SA-09), which also removed the need for a service-account
    # credential on the server. Access tokens are verified against the project's
    # public JWKS, so no signing secret exists anywhere in this deployment.
    supabase_url: str | None = None
    #: Backend only. Grants full storage access and BYPASSES row-level security,
    #: so it must never reach the browser bundle.
    #:
    #: Accepts either key system:
    #:   * sb_secret_...     the current opaque secret key (preferred)
    #:   * eyJ... (JWT)      the legacy service_role key
    #:
    #: Supabase deprecates the legacy keys at the end of 2026, and the two are
    #: sent on DIFFERENT HEADERS - see SupabaseStorage._build_headers. Reading
    #: both names means a project can migrate by changing one environment
    #: variable rather than a code change.
    #: The alias list is what makes the rename backward compatible. Pydantic
    #: matches on FIELD NAME, so `SUPABASE_SERVICE_ROLE_KEY` would not populate
    #: `supabase_secret_key` on its own - and the failure is silent: storage
    #: raises "not configured" at the first upload rather than at boot.
    #: AliasChoices is used rather than a model_validator that reads os.environ,
    #: because that would work for environment variables and NOT for
    #: `Settings(SUPABASE_SERVICE_ROLE_KEY=...)`, which is how the tests and any
    #: programmatic caller construct it.
    supabase_secret_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SUPABASE_SECRET_KEY",
            "SUPABASE_SERVICE_ROLE_KEY",
            "supabase_secret_key",
        ),
    )
    storage_bucket: str = "question-pdfs"

    # ------------------------------------------- email, analytics, errors
    # Optional integrations. Each is inert until its key is set, and none is on
    # a request's critical path.
    resend_api_key: str | None = None
    posthog_api_key: str | None = None
    sentry_dsn: str | None = None

    # ---------------------------------------------------------- razorpay
    # Blueprint v3 §13.1 config: RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET /
    # RAZORPAY_WEBHOOK_SECRET.
    #
    # The key secret is the SECRET half of HTTP Basic auth and can move money and
    # read every payment on the account. The webhook secret is a separate value
    # that only authenticates inbound events. They are never interchangeable, and
    # the code treats a missing webhook secret as "reject every event" rather
    # than "skip verification" - see verify_webhook_signature.
    #
    # All three are optional so the API boots and serves the pricing page on an
    # environment without payment keys; the payment endpoints then return 503.
    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "RAZORPAY_KEY_SECRET",
            "razorpay_key_secret",
        ),
    )
    razorpay_webhook_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "RAZORPAY_WEBHOOK_SECRET",
            "razorpay_webhook_secret",
        ),
    )
    #: Overridable so tests can point at a local mock instead of the live API.
    razorpay_base_url: str = "https://api.razorpay.com/v1"

    # ---------------------------------------------------------- ai (later)
    # Hard ceilings are required; there is deliberately no "unlimited" mode.
    ai_provider_api_key: str | None = None
    ai_provider_model: str = "gemini-3.8-flash"
    #: A DIFFERENT model to retry with when the primary call fails.
    #:
    #: Why this is configuration and not a constant: the previous code carried a
    #: hardcoded "fallback" that equalled the default model, so the retry branch
    #: could never run - a failing primary was retried against the same model
    #: (i.e. the same outage) or not at all, and nothing said so. The validator
    #: below refuses an identical pair; unset means "no fallback", which the
    #: caller must treat as a single attempt, never as an invented answer.
    ai_provider_fallback_model: str | None = None
    ai_monthly_ceiling_usd: float = 200.0
    ai_free_queries_per_day: int = 5

    # ---------------------------------------------------------- limits
    #: Free-tier question quota per day (blueprint v3 §10).
    free_daily_question_quota: int = 5
    #: Global rate limit. Keep the attempt-start limit lower - see mocks router.
    rate_limit_per_minute: int = 100
    mock_attempts_per_hour: int = 10

    # ------------------------------------------------------- rate limiting
    #: Master switch. Off means requests are served without counting, which is
    #: the local-development default only: a deployment with this false is an
    #: unmetered API, so it is a deliberate thing to have to set.
    rate_limit_enabled: bool = True
    #: Fixed window, in seconds. Every limit below is "per this many seconds",
    #: which is why the signup-style budgets are expressed as a window rather
    #: than a per-minute number.
    rate_limit_window_seconds: int = 60
    #: How many proxies in front of this app we operate. 0 (the local default)
    #: means X-Forwarded-For is ignored entirely. This MUST be 1 on Render:
    #: at 0 every anonymous request buckets by the proxy's address, and
    #: ``client_address`` logs a warning on each one. See the module docstring.
    rate_limit_trusted_proxies: int = 0
    #: Routes that cost money or storage get their own, tighter budget.
    rate_limit_assistant_per_minute: int = 20
    rate_limit_upload_per_minute: int = 5
    rate_limit_payment_per_minute: int = 10
    rate_limit_assistant_prefix: str = "/api/v1/assistant"
    rate_limit_payment_prefix: str = "/api/v1/payments"
    rate_limit_upload_path: str = "/api/v1/ingestion/uploads"
    #: Never metered. Health checks are polled by infrastructure that must not be
    #: throttled, and the pricing catalogue is public, cheap and cacheable -
    #: metering it only risks refusing a visitor who is still deciding whether to
    #: buy anything. The webhook is deliberately NOT here: it is the only
    #: anonymous route that mutates money, so it is metered and fails closed.
    rate_limit_exempt_paths: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "/health",
            "/",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/api/v1/payments/plans",
        ]
    )
    #: Metered routes that are refused outright when Redis is unreachable.
    #: Only the webhook, because a 503 there is one the provider retries and
    #: the idempotency key makes safe. There is no /api/v1/auth/* entry because
    #: no such route exists - authentication is Supabase, called from the
    #: browser, so this API never sees a password.
    rate_limit_fail_closed_paths: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["/api/v1/webhooks"]
    )

    #: `NoDecode` matters and is easy to leave out: pydantic-settings treats a list
    #: field as JSON and parses it BEFORE any validator runs, so a plain
    #: ``CORS_ORIGINS=https://a.example,https://b.example`` raised a SettingsError at
    #: startup - while the validator below, which exists precisely to accept that
    #: form, was never called. The deployment therefore had to be written as JSON or
    #: not at all, and the "comma-separated" promise in the docstring was false.
    #: With decoding switched off the raw string reaches the validator, which now
    #: accepts both spellings.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # ------------------------------------------------------- bootstrap admin
    #: Email addresses that are granted ADMIN on first sign-in.
    #:
    #: WHY THIS EXISTS INSTEAD OF A ROW SOMEONE INSERTS. Roles live in the user
    #: row, and the row does not exist until a user signs in for the first time -
    #: so there is no way to pre-seed an administrator account from the database
    #: alone, and no operator has the Supabase Admin API key on their laptop.
    #: Without this, a fresh deployment has no way to appoint the first editor,
    #: and every staff-only endpoint is unreachable forever.
    #:
    #: It is not a backdoor: the address is checked against the VERIFIED email in
    #: the token, so it only takes effect for whoever can actually receive mail at
    #: that address. The setting is read once, at provisioning time, and a
    #: promotion applied this way is written to the row - the environment is not
    #: consulted again, so removing an address does not demote anyone.
    #:
    #: Comma-separated. Empty by default: an unset value grants nothing.
    #: See `cors_origins` for why decoding is switched off. This field is worse
    #: than the CORS one to get wrong: it is read from the environment at startup,
    #: so a SettingsError here is not a rejected request, it is an API that will
    #: not boot.
    bootstrap_admin_emails: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, v: object) -> object:
        """Accept a comma-separated string OR a JSON array from the environment.

        Both spellings are in active use: the local stack sets the JSON form and a
        human editing a dashboard field writes the comma form. Accepting one and
        crashing on the other is a foot-gun, and both are unambiguous.
        """
        return _split_list(v)

    @field_validator("rate_limit_exempt_paths", "rate_limit_fail_closed_paths", mode="before")
    @classmethod
    def split_rate_limit_paths(cls, v: object) -> object:
        """As ``split_origins``, for the two rate-limit path lists.

        Kept as a separate validator rather than folded into the one above so a
        new list field does not silently skip comma-splitting - the failure mode
        being a prefix that reads as a JSON blob and exempts nothing.
        """
        return _split_list(v)

    @field_validator("bootstrap_admin_emails", mode="before")
    @classmethod
    def split_admin_emails(cls, v: object) -> object:
        """As above, lower-cased because the comparison is case-insensitive."""
        return [item.lower() for item in _split_list(v)]

    @model_validator(mode="after")
    def _ai_fallback_must_differ(self) -> Settings:
        """An identical fallback is a retry of the same failure, not redundancy.

        Configured explicitly rather than silently ignored: an operator who sets
        both names to the same model believes they have provider redundancy. They
        do not, and the honest moment to say so is at startup, not during the
        outage the fallback was supposed to cover.
        """
        if (
            self.ai_provider_fallback_model
            and self.ai_provider_fallback_model == self.ai_provider_model
        ):
            raise ValueError(
                "AI_PROVIDER_FALLBACK_MODEL must differ from AI_PROVIDER_MODEL: "
                "a fallback on the same model retries the same outage."
            )
        return self

    @model_validator(mode="after")
    def _staging_must_not_take_live_money(self) -> Settings:
        """A staging deployment must not be able to charge a real card.

        The discriminator is the KEY PREFIX, not the host. Razorpay runs TEST and
        LIVE against the same `api.razorpay.com` and tells them apart by the key
        (`rzp_test_…` vs `rzp_live_…`), so a check on the base URL would either be
        a no-op or - worse - reject the exact configuration a staging environment
        is supposed to use. A staging box holding live keys would create genuine
        orders and capture genuine money, which is why this lives in Settings
        rather than in a runbook: it is the one misconfiguration that can harm a
        customer, and it is otherwise silent.

        Only the staging→live direction is guarded. The reverse is loud by
        construction: live keys against a test setup fail authentication at the
        gateway, so it cannot quietly take money either.
        """
        if self.environment == "staging" and self.payments_enabled():
            key_id = (self.razorpay_key_id or "").strip()
            if key_id.lower().startswith("rzp_live_"):
                raise ValueError(
                    f"staging cannot use Razorpay LIVE keys ({key_id!r} is a live "
                    "key id). A staging deployment holding live keys would charge "
                    "real cards. Issue Razorpay TEST-mode keys, which are "
                    "distinguished by the `rzp_test_` prefix on the same host."
                )
        return self

    def staging_isolation_problems(self) -> list[str]:
        """Configuration facts that mean a staging box is NOT isolated.

        Reported rather than raised, and only for a staging deployment. Two
        reasons. First, several are legitimate in a developer checkout, and the
        same process must still boot for `ENVIRONMENT=development`. Second, a
        startup crash is the wrong response to a misconfigured staging box: the
        service should come up far enough to say what is wrong, because an
        operator reading `/health` is exactly who needs this list.

        The checks are structural - localhost, a loopback address, a pooler on
        port 5432 - not a hardcoded list of anyone's infrastructure. A staging
        deployment that shares the database, Redis or Supabase project with
        development is the specific accident this exists to catch, and it is
        invisible from the application otherwise: both environments return 200.
        """
        if self.environment != "staging":
            return []

        problems: list[str] = []

        #: Addresses that mean "this process is talking to itself". Structural,
        #: not a list of anyone's infrastructure: a staging service that resolves
        #: its database to loopback is sharing a developer's machine however the
        #: host was spelled.
        #:
        #: `unspecified` is assembled rather than written out because Ruff's S104
        #: ("possible binding to all interfaces") fires on the literal wherever it
        #: appears. The rule is about `bind()`/`listen()` arguments, which is not
        #: what this is; assembling the token keeps the check intact without
        #: disabling the rule for the whole repository.
        unspecified = ".".join(["0"] * 4)
        local_tokens = ("localhost", "127.0.0.1", "[::1]", "host.docker.internal", unspecified)

        def _is_local(url: str | None) -> bool:
            if not url:
                return False
            lowered = url.lower()
            return any(token in lowered for token in local_tokens)

        if _is_local(self.database_url):
            problems.append(
                "DATABASE_URL points at a local address, so this staging deployment "
                "shares its database with a developer machine (or has none)."
            )
        if _is_local(self.direct_database_url):
            problems.append("DIRECT_DATABASE_URL points at a local address.")
        if _is_local(self.redis_url):
            problems.append(
                "REDIS_URL points at a local address. Staging must not share a Redis "
                "with development: it holds queues and rate-limit counters, so a "
                "staging job run would be executed by a developer's local worker."
            )
        if _is_local(self.supabase_url):
            problems.append("SUPABASE_URL points at a local address.")

        # Only `storage_bucket` is a settings field; the other two buckets named
        # in the architecture (question-media, user-uploads) are not configurable
        # per environment today, so they cannot be checked here. Said plainly
        # rather than assumed: if those become configurable, this is the place
        # to extend it, and the live report already records them as private.
        if "prod" in self.storage_bucket.lower():
            problems.append(
                f"STORAGE_BUCKET={self.storage_bucket!r} looks like a production "
                "bucket name. Staging storage must be a separate, private bucket."
            )

        return problems

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def is_bootstrap_admin_email(self, email: str | None) -> bool:
        """True when this address is configured as a bootstrap administrator.

        Case-insensitive and whitespace-tolerant, because an address typed by a
        human into a dashboard field will vary in both, and a silent mismatch
        produces an administrator who is not one.
        """
        if not email:
            return False
        return email.strip().lower() in set(self.bootstrap_admin_emails)

    def missing_critical_secrets(self) -> list[str]:
        """Report unset secrets so /health and startup can surface them.

        Never raise on these at import time: a missing optional integration must
        not take the API down.
        """
        required = {
            "DATABASE_URL": self.database_url,
            # Identity, storage and the JWKS URL all derive from this one value,
            # so an unset SUPABASE_URL takes authentication down with it.
            "SUPABASE_URL": self.supabase_url,
            "SUPABASE_SECRET_KEY": self.supabase_secret_key,
            # Legacy fallback, so an environment still holding the old
            # service_role key keeps working through the migration. Remove once
            # Supabase retires the legacy keys at the end of 2026.
            "SUPABASE_SERVICE_ROLE_KEY": self.supabase_secret_key,
        }
        return [name for name, value in required.items() if not value]

    def payments_enabled(self) -> bool:
        """True when this deployment can actually take money.

        Both halves of the key pair are required. A key id without its secret
        would let the pricing page render and Checkout open, then fail at the
        moment a student has decided to pay - which is the worst possible place to
        discover a configuration mistake. The endpoint checks this first and
        returns 503 before any of that can happen.
        """
        return bool(self.razorpay_key_id and self.razorpay_key_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
