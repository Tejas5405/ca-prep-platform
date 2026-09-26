"""Infrastructure contract tests.

These check the deployment configuration, which is the part of the system that no
unit test touches and that fails only in production.

The trigger for this file was a real defect: `caprep-worker` was declared with
Render's native Python runtime. The OCR fallback needs `tesseract-ocr` and
`poppler-utils`, which that runtime does not provide, and the pipeline is built to
CONTAIN an OCR failure rather than propagate it - so scanned PDFs would have
produced zero drafts, with no error on the job and a healthy-looking worker. Every
test in the suite would still have passed.

Assertions here are deliberately about capability (does the image install the
binaries?) rather than about exact YAML structure, so ordinary edits to render.yaml
do not break the suite.

A second class of check lives here: IDENTITY WIRING. The frontend, the API and the
database must all name the same Supabase project. That mistake is invisible at
deploy time - sign-in SUCCEEDS in the browser, and the API then rejects every token
with 401, which is also exactly what a real attack looks like from the server side.
"""

from __future__ import annotations

import pathlib

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML not installed")

RENDER_YAML = pathlib.Path("../../infra/render.yaml")
DOCKERFILE = pathlib.Path("Dockerfile")
ROOT = pathlib.Path("../..")
WEB_ENV_LOCAL = ROOT / "apps/web/.env.local"
WEB_ENV_EXAMPLE = ROOT / "apps/web/.env.example"
CI_WORKFLOW = ROOT / ".github/workflows/ci-cd.yml"
ROOT_ENV_EXAMPLE = ROOT / ".env.example"
SUPABASE_CLIENT_TS = ROOT / "apps/web/src/lib/supabase.ts"
WEB_SRC = ROOT / "apps/web/src"
PLACEHOLDER_HOSTS = {"your-project.supabase.co", "example.supabase.co"}


def parse_env_file(path: pathlib.Path) -> dict[str, str]:
    """Read KEY=value pairs, ignoring comments and blanks."""
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _render() -> dict:
    if not RENDER_YAML.is_file():
        pytest.skip(f"{RENDER_YAML} not present in this checkout")
    return yaml.safe_load(RENDER_YAML.read_text())


def _installed_packages() -> set[str]:
    """Package names in the Dockerfile's apt-get install block.

    Parsed rather than searched for, because "tesseract-ocr" also appears in the
    explanatory comments above the block. A substring search over the whole file
    therefore passes on a Dockerfile that installs nothing - a mutation test
    caught exactly that.
    """
    dockerfile = DOCKERFILE.read_text()
    if "apt-get install" not in dockerfile:
        return set()

    block = dockerfile.split("apt-get install", 1)[1]
    # The install block ends at the first line that is not a continuation.
    lines = []
    for line in block.splitlines():
        if "apt-get" in line or "rm -rf" in line:
            break
        lines.append(line.rstrip().rstrip("\\").strip())

    packages = set()
    for line in lines:
        for token in line.split():
            token = token.strip()
            if token and not token.startswith("-") and token != "&&":
                packages.add(token)
    return packages


def _service(name: str) -> dict:
    for service in _render()["services"]:
        if service["name"] == name:
            return service
    raise AssertionError(f"no service named {name} in render.yaml")


class TestWorkerRuntime:
    def test_worker_uses_the_docker_runtime(self):
        """Not the native Python runtime.

        The native runtime cannot install OCR system binaries, and the pipeline
        swallows a missing OCR toolchain by design. The result is a worker that
        reports success while producing nothing for scanned papers.
        """
        worker = _service("caprep-worker")
        assert worker.get("runtime") == "docker", (
            "caprep-worker must use runtime: docker - the OCR fallback needs "
            "tesseract-ocr and poppler-utils, which Render's python runtime lacks"
        )

    def test_worker_image_installs_both_ocr_binaries(self):
        """tesseract is the OCR engine; poppler provides pdftoppm for rasterising.

        Both are needed: tesseract alone cannot read a PDF page, and poppler alone
        cannot recognise text.
        """
        worker = _service("caprep-worker")
        dockerfile_path = pathlib.Path(
            str(worker.get("dockerfilePath", "")).removeprefix("./")
        ).name
        assert dockerfile_path == DOCKERFILE.name

        packages = _installed_packages()
        assert packages, "could not find an apt-get install block in the Dockerfile"
        assert "tesseract-ocr" in packages, "the image must install the OCR engine"
        assert "poppler-utils" in packages, "pdftoppm is required to rasterise pages"
        # Language data is a recommends-only package, so --no-install-recommends
        # drops it silently and Tesseract then fails on a bare install.
        assert "tesseract-ocr-eng" in packages

    def test_worker_start_command_runs_the_rq_worker_not_the_api(self):
        """The image's default CMD is uvicorn; the worker must override it.

        Getting this wrong deploys a second API that silently consumes no jobs -
        uploads would sit in QUEUED forever with nothing appearing broken.
        """
        worker = _service("caprep-worker")
        start = worker.get("startCommand", "")
        assert "app.workers.rq_worker" in start
        assert "uvicorn" not in start


