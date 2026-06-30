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

sign_one() {
  codesign --force --options runtime --timestamp \
           --keychain "${KEYCHAIN}" --sign "${APPLE_SIGNING_IDENTITY}" "$1"
}

# 1) The QtWebEngine helper(s) — the exact binary Gatekeeper blocks when unsigned.
while IFS= read -r -d '' f; do echo "[sign_macos]  helper: $f"; sign_one "$f"; done \
  < <(find "${WORK}" -name 'QtWebEngineProcess' -type f -print0)
# 2) Mach-O leaves: Python extension modules + shared libs.
while IFS= read -r -d '' f; do sign_one "$f"; done \
  < <(find "${WORK}" \( -name '*.so' -o -name '*.dylib' \) -type f -print0)
# 3) Frameworks (sign the bundle dir after its internals are signed).
while IFS= read -r -d '' fw; do sign_one "$fw"; done \
  < <(find "${WORK}" -name '*.framework' -type d -print0)
# 4) The bundled interpreters.
while IFS= read -r -d '' exe; do sign_one "$exe"; done \
  < <(find "${WORK}" \( -path '*/python/bin/*' -o -path '*/node/bin/*' \) -type f -perm +111 -print0)

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

# Stapling attaches the notarization ticket so Gatekeeper accepts the bundle
# offline. Zip bundles get stapled best-effort (notarytool staples the zip
# contents but stapler expects an app or dmg — non-fatal).
echo "[sign_macos] Stapling"
xcrun stapler staple "${ARTIFACT}" || echo "[sign_macos] Staple skipped (zip format)"

echo "[sign_macos] Done — ${ARTIFACT} is signed + notarized"
