#!/usr/bin/env bash
#
# Deploy the WeChat GLM Assistant onto a fresh Linux server.
#
# Run from the project root, as root:
#
#   sudo bash deploy/install.sh --server-address 203.0.113.10
#
# The script is idempotent and can be re-run safely. On the first run it
# creates .env from .env.example; fill in your secrets and run it again.
set -euo pipefail

APP_NAME="wechat-glm-assistant"
APP_USER="wechat-glm"
INSTALL_DIR="/opt/wechat-glm-assistant"
NGINX_CONF="/etc/nginx/conf.d/wechat-glm.conf"
SERVICE_UNIT="/etc/systemd/system/wechat-glm-assistant.service"
SERVICE_NAME="wechat-glm-assistant.service"
SERVER_ADDRESS=""
SKIP_PACKAGES=0
ENV_READY=1

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log() { printf '\033[32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[!]\033[0m %s\n' "$*" >&2; }
die() {
  printf '\033[31m[x]\033[0m %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Deploy the WeChat GLM Assistant on a Linux server.

Options:
  --server-address <addr>   Public IP or domain WeChat will call (required).
  --install-dir <path>      Install location (default: /opt/wechat-glm-assistant).
  --user <name>             Service account (default: wechat-glm).
  --skip-packages           Do not install system packages.
  -h, --help                Show this help.

Example:
  sudo bash deploy/install.sh --server-address 203.0.113.10
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --server-address)
        SERVER_ADDRESS="${2:-}"
        shift 2
        ;;
      --install-dir)
        INSTALL_DIR="${2:-}"
        shift 2
        ;;
      --user)
        APP_USER="${2:-}"
        shift 2
        ;;
      --skip-packages)
        SKIP_PACKAGES=1
        shift
        ;;
      -h | --help)
        usage
        exit 0
        ;;
      *)
        die "Unknown option: $1 (use --help)"
        ;;
    esac
  done

  [[ -n "$SERVER_ADDRESS" ]] ||
    die "--server-address is required (the public IP or domain WeChat will call)."
}

install_packages() {
  if [[ $SKIP_PACKAGES -eq 1 ]]; then
    log "Skipping package installation."
    return
  fi

  if command -v dnf >/dev/null 2>&1; then
    log "Installing packages with dnf..."
    dnf install -y python3 python3-pip nginx curl rsync || die "dnf install failed."
  elif command -v apt-get >/dev/null 2>&1; then
    log "Installing packages with apt-get..."
    apt-get update -y
    apt-get install -y python3 python3-venv python3-pip nginx curl rsync ||
      die "apt-get install failed."
  else
    warn "No supported package manager found. Install python3 and nginx manually."
  fi
}

ensure_user() {
  if id "$APP_USER" >/dev/null 2>&1; then
    log "User $APP_USER already exists."
    return
  fi

  log "Creating system user $APP_USER..."
  if [[ -x /usr/sbin/nologin ]]; then
    useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$APP_USER"
  else
    useradd --system --home-dir "$INSTALL_DIR" --shell /sbin/nologin "$APP_USER"
  fi
}

sync_files() {
  log "Copying project files to $INSTALL_DIR ..."
  mkdir -p "$INSTALL_DIR"

  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
      --exclude '.git' \
      --exclude '.venv' \
      --exclude '__pycache__' \
      --exclude '*.pyc' \
      --exclude '.workbuddy' \
      --exclude '.claude' \
      --exclude '.agents' \
      --exclude '.pytest_cache' \
      --exclude '.ruff_cache' \
      --exclude '.mypy_cache' \
      --exclude '.env' \
      "$REPO_ROOT/" "$INSTALL_DIR/"
  else
    warn "rsync not found; falling back to cp (stale files are not pruned)."
    cp -a "$REPO_ROOT/." "$INSTALL_DIR/"
    rm -rf "$INSTALL_DIR/.git" "$INSTALL_DIR/.venv"
    find "$INSTALL_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  fi

  chown -R "$APP_USER:$APP_USER" "$INSTALL_DIR"
}

setup_venv() {
  log "Creating virtualenv and installing dependencies..."
  if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]]; then
    python3 -m venv "$INSTALL_DIR/.venv" || die "Failed to create the virtualenv."
  fi

  "$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip -q
  "$INSTALL_DIR/.venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt" -q
  chown -R "$APP_USER:$APP_USER" "$INSTALL_DIR/.venv"
}

