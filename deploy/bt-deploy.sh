#!/usr/bin/env bash
# 宝塔面板专用一键部署脚本
#
# 用法（在宝塔「终端」里，以 root 执行）：
#   cd /www/wwwroot/wechat-glm-bot
#   bash deploy/bt-deploy.sh 你的公网IP
#
# 与 deploy/install.sh 的区别：不会去装系统 Nginx，而是复用宝塔自带的
# Nginx，把配置文件写进宝塔的 vhost 目录，避免和面板抢 80 端口。

set -euo pipefail

SERVER_ADDRESS="${1:-}"
INSTALL_DIR="${2:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
APP_USER="wechat-glm"
SERVICE_NAME="wechat-glm-assistant"
APP_PORT=8000
BT_NGINX_BIN="/www/server/nginx/sbin/nginx"
BT_NGINX_VHOST_DIR="/www/server/panel/vhost/nginx"

log() { printf '\033[32m[ok]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[warn]\033[0m %s\n' "$*"; }
die() {
  printf '\033[31m[error]\033[0m %s\n' "$*" >&2
  exit 1
}

[[ $EUID -eq 0 ]] || die "请用 root 执行（宝塔终端默认就是 root）。"
[[ -n "$SERVER_ADDRESS" ]] || die "用法: bash deploy/bt-deploy.sh 你的公网IP"
[[ -f "$INSTALL_DIR/main.py" ]] || die "在 $INSTALL_DIR 找不到 main.py，请先 cd 到项目目录。"

# ---------- 1. Python ----------
py_ok() {
  # A distro/panel build can pass the version check but miss the ssl module
  # (CentOS 7 ships OpenSSL 1.0.2), which makes pip unusable. Require ssl.
  [[ -n "${1:-}" ]] && [[ -x "$1" ]] && "$1" -c 'import sys, ssl, venv; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null
}

