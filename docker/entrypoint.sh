#!/bin/sh
set -eu

if [ "${EKAA_RUN_MIGRATIONS:-false}" = "true" ]; then
  alembic upgrade head
fi

exec "$@"
