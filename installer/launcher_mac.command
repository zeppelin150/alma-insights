#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
export PATH="$DIR/node/bin:$HOME/.local/bin:$PATH"
xattr -rd com.apple.quarantine "$DIR/python" 2>/dev/null
xattr -rd com.apple.quarantine "$DIR/node" 2>/dev/null
cd "$DIR/app"
# On Apple Silicon, force the native slice even if this shell was translated —
# a Rosetta x86_64 preference otherwise reaches QtWebEngineProcess and kills
# the renderer (mach page-size mismatch; QTBUG-98487 class -> blank web UI).
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then
    exec arch -arm64 "$DIR/python/bin/python3" "$DIR/app/main.py"
fi
exec "$DIR/python/bin/python3" "$DIR/app/main.py"
