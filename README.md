# 微信贾维斯

一个基于 FastAPI 的微信公众号测试号回调服务。微信把消息回调到服务器，服务调用智谱 GLM 文本/视觉模型，再用微信被动回复返回到聊天窗口。

## 功能

- 支持微信公众号服务器校验：`GET /wechat`
- 支持微信消息回调：`POST /wechat`
- 支持文字消息：调用 `TEXT_MODEL`
- 支持短期会话记忆：同一微信用户最近几轮文字、语音识别、链接、图片分析和位置消息会带入模型上下文
- 支持“帮助”和“清空会话/重置会话”指令
- 支持图片消息：把微信 `PicUrl` 交给 `VISION_MODEL` 分析
- 支持语音识别文本、链接、位置、关注/扫码事件的兜底回复
- 支持签名时间窗口校验，降低重放风险
- 支持请求体大小限制
- 支持按微信用户限流，降低刷屏或误用导致的模型调用风险
- 支持模型输入长度裁剪，避免超长文本带来过高成本和延迟
- 支持短期重复消息缓存，避免微信重试时重复调用 GLM
- 支持进行中消息去重，并发收到相同 `MsgId` 时只调用一次 GLM
- 支持模型调用并发上限，避免突发请求耗尽线程和 API 配额
- 支持模型调用熔断器：连续失败达到阈值后短暂停止调用 GLM，避免故障期间持续超时和放大排队
- 对连接类瞬时错误（连接失败/连接超时）自动重试一次，读超时和业务错误不重试
- 支持可选的本地关键词黑名单兜底审核，命中时用固定安全话术替换模型回复
- 支持“删除我的数据”指令，一次性清空指定用户的会话记忆和限流记录
- 支持可选的飞书自定义机器人告警：熔断器打开时异步推送告警，并做节流防止刷屏
- 支持可选的微信客服消息异步兜底：被动回复被截断，或命中兜底文案时，后台再尝试通过客服消息补发完整/真实回答
- 支持可选的微信自定义菜单（帮助/清空会话/关于），点击事件按 `EventKey` 分发
- 提供健康检查：`GET /health`
- 提供就绪检查：`GET /ready`，用于确认必要配置和 SDK 依赖是否齐全
- 提供运行指标：`GET /metrics`，用于查看消息量、缓存命中、限流和错误计数
- 提供 Prometheus 格式指标：`GET /metrics/prometheus`
- 提供根路径摘要：`GET /`，方便浏览器直接确认服务存在
- 提供离线配置检查脚本：`scripts/check_config.py`
- 提供依赖漏洞扫描（`pip-audit`），已接入 `scripts/run_checks.py`
- 提供 Token 轮换脚本：`scripts/rotate_tokens.py`
- 提供微信自定义菜单管理脚本：`scripts/setup_wechat_menu.py`
- 支持通过 `ENABLE_DOCS=false` 关闭 FastAPI 自动文档
- 每个响应都会带 `X-Request-ID` 和 `X-Response-Time-Ms`，方便排查请求链路
- Nginx 会生成并转发 `X-Request-ID`，方便把网关访问日志、应用日志和错误响应串联起来
- 每个响应都会带基础安全头，如 `X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy`
- 未捕获异常会返回统一 `500` JSON，并带上 `request_id`
- 管理接口（`/ready`、`/metrics`、`/metrics/prometheus`）的鉴权成功/失败都会记录审计日志（路径、来源 IP）

会话记忆是单进程内存缓存，服务重启后会清空。图片消息会记住“用户发过图片”和当时的视觉分析结果，不会把图片文件本身持久保存。多进程或多台机器部署时，如果需要稳定共享上下文，后续应接入 Redis 或数据库。

## 架构

```text
微信用户
 -> 微信公众号测试号
 -> http://your-server-ip/wechat
 -> Nginx 80
 -> FastAPI 127.0.0.1:8000
 -> 智谱 GLM 文本/视觉模型
 -> 微信聊天窗口
```

