#!/usr/bin/env bash
# Muse Storage Disc Assist - POSIX Shell Runner & Cloud Dog Companion
set -euo pipefail

COLOR_CYAN="\033[1;36m"
COLOR_GREEN="\033[1;32m"
COLOR_YELLOW="\033[1;33m"
COLOR_RESET="\033[0m"

echo -e "${COLOR_CYAN}=== Muse Storage Disc Assist (Cloud Dog Companion) ===${COLOR_RESET}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$(command -v python3 || command -v python || true)"

if [ -z "$PYTHON_BIN" ]; then
  echo "Error: Python 3 is required but not found in PATH." >&2
  exit 1
fi

MUSE_DL_MAC="/Library/Application Support/MuseHub/Downloads"
MUSE_DIAG_MAC="/Library/Application Support/MuseHub/Diagnostics"

scan_dirs() {
  echo "1. Running Cloud Dog engine scan..."
  if [ -f "${SCRIPT_DIR}/cloud_dog.py" ]; then
    "$PYTHON_BIN" "${SCRIPT_DIR}/cloud_dog.py" scan
  fi

  echo -e "\n2. Checking Muse Hub staging paths:"
  if [ -d "$MUSE_DL_MAC" ]; then
    echo -e "   📦 Download Staging: $(du -sh "$MUSE_DL_MAC" 2>/dev/null | cut -f1)"
  fi
  if [ -d "$MUSE_DIAG_MAC" ]; then
    echo -e "   💥 Crash Dumps: $(du -sh "$MUSE_DIAG_MAC" 2>/dev/null | cut -f1)"
  fi
}

reap_dry_run() {
  echo -e "${COLOR_YELLOW}Running Cloud Dog dry-run reap (verify-then-delete)...${COLOR_RESET}"
  if [ -f "${SCRIPT_DIR}/cloud_dog.py" ]; then
    "$PYTHON_BIN" "${SCRIPT_DIR}/cloud_dog.py" reap
  fi
}

reap_apply() {
  echo -e "${COLOR_YELLOW}Enforcing No-Local-Clutter Law with Cloud Dog (--apply)...${COLOR_RESET}"
  if [ -f "${SCRIPT_DIR}/cloud_dog.py" ]; then
    "$PYTHON_BIN" "${SCRIPT_DIR}/cloud_dog.py" enforce --apply
  fi

  # Clean local verified scratch
  if [ -d "$MUSE_DL_MAC" ]; then
    find "$MUSE_DL_MAC" -type f \( -name "*.musepkg" -o -name "*.part" -o -name "*.download" \) -delete 2>/dev/null || true
    echo -e "${COLOR_GREEN}✓ Local installer archives pruned.${COLOR_RESET}"
  fi
  if [ -d "$MUSE_DIAG_MAC" ]; then
    find "$MUSE_DIAG_MAC" -type f -name "*.dmp" -delete 2>/dev/null || true
    echo -e "${COLOR_GREEN}✓ Crash dumps cleaned.${COLOR_RESET}"
  fi
}

case "${1:-help}" in
  scan)
    scan_dirs
    ;;
  reap)
    reap_dry_run
    ;;
  enforce|clean)
    reap_apply
    ;;
  registry)
    "$PYTHON_BIN" "${SCRIPT_DIR}/cloud_dog.py" registry
    ;;
  *)
    echo "Usage: ./muse-assist.sh [scan | reap | enforce | registry]"
    echo "  scan       Report disk usage % and largest consumers"
    echo "  reap       Dry-run: verify registered files against store"
    echo "  enforce    Verify and delete unneeded files (apply mode)"
    echo "  registry   Show active store registry"
    ;;
esac
