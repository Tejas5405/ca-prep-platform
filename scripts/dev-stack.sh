#!/usr/bin/env bash
#
# One-command recovery of this project's development stack.
#
# WHY THIS EXISTS
#
# This workspace keeps `/home/user` and nothing else. Installed Python packages,
# `/tmp`, downloaded binaries and running processes are all discarded between
# sessions. That is not an edge case here, it is the normal state of affairs: a
# session that begins by running `pytest` finds no `fastapi`, and a session that
# begins by connecting to PostgreSQL finds no server and no data directory.
#
# Rebuilding that by hand each time is where an hour goes, and worse, it is done
# slightly differently each time, so a failure can come from the setup rather
# than from the code. This script is the setup, written down once and run
# idempotently.
#
# WHAT IT DOES NOT DO
#
# It never starts a long-running server. `bash` here has no process that outlives
# the command, so a `pg_ctl start` or a `uvicorn &` inside it would be killed the
# moment the step returns. `up` therefore execs PostgreSQL in the FOREGROUND,
# which is what the process supervisor wants; the API and the web app are started
# the same way, one foreground command each.
#
# USAGE (each step is safe to re-run)
#
#   scripts/dev-stack.sh prepare     # python deps + postgres binaries + initdb
#   scripts/dev-stack.sh up          # exec postgres in the foreground (port 5433)
#   scripts/dev-stack.sh db-create   # create both databases, migrate, seed
#   scripts/dev-stack.sh test        # the full suite against caprep_test
#   scripts/dev-stack.sh api         # exec uvicorn on port 8000
#   scripts/dev-stack.sh web         # exec vite on port 5173
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API="$ROOT/apps/api"
WEB="$ROOT/apps/web"

# Zonky's embedded PostgreSQL is used because this image has no Docker daemon and
# no system PostgreSQL, and it is the only route to a real 17.x server. The
# archive inside the jar is relocatable: it runs as an unprivileged user once
# LD_LIBRARY_PATH points at its own lib directory.
PG_VERSION="17.11.0"
PG_URL="https://repo1.maven.org/maven2/io/zonky/test/postgres/embedded-postgres-binaries-linux-amd64/${PG_VERSION}/embedded-postgres-binaries-linux-amd64-${PG_VERSION}.jar"
PG_HOME="/tmp/pg"
PG_DATA="$PG_HOME/data"
PG_PORT="5433"

# `-c fsync=off -c full_page_writes=off` are test-server settings: they trade
# crash durability for speed on a database whose contents are disposable. They
# are never appropriate for the managed database the app deploys against.
PG_OPTS=(-p "$PG_PORT"
  -c listen_addresses=127.0.0.1
  -c unix_socket_directories=/tmp
  -c fsync=off
  -c full_page_writes=off
  -c log_min_messages=warning)

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok() { printf '    \033[0;32m%s\033[0m\n' "$*"; }
warn() { printf '    \033[0;33m%s\033[0m\n' "$*"; }

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing required tool: $1" >&2; exit 1; }; }

# --------------------------------------------------------------------- python
deps() {
  log "python dependencies"
  need python3
  python3 -m pip install --quiet --disable-pip-version-check -r "$API/requirements-dev.txt"
  ok "$(python3 -m pip list 2>/dev/null | grep -ciE 'fastapi|sqlalchemy|pytest' ) of the main packages present"
}

# ------------------------------------------------------------------- postgres
pg_install() {
  need curl
  need tar
  # Already extracted: nothing to do. The check is on the binary, not the
  # directory, because an interrupted extraction leaves a directory behind.
  if [[ -x "$PG_HOME/bin/postgres" ]]; then
    ok "postgres binaries already at $PG_HOME"
  else
    log "fetching embedded PostgreSQL $PG_VERSION"
    mkdir -p /tmp/pg-download "$PG_HOME"
    if [[ ! -s /tmp/pg-download/pg.jar ]]; then
      curl -fsSL "$PG_URL" -o /tmp/pg-download/pg.jar
    fi
    ok "jar $(du -h /tmp/pg-download/pg.jar | cut -f1)"

    # The jar holds a .txz; the archive is not a zip entry we can stream to tar
    # directly, so it is extracted to a file first.
    if command -v unzip >/dev/null 2>&1; then
      unzip -o -q /tmp/pg-download/pg.jar postgres-linux-x86_64.txz -d /tmp/pg-download
    else
      python3 -c "
import zipfile
zipfile.ZipFile('/tmp/pg-download/pg.jar').extract('postgres-linux-x86_64.txz', '/tmp/pg-download')
"
    fi
    # NO --strip-components. The archive's top level is already bin/, lib/ and
    # share/, so stripping one component flattens those three directories into
    # the root: initdb lands at $PG_HOME/initdb instead of $PG_HOME/bin/initdb,
    # and the next line fails with "No such file or directory" on a path that
    # looks correct in the script.
    rm -rf "$PG_HOME"
    mkdir -p "$PG_HOME"
    tar -xJf /tmp/pg-download/postgres-linux-x86_64.txz -C "$PG_HOME"
    ok "extracted"
  fi

  if [[ -f "$PG_DATA/PG_VERSION" ]]; then
    ok "data directory already initialised ($(cat "$PG_DATA/PG_VERSION"))"
    return
  fi

  log "initdb into $PG_DATA"
  # -A trust: the server listens on loopback only and holds seeded test content.
  # A password here would be security theatre that only breaks scripts.
  # --locale=C: avoids depending on locales the image may not generate.
  LD_LIBRARY_PATH="$PG_HOME/lib" "$PG_HOME/bin/initdb" \
    -D "$PG_DATA" -U postgres -A trust --encoding=UTF8 --locale=C >/dev/null
  ok "initialised"
}

