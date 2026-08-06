#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_DIR="/etc/systemd/system"
TARGET_USER="${SUDO_USER:-${USER}}"
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "$TMP_DIR"
}

trap cleanup EXIT

render_unit() {
  local src="$1"
  local dest="$2"

  sed \
    -e "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" \
    -e "s|__SERVICE_USER__|$TARGET_USER|g" \
    "$src" > "$dest"
}

render_unit "$PROJECT_ROOT/systemd/cctv-recorder.service" "$TMP_DIR/cctv-recorder.service"
render_unit "$PROJECT_ROOT/systemd/cctv-replayer.service" "$TMP_DIR/cctv-replayer.service"
cp "$PROJECT_ROOT/systemd/cctv-nvr.target" "$TMP_DIR/cctv-nvr.target"

sudo install -m 644 "$TMP_DIR/cctv-recorder.service" "$SYSTEMD_DIR/cctv-recorder.service"
sudo install -m 644 "$TMP_DIR/cctv-replayer.service" "$SYSTEMD_DIR/cctv-replayer.service"
sudo install -m 644 "$TMP_DIR/cctv-nvr.target" "$SYSTEMD_DIR/cctv-nvr.target"

sudo systemctl daemon-reload
sudo systemctl enable --now cctv-recorder.service cctv-replayer.service

echo "Installed and started:"
echo "  cctv-recorder.service"
echo "  cctv-replayer.service"
echo
echo "Useful commands:"
echo "  sudo systemctl status cctv-recorder.service"
echo "  sudo systemctl status cctv-replayer.service"
echo "  sudo journalctl -u cctv-recorder.service -f"
echo "  sudo journalctl -u cctv-replayer.service -f"
