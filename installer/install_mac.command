#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
echo ""
echo "  Alma Insights -- Express Installer"
echo ""

# Remove macOS quarantine flag from bundled Python (Gatekeeper block)
echo "  Preparing files..."
xattr -rd com.apple.quarantine "$DIR" 2>/dev/null

"$DIR/python/bin/python3" "$DIR/_installer/install.py" "$DIR"
echo ""
read -p "  Press Enter to close..."
