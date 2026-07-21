#!/usr/bin/env bash
# ============================================================================
# ⛔ DO NOT RUN — KEPT FOR HISTORICAL REFERENCE ONLY (2026-07-08 post-mortem).
#
# This script's premise was EMPIRICALLY DISPROVEN: the pristine pip PySide6
# frameworks need NO entitlements and NO re-signing (a clean venv renders
# unsigned). Running it CORRUPTED the QtWebEngine framework and caused the
# multi-day "Agent renders blank" saga; recovery required
#   pip install --force-reinstall --no-deps PySide6 PySide6-Essentials \
#       PySide6-Addons shiboken6   (all four — frameworks live in Addons)
# plus removing leftover _CodeSignature dirs. The REAL blank-render causes
# were (1) that corruption and (2) Rosetta via a script-only .app wrapper
# (fixed by LSRequiresNativeExecution + arch -arm64 in installer/install.py).
# The shipped-build signing path is installer/ci/sign_macos.sh — never this.
#
# See ~/.claude/projects/C--alma-insights/memory/macos_agent_blank_render.md.
# ============================================================================
if [ "${ALMA_I_UNDERSTAND_THIS_BREAKS_RENDERING:-}" != "1" ]; then
  echo "REFUSING TO RUN: sign_qtwebengine_dev.sh corrupts the PySide6 QtWebEngine"
  echo "framework (proven 2026-07-08). Never re-sign the pip frameworks."
  echo "If you are ABSOLUTELY sure: ALMA_I_UNDERSTAND_THIS_BREAKS_RENDERING=1 $0 ..."
  exit 3
fi
# Offline ad-hoc code-sign repair for the bundled PySide6 QtWebEngine on macOS
# (Apple Silicon). Fixes, in ONE consistent inner→outer pass:
#   1. Re-seals QtWebEngineCore.framework (undoes the broken seal from piecemeal
#      `codesign --force` on the helper → the SIGKILL / exit=9).
#   2. Gives QtWebEngineProcess the JIT entitlements V8/ANGLE need under the
#      hardened runtime on Apple Silicon (missing → renderer exits 0 / blank).
#   3. RE-SIGNS the app's Python, which `codesign --remove-signature` left
#      unsigned — arm64 needs a valid signature, and Keychain (pat_store) ACLs
#      are keyed to it, so an unsigned Python silently loses API-key access.
#
# 100% OFFLINE / airgap-safe: ad-hoc (`-s -`) + `--timestamp=none`, no network.
# No `pip reinstall` needed — `codesign --force` overwrites the mangled sigs.
#
# Usage (pass the APP's python, NOT system /usr/bin/python3 which lacks PySide6):
#   bash scripts/sign_qtwebengine_dev.sh /Users/chris/Applications/AlmaInsights/python/bin/python3
#
# For the SHIPPED build, installer/ci/sign_macos.sh must apply these SAME
# entitlements to the helper AND the main app binary — it currently signs with
# --options runtime but NO --entitlements, so every shipped M1 Mac blanks.
set -euo pipefail

APP_PYTHON="${1:-}"
if [ -z "${APP_PYTHON}" ]; then
  echo "Usage: bash scripts/sign_qtwebengine_dev.sh <path-to-APP-python3>"
  echo "  e.g. /Users/chris/Applications/AlmaInsights/python/bin/python3"
  echo "  (must be the Python that imports PySide6 — system python3 does NOT)"
  exit 2
fi
PS="$("${APP_PYTHON}" -c 'import PySide6; print(PySide6.__path__[0])')" \
  || { echo "ERROR: ${APP_PYTHON} cannot import PySide6"; exit 1; }
FW="${PS}/Qt/lib/QtWebEngineCore.framework"
[ -d "${FW}" ] || { echo "ERROR: QtWebEngineCore.framework not found under ${PS}"; exit 1; }
HELPER_APP="$(find "${FW}" -name 'QtWebEngineProcess.app' -type d | head -1)"
[ -n "${HELPER_APP}" ] || { echo "ERROR: QtWebEngineProcess.app not found"; exit 1; }

echo "app python : ${APP_PYTHON}"
echo "PySide6    : ${PS}"
echo "helper     : ${HELPER_APP}"

