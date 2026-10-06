#!/usr/bin/env bash
# Launch a separate Chrome window that an agent can drive over CDP (port 9222).
# It uses its own profile in ~/.agent-chrome: log in to Google there once and it stays logged in.
# (Chrome 136+ refuses remote debugging on your default profile, so a separate one is required.)
set -euo pipefail
PORT="${PORT:-9222}"
PROFILE="${PROFILE:-$HOME/.agent-chrome}"

if [[ "$(uname)" == "Darwin" ]]; then
  CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
else
  CHROME="$(command -v google-chrome || command -v google-chrome-stable || command -v chromium || command -v chromium-browser)"
fi

mkdir -p "$PROFILE"
echo "Chrome on http://127.0.0.1:$PORT  (profile: $PROFILE)"
exec "$CHROME" --remote-debugging-port="$PORT" --remote-debugging-address=127.0.0.1 \
  --user-data-dir="$PROFILE" --no-first-run --no-default-browser-check "$@"
