#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/BBB-Plugin/greenlight-recording-tools.git"
INSTALL_DIR="/opt/greenlight-recording-tools"
BIN_LINK="/usr/local/bin/greenlight-recordings"

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "ERROR: run this installer as root" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required" >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "ERROR: git is required" >&2
  exit 1
fi

if [[ -d "$INSTALL_DIR/.git" ]]; then
  git -C "$INSTALL_DIR" fetch --quiet origin
  git -C "$INSTALL_DIR" reset --hard origin/main
else
  rm -rf "$INSTALL_DIR"
  git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi

chmod 0755 "$INSTALL_DIR/greenlight_recordings.py"
ln -sfn "$INSTALL_DIR/greenlight_recordings.py" "$BIN_LINK"

echo "Installed: $BIN_LINK"
echo "Try: greenlight-recordings --help"
