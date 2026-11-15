#!/usr/bin/env bash
# Start the Next.js frontend on :3000. Set FINN_NODE_BIN to use a Node not on PATH.
set -euo pipefail
cd "$(dirname "$0")/../frontend"
[ -n "${FINN_NODE_BIN:-}" ] && export PATH="$FINN_NODE_BIN:$PATH"
# Node < 22.12 needs this flag for Tailwind v4's ESM-only deps; harmless on newer versions.
export NODE_OPTIONS="${NODE_OPTIONS:-} --experimental-require-module"
[ -d node_modules ] || npm ci
exec npx next dev -p "${PORT:-3000}"
