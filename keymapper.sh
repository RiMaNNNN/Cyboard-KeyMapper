#!/usr/bin/env sh
# KeyMapper launcher (macOS/Linux): reuses a running server or starts one and
# opens the browser. The Windows equivalent is KeyMapper.bat.
set -e
cd "$(dirname "$0")/backend"

PORT=$(sed -n 's/^ *port: *\([0-9][0-9]*\).*/\1/p' data/configuration/config.yaml 2>/dev/null | head -n 1)
PORT=${PORT:-8756}
URL="http://127.0.0.1:${PORT}"

if curl -s -o /dev/null -m 2 "$URL/api/health"; then
    # Server already running: just open the UI.
    (open "$URL" 2>/dev/null || xdg-open "$URL" 2>/dev/null) &
    exit 0
fi

# --open makes the server itself open the browser once it is up; the server
# shuts down on its own a few seconds after the last browser tab closes.
exec .venv/bin/python -m src --open