find_python() {
  local candidate
  for candidate in python3.13 python3.12 python3.11 python3.10 python3.9 python3 \
    /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11 \
    /usr/local/bin/python3.10 /usr/local/bin/python3.9 \
    /opt/python/bin/python3 \
    /www/server/pyenv/versions/*/bin/python3 \
    /www/server/pyporject_evn/versions/*/bin/python3 \
    /www/server/python_manager/versions/*/bin/python3 \
    /www/server/panel/pyenv/bin/python3; do
    if [[ "$candidate" == *"*"* ]]; then
      for expanded in $candidate; do
        py_ok "$expanded" && { echo "$expanded"; return 0; }
      done
      continue
    fi
    if command -v "$candidate" >/dev/null 2>&1 || [[ -x "$candidate" ]]; then
      py_ok "$candidate" && { echo "$candidate"; return 0; }
    fi
  done
  return 1
}

PYTHON_BIN=$(find_python || true)

if [[ -z "$PYTHON_BIN" ]]; then
  warn "没找到 Python 3.9+，尝试用包管理器安装 ..."
  if command -v dnf >/dev/null 2>&1; then
    for pkg in python3.11 python3.11-pip python3.12 python3.12-pip python3.9; do
      dnf install -y "$pkg" >/dev/null 2>&1 || true
    done
  elif command -v yum >/dev/null 2>&1; then
    yum install -y epel-release >/dev/null 2>&1 || true
    for pkg in python3.11 python3.11-pip python39 python3; do
      yum install -y "$pkg" >/dev/null 2>&1 || true
    done
  elif command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq >/dev/null 2>&1 || true
    for pkg in python3.11 python3.11-venv python3.10 python3.10-venv python3.9 python3.9-venv python3 python3-venv python3-pip; do
      DEBIAN_FRONTEND=noninteractive apt-get install -y "$pkg" >/dev/null 2>&1 || true
    done
  fi
  PYTHON_BIN=$(find_python || true)
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo
  echo "系统信息：$(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")"
  ls /usr/bin/python* /usr/local/bin/python* 2>/dev/null
  die "找不到可用的 Python 3.9+。请到宝塔「软件商店」安装 Python 3.11（或 Python 项目管理器），装完重跑本脚本。"
fi
log "使用 $("$PYTHON_BIN" -V 2>&1) -> $PYTHON_BIN"

if ! "$PYTHON_BIN" -m venv --help >/dev/null 2>&1; then
  warn "该 Python 缺少 venv 模块，尝试安装对应包 ..."
  if command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; then
    PM=$(command -v dnf || command -v yum)
    "$PM" install -y python3-venv >/dev/null 2>&1 || true
  elif command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv >/dev/null 2>&1 || true
  fi
  "$PYTHON_BIN" -m venv --help >/dev/null 2>&1 || \
    die "这个 Python 没有 venv 模块，请安装 python3-venv 后重试。"
fi

# ---------- 2. 运行用户 ----------
if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER" 2>/dev/null || \
    useradd --system --create-home --shell /sbin/nologin "$APP_USER"
  log "已创建系统用户 $APP_USER"
fi

# ---------- 3. 虚拟环境与依赖 ----------
if [[ -x "$INSTALL_DIR/.venv/bin/python" ]] && \
  ! "$INSTALL_DIR/.venv/bin/python" -c 'import ssl' >/dev/null 2>&1; then
  warn "现有虚拟环境缺少 ssl 模块（pip 无法联网），重建中 ..."
  rm -rf "$INSTALL_DIR/.venv"
fi

if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]]; then
  log "创建虚拟环境 ..."
  "$PYTHON_BIN" -m venv "$INSTALL_DIR/.venv" || die "创建 venv 失败（Debian/Ubuntu 可能缺少 python3-venv）"
fi

log "安装依赖（使用清华镜像加速）..."
"$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip -q \
  -i https://pypi.tuna.tsinghua.edu.cn/simple || true
if ! "$INSTALL_DIR/.venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt" -q \
  -i https://pypi.tuna.tsinghua.edu.cn/simple; then
  die "依赖安装失败。多半是这个 Python 缺少 ssl 模块，请改用 /opt/python 那份独立 Python 后重试。"
fi
log "依赖安装完成"

# ---------- 4. .env ----------
if [[ ! -f "$INSTALL_DIR/.env" ]]; then
  cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
  TOKEN=$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_urlsafe(24))')
  "$PYTHON_BIN" - "$INSTALL_DIR/.env" "$TOKEN" <<'PY'
import sys
path, token = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
text = text.replace("WECHAT_TOKEN=replace-with-your-wechat-token", f"WECHAT_TOKEN={token}")
open(path, "w", encoding="utf-8").write(text)
PY
  log "已从模板生成 .env（WECHAT_TOKEN 已随机生成）"
else
  log ".env 已存在，保留原内容"
fi
chmod 600 "$INSTALL_DIR/.env"
chown -R "$APP_USER:$APP_USER" "$INSTALL_DIR/.venv" "$INSTALL_DIR/.env"

# ---------- 5. systemd ----------
log "写入 systemd 服务 ..."
cat >"/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=WeChat GLM Assistant
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${INSTALL_DIR}
Environment="PATH=${INSTALL_DIR}/.venv/bin"
Environment="PYTHONUNBUFFERED=1"
EnvironmentFile=-${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/.venv/bin/uvicorn main:app --host 127.0.0.1 --port ${APP_PORT} --proxy-headers
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || true
systemctl restart "$SERVICE_NAME"

HEALTHY=0
for _ in $(seq 1 25); do
  if curl -fsS --max-time 3 "http://127.0.0.1:${APP_PORT}/health" >/dev/null 2>&1; then
    HEALTHY=1
    break
  fi
  sleep 1
done
[[ $HEALTHY -eq 1 ]] || die "服务没起来，执行 journalctl -u ${SERVICE_NAME} -n 50 查看原因"
log "后端已就绪：127.0.0.1:${APP_PORT}"

# ---------- 6. Nginx 反向代理 ----------
PROXY_BLOCK=$(cat <<EOF
# 由 deploy/bt-deploy.sh 生成：把 ${SERVER_ADDRESS} 的请求转给 FastAPI
server {
    listen 80;
    server_name ${SERVER_ADDRESS};

    access_log /www/wwwlogs/wechat-glm-access.log;
    error_log  /www/wwwlogs/wechat-glm-error.log;

    client_max_body_size 2m;

    location / {
        proxy_pass http://127.0.0.1:${APP_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Request-ID \$request_id;
        proxy_read_timeout 15s;
        proxy_send_timeout 15s;
    }
}
EOF
)

if [[ -x "$BT_NGINX_BIN" ]]; then
  log "检测到宝塔 Nginx，写入 ${BT_NGINX_VHOST_DIR}/wechat-glm.conf"
  mkdir -p "$BT_NGINX_VHOST_DIR"
  printf '%s\n' "$PROXY_BLOCK" >"${BT_NGINX_VHOST_DIR}/wechat-glm.conf"
  "$BT_NGINX_BIN" -t || die "Nginx 配置检查失败，请把上面的报错发我"
  "$BT_NGINX_BIN" -s reload
  NGINX_MODE="宝塔 Nginx"
elif command -v nginx >/dev/null 2>&1; then
  log "写入 /etc/nginx/conf.d/wechat-glm.conf"
  printf '%s\n' "$PROXY_BLOCK" | sed 's#/www/wwwlogs/#/var/log/nginx/#g' \
    >/etc/nginx/conf.d/wechat-glm.conf
  nginx -t || die "Nginx 配置检查失败"
  systemctl reload nginx || nginx -s reload
  NGINX_MODE="系统 Nginx"
else
  warn "没有找到 Nginx，跳过反向代理。你可以先用 http://${SERVER_ADDRESS}:${APP_PORT} 测试（需放行端口）。"
  NGINX_MODE="未配置"
fi

# ---------- 7. 外网自检 ----------
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "http://${SERVER_ADDRESS}/health" || true)

echo
log "部署完成（${NGINX_MODE}）"
cat <<EOF

  后端服务：${SERVICE_NAME}（systemd，开机自启）
  安装目录：${INSTALL_DIR}
  配置文件：${INSTALL_DIR}/.env
  查看日志：journalctl -u ${SERVICE_NAME} -f

  外网 /health 返回：${code:-无响应}

  ── 还差一步：填模型 Key ──
  编辑 ${INSTALL_DIR}/.env，把这一行改成你的 DeepSeek Key：
      LLM_API_KEY=sk-xxxxxxxxxxxxxxxx
  然后执行：
      systemctl restart ${SERVICE_NAME}

  ── 微信测试号后台填 ──
  URL:   http://${SERVER_ADDRESS}/wechat
  Token: $(grep -E '^WECHAT_TOKEN=' "$INSTALL_DIR/.env" | cut -d= -f2-)
  消息加解密方式：明文模式

EOF
