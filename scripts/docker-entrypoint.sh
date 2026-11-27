#!/bin/sh
# Container entrypoint: the data folders may be volumes or a platform's persistent disk (Render mounts one owned by
# root), so start as root, hand the writable folders to the unprivileged `app` user, then run the command as it.
set -e
if [ "$(id -u)" = "0" ]; then
    for dir in /app/logs /data "$SERVICES_DATA_DIR" "$LIBRARY_DATA_DIR" "$LOG_DIR" "$(dirname "${DATABASE_PATH:-/data/app.db}")"; do
        [ -n "$dir" ] && mkdir -p "$dir" && chown -R app "$dir"
    done
    exec setpriv --reuid=app --regid=app --clear-groups "$@"
fi
exec "$@"