## 本地开发

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements-dev.txt
cp .env.example .env
```

编辑 `.env`，至少填写：

```env
ZHIPU_API_KEY=你的智谱 API Key
WECHAT_TOKEN=你在微信测试号后台填写的 Token
```

`WECHAT_TOKEN` 和 `ADMIN_TOKEN` 建议使用随机长字符串；公网部署时，`WECHAT_TOKEN` 建议至少 16 位，`ADMIN_TOKEN` 建议至少 24 位。自动文档和 URL 管理令牌默认关闭；本地需要接口文档时可在自己的 `.env` 中设置 `ENABLE_DOCS=true`。

启动服务：

```bash
uvicorn main:app --host 127.0.0.1 --port 8000
```

健康检查：

```bash
curl http://127.0.0.1:8000/
curl http://127.0.0.1:8000/health
```

就绪检查：

```bash
curl http://127.0.0.1:8000/ready
```

`/health` 只表示服务进程活着；`/ready` 会在 `WECHAT_TOKEN`、`ZHIPU_API_KEY` 或 `zhipuai` SDK 缺失时返回 `503`。
如果设置了 `ADMIN_TOKEN`，访问 `/ready` 需要带 `X-Admin-Token` 请求头。只有显式设置 `ALLOW_ADMIN_TOKEN_QUERY=true` 时才允许使用 `?admin_token=...` 查询参数，正式环境应使用请求头传递。

查看运行指标：

```bash
curl http://127.0.0.1:8000/metrics
```

`/metrics` 返回 JSON，包含 HTTP 请求数、非法请求 ID 次数、未捕获异常次数、消息总数、各消息类型计数、文本/视觉模型调用次数、模型调用失败次数、模型忙碌次数、模型调用耗时、缓存与进行中消息命中、限流次数，以及当前内存状态大小。如果设置了 `ADMIN_TOKEN`，访问 `/metrics` 也需要带 `X-Admin-Token` 请求头。正式公网环境也可在 Nginx 侧增加 IP 白名单或内网访问限制。

查看 Prometheus 格式指标（同样需要 `X-Admin-Token`，如果设置了 `ADMIN_TOKEN`）：

```bash
curl http://127.0.0.1:8000/metrics/prometheus
```


运行测试：

```bash
python -m pytest
```

一键运行本地检查：

```bash
python scripts/run_checks.py
```

检查本地配置文件：

```bash
python scripts/check_config.py --skip-dependencies
```

如果希望 warning 也让检查失败，可用于 CI 或上线前把关：

```bash
python scripts/check_config.py --skip-dependencies --strict-warnings
```

安装依赖后可以连 Python 包一起检查：

```bash
python scripts/check_config.py
```

如果当前服务已经启动，也可以让统一检查脚本顺便跑 smoke：

```bash
python scripts/run_checks.py --smoke --smoke-base-url http://127.0.0.1:8000
```

本地启动服务后，可以用 smoke 脚本模拟微信签名和回调：

```bash
python scripts/smoke_wechat.py --base-url http://127.0.0.1:8000 --token 你的微信Token
```

如果当前目录有 `.env`，smoke 脚本会自动读取 `WECHAT_TOKEN`、`ADMIN_TOKEN` 和 `SMOKE_BASE_URL`。环境变量优先于 `.env`，命令行参数优先于环境变量。

```bash
python scripts/smoke_wechat.py --base-url http://127.0.0.1:8000
```

默认 smoke 只发送 `帮助`、位置、`清空会话` 等不会调用 GLM 的消息，适合部署后快速确认签名、路由和 XML 回复正常。如果要连 GLM 一起测：

```bash
python scripts/smoke_wechat.py --base-url http://127.0.0.1:8000 --token 你的微信Token --with-model
```

如果希望 smoke 同时要求 `/ready` 通过：

```bash
python scripts/smoke_wechat.py --base-url http://127.0.0.1:8000 --token 你的微信Token --require-ready
```

如果服务设置了 `ADMIN_TOKEN`：

```bash
python scripts/smoke_wechat.py --base-url http://127.0.0.1:8000 --token 你的微信Token --require-ready --admin-token 你的诊断Token
```

## 配置项

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `ZHIPU_API_KEY` | 智谱 API Key | 空 |
| `WECHAT_TOKEN` | 微信公众号后台配置的 Token，公网建议至少 16 位随机字符串 | 空 |
| `ADMIN_TOKEN` | `/ready` 和 `/metrics` 的可选访问令牌，公网建议至少 24 位随机字符串，留空则不启用 | 空 |
| `ENABLE_DOCS` | 是否启用 `/docs`、`/redoc`、`/openapi.json` | `false` |
| `ALLOW_ADMIN_TOKEN_QUERY` | 是否允许用 `?admin_token=...` 访问诊断接口；建议使用 `X-Admin-Token` 请求头 | `false` |
| `LOG_LEVEL` | 服务日志级别，可选 `DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL` | `INFO` |
| `TEXT_MODEL` | 文本模型 | `glm-4-flash` |
| `VISION_MODEL` | 视觉模型 | `glm-4v-flash` |
| `BOT_NAME` | 关注欢迎语中的助手名 | `贾维斯` |
| `GLM_TIMEOUT_SECONDS` | GLM 调用超时 | `4` |
| `MODEL_MAX_TOKENS` | 单次模型回复的最大 token 数，调小可降低延迟和成本 | `800` |
| `MODEL_MAX_CONCURRENT_CALLS` | 单进程同时执行的 GLM 调用上限，超过时快速返回忙碌提示 | `4` |
| `MAX_REPLY_CHARS` | 微信文本回复最大字符数 | `650` |
| `MAX_MODEL_INPUT_CHARS` | 单次发给模型的用户文本最大字符数，设为 `0` 可关闭裁剪 | `4000` |
| `MAX_REQUEST_BYTES` | 微信回调请求体最大字节数 | `65536` |
| `WECHAT_SIGNATURE_TOLERANCE_SECONDS` | 签名时间戳容忍窗口，设为 `0` 可关闭 | `600` |
| `DUPLICATE_CACHE_TTL_SECONDS` | 重复消息缓存时间，设为 `0` 可关闭 | `300` |
| `DUPLICATE_CACHE_MAX_SIZE` | 重复消息缓存条数上限 | `1024` |
| `CONVERSATION_MEMORY_TTL_SECONDS` | 会话记忆保留秒数，设为 `0` 可关闭 | `1800` |
| `CONVERSATION_MEMORY_MAX_TURNS` | 每个用户最多保留几轮问答，设为 `0` 可关闭 | `6` |
| `CONVERSATION_MEMORY_MAX_USERS` | 会话记忆最多保留多少个用户 | `1024` |
| `RATE_LIMIT_WINDOW_SECONDS` | 单用户限流窗口秒数，设为 `0` 可关闭 | `60` |
| `RATE_LIMIT_MAX_MESSAGES` | 单用户窗口内最多处理几条重消息，设为 `0` 可关闭 | `20` |
| `RATE_LIMIT_MAX_USERS` | 限流状态最多保留多少个用户 | `2048` |
| `SYSTEM_PROMPT` | 文本模型系统提示词 | 见 `.env.example` |
| `IMAGE_PROMPT` | 视觉模型分析图片时使用的提示词 | 见 `.env.example` |
| `MODERATION_BLOCKLIST_FILE` | 本地关键词黑名单文件路径（每行一个关键词），留空关闭内容审核兜底 | 空 |
| `MODEL_CIRCUIT_BREAKER_THRESHOLD` | 滚动窗口内累计多少次模型调用失败后打开熔断器 | `5` |
| `MODEL_CIRCUIT_BREAKER_WINDOW_SECONDS` | 熔断器失败计数的滚动窗口秒数 | `60` |
| `MODEL_CIRCUIT_BREAKER_COOLDOWN_SECONDS` | 熔断器打开后拒绝调用的冷却秒数 | `30` |
| `ALERT_WEBHOOK_URL` | 飞书自定义机器人 webhook 地址，用于熔断器打开等告警，留空关闭告警 | 空 |
| `ALERT_MIN_INTERVAL_SECONDS` | 告警最小发送间隔，防止刷屏 | `300` |
| `WECHAT_APP_ID` | 微信公众号 AppID，用于获取 `access_token`（自定义菜单、客服消息） | 空 |
| `WECHAT_APP_SECRET` | 微信公众号 AppSecret，用于获取 `access_token` | 空 |
| `ENABLE_CUSTOMER_SERVICE_FALLBACK` | 是否在回复被截断或走兜底文案时，通过客服消息异步补发完整/真实回答；需要同时配置 `WECHAT_APP_ID`/`WECHAT_APP_SECRET` | `false` |

## 微信内指令

- `帮助`、`help`、`/help`：查看助手能力说明
- `清空会话`、`重置会话`、`reset`、`/reset`：清空当前微信用户的上下文记忆
- `删除我的数据`、`delete my data`：清空当前微信用户的会话记忆和限流记录

## 微信主动消息能力（可选）

以下功能依赖 `WECHAT_APP_ID`/`WECHAT_APP_SECRET`，用于调用微信 `access_token` 相关接口。不配置时这些功能自动跳过，不影响被动回复主流程。

### 客服消息异步兜底

设置 `ENABLE_CUSTOMER_SERVICE_FALLBACK=true` 并配置好 `WECHAT_APP_ID`/`WECHAT_APP_SECRET` 后：

- 如果被动回复因为超过 `MAX_REPLY_CHARS` 被截断，服务会在后台用客服消息把未截断的完整内容补发给用户。
- 如果本次回复命中了熔断器兜底、模型忙碌兜底或其他异常兜底文案，服务会在后台重新尝试一次模型调用，成功则把真实回答通过客服消息补发（只重试一次，不循环）。

客服消息需要用户在 48 小时内与公众号有过交互，这是微信平台本身的限制。

### 自定义菜单

创建默认菜单（帮助 / 清空会话 / 关于）：

```bash
python scripts/setup_wechat_menu.py --create
```

删除当前菜单：

```bash
python scripts/setup_wechat_menu.py --delete
```

菜单点击事件会带上 `EventKey`，服务按 `MENU_HELP`/`MENU_RESET`/`MENU_ABOUT` 分发到已有的帮助说明、清空会话和关于文案。这是一次性管理操作，需要手动运行，服务启动时不会自动调用微信菜单接口。

## 运维辅助脚本

### 依赖漏洞扫描

`scripts/run_checks.py` 默认会跑一次 `pip-audit`。如果暂时不需要（例如离线环境），可以跳过：

```bash
python scripts/run_checks.py --skip-audit
```

### Token 轮换

生成新的 `WECHAT_TOKEN`/`ADMIN_TOKEN`（默认只打印，不修改文件）：

```bash
python scripts/rotate_tokens.py
```

直接写入 `.env`：

```bash
python scripts/rotate_tokens.py --apply --env-file .env
```

只轮换其中一个：

```bash
python scripts/rotate_tokens.py --apply --env-file .env --wechat-token
```

轮换 `WECHAT_TOKEN` 后，别忘了同步更新微信公众号后台的 Token 并重启服务；轮换 `ADMIN_TOKEN` 后重启服务即可生效。

## 服务器部署

项目自带一键部署脚本 `deploy/install.sh`，适合在一台全新的 Linux 服务器上（CentOS / AlmaLinux / Rocky 用 `dnf`，Debian / Ubuntu 用 `apt`）从零装好 systemd 服务、Nginx 反向代理和虚拟环境。

准备一台有公网 IP 的服务器，把项目传到服务器后执行：

```bash
cd wechat-glm-bot
sudo bash deploy/install.sh --server-address 你的公网IP或域名
```

`--server-address` 是微信要回调的地址，脚本会把它写进 Nginx 的 `server_name`。

脚本会自动完成：

- 安装 `python3`、`nginx`、`curl`、`rsync`（可用 `--skip-packages` 跳过）
- 创建系统用户 `wechat-glm` 和安装目录 `/opt/wechat-glm-assistant`
- 同步项目文件（自动排除 `.git`、`.venv`、`.env`、缓存目录）
- 建虚拟环境并安装 `requirements.txt`
- 首次运行从 `.env.example` 生成 `.env`，权限设为 `600`
- 写入 Nginx 配置并 `nginx -t` 校验后重启
- 写入并启用 systemd 服务，等待 `/health` 通过

首次运行后 `.env` 里还没有真实密钥，脚本会提示你补齐并重启：

```bash
sudo nano /opt/wechat-glm-assistant/.env
sudo systemctl restart wechat-glm-assistant
```

脚本可重复执行（幂等），改完代码重新 `sudo bash deploy/install.sh --server-address ...` 即可完成更新和重启。

> 安全组 / 防火墙需要放行入站 TCP 80，否则微信回调会连不上。

公网部署建议设置 `ADMIN_TOKEN`，把 `ENABLE_DOCS=false`，并设置 `ALLOW_ADMIN_TOKEN_QUERY=false`；配置检查脚本会对公开诊断接口、URL 查询参数令牌和自动文档给出 warning。
上线前可使用 `python scripts/check_config.py --strict-warnings`，或在统一检查中使用 `python scripts/run_checks.py --strict-config`，让 warning 也阻断发布。

Nginx 会把 `$request_id` 作为 `X-Request-ID` 传给 FastAPI；排查线上问题时，可用响应头里的 `X-Request-ID` 对照 `/var/log/nginx/wechat-glm-access.log` 和 `journalctl -u wechat-glm-assistant -f`。访问日志只记录路径，不记录 query string，避免临时诊断参数落盘。

查看日志：

```bash
journalctl -u wechat-glm-assistant -f
```

### 手动部署（不使用脚本）

如果你想自己控制每一步：

```bash
sudo dnf install -y git curl nginx python3 python3-pip
cd ~/wechat-glm-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
cp .env.example .env
nano .env
python scripts/check_config.py
```

手动启动验证：

```bash
uvicorn main:app --host 127.0.0.1 --port 8000 --proxy-headers
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

