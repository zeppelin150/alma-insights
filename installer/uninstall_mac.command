#!/usr/bin/env bash
# Alma Insights — macOS uninstaller launcher
# Runs from the installed bundle; forwards any flags to uninstall.py.

set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${INSTALL_DIR}/python/bin/python3"
UNINSTALL="${INSTALL_DIR}/_installer/uninstall.py"

if [ ! -x "${PYTHON}" ]; then
  echo "[ERROR] Bundled Python not found at ${PYTHON}" >&2
  exit 1
fi
if [ ! -f "${UNINSTALL}" ]; then
  echo "[ERROR] Uninstaller not found at ${UNINSTALL}" >&2
  exit 1
fi

exec "${PYTHON}" "${UNINSTALL}" --install-dir "${INSTALL_DIR}" "$@"
