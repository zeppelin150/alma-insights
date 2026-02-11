#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
echo ""
echo "  Alma Insights -- Express Installer"
echo ""
"$DIR/python/bin/python3" "$DIR/_installer/install.py" "$DIR"
echo ""
read -p "  Press Enter to close..."
