#!/usr/bin/env bash
# Restores a backup into a database, replacing what is there.
#   deploy/restore.sh <backup-file> [database] [container]
# The database defaults to the live one. Name another to test a backup safely.
# Stop the API before restoring over the live database.
set -euo pipefail

file="${1:?Usage: deploy/restore.sh <backup-file> [database] [container]}"
container="${3:-festos-db}"
user="$(docker exec "$container" sh -c 'echo "$POSTGRES_USER"')"
live="$(docker exec "$container" sh -c 'echo "$POSTGRES_DB"')"
target="${2:-$live}"

if [ "$target" = "$live" ] && [ "${CONFIRM_RESTORE:-}" != "yes" ]; then
    echo "This replaces everything in $live. Rerun with CONFIRM_RESTORE=yes to continue." >&2
    exit 1
fi

docker exec "$container" psql -q -U "$user" -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$target' AND pid<>pg_backend_pid()" > /dev/null
if [ "$target" != "$live" ]; then
    docker exec "$container" psql -q -U "$user" -d postgres -c "DROP DATABASE IF EXISTS \"$target\""
    docker exec "$container" psql -q -U "$user" -d postgres -c "CREATE DATABASE \"$target\""
fi
docker exec -i "$container" pg_restore --clean --if-exists --no-owner -U "$user" -d "$target" < "$file"
echo "Restored $file into $target"
