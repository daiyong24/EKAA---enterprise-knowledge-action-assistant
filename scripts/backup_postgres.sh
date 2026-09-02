#!/bin/sh
set -eu

: "${EKAA_DATABASE_URL:?EKAA_DATABASE_URL must be provided by the secret store}"
output="${1:-ekaa-backup-$(date -u +%Y%m%dT%H%M%SZ).dump}"

case "$output" in
  */*|*\\*|*..*) echo "backup path must be a safe basename" >&2; exit 2 ;;
esac
[ -L "$output" ] && { echo "backup target must not be a symlink" >&2; exit 2; }

umask 077
pg_dump --format=custom --no-owner --file="$output" "$EKAA_DATABASE_URL"
echo "Created backup artifact; encrypt it and upload it through the approved storage boundary: $output"