db_running() {
  python3 - "$PG_PORT" <<'PY'
import socket, sys
sock = socket.socket()
sock.settimeout(1)
try:
    sock.connect(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
PY
}

# Runs in the FOREGROUND on purpose - see the header.
pg_up() {
  [[ -f "$PG_DATA/PG_VERSION" ]] || { echo "run 'prepare' first" >&2; exit 1; }
  log "postgres on port $PG_PORT (foreground; Ctrl-C to stop)"
  export LD_LIBRARY_PATH="$PG_HOME/lib"
  exec "$PG_HOME/bin/postgres" -D "$PG_DATA" "${PG_OPTS[@]}"
}

# psql is not guaranteed in the bundle; python3 is always there, and the driver
# the API itself uses is the most honest way to talk to the server.
sql() {
  python3 - "$@" <<'PY'
import sys, psycopg
maintenance, statement = sys.argv[1], sys.argv[2]
with psycopg.connect(f"postgresql://postgres@127.0.0.1:5433/{maintenance}", autocommit=True) as conn:
    conn.execute(statement)
PY
}

db_create() {
  db_running || { echo "postgres is not listening on $PG_PORT - start it first" >&2; exit 1; }
  log "databases, migrations, seed"
  for name in caprep caprep_test; do
    # CREATE DATABASE cannot run inside a transaction, hence autocommit in sql().
    if sql postgres "SELECT 1 FROM pg_database WHERE datname = '$name'" | grep -q 1; then
      ok "$name exists"
    else
      sql postgres "CREATE DATABASE $name" && ok "$name created"
    fi
  done

  log "alembic upgrade head (caprep and caprep_test)"
  for name in caprep caprep_test; do
    ( cd "$API" && DATABASE_URL="postgresql://postgres@127.0.0.1:$PG_PORT/$name" alembic upgrade head >/dev/null )
    ok "$name at head"
  done

  log "seeding reference content"
  ( cd "$API" && DATABASE_URL="postgresql+psycopg://postgres@127.0.0.1:$PG_PORT/caprep" python3 -m app.seed )
}

# ---------------------------------------------------------------------- gates
test_suite() {
  db_running || { echo "postgres is not listening on $PG_PORT - start it first" >&2; exit 1; }
  log "ruff + pytest (with database)"
  ( cd "$API" && python3 -m ruff check . && python3 -m ruff format --check . )
  ( cd "$API" && TEST_DATABASE_URL="postgresql://postgres@127.0.0.1:$PG_PORT/caprep_test" python3 -m pytest -q )
}

# ------------------------------------------------------------------ servers
api() {
  log "uvicorn on 8000"
  cd "$API"
  # The root .env is loaded so the real Supabase URL is present, which is what
  # token verification needs (its JWKS and its issuer both derive from it);
  # DATABASE_URL is overridden to the local server because .env points at a
  # managed host that is not reachable from here.
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
  exec env DATABASE_URL="postgresql+psycopg://postgres@127.0.0.1:$PG_PORT/caprep" \
    CORS_ORIGINS='["*"]' \
    python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
}

web() {
  log "vite on 5173"
  cd "$WEB"
  [[ -d node_modules ]] || { warn "installing npm packages (first run)"; npm install --silent; }
  # Vite must bind all interfaces and accept the preview proxy's Host header.
  # Both are set in vite.config.ts; this only fails if that file was changed.
  exec npx vite --host 0.0.0.0 --port 5173
}

prepare() { deps; pg_install; }

case "${1:-}" in
  prepare) prepare ;;
  deps) deps ;;
  pg-install) pg_install ;;
  up) pg_up ;;
  db-create) db_create ;;
  test) test_suite ;;
  api) api ;;
  web) web ;;
  *)
    sed -n '/^# USAGE/,/^#$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