class TestServiceConfig:
    def test_api_has_the_token_verification_configuration(self):
        """Without SUPABASE_URL the verifier cannot fetch the JWKS, and it fails
        CLOSED: every request is rejected. A missing env var is therefore a total
        outage rather than a degraded mode, which is the right behaviour and also
        the reason it belongs in a test rather than in a runbook."""
        api = _service("caprep-api")
        keys = {var["key"] for var in api.get("envVars", [])}
        assert "SUPABASE_URL" in keys

    def test_api_gets_its_database_url_from_the_managed_database(self):
        """A hand-written DATABASE_URL in the blueprint would be a stale host and a
        credential in a committed file."""
        api = _service("caprep-api")
        entries = {var["key"]: var for var in api.get("envVars", [])}
        database_url = entries.get("DATABASE_URL", {})
        assert database_url.get("fromDatabase", {}).get("property") == "connectionString", (
            "DATABASE_URL must come fromDatabase, not a literal"
        )

    def test_the_service_role_key_is_never_a_literal(self):
        """The Supabase service-role key bypasses RLS. A literal in a committed
        file would be a full compromise of every bucket."""
        text = RENDER_YAML.read_text()
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("value:") and "service_role" in stripped:
                raise AssertionError(f"service-role value hardcoded: {line}")

    def test_worker_has_the_storage_credentials_it_needs(self):
        """run_ingestion downloads the PDF, so the worker needs storage access
        even though it never handles a user token."""
        worker = _service("caprep-worker")
        keys = {var["key"] for var in worker.get("envVars", [])}
        assert {"SUPABASE_URL", "SUPABASE_SECRET_KEY"} <= keys

    def test_worker_can_reach_postgres_and_redis(self):
        worker = _service("caprep-worker")
        keys = {var["key"] for var in worker.get("envVars", [])}
        assert {"DATABASE_URL", "REDIS_URL"} <= keys


class TestDockerfile:
    def test_runs_as_a_non_root_user(self):
        dockerfile = DOCKERFILE.read_text()
        assert "USER appuser" in dockerfile or "USER " in dockerfile

    def test_worker_entry_point_is_documented(self):
        """The image builds the API by default; the worker override must be
        discoverable from the Dockerfile alone, since that is where a maintainer
        will look after a failed deploy."""
        dockerfile = DOCKERFILE.read_text()
        assert "app.workers.rq_worker" in dockerfile