把 `deploy/nginx-wechat-glm.conf` 里的 `your-domain-or-server-ip` 改成真实地址后安装：

```bash
sudo cp deploy/nginx-wechat-glm.conf /etc/nginx/conf.d/wechat-glm.conf
sudo nginx -t && sudo systemctl enable --now nginx && sudo systemctl restart nginx
```

把 `deploy/wechat-glm-assistant.service` 里的 `User=` 和 `/opt/wechat-glm-assistant` 改成你的真实用户和路径后安装：

```bash
sudo cp deploy/wechat-glm-assistant.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wechat-glm-assistant
```

### 部署后验证

部署完成后，可以在服务器上或本机直接验证外网入口：

```bash
python scripts/smoke_wechat.py --base-url http://你的服务器IP --token 你的微信Token
```

如果服务器 `.env` 已经填好 `ZHIPU_API_KEY` 且依赖安装完整，可以加上 `--require-ready`：

```bash
python scripts/smoke_wechat.py --base-url http://你的服务器IP --token 你的微信Token --require-ready
```

如果服务器配置了 `ADMIN_TOKEN`：

```bash
python scripts/smoke_wechat.py --base-url http://你的服务器IP --token 你的微信Token --require-ready --admin-token 你的诊断Token
```

### 升级到 HTTPS

