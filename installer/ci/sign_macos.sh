#!/usr/bin/env bash
# Sign and notarize a macOS bundle produced by installer/build_release.py.
#
# Required environment:
#   APPLE_ID              — Apple Developer account email
#   APPLE_TEAM_ID         — 10-char team identifier
#   APPLE_APP_PASSWORD    — app-specific password for altool / notarytool
#   SIGNING_IDENTITY      — e.g. "Developer ID Application: Alma Health (TEAMID)"
#
# The script is a no-op (exit 0) when any required secret is missing so
# CI runs from forks don't fail.

set -euo pipefail

ARTIFACT="${1:-}"

if [ -z "${ARTIFACT}" ] || [ ! -f "${ARTIFACT}" ]; then
  echo "[sign_macos] Artifact not found: ${ARTIFACT}" >&2
  exit 1
fi

if [ -z "${APPLE_ID:-}" ] || [ -z "${APPLE_TEAM_ID:-}" ] \
   || [ -z "${APPLE_APP_PASSWORD:-}" ] || [ -z "${SIGNING_IDENTITY:-}" ]; then
  echo "[sign_macos] Apple credentials not configured — skipping signing."
  exit 0
fi

echo "[sign_macos] Signing ${ARTIFACT}"
codesign --force --deep --options runtime \
         --sign "${SIGNING_IDENTITY}" \
         --timestamp \
         "${ARTIFACT}"

echo "[sign_macos] Submitting for notarization"
xcrun notarytool submit "${ARTIFACT}" \
    --apple-id "${APPLE_ID}" \
    --team-id "${APPLE_TEAM_ID}" \
    --password "${APPLE_APP_PASSWORD}" \
    --wait

echo "[sign_macos] Stapling"
xcrun stapler staple "${ARTIFACT}" || true
echo "[sign_macos] Done"
