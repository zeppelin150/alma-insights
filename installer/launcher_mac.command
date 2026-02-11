#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR/app"
"$DIR/python/bin/python3" "$DIR/app/main.py"
