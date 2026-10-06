#!/usr/bin/env bash
# Give Claude Code "hands" in Chrome. Run once on your own machine (needs Node 18+ and the `claude` CLI).
# Pick one (both commands also work from PowerShell):
#
# A) Your everyday Chrome and its open tabs (Chrome 144+). In Chrome open
#    chrome://inspect/#remote-debugging, switch remote debugging on, click Allow when asked.
#      claude mcp add --scope user chrome -- npx -y chrome-devtools-mcp@latest --autoConnect
#
# B) The separate agent window from start-chrome.sh (port 9222):
#      claude mcp add --scope user browser -- npx -y @playwright/mcp@latest --cdp-endpoint http://127.0.0.1:9222
set -euo pipefail
case "${1:-mine}" in
  mine)  claude mcp add --scope user chrome -- npx -y chrome-devtools-mcp@latest --autoConnect ;;
  agent) claude mcp add --scope user browser -- npx -y @playwright/mcp@latest --cdp-endpoint "http://127.0.0.1:${PORT:-9222}" ;;
  *) echo "usage: $0 [mine|agent]"; exit 2 ;;
esac
echo 'Done. In Claude Code, say e.g. "Using Chrome, open my Google Cloud console and ..."'
