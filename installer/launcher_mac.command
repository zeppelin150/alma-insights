#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
xattr -rd com.apple.quarantine "$DIR/python" 2>/dev/null
cd "$DIR/app"
"$DIR/python/bin/python3" "$DIR/app/main.py"
