#!/bin/sh
set -eu

# Default behavior:
#
# RUN_DB_SETUP=true   -> wait for DB, run migrations
# RUN_DB_SETUP=false  -> skip DB setup and start the given command immediately
#
# LOAD_FIXTURES=true  -> load fixtures after migrations
# LOAD_FIXTURES=false -> skip fixtures
#
# In the provided compose.yml:
#   - web runs DB setup by default
#   - celery, celery_beat, and flower skip DB setup

if [ "${RUN_DB_SETUP:-false}" = "true" ]; then
  echo "Waiting for database..."

  retries="${DB_WAIT_RETRIES:-30}"
  sleep_seconds="${DB_WAIT_SLEEP:-2}"

  until python manage.py check --database default >/dev/null 2>&1; do
    retries=$((retries - 1))

    if [ "$retries" -le 0 ]; then
      echo "Database did not become ready in time." >&2
      exit 1
    fi

    echo "Database unavailable, retrying in ${sleep_seconds}s..."
    sleep "$sleep_seconds"
  done

  echo "Applying database migrations..."
  python manage.py migrate --noinput

  if [ "${LOAD_FIXTURES:-false}" = "true" ]; then
    echo "Installing fixtures..."
    python manage.py loaddata \
      ./fixtures/constitution.json \
      ./fixtures/counties.json \
      ./fixtures/constituencies.json
  fi
fi

echo "Starting process..."
exec "$@"