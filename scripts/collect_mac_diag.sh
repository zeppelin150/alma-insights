#!/usr/bin/env bash
# READ-ONLY diagnostics for the macOS QtWebEngine blank-render issue.
# Modifies NOTHING. Run on the Mac and paste the whole output:
#   bash scripts/collect_mac_diag.sh /Users/chris/Applications/AlmaInsights/python/bin/python3
set -uo pipefail   # deliberately NOT -e — keep going and report every failure

APP_PYTHON="${1:-python3}"
line(){ echo; echo "===== $* ====="; }

line "ENVIRONMENT"
sw_vers 2>&1
echo "arch          : $(uname -m)"
echo "app python    : ${APP_PYTHON}"
"${APP_PYTHON}" -c 'import sys,PySide6,PySide6.QtCore as C; \
print("python ver  :", sys.version.split()[0]); \
print("PySide6 ver :", PySide6.__version__); \
print("Qt ver      :", C.qVersion()); \
print("PySide6 path:", PySide6.__path__[0])' 2>&1

PS="$("${APP_PYTHON}" -c 'import PySide6; print(PySide6.__path__[0])' 2>/dev/null || true)"
if [ -z "${PS}" ]; then echo "!! ${APP_PYTHON} cannot import PySide6 — wrong interpreter?"; exit 0; fi
FW="${PS}/Qt/lib/QtWebEngineCore.framework"
HELPER="$(find "${FW}" -name QtWebEngineProcess.app -type d 2>/dev/null | head -1)"

line "PYTHON SIGNATURE (was --remove-signature run on it?)"
codesign -dvvv "${APP_PYTHON}" 2>&1 | grep -iE 'Signature=|Authority|Identifier=|flags|TeamIdentifier|adhoc|Runtime' || echo "(python appears UNSIGNED)"

line "HELPER: ${HELPER}"
codesign -dvvv "${HELPER}" 2>&1 | grep -iE 'Signature=|Authority|flags|adhoc|Runtime' || echo "(helper unsigned?)"
echo "--- helper entitlements (informational ONLY) ---"
# CORRECTED 2026-07-14: the pristine pip PySide6 needs NO entitlements — a
# clean venv renders unsigned (empirically proven 2026-07-08). Missing JIT
# entitlements are NOT a blank-render cause; the real causes were framework
# corruption from manual re-signing and Rosetta translation (QTBUG-98487).
# Run `python scripts/web_diag.py` for the authoritative fact sheet.
codesign -d --entitlements :- "${HELPER}" 2>&1 | grep -iE 'jit|executable-memory|library-validation' \
  || echo "(no JIT entitlements — NORMAL for the pip wheel; not a failure)"
echo "--- does Qt ship its own entitlements file here? ---"
ls -la "${HELPER}/Contents/Resources/QtWebEngineProcess.entitlements" 2>&1

line "FRAMEWORK SEAL (broken = the SIGKILL/exit-9 state)"
codesign --verify --deep --strict --verbose=2 "${FW}" 2>&1 | tail -6

line "QUARANTINE (blocks offline launch if present)"
{ xattr -l "${APP_PYTHON}" 2>&1 | grep -i quarantine && echo "  ^ python quarantined"; } || echo "python  : no quarantine"
{ xattr -l "${HELPER}"     2>&1 | grep -i quarantine && echo "  ^ helper quarantined"; } || echo "helper  : no quarantine"

line "BACKUP PRESENT?"
[ -d "${FW}.bak" ] && echo "framework .bak EXISTS (${FW}.bak)" || echo "no framework .bak"

line "DONE — paste everything above"
