#!/usr/bin/env bash
# Sign and notarize a macOS bundle produced by installer/build_release.py.
#
# Required environment (all 6 must be present — script exits 0 cleanly if ANY
# is missing so CI runs from forks / pre-enrollment builds don't fail):
#
#   APPLE_ID                            Developer account email
#   APPLE_TEAM_ID                       10-char team identifier
#   APPLE_APP_PASSWORD                  app-specific password for notarytool
#   APPLE_SIGNING_IDENTITY              e.g. "Developer ID Application: Name (TEAMID)"
#   APPLE_SIGNING_CERTIFICATE_P12_BASE64    base64 of the .p12 export of the
#                                           signing cert + private key
#   APPLE_SIGNING_CERTIFICATE_PASSWORD      password used when exporting the .p12
#
# GitHub's macOS runners start with an empty Keychain, so we import the .p12
# into a temporary keychain at build time. The keychain is deleted after
# signing via an EXIT trap — the runner is ephemeral anyway, but defense in
# depth.

set -euo pipefail

ARTIFACT="${1:-}"

if [ -z "${ARTIFACT}" ] || [ ! -f "${ARTIFACT}" ]; then
  echo "[sign_macos] Artifact not found: ${ARTIFACT}" >&2
  exit 1
fi

# ── Enrollment check ────────────────────────────────────────────────
if [ -z "${APPLE_ID:-}" ] \
   || [ -z "${APPLE_TEAM_ID:-}" ] \
   || [ -z "${APPLE_APP_PASSWORD:-}" ] \
   || [ -z "${APPLE_SIGNING_IDENTITY:-}" ] \
   || [ -z "${APPLE_SIGNING_CERTIFICATE_P12_BASE64:-}" ] \
   || [ -z "${APPLE_SIGNING_CERTIFICATE_PASSWORD:-}" ]; then
  echo "[sign_macos] Apple credentials not fully configured — skipping signing."
  echo "[sign_macos] Required secrets:"
  echo "              APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD,"
  echo "              APPLE_SIGNING_IDENTITY,"
  echo "              APPLE_SIGNING_CERTIFICATE_P12_BASE64,"
  echo "              APPLE_SIGNING_CERTIFICATE_PASSWORD"
  exit 0
fi

# ── Temp keychain (auto-cleaned on exit) ────────────────────────────
KEYCHAIN="build.keychain-db"
KEYCHAIN_PASSWORD="$(uuidgen)"
CERT_FILE="$(mktemp -t alma_sign_cert.XXXXXX).p12"

cleanup() {
  security delete-keychain "${KEYCHAIN}" 2>/dev/null || true
  rm -f "${CERT_FILE}" 2>/dev/null || true
}
trap cleanup EXIT

echo "[sign_macos] Creating temporary keychain"
security create-keychain -p "${KEYCHAIN_PASSWORD}" "${KEYCHAIN}"
security set-keychain-settings -lut 21600 "${KEYCHAIN}"
security unlock-keychain -p "${KEYCHAIN_PASSWORD}" "${KEYCHAIN}"

# Prepend the new keychain to the search list so codesign finds it without
# disturbing login.keychain (which may not exist on runners anyway).
security list-keychains -d user -s "${KEYCHAIN}" $(security list-keychains -d user | tr -d '"')

echo "[sign_macos] Importing signing certificate"
echo "${APPLE_SIGNING_CERTIFICATE_P12_BASE64}" | base64 --decode > "${CERT_FILE}"
security import "${CERT_FILE}" \
  -k "${KEYCHAIN}" \
  -P "${APPLE_SIGNING_CERTIFICATE_PASSWORD}" \
  -T /usr/bin/codesign \
  -T /usr/bin/security

# Allow codesign to use the key without an interactive prompt.
security set-key-partition-list \
  -S "apple-tool:,apple:,codesign:" \
  -s -k "${KEYCHAIN_PASSWORD}" \
  "${KEYCHAIN}" >/dev/null

# ── Deep-sign every nested Mach-O, then repack ──────────────────────
# `codesign --deep "<zip>"` is a NO-OP: codesign cannot reach binaries inside a
# .zip, so the bundled QtWebEngineProcess + Qt frameworks would ship UNSIGNED and
# Gatekeeper kills the helper → the Agent chat's QWebEngineView renders BLANK.
# The fix is to unpack, sign every Mach-O leaf inner→outer (the WebEngine helper,
# then .so/.dylib, then the .framework bundles, then the interpreters), repack
# preserving symlinks, and notarize the signed archive.
ARTIFACT_ABS="$(cd "$(dirname "${ARTIFACT}")" && pwd)/$(basename "${ARTIFACT}")"
WORK="$(mktemp -d)"
echo "[sign_macos] Unpacking for deep signing → ${WORK}"
ditto -x -k "${ARTIFACT_ABS}" "${WORK}"