prepare_env() {
  if [[ ! -f "$INSTALL_DIR/.env" ]]; then
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    log "Created $INSTALL_DIR/.env from the example."
  fi
  chown "$APP_USER:$APP_USER" "$INSTALL_DIR/.env"
  chmod 600 "$INSTALL_DIR/.env"

  local missing=()
  grep -qE '^WECHAT_TOKEN=(replace-with-|$)' "$INSTALL_DIR/.env" &&
    missing+=("WECHAT_TOKEN")

  # The model key may sit in LLM_API_KEY or in a provider-specific alias.
  local provider key_found=0
  provider=$(grep -E '^LLM_PROVIDER=' "$INSTALL_DIR/.env" | head -1 | cut -d= -f2- | tr -d '[:space:]')
  provider=${provider:-zhipu}
  for name in LLM_API_KEY "$(echo "$provider" | tr '[:lower:]' '[:upper:]')_API_KEY"; do
    local value
    value=$(grep -E "^${name}=" "$INSTALL_DIR/.env" | head -1 | cut -d= -f2- | tr -d '[:space:]')
    if [[ -n "$value" && "$value" != replace-with-* ]]; then
      key_found=1
      break
    fi
  done
  [[ $key_found -eq 1 ]] || missing+=("LLM_API_KEY (provider=${provider})")

  if [[ ${#missing[@]} -gt 0 ]]; then
    ENV_READY=0
    warn "These values are still unset in $INSTALL_DIR/.env: ${missing[*]}"
  else
    log ".env looks configured (LLM_PROVIDER=${provider})."
  fi
}

install_nginx() {
  log "Installing Nginx config for $SERVER_ADDRESS ..."
  sed "s/your-domain-or-server-ip/${SERVER_ADDRESS}/" \
    "$INSTALL_DIR/deploy/nginx-wechat-glm.conf" >"$NGINX_CONF"

  nginx -t || die "Nginx config test failed."
  systemctl enable nginx >/dev/null 2>&1 || true
  systemctl restart nginx
}

install_service() {
  log "Installing systemd unit..."
  sed -e "s#/opt/wechat-glm-assistant#${INSTALL_DIR}#g" \
    -e "s#^User=wechat-glm#User=${APP_USER}#" \
    "$INSTALL_DIR/deploy/${APP_NAME}.service" >"$SERVICE_UNIT"

  systemctl daemon-reload
  systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || true
  systemctl restart "$SERVICE_NAME"
}

verify() {
  log "Waiting for the service to become healthy..."
  local healthy=0
  for _ in $(seq 1 20); do
    if curl -fsS --max-time 3 http://127.0.0.1:8000/health >/dev/null 2>&1; then
      healthy=1
      break
    fi
    sleep 1
  done
  [[ $healthy -eq 1 ]] ||
    die "Service did not become healthy. Check: journalctl -u $SERVICE_NAME -n 50"

  log "Health check passed."
  if ! curl -fsS --max-time 3 http://127.0.0.1:8000/ready >/dev/null 2>&1; then
    warn "/ready is not 200 yet (check LLM_API_KEY / WECHAT_TOKEN and the other .env values)."
  fi

  echo
  log "Deployment finished."
  cat <<EOF

Configure the WeChat backend with:

  URL:   http://${SERVER_ADDRESS}/wechat
  Token: the same value as WECHAT_TOKEN in ${INSTALL_DIR}/.env
  Mode:  plaintext (明文模式)

Make sure the security group / firewall allows inbound TCP 80.

Useful commands:
  systemctl status ${SERVICE_NAME}
  journalctl -u ${SERVICE_NAME} -f
  ${INSTALL_DIR}/.venv/bin/python ${INSTALL_DIR}/scripts/smoke_wechat.py \\
      --base-url http://${SERVER_ADDRESS} --token <TOKEN>

EOF

  if [[ $ENV_READY -eq 0 ]]; then
    cat <<EOF
The service is installed but not fully configured yet. Edit the env file
and restart it:

  sudo nano ${INSTALL_DIR}/.env
  sudo systemctl restart ${SERVICE_NAME}

EOF
  fi
}

main() {
  parse_args "$@"
  [[ $EUID -eq 0 ]] || die "Run as root: sudo bash deploy/install.sh ..."

  install_packages
  ensure_user
  sync_files
  setup_venv
  prepare_env
  install_nginx
  install_service
  verify
}

main "$@"
