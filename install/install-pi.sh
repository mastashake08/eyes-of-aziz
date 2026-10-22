#!/usr/bin/env bash
# Eyes Of Aziz Bridge installer for a Raspberry Pi (or any Debian machine).
#
#   curl -fsSL https://raw.githubusercontent.com/mastashake08/eyes-of-aziz/main/install/install-pi.sh | bash
#
# Run it as the user the bridge should run as (not root); it asks for sudo
# for packages and services. Safe to run again: it upgrades in place and
# keeps existing configs, identities and registrations.
#
# Sets up, as systemd services:
#   rnsd               Reticulum transport node (LAN, internet backbones, LoRa on USB)
#   lxmd               LXMF propagation node: holds messages for offline phones
#   eyes-of-aziz-mesh  this bridge's LXMF address, commands and heartbeats
#   rnsh               remote shell over Reticulum, for allowed identities only
#   eyes-of-aziz-bridge  the camera bridge - Pi 4/5 (or 2 GB+ machines) only
#
# Options:
#   --ref REF                  git tag/branch to install (default: main)
#   --no-cameras               skip the camera bridge even if this machine can run it
#   --skip-setup               don't sign in / register now (run eyes-of-aziz-mesh setup later)
#   --rnode PATH|none          LoRa board's serial port (default: first one found)
#   --allow-shell IDENTITY     let this rnsh identity (from `rnsh -p`) connect
set -euo pipefail

REF="main"
CAMERAS="auto"
SKIP_SETUP=0
RNODE="auto"
ALLOW_SHELL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --ref) REF="$2"; shift 2 ;;
    --no-cameras) CAMERAS="no"; shift ;;
    --skip-setup) SKIP_SETUP=1; shift ;;
    --rnode) RNODE="$2"; shift 2 ;;
    --allow-shell) ALLOW_SHELL="$2"; shift 2 ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if [ "$(id -u)" = 0 ]; then
  echo "Run this as the user the bridge should run as, not root (it uses sudo where needed)." >&2
  exit 1
fi

USER_NAME="$(id -un)"
APP_DIR="$HOME/.eyes-of-aziz"
VENV="$APP_DIR/venv"
MESH_HOME="$HOME/.eyes-of-aziz-mesh"
SOURCE="git+https://github.com/mastashake08/eyes-of-aziz@${REF}"
step() { printf '\n==> %s\n' "$*"; }

step "Installing system packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip git >/dev/null
# Serial access for a LoRa board on USB.
sudo usermod -aG dialout "$USER_NAME"

step "Installing Eyes Of Aziz ($REF) into $VENV"
mkdir -p "$APP_DIR"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
# Mesh side first, without the camera dependencies (OpenCV), so a Pi 3 gets
# only what it can run.
"$VENV/bin/pip" install -q --upgrade --no-deps "eyes-of-aziz @ $SOURCE"
"$VENV/bin/pip" install -q --upgrade "rns>=1.3,<2" "lxmf>=1.0,<2" "requests>=2.31,<3" "python-dotenv>=1.0,<2"

if [ "$CAMERAS" = "auto" ]; then
  if "$VENV/bin/eyes-of-aziz-mesh" cameras-supported; then CAMERAS="yes"; else CAMERAS="no"; fi
fi
if [ "$CAMERAS" = "yes" ]; then
  step "Installing the camera bridge"
  "$VENV/bin/pip" install -q --upgrade "eyes-of-aziz[mesh] @ $SOURCE"
fi

step "Moving over an earlier Aziz Pi setup, if there is one"
if [ -d "$HOME/.aziz-pi" ] && [ ! -d "$MESH_HOME" ]; then
  mv "$HOME/.aziz-pi" "$MESH_HOME"   # keeps its identity, so its LXMF address stays the same
  echo "Moved ~/.aziz-pi to $MESH_HOME"
fi
for unit in aziz-pi aziz-echo rnsh; do
  if [ -f "$HOME/.config/systemd/user/$unit.service" ]; then
    systemctl --user disable --now "$unit" 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/$unit.service"
    echo "Removed the old per-user $unit service"
  fi
done
systemctl --user daemon-reload 2>/dev/null || true

