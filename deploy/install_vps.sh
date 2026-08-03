#!/usr/bin/env bash
set -eu

APP_DIR=${APP_DIR:-/opt/pa-agent}
STATE_DIR=${STATE_DIR:-/var/lib/pa-agent}
CONFIG_DIR=${CONFIG_DIR:-/etc/pa-agent}
SERVICE_USER=${SERVICE_USER:-pa-agent}
CONDA_EXE=${CONDA_EXE:-/opt/miniconda3/bin/conda}

if [ "$(id -u)" -ne 0 ]; then
  printf '%s\n' "Run this installer as root." >&2
  exit 2
fi
if [ ! -f "$APP_DIR/pyproject.toml" ]; then
  printf '%s\n' "Clone PA Agent into $APP_DIR before running this installer." >&2
  exit 2
fi
if [ ! -x "$CONDA_EXE" ]; then
  printf '%s\n' "Miniconda is unavailable at $CONDA_EXE." >&2
  exit 2
fi

cd "$APP_DIR"

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$STATE_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

"$CONDA_EXE" env update --file "$APP_DIR/environment-vps.yml"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 "$STATE_DIR"
install -d -o root -g "$SERVICE_USER" -m 0750 "$CONFIG_DIR"

if [ ! -f "$STATE_DIR/config/settings.json" ]; then
  install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 "$STATE_DIR/config"
  install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0600 \
    "$APP_DIR/config/settings.vps.example.json" "$STATE_DIR/config/settings.json"
fi
if [ ! -f "$CONFIG_DIR/pa-agent.env" ]; then
  install -o root -g "$SERVICE_USER" -m 0640 \
    "$APP_DIR/deploy/systemd/pa-agent.env.example" "$CONFIG_DIR/pa-agent.env"
fi

install -o root -g root -m 0644 \
  "$APP_DIR/deploy/systemd/pa-agent.service" /etc/systemd/system/pa-agent.service
systemctl daemon-reload

printf '%s\n' "Installed without starting the service."
printf '%s\n' "Create $CONFIG_DIR/api_key, run --check and --probe-data, then enable it."