sign_one() {  # plain hardened-runtime sign (dylibs, frameworks, node)
  codesign --force --options runtime --timestamp \
           --keychain "${KEYCHAIN}" --sign "${APPLE_SIGNING_IDENTITY}" "$1"
}
sign_jit() {  # hardened runtime + JIT entitlements (QtWebEngine helper + Python)
  codesign --force --options runtime --timestamp --entitlements "${HELPER_ENT}" \
           --keychain "${KEYCHAIN}" --sign "${APPLE_SIGNING_IDENTITY}" "$1"
}

# QtWebEngine's helper hosts Chromium/V8, which JIT-compiles JS. Under the
# hardened runtime it MUST carry com.apple.security.cs.allow-jit or the renderer
# can't allocate executable memory → it exits → the QWebEngineView renders BLANK.
# Qt ships the authoritative entitlements file INSIDE the framework — prefer it;
# fall back to writing the same three it requests. (Qt docs: sign the WebEngine
# process "with an entitlements file that at least contains" these.)
HELPER_ENT="$(find "${WORK}" -path '*QtWebEngineProcess.app/Contents/Resources/QtWebEngineProcess.entitlements' | head -1)"
if [ -z "${HELPER_ENT}" ]; then
  HELPER_ENT="$(mktemp -t webengine_ent).plist"
  cat > "${HELPER_ENT}" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>com.apple.security.cs.allow-jit</key><true/>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
  <key>com.apple.security.cs.disable-library-validation</key><true/>
</dict></plist>
PLIST
fi
echo "[sign_macos] helper entitlements: ${HELPER_ENT}"

# 1) The QtWebEngine helper — inner Mach-O first, THEN its .app bundle, BOTH with
#    the JIT entitlements. This is the fix for the blank render on Apple Silicon.
while IFS= read -r -d '' f; do echo "[sign_macos]  helper bin: $f"; sign_jit "$f"; done \
  < <(find "${WORK}" -name 'QtWebEngineProcess' -type f -print0)
while IFS= read -r -d '' a; do echo "[sign_macos]  helper app: $a"; sign_jit "$a"; done \
  < <(find "${WORK}" -name 'QtWebEngineProcess.app' -type d -print0)
# 2) Mach-O leaves: Python extension modules + shared libs.
while IFS= read -r -d '' f; do sign_one "$f"; done \
  < <(find "${WORK}" \( -name '*.so' -o -name '*.dylib' \) -type f -print0)
# 3) Frameworks (sign the bundle dir after its internals are signed).
while IFS= read -r -d '' fw; do sign_one "$fw"; done \
  < <(find "${WORK}" -name '*.framework' -type d -print0)
# 4) The bundled interpreters. Python also hosts V8 on the in-process-gpu /
#    single-process paths, so it gets the JIT entitlements too; node does not.
while IFS= read -r -d '' exe; do sign_jit "$exe"; done \
  < <(find "${WORK}" -path '*/python/bin/*' -type f -perm +111 -print0)
while IFS= read -r -d '' exe; do sign_one "$exe"; done \
  < <(find "${WORK}" -path '*/node/bin/*' -type f -perm +111 -print0)

# Sanity-check that the helper Gatekeeper cares about is actually signed.
HELPER="$(find "${WORK}" -name 'QtWebEngineProcess' -type f | head -1)"
[ -n "${HELPER}" ] && codesign --verify --strict --verbose=2 "${HELPER}"

echo "[sign_macos] Repacking signed tree (symlinks preserved)"
rm -f "${ARTIFACT_ABS}"
( cd "${WORK}" && zip -r -q -y -X "${ARTIFACT_ABS}" . )
rm -rf "${WORK}"

# ── Notarize ────────────────────────────────────────────────────────
echo "[sign_macos] Submitting for notarization (blocks until Apple responds)"
xcrun notarytool submit "${ARTIFACT}" \
  --apple-id "${APPLE_ID}" \
  --team-id "${APPLE_TEAM_ID}" \
  --password "${APPLE_APP_PASSWORD}" \
  --wait

# Stapling attaches the notarization ticket so Gatekeeper validates the app
# OFFLINE — critical for airgapped installs (it reads the embedded ticket instead
# of phoning Apple). IMPORTANT: stapler only works on a .app / .dmg / .pkg, NOT a
# .zip. A .zip notarizes but CANNOT be stapled, so a quarantined copy on an
# airgapped Mac has no ticket to read and Gatekeeper blocks launch. For the
# offline story to actually hold, build_release.py must emit a .dmg or .pkg
# (wrapping the .app) and pass THAT here.
echo "[sign_macos] Stapling"
if xcrun stapler staple "${ARTIFACT}"; then
  echo "[sign_macos] Stapled — validates offline on airgapped Macs."
else
  echo "[sign_macos] WARNING: could not staple a .${ARTIFACT##*.} artifact." >&2
  echo "[sign_macos]          Notarized but UNSTAPLED → a quarantined copy will NOT" >&2
  echo "[sign_macos]          launch offline. Package as .dmg/.pkg and staple that." >&2
fi

echo "[sign_macos] Done — ${ARTIFACT} is signed + notarized"
