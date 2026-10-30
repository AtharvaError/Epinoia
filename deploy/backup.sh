#!/usr/bin/env bash
# Backs up the database and checks the file is readable.
#   deploy/backup.sh [container]        Default container: festos-db
# Set KEEP to change how many backups are kept (default 14).
set -euo pipefail

container="${1:-festos-db}"
folder="$(dirname "$0")/../backups"
mkdir -p "$folder"
file="$folder/festos-$(date +%Y%m%d-%H%M%S).dump"

docker exec "$container" sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$file"

# A backup that cannot be read is worse than none, because it looks like safety.
docker exec -i "$container" pg_restore --list < "$file" > /dev/null
ls -1t "$folder"/festos-*.dump | tail -n +$(( ${KEEP:-14} + 1 )) | xargs -r rm --
echo "Backup written and verified: $file ($(wc -c < "$file") bytes)"
