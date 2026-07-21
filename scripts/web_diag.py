"""Print the web-stack diagnostics fact sheet for THIS machine.

Run this first on any "the Agent/Calendar/Workbench page is blank" report:

    python scripts/web_diag.py            # human report, exit 1 on any FAIL
    python scripts/web_diag.py --json     # machine-readable

Covers both OSes: Windows (helper exe, offscreen notes) and macOS (Rosetta
translation, python-vs-QtWebEngineProcess arch mismatch, quarantine,
framework re-sign leftovers) plus the shared checks (Qt/WebEngine import,
bundle presence, env flags, mach page-size fingerprint).
"""

import json
import os
import sys

# Windows consoles default to cp1252 — never let an encoding error mask the
# diagnostics this script exists to surface.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ui.web.web_diag import FAIL, format_report, run_diagnostics  # noqa: E402


def main() -> int:
    diag = run_diagnostics()
    if "--json" in sys.argv:
        print(json.dumps(diag, indent=2))
    else:
        print(format_report(diag))
    return 1 if diag["summary"][FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