微信正式号要求回调走 HTTPS。拿到域名后，推荐用 Certbot 申请免费证书：

```bash
sudo dnf install -y certbot python3-certbot-nginx   # Debian/Ubuntu 用 apt
sudo certbot --nginx -d your.domain.com
```

Certbot 会自动改写 Nginx 配置并加上自动续期。之后把微信后台的 URL 从 `http://` 换成 `https://` 即可。

## 微信测试号配置

在微信公众号测试号后台填写：

```text
URL: http://你的服务器IP/wechat
Token: 和 .env 里的 WECHAT_TOKEN 完全一致
消息加解密方式: 明文模式
```

URL 必须是公网可达的地址。如果服务器还没备案，直接用公网 IP 即可，不需要域名。

第一版是明文 HTTP 测试部署。正式使用建议尽快配置 HTTPS，并把 Nginx `server_name` 改成域名（见上文「升级到 HTTPS」）。

## 常见问题

微信后台保存 URL 失败：

- 检查服务器安全组 / 防火墙是否放行 80
- 检查 `sudo nginx -t` 是否通过
- 检查 `.env` 里的 `WECHAT_TOKEN` 是否和后台 Token 一致
- 检查服务是否运行：`curl http://127.0.0.1:8000/health`
- 如果服务器时间不准，签名时间窗口可能失败；先同步 NTP：`sudo timedatectl set-ntp true`，或临时把 `WECHAT_SIGNATURE_TOLERANCE_SECONDS=0`
- 用 `sudo bash deploy/install.sh --server-address ...` 时，确认传入的地址和微信后台填写的一致

微信收到“处理慢了或出错了”：

- 检查 `ZHIPU_API_KEY` 是否填对
- 检查模型名是否可用
- 查看日志：`journalctl -u wechat-glm-assistant -f`
- 被动回复需要尽快返回；如果模型经常超时，先降低 `GLM_TIMEOUT_SECONDS` 或 `MODEL_MAX_TOKENS`，后续再升级为异步客服消息方案
- 确认 `/ready` 返回 200：`curl http://127.0.0.1:8000/ready`
