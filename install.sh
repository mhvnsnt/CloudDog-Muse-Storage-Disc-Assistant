#!/usr/bin/env bash
# One-liner installer for Cloud Dog — Muse Storage Disc Assistant
set -euo pipefail

INSTALL_DIR="${HOME}/.local/bin"
mkdir -p "$INSTALL_DIR"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing Cloud Dog & Muse Storage Disc Assist..."

# If running from cloned repo
if [ -f "${SCRIPT_DIR}/cloud_dog.py" ]; then
  cp "${SCRIPT_DIR}/cloud_dog.py" "${INSTALL_DIR}/cloud_dog"
  cp "${SCRIPT_DIR}/muse-assist.sh" "${INSTALL_DIR}/muse-assist"
  chmod +x "${INSTALL_DIR}/cloud_dog" "${INSTALL_DIR}/muse-assist"
else
  # Remote curl installation fallback
  curl -fsSL https://raw.githubusercontent.com/mhvnsnt/CloudDog-Muse-Storage-Disc-Assistant/main/cloud_dog.py -o "${INSTALL_DIR}/cloud_dog"
  curl -fsSL https://raw.githubusercontent.com/mhvnsnt/CloudDog-Muse-Storage-Disc-Assistant/main/muse-assist.sh -o "${INSTALL_DIR}/muse-assist"
  chmod +x "${INSTALL_DIR}/cloud_dog" "${INSTALL_DIR}/muse-assist"
fi

echo "✓ Successfully installed to ${INSTALL_DIR}/cloud_dog and ${INSTALL_DIR}/muse-assist"
echo ""
echo "Verify installation by running:"
echo "  cloud_dog scan"
echo "  muse-assist scan"
