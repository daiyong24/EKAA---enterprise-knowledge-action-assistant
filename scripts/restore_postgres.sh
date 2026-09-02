#!/bin/sh
set -eu

: "${EKAA_DATABASE_URL:?EKAA_DATABASE_URL must be provided by the secret store}"
backup="${1:?usage: restore_postgres.sh BACKUP.dump}"

case "$backup" in
  */*|*\\*|*..*) echo "backup path must be a safe basename" >&2; exit 2 ;;
esac
[ -L "$backup" ] && { echo "backup source must not be a symlink" >&2; exit 2; }
[ -f "$backup" ] || { echo "backup file does not exist" >&2; exit 2; }

if [ "${EKAA_CONFIRM_RESTORE:-}" != "YES" ]; then
  echo "Set EKAA_CONFIRM_RESTORE=YES after the operator runbook approves this destructive restore" >&2
  exit 3
fi

pg_restore --clean --if-exists --no-owner --dbname="$EKAA_DATABASE_URL" "$backup"