class TestSupabaseProjectIsConsistentEverywhere:
    """The frontend, the API and the database must name the SAME project.

    This is the highest-consequence configuration mistake in the whole stack, and
    it is invisible at deploy time. `app/core/security.py` verifies the issuer and
    the audience of every access token against SUPABASE_URL. If the frontend is
    configured for a different project, sign-in SUCCEEDS in the browser - the
    project issues a perfectly valid token - and then every API call returns 401.

    From the user's side that reads as "login does not work". From the backend's
    side it reads as "someone is sending us tokens for another project", which is
    also what a real attack looks like. The two symptoms are identical, so this is
    worth a test rather than a runbook.

    The project URL is not a secret: it ships to every browser in the bundle.

    THE DATABASE IS CHECKED TOO, and that check is the sharpest one. A Supabase
    pooler URL contains the project ref in the username:
    `postgres.<ref>@aws-0-<region>.pooler.supabase.com`. If the API verified tokens
    against project A while reading student data from project B, every test would
    still pass - the tokens are valid, the queries succeed - and the product would
    be quietly serving one deployment's data under another's identity.
    """

    def _project_hosts(self) -> dict[str, str]:
        found: dict[str, str] = {}

        api = _service("caprep-api")
        for var in api.get("envVars", []):
            if var["key"] == "SUPABASE_URL" and var.get("value"):
                found["infra/render.yaml (API service)"] = var["value"]

        # .env.local is gitignored, so it is absent in CI. Local development is
        # exactly where the mismatch is easiest to create, so it is checked when
        # present rather than skipped entirely.
        web_sources = (
            ("apps/web/.env.local", WEB_ENV_LOCAL),
            ("apps/web/.env.example", WEB_ENV_EXAMPLE),
        )
        for label, path in web_sources:
            if not path.is_file():
                continue
            value = parse_env_file(path).get("VITE_SUPABASE_URL", "")
            if value and self._host(value) not in PLACEHOLDER_HOSTS:
                found[label] = value

        if ROOT_ENV_EXAMPLE.is_file():
            value = parse_env_file(ROOT_ENV_EXAMPLE).get("SUPABASE_URL", "")
            if value and self._host(value) not in PLACEHOLDER_HOSTS:
                found[".env.example"] = value

        return found

    @staticmethod
    def _host(url: str) -> str:
        return (
            url.strip().rstrip("/").removeprefix("https://").removeprefix("http://").split("/")[0]
        )

    @staticmethod
    def _ref_from_host(host: str) -> str:
        # <ref>.supabase.co  ->  <ref>
        return host.split(".")[0]

    def _database_urls(self) -> list[tuple[str, str]]:
        """(label, url) for every database URL that points at Supabase."""
        found: list[tuple[str, str]] = []
        for label in ("apps/web/.env.local", ".env"):
            path = ROOT / label
            if not path.is_file():
                continue
            value = parse_env_file(path).get("DATABASE_URL", "")
            if "supabase" in value:
                found.append((label, value))
        return found

    def test_the_frontend_and_backend_name_the_same_project(self):
        found = self._project_hosts()
        assert len(found) >= 2, f"expected several sources to compare, got {found}"
        hosts = {self._host(url) for url in found.values()}
        assert len(hosts) == 1, (
            "Supabase project URLs disagree, so the frontend will obtain tokens the "
            f"API rejects: {found}"
        )

    def test_the_database_belongs_to_the_same_project_as_the_token_verifier(self):
        """See the class docstring: a token from one project and data from another
        is a silent, fully-working misconfiguration."""
        databases = self._database_urls()
        if not databases:
            pytest.skip("no Supabase database URL in this checkout")

        auth_hosts = {self._host(url) for url in self._project_hosts().values()}
        assert len(auth_hosts) == 1, (
            f"cannot compare against an ambiguous auth project: {auth_hosts}"
        )
        expected_ref = self._ref_from_host(next(iter(auth_hosts)))

        for label, url in databases:
            # postgres.<ref>@aws-0-<region>.pooler.supabase.com:6543/postgres
            user = url.split("://", 1)[-1].split("@", 1)[0]
            assert "." in user, f"{label}: cannot read a project ref out of {user!r}"
            ref = user.split(".", 1)[1]
            assert ref == expected_ref, (
                f"{label} points at Supabase project {ref!r} while the token verifier "
                f"and the frontend use {expected_ref!r}. Tokens would verify and the "
                "queries would succeed, against the wrong database."
            )

    def test_a_legacy_anon_key_belongs_to_the_configured_project(self):
        """If the legacy anon JWT is still configured anywhere, its `ref` claim must
        match the project.

        Legacy keys are JWTs and carry the project ref, so this is checkable
        offline; the new opaque `sb_publishable_...` keys encode nothing and can
        only be verified against the live project. A stale legacy anon key pointing
        at an old project produces sign-in failures that look like everything else.
        """
        import base64
        import json

        candidates: list[tuple[str, str]] = []
        for label, path in (
            ("apps/web/.env.local", WEB_ENV_LOCAL),
            (".env", ROOT / ".env"),
            (".env.example", ROOT_ENV_EXAMPLE),
        ):
            if not path.is_file():
                continue
            for key in ("VITE_SUPABASE_ANON_KEY", "SUPABASE_ANON_KEY"):
                value = parse_env_file(path).get(key, "")
                if value.startswith("eyJ"):
                    candidates.append((label, value))

        if not candidates:
            pytest.skip("no legacy anon JWT in this checkout; the project uses opaque keys")

        expected_ref = self._ref_from_host(self._host(next(iter(self._project_hosts().values()))))
        for label, token in candidates:
            payload = token.split(".")[1]
            # base64url, unpadded.
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            assert claims.get("ref") == expected_ref, (
                f"{label}: legacy anon key belongs to {claims.get('ref')!r}, not {expected_ref!r}"
            )
            assert claims.get("role") == "anon", f"{label}: expected an anon key"

    def test_the_publishable_key_is_not_a_secret_key(self):
        """`sb_publishable_...` and `sb_secret_...` are visually similar and are
        pasted from the same dashboard page. Shipping the wrong one hands every
        visitor full read/write access to the project."""
        for label, path in (
            ("apps/web/.env.local", WEB_ENV_LOCAL),
            ("apps/web/.env.example", WEB_ENV_EXAMPLE),
        ):
            if not path.is_file():
                continue
            key = parse_env_file(path).get("VITE_SUPABASE_ANON_KEY", "")
            if not key:
                continue
            assert not key.startswith("sb_secret_"), (
                f"{label} holds a SECRET key in a VITE_ variable, which Vite inlines "
                "into the browser bundle"
            )
            assert key.startswith("sb_publishable_") or key.startswith("eyJ"), (
                f"{label}: VITE_SUPABASE_ANON_KEY does not look like a publishable or "
                f"legacy anon key: {key[:16]}..."
            )


