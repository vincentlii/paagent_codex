#!/usr/bin/env bash
set -eu

APP_DIR=/opt/pa-agent
STATE_DIR=/var/lib/pa-agent
CONFIG_DIR=/etc/pa-agent
SERVICE_USER=pa-agent
CONTROL_GROUP=pa-agent-control
DASHBOARD_USER=pa-agent-dashboard
CODEX_USER=pa-agent-codex
CODEX_GROUP=pa-agent-codex-spool
CONDA_EXE=/opt/miniconda3/bin/conda
ENV_PYTHON=/opt/miniconda3/envs/pa-agent/bin/python
ENV_UV=/opt/miniconda3/envs/pa-agent/bin/uv

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

if ! getent group "$CONTROL_GROUP" >/dev/null 2>&1; then
  groupadd --system "$CONTROL_GROUP"
fi
if ! getent group "$DASHBOARD_USER" >/dev/null 2>&1; then
  groupadd --system "$DASHBOARD_USER"
fi
if ! getent group "$CODEX_GROUP" >/dev/null 2>&1; then
  groupadd --system "$CODEX_GROUP"
fi
if ! getent group "$CODEX_USER" >/dev/null 2>&1; then
  groupadd --system "$CODEX_USER"
fi
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$STATE_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi
usermod --append --groups "$CONTROL_GROUP" "$SERVICE_USER"
usermod --append --groups "$CODEX_GROUP" "$SERVICE_USER"
if ! id "$CODEX_USER" >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/pa-agent-codex --shell /usr/sbin/nologin \
    --gid "$CODEX_USER" --groups "$CODEX_GROUP" "$CODEX_USER"
else
  usermod --gid "$CODEX_USER" --append --groups "$CODEX_GROUP" "$CODEX_USER"
fi
if ! id "$DASHBOARD_USER" >/dev/null 2>&1; then
  useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin \
    --gid "$DASHBOARD_USER" --groups "$CONTROL_GROUP" "$DASHBOARD_USER"
else
  usermod --gid "$DASHBOARD_USER" --append --groups "$CONTROL_GROUP" "$DASHBOARD_USER"
fi

"$CONDA_EXE" env update --file "$APP_DIR/environment-vps.yml"
LOCK_REQUIREMENTS=$(mktemp)
trap 'rm -f "$LOCK_REQUIREMENTS"' EXIT
"$ENV_UV" export \
  --frozen \
  --extra vps \
  --no-dev \
  --no-emit-project \
  --output-file "$LOCK_REQUIREMENTS"
"$ENV_UV" pip install \
  --python "$ENV_PYTHON" \
  --require-hashes \
  --requirements "$LOCK_REQUIREMENTS"
"$ENV_UV" pip install \
  --python "$ENV_PYTHON" \
  --no-deps \
  "$APP_DIR"
"$ENV_UV" pip check --python "$ENV_PYTHON"
cd /
"$ENV_PYTHON" -m pa_agent.agent_runtime.runtime_audit \
  --source-assets "$APP_DIR/pa_agent/agent_runtime/assets"
chown -R root:root "$APP_DIR"
chmod -R go-w "$APP_DIR"
chmod 0755 "$APP_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 "$STATE_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 \
  "$STATE_DIR/records" "$STATE_DIR/trade_records"
install -d -o root -g "$CODEX_GROUP" -m 0750 /var/lib/pa-agent-codex
install -d -o "$SERVICE_USER" -g "$CODEX_GROUP" -m 2750 \
  /var/lib/pa-agent-codex/jobs
install -d -o root -g "$CODEX_GROUP" -m 2770 \
  /var/lib/pa-agent-codex/inbox \
  /var/lib/pa-agent-codex/results
install -d -o "$CODEX_USER" -g "$CODEX_USER" -m 0700 \
  /var/lib/pa-agent-codex/home \
  /var/lib/pa-agent-codex/runtime \
  /var/lib/pa-agent-codex/outbox
install -d -o root -g "$CODEX_USER" -m 0750 \
  /var/lib/pa-agent-codex/home/rules
install -o root -g "$CODEX_USER" -m 0440 \
  "$APP_DIR/pa_agent/agent_runtime/assets/config.toml" \
  /var/lib/pa-agent-codex/home/config.toml
