#!/usr/bin/env bash
# macOS verification harness for the enablement web pivot (M6).
# Run on the Mac (dev venv or the installed app's python). Non-destructive.
#
#   bash scripts/verify_web_pivot_mac.sh [path-to-python3]
#
# Steps mirror the M6 checklist:
#   1. web_diag fact sheet (Rosetta / arch / frameworks / bundle) — must be 0 FAIL
#   2. pinned-version parity note (installer pins may differ from dev)
#   3. WebEngine round-trips, run SINGLY (offscreen teardown stacks exit 255)
#   4. RSS snapshot with all three web surfaces
set -uo pipefail

PY="${1:-python3}"
cd "$(dirname "$0")/.."
FAILURES=0

step() { printf '\n── %s ──\n' "$1"; }

step "1/4 web_diag fact sheet"
"$PY" scripts/web_diag.py || { echo "web_diag reported FAILs — fix these first"; FAILURES=$((FAILURES+1)); }

step "2/4 version parity"
"$PY" - <<'EOF'
import PySide6, platform
print(f"PySide6 {PySide6.__version__} on {platform.machine()} "
      f"(installer pins may differ — compare against requirements.txt)")
EOF

step "3/4 WebEngine round-trips (singly)"
for t in test_agent_bridge_local test_calendar_web_local test_workbench_web_local \
         test_home_web_local; do
  if [ -f "tests/$t.py" ]; then
    QT_QPA_PLATFORM=offscreen "$PY" -m pytest "tests/$t.py" -q || FAILURES=$((FAILURES+1))
  else
    echo "tests/$t.py not present on this checkout (gitignored *_local.py) — skipped"
  fi
done

step "4/4 RSS snapshot (3 web surfaces)"
"$PY" scripts/measure_web_rss.py || echo "(RSS probe is informational — not a failure)"

step "result"
if [ "$FAILURES" -eq 0 ]; then
  echo "web pivot verification: PASS"
else
  echo "web pivot verification: $FAILURES step(s) FAILED"
fi
exit "$FAILURES"