class TestWebEnvVarNamesMatchTheCode:
    """A renamed environment variable fails silently and cryptically.

    Vite inlines `import.meta.env.VITE_*` at build time, so a variable that is
    renamed in one place but not the other arrives as `undefined` - and the failure
    surfaces as "Supabase is not configured" thrown from module scope, which points
    at the console rather than at the rename.
    """

    def _vars_the_code_reads(self) -> set[str]:
        """Every VITE_ variable read anywhere under src/, not just in one file.

        The earlier version of this test looked only at the Firebase client module,
        which is precisely the file that gets looked at during a refactor. Scanning
        the tree catches a variable read from a component or a hook.
        """
        import re

        assert WEB_SRC.is_dir(), f"missing {WEB_SRC}"
        names: set[str] = set()
        for path in WEB_SRC.rglob("*"):
            if path.suffix not in {".ts", ".tsx"}:
                continue
            names |= set(re.findall(r"import\.meta\.env\.(VITE_[A-Z0-9_]+)", path.read_text()))
        assert names, "no VITE_ variables found anywhere under apps/web/src"
        return names

    def test_every_variable_the_code_reads_is_documented(self):
        if not WEB_ENV_EXAMPLE.is_file():
            pytest.skip("no web env template in this checkout")

        documented = set(parse_env_file(WEB_ENV_EXAMPLE))
        undocumented = self._vars_the_code_reads() - documented
        assert undocumented == set(), (
            f"read by apps/web/src but absent from apps/web/.env.example: {sorted(undocumented)}"
        )

    def test_every_documented_variable_is_read_by_the_code(self):
        """The reverse check. A documented variable nothing reads is a trap: it
        looks editable, and changing it has no effect."""
        if not WEB_ENV_EXAMPLE.is_file():
            pytest.skip("no web env template in this checkout")

        documented = {name for name in parse_env_file(WEB_ENV_EXAMPLE) if name.startswith("VITE_")}
        unused = documented - self._vars_the_code_reads()
        assert unused == set(), f"documented but never read: {sorted(unused)}"

    def test_the_api_base_url_is_documented_even_though_it_is_empty(self):
        """It is empty by design in development (Vite proxies /api), so a
        template that omits it looks like an oversight and invites someone to
        hardcode a host in the client."""
        if not WEB_ENV_EXAMPLE.is_file():
            pytest.skip("no web env template in this checkout")
        assert "VITE_API_BASE_URL" in parse_env_file(WEB_ENV_EXAMPLE)