# Safety: framework backup (your airgap-safe rollback — no pip reinstall).
if [ ! -d "${FW}.bak" ]; then cp -R "${FW}" "${FW}.bak"; echo "backup     : ${FW}.bak"; \
  else echo "backup     : ${FW}.bak (exists — kept)"; fi

# Entitlements: prefer Qt's OWN authoritative file shipped inside the framework
# (Qt docs say the helper must be signed with "at least" these) — fall back to
# writing the same three it requests. ENT_TMP tracks whether we made a temp file.
ENT="$(find "${FW}" -path '*QtWebEngineProcess.app/Contents/Resources/QtWebEngineProcess.entitlements' | head -1)"
ENT_TMP=""
if [ -z "${ENT}" ]; then
  ENT="$(mktemp -t alma_ent).plist"; ENT_TMP="${ENT}"
  cat > "${ENT}" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.security.cs.allow-jit</key><true/>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
  <key>com.apple.security.cs.disable-library-validation</key><true/>
</dict>
</plist>
PLIST
fi
echo "entitlements: ${ENT}"

RUNTIME=(codesign --force --options runtime --timestamp=none -s -)  # executables that run
PLAIN=(codesign --force --timestamp=none -s -)                      # dylibs / bundle seals

# ── inner → outer ─────────────────────────────────────────────────────────
echo "[1/5] helper exe + app (JIT entitlements, hardened runtime)…"
"${RUNTIME[@]}" --entitlements "${ENT}" "${HELPER_APP}/Contents/MacOS/QtWebEngineProcess"
"${RUNTIME[@]}" --entitlements "${ENT}" "${HELPER_APP}"

echo "[2/5] nested dylibs (if any)…"
while IFS= read -r -d '' lib; do
  [ "${lib}" = "${FW}/Versions/A/QtWebEngineCore" ] && continue
  "${PLAIN[@]}" "${lib}" || echo "WARN: skipped ${lib}"
done < <(find "${FW}/Versions/A" -type f \( -name '*.dylib' -o -name '*.so' \) -print0)

echo "[3/5] framework main binary (loaded into browser process — no runtime/JIT)…"
"${PLAIN[@]}" "${FW}/Versions/A/QtWebEngineCore"

echo "[4/5] re-seal the framework bundle…"
"${PLAIN[@]}" "${FW}"

echo "[5/5] app Python signature…"
if codesign -dv "${APP_PYTHON}" 2>&1 | grep -q 'Signature='; then
  echo "       already signed (restored from backup?) — leaving it alone."
else
  echo "       UNSIGNED (--remove-signature) → PLAIN ad-hoc re-sign, NO hardened"
  echo "       runtime, to match the linker-signed state the pip wheel ships"
  echo "       (runtime here would enable library validation and break the"
  echo "       PySide6 import). NOTE: this makes Python RUN but does NOT restore"
  echo "       Keychain access to keys stored under the old signature — for that,"
  echo "       restore python/ from your backup instead."
  "${PLAIN[@]}" "${APP_PYTHON}"
fi

# ── verify ─────────────────────────────────────────────────────────────────
echo "── verify ──"
codesign --verify --deep --strict --verbose=2 "${FW}" && echo "OK: framework seal consistent"
codesign -d --entitlements :- "${HELPER_APP}" 2>&1 | grep -q 'allow-jit' && echo "OK: helper has allow-jit"
codesign --verify --verbose=2 "${APP_PYTHON}" && echo "OK: python signed"
[ -n "${ENT_TMP}" ] && rm -f "${ENT_TMP}"   # only delete a temp plist, never Qt's own file

cat <<EOF

Done. Launch WITHOUT --single-process / GPU flags — the renderer should stay up.

KEYCHAIN: re-signing Python changes its code identity, so the OS Keychain may no
longer grant access to API keys stored under the OLD signature (this is likely
the "silent pat_store" issue). After launch, check whether the app reads its keys.
If denied: either click "Always Allow" on the Keychain prompt, or re-enter the
keys in Settings (they get re-stored under the new signature). This is a one-time
cost of the --remove-signature you ran; a proper full-bundle sign avoids it.

Rollback (framework only): rm -rf "${FW}" && mv "${FW}.bak" "${FW}"
EOF
