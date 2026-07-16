#!/bin/sh
set -e
# docker-entrypoint.sh
# ---------------------
# Most PaaS hosts (Render, Cloud Run, Heroku-style platforms) assign a random
# port at deploy time and inject it as $PORT, then route external traffic to
# whatever the container listens on. Local docker-compose usage never sets
# $PORT, so this defaults to 8000 to preserve that existing behavior.
#
# `exec` replaces this shell process with gunicorn (rather than forking a
# child), so gunicorn becomes PID 1 and receives SIGTERM directly on
# `docker stop` / platform shutdown — without exec, the shell would eat the
# signal and gunicorn would only die on SIGKILL after the grace period.
#
# --no-control-socket: Gunicorn 25.1+ enables a Unix control socket (for the
# separate `gunicornc` runtime-management tool) by default, under
# $HOME/.gunicorn/ if $XDG_RUNTIME_DIR isn't set. We don't use gunicornc —
# the platform (Render/Cloud Run/docker) manages the process lifecycle — and
# this feature is a known source of "Permission denied" errors and even
# restart loops on minimal/restricted-permission containers. Disabled outright.

# Apply any pending Alembic migrations before serving traffic. Without this,
# a migration can sit committed-but-unapplied against the production database
# indefinitely (see e.g. revision 0002, which added a Postgres enum value that
# the running app depended on but the deployed DB didn't have yet, causing
# 500s on every request that touched it). alembic/env.py reads the same
# DATABASE_URL as the app, so this targets whatever DB this container talks to.
#
# Run as `python -m scripts.run_migrations`, not the bare `alembic` command:
# -m puts the cwd (/app) on sys.path, which both `app.core.database` and
# `alembic.ini`'s relative script_location need. The wrapper script also
# auto-stamps revision 0001 on first run against a database that already has
# the pre-Alembic schema (created by create_all_tables()) but no
# alembic_version row yet — see scripts/run_migrations.py for why.
python -m scripts.run_migrations

exec gunicorn app.main:app \
    -w 4 \
    -k uvicorn.workers.UvicornWorker \
    --bind "0.0.0.0:${PORT:-8000}" \
    --no-control-socket \
    --access-logfile - \
    --error-logfile - \
    --timeout 120