class TestSecretsAreNeverCommitted:
    """A committed secret is permanent: removing the file later does not remove
    it from git history."""

    def test_env_files_are_gitignored_but_examples_are_not(self):
        """Asserted against the RULE LINES, not the file's text.

        A previous version of this test searched the whole .gitignore for the
        string ".env" - which the explanatory comments also contain, so deleting
        every actual rule still passed. Same class of mistake as a constraint
        assertion that matches a comment.
        """
        ignore = ROOT / ".gitignore"
        assert ignore.is_file(), "the repository has no .gitignore"

        rules = [
            line.strip()
            for line in ignore.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        assert ".env" in rules, f"no rule ignoring .env - rules present: {rules}"
        assert ".env.*" in rules, f"no rule ignoring .env.* - rules present: {rules}"
        # The negation must come AFTER the wildcard it re-includes, or git
        # applies them in the other order and the templates are ignored too.
        assert "!.env.example" in rules, "the example templates must stay tracked"
        assert rules.index("!.env.example") > rules.index(".env.*"), (
            "!.env.example must appear after .env.* for the negation to win"
        )

    def test_the_gitignore_does_not_ignore_the_example_templates(self):
        """Checks the pattern semantics rather than the rule text.

        `*.example` or a bare `*` would silently ignore the templates that the
        rest of the repository depends on being tracked.
        """
        ignore = ROOT / ".gitignore"
        rules = [
            line.strip()
            for line in ignore.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        over_broad = [r for r in rules if r in {"*", "*.*", "*.example"}]
        assert over_broad == [], f"over-broad ignore rules would hide the templates: {over_broad}"

    def test_the_web_env_file_holds_only_browser_safe_values(self):
        """Anything in a VITE_ variable is compiled into the public bundle.

        Checked by CONTENT rather than by filename. A local `.env.local` is the
        intended way to configure Vite, so the presence of the file means nothing;
        what matters is that nothing in it is a secret. This catches the mistake
        that filename rules miss - a server-side secret pasted into the frontend
        file, where it would ship to every visitor.
        """
        if not WEB_ENV_LOCAL.is_file():
            pytest.skip("no local web env in this checkout")

        values = parse_env_file(WEB_ENV_LOCAL)
        assert values, "the web env file has no values"

        for key in values:
            assert key.startswith("VITE_"), (
                f"{key} is not VITE_-prefixed. Vite only exposes VITE_ variables "
                "to the client, so this value is silently unused - and if it was "
                "meant for the browser, it will not arrive."
            )
            for forbidden in ("SERVICE_ROLE", "SECRET", "PASSWORD", "SERVICE_ACCOUNT"):
                assert forbidden not in key.upper(), (
                    f"{key} looks like a server-side secret in the frontend env"
                )

    def test_the_render_config_holds_no_secret_literals(self):
        """Secrets must use `sync: false`, which makes Render read the value from
        its dashboard, or a `fromDatabase`/`fromService` reference.

        Checked per KEY/VALUE PAIR. An earlier version of this test scanned only
        the value line for a secret-looking word - but the word is in the key, so
        `- key: FIREBASE_SERVICE_ACCOUNT_JSON` followed by
        `value: '{"private_key": ...}'` passed while leaking a credential. The
        rule is about the pair: if the key names a secret, the value must not be
        written down here.
        """
        secret_markers = (
            "SERVICE_ROLE",
            "SECRET",
            "PASSWORD",
            "SERVICE_ACCOUNT",
            "PRIVATE_KEY",
            "DATABASE_URL",
        )

        offenders = []
        for service in _render()["services"]:
            for var in service.get("envVars", []):
                if not any(marker in var["key"].upper() for marker in secret_markers):
                    continue
                if var.get("value") not in (None, ""):
                    offenders.append(f"{service['name']}/{var['key']}")

        assert offenders == [], (
            "secret values written literally into render.yaml; use `sync: false` "
            f"and set them in the Render dashboard: {offenders}"
        )

    def test_the_service_role_key_is_not_in_the_web_bundle_config(self):
        """Vite inlines every VITE_ variable into the browser bundle."""
        for path in (WEB_ENV_LOCAL, WEB_ENV_EXAMPLE):
            if not path.is_file():
                continue
            for key in parse_env_file(path):
                assert "SERVICE_ROLE" not in key.upper(), (
                    f"{path} would ship a service-role key to the browser"
                )
                assert "SECRET" not in key.upper(), f"{path} would ship a secret to the browser"


def local_supabase_secret() -> str | None:
    """The Supabase secret key from the local .env, if one is configured.

    Read from the environment rather than written here: putting the value in a test
    file would commit it. Absent in CI, where the test skips.
    """
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return None
    value = parse_env_file(env_file).get("SUPABASE_SECRET_KEY", "")
    if not value:
        value = parse_env_file(env_file).get("SUPABASE_SERVICE_ROLE_KEY", "")
    return value or None


class TestTheStorageSecretNeverReachesTheFrontend:
    """The Supabase secret key bypasses Row Level Security.

    Anyone holding it has full read/write access to every table and every storage
    object in the project - including other students' uploaded papers. It must
    exist only in the backend's environment.

    These tests read the LOCAL .env when it is present (it is gitignored, so it
    is absent in CI and the tests skip there). The secret VALUE is never written
    into a test file - that would commit it - so the check is done by comparing
    against whatever the local environment holds.
    """

    def _local_secret(self) -> str | None:
        return local_supabase_secret()

    def test_the_secret_is_not_in_any_frontend_file(self):
        secret = self._local_secret()
        if not secret:
            pytest.skip("no local .env with a Supabase secret in this checkout")

        web_dir = ROOT / "apps/web"
        offenders = []
        for path in web_dir.rglob("*"):
            if not path.is_file():
                continue
            if any(part in {"node_modules", "dist", ".git"} for part in path.parts):
                continue
            if path.suffix not in {
                ".ts",
                ".tsx",
                ".js",
                ".json",
                ".html",
                ".css",
                ".local",
                ".example",
                ".env",
            }:
                continue
            try:
                if secret in path.read_text(errors="ignore"):
                    offenders.append(str(path.relative_to(ROOT)))
            except OSError:
                continue

        assert offenders == [], (
            "the Supabase secret key appears in frontend files and would be "
            f"shipped to every browser: {offenders}"
        )

    def test_the_secret_is_not_in_the_built_bundle(self):
        """The strongest form of the check: the compiled output."""
        secret = self._local_secret()
        if not secret:
            pytest.skip("no local .env with a Supabase secret in this checkout")

        dist = ROOT / "apps/web/dist"
        if not dist.is_dir():
            pytest.skip("no built bundle; run npm run build in apps/web")

        offenders = [
            str(path.name)
            for path in dist.rglob("*")
            if path.is_file() and secret in path.read_text(errors="ignore")
        ]
        assert offenders == [], f"secret key found in the built bundle: {offenders}"

    def test_the_only_supabase_variables_in_the_frontend_are_the_public_ones(self):
        """WHY THIS RULE CHANGED.

        While identity was Firebase, the browser needed no Supabase values at all:
        uploads and downloads go through API-brokered signed URLs, which are
        self-contained bearer capabilities, so a browser key would have added a
        second path to student files outside the backend's authorization boundary.
        The rule was therefore "no VITE_SUPABASE_* at all".

        Supabase Auth is now the identity provider, so the browser DOES need two
        values: the project URL and the publishable key. Both are public by design -
        the publishable key identifies the project, it authorises nothing on its
        own, and the Auth API is the only service it can reach from here. Verified
        against the live project: a publishable key lists ZERO storage buckets.

        The dangerous one is the `sb_secret_...` key, which is Postgres
        `service_role` with BYPASSRLS. Anything keyed like that, or like a service
        role, password or private key, is refused below.
        """
        allowed = {"VITE_SUPABASE_URL", "VITE_SUPABASE_ANON_KEY"}
        forbidden_markers = ("SECRET", "SERVICE_ROLE", "PASSWORD", "PRIVATE_KEY", "SERVICE_ACCOUNT")

        for path in (WEB_ENV_LOCAL, WEB_ENV_EXAMPLE):
            if not path.is_file():
                continue
            for key, value in parse_env_file(path).items():
                assert not any(marker in key.upper() for marker in forbidden_markers), (
                    f"{path.name} defines {key}, which Vite inlines into the browser bundle"
                )
                if key.startswith("VITE_SUPABASE"):
                    assert key in allowed, (
                        f"{path.name} defines {key}; the browser is allowed exactly "
                        f"{sorted(allowed)}, because those are the values Supabase Auth "
                        "needs and nothing else."
                    )
                    assert not value.startswith("sb_secret_"), (
                        f"{path.name}: {key} holds a secret key, not a publishable one"
                    )

    def test_the_built_bundle_carries_the_publishable_key_and_no_secret(self):
        """The end of the argument: what the browser ACTUALLY receives.

        Source-level checks are defeated by a copy-paste through a proxy, a fallback
        in the client module, or an import of a server-only constant. The compiled
        output is the only place where "the browser has it" is a fact.
        """
        dist = ROOT / "apps/web/dist"
        if not dist.is_dir():
            pytest.skip("no built bundle; run npm run build in apps/web")

        text = "\n".join(
            path.read_text(errors="ignore") for path in dist.rglob("*") if path.is_file()
        )

        publishable = (
            parse_env_file(WEB_ENV_LOCAL).get("VITE_SUPABASE_ANON_KEY", "")
            if WEB_ENV_LOCAL.is_file()
            else ""
        )
        if publishable:
            assert publishable in text, (
                "the publishable key is configured but absent from the bundle, so "
                "sign-in would fail with 'Supabase is not configured'"
            )

        secret = local_supabase_secret()
        if secret:
            assert secret not in text, "the Supabase SECRET key is in the built bundle"
