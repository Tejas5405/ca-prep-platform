"""Application settings.

Secrets rule (blueprint v3 §5.1): only public Supabase configuration belongs in
the React bundle. Service-role keys, payment secrets, database URLs and Redis
credentials stay server-side on Render and are never exposed to the browser.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["development", "staging", "production", "test"] = "development"
    debug: bool = False

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
    ai_monthly_ceiling_usd: float = 200.0
    ai_free_queries_per_day: int = 5

    # ---------------------------------------------------------- limits
    #: Free-tier question quota per day (blueprint v3 §10).
    free_daily_question_quota: int = 5
    #: Global rate limit. Keep the attempt-start limit lower - see mocks router.
    rate_limit_per_minute: int = 100
    mock_attempts_per_hour: int = 10

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

    @field_validator("bootstrap_admin_emails", mode="before")
    @classmethod
    def split_admin_emails(cls, v: object) -> object:
        """As above, lower-cased because the comparison is case-insensitive."""
        return [item.lower() for item in _split_list(v)]

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