install -o root -g "$CODEX_USER" -m 0440 \
  "$APP_DIR/pa_agent/agent_runtime/assets/rules/pa-agent.rules" \
  /var/lib/pa-agent-codex/home/rules/pa-agent.rules
install -d -o root -g "$CONTROL_GROUP" -m 2750 /var/lib/pa-agent-control
install -d -o "$SERVICE_USER" -g "$CONTROL_GROUP" -m 2750 \
  /var/lib/pa-agent-control/status
install -d -o "$DASHBOARD_USER" -g "$CONTROL_GROUP" -m 2750 \
  /var/lib/pa-agent-control/control
CONTROL_PATH=/var/lib/pa-agent-control/control/control.json
CONTROL_LOCK=/var/lib/pa-agent-control/control/.control.json.lock
if [ ! -f "$CONTROL_PATH" ]; then
  "$ENV_PYTHON" -c 'import sys; from pathlib import Path; from pa_agent.operations.runtime_state import RuntimeControlStore; RuntimeControlStore(Path(sys.argv[1])).set_desired_state("paused", actor="installer")' "$CONTROL_PATH"
  chown "$DASHBOARD_USER:$CONTROL_GROUP" "$CONTROL_PATH" "$CONTROL_LOCK"
  chmod 0640 "$CONTROL_PATH"
  chmod 0660 "$CONTROL_LOCK"
fi
install -d -o root -g "$SERVICE_USER" -m 0750 "$CONFIG_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 /var/backups/pa-agent

if [ ! -f "$STATE_DIR/config/settings.json" ]; then
  install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 "$STATE_DIR/config"
  install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0600 \
    "$APP_DIR/config/settings.vps.example.json" "$STATE_DIR/config/settings.json"
fi
if [ ! -f "$CONFIG_DIR/pa-agent.env" ]; then
  install -o root -g "$SERVICE_USER" -m 0640 \
    "$APP_DIR/deploy/systemd/pa-agent.env.example" "$CONFIG_DIR/pa-agent.env"
fi
if [ ! -f "$CONFIG_DIR/pa-agent-codex.env" ]; then
  install -o root -g "$CODEX_GROUP" -m 0640 \
    "$APP_DIR/deploy/systemd/pa-agent-codex.env.example" \
    "$CONFIG_DIR/pa-agent-codex.env"
fi

install -o root -g root -m 0644 \
  "$APP_DIR/deploy/systemd/pa-agent.service" /etc/systemd/system/pa-agent.service
install -o root -g root -m 0644 \
  "$APP_DIR/deploy/systemd/pa-agent-dashboard.service" \
  /etc/systemd/system/pa-agent-dashboard.service
install -o root -g root -m 0644 \
  "$APP_DIR/deploy/systemd/pa-agent-codex-worker.service" \
  /etc/systemd/system/pa-agent-codex-worker.service
install -o root -g root -m 0644 \
  "$APP_DIR/deploy/systemd/pa-agent-backup.service" \
  /etc/systemd/system/pa-agent-backup.service
install -o root -g root -m 0644 \
  "$APP_DIR/deploy/systemd/pa-agent-backup.timer" \
  /etc/systemd/system/pa-agent-backup.timer
install -o root -g root -m 0644 \
  "$APP_DIR/deploy/pa-agent-tmpfiles.conf" \
  /etc/tmpfiles.d/pa-agent.conf
install -o root -g root -m 0644 \
  "$APP_DIR/deploy/pa-agent-logrotate" \
  /etc/logrotate.d/pa-agent
systemd-tmpfiles --create /etc/tmpfiles.d/pa-agent.conf
systemctl daemon-reload

printf '%s\n' "Installed without starting the service."
printf '%s\n' "Backup timer is installed but remains disabled until release acceptance."
printf '%s\n' "Create $CONFIG_DIR/api_key and $CONFIG_DIR/dashboard_token, authenticate Codex as $CODEX_USER if enabled, then run preflight checks."
printf '%s\n' "Before enabling services, run runtime_audit --check-permissions and the explicit --live-canary command documented in docs/linux_headless_deployment.md."
printf '%s\n' "Keep Headless paused until the DeepSeek and Codex smoke results are reviewed."