step "Writing Reticulum and propagation node configs (existing ones are kept)"
"$VENV/bin/eyes-of-aziz-mesh" write-configs --rnode "$RNODE" --name "Eyes Of Aziz Bridge $(hostname)"
mkdir -p "$MESH_HOME" "$HOME/.rnsh"
chmod 700 "$MESH_HOME"
if [ -n "$ALLOW_SHELL" ]; then
  grep -qsx "$ALLOW_SHELL" "$HOME/.rnsh/allowed_identities" || echo "$ALLOW_SHELL" >> "$HOME/.rnsh/allowed_identities"
fi

step "Installing services"
unit() {  # unit NAME DESCRIPTION AFTER EXEC...
  local name="$1" description="$2" after="$3"; shift 3
  local path="/etc/systemd/system/$name.service"
  if [ -f "$path" ] && ! grep -q "$VENV" "$path"; then
    sudo cp "$path" "$path.before-eyes-of-aziz"   # someone else's unit: keep a copy
  fi
  sudo tee "$path" >/dev/null <<UNIT
[Unit]
Description=$description
After=$after
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
Environment=HOME=$HOME
WorkingDirectory=$APP_DIR
ExecStart=$*
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
}
unit rnsd "Reticulum transport node" "network-online.target" "$VENV/bin/rnsd --service"
unit lxmd "LXMF propagation node" "rnsd.service" "/bin/sh -c 'sleep 5; exec $VENV/bin/lxmd --service --propagation-node'"
unit eyes-of-aziz-mesh "Eyes Of Aziz Bridge mesh agent" "lxmd.service" "/bin/sh -c 'sleep 8; exec $VENV/bin/eyes-of-aziz-mesh run'"
unit rnsh "Remote shell over Reticulum (allowed identities only)" "rnsd.service" "/bin/sh -c 'sleep 5; exec $VENV/bin/rnsh -l -s aziz -b 1800'"
if [ "$CAMERAS" = "yes" ]; then
  mkdir -p "$APP_DIR/cameras"
  unit eyes-of-aziz-bridge "Eyes Of Aziz camera bridge" "network-online.target" \
    "$VENV/bin/eyes-of-aziz-bridge --all --cameras-dir $APP_DIR/cameras --status-host 0.0.0.0"
fi

# rnsh's address, for the app and heartbeats.
"$VENV/bin/rnsh" -l -s aziz -p 2>/dev/null | sed -n 's/.*Listening on *: *<\([0-9a-f]\{32\}\)>.*/\1/p' > "$MESH_HOME/shell_address" || true

sudo systemctl daemon-reload
sudo systemctl enable -q rnsd lxmd eyes-of-aziz-mesh rnsh
sudo systemctl restart rnsd
sleep 3
sudo systemctl restart lxmd eyes-of-aziz-mesh rnsh

# Reads the answers from the terminal even when this script is piped from curl.
if [ "$SKIP_SETUP" = 0 ] && [ -r /dev/tty ]; then
  step "Registering this bridge with Project Aziz"
  "$VENV/bin/eyes-of-aziz-mesh" setup < /dev/tty || echo "Not registered - run: $VENV/bin/eyes-of-aziz-mesh setup"
  sudo systemctl restart eyes-of-aziz-mesh
fi

if [ "$CAMERAS" = "yes" ]; then
  step "Cameras"
  echo "This machine can run the camera bridge. To add cameras:"
  echo "  cd $APP_DIR && $VENV/bin/eyes-of-aziz-setup"
  echo "  sudo systemctl enable --now eyes-of-aziz-bridge"
else
  echo
  echo "This machine runs the mesh side only (the camera bridge needs a Raspberry Pi 4/5 or 2 GB of RAM)."
fi

sleep 10
step "Done"
echo "LXMF address:      $(cat "$MESH_HOME/address" 2>/dev/null || echo 'starting...')"
echo "Remote shell:      $(cat "$MESH_HOME/shell_address" 2>/dev/null || echo 'unknown')"
echo "Commands:          $VENV/bin/eyes-of-aziz-mesh --help"
echo "Allow your phone:  $VENV/bin/eyes-of-aziz-mesh allow <its LXMF address>"
if ! id -nG "$USER_NAME" | grep -qw dialout; then
  echo "Log out and back in (or reboot) so the LoRa board on USB can be opened."
fi
