import hashlib
import hmac
import importlib.util
import logging
import os
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache
from threading import BoundedSemaphore, Event, Lock, Thread
from typing import Any, Dict, List, Optional, Tuple

import httpx
from defusedxml.common import DefusedXmlException
from defusedxml import ElementTree
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse, Response

load_dotenv()

LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def parse_log_level(value: Optional[str]) -> int:
    if not value:
        return logging.INFO
    return LOG_LEVELS.get(value.strip().upper(), logging.INFO)


_raw_log_level = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=parse_log_level(_raw_log_level),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("wechat_glm_assistant")
if _raw_log_level and _raw_log_level.strip().upper() not in LOG_LEVELS:
    logger.warning("Invalid LOG_LEVEL=%r, using INFO", _raw_log_level)

# httpx logs every request URL at INFO, and our outbound URLs carry secrets in
# the query string (appid/secret, access_token, the alert webhook path). Keep it
# at WARNING regardless of LOG_LEVEL so those never reach the log.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

DEFAULT_BOT_NAME = "贾维斯"
DEFAULT_SYSTEM_PROMPT = (
    "你是贾维斯，一个运行在微信里的中文智能助手。"
    "回答要可靠、简洁、友好。遇到不确定的信息要说明不确定，不要编造。"
)
DEFAULT_IMAGE_PROMPT = (
    "请用中文简洁分析这张微信图片。如果图片里有文字，也请尽量识别出来。"
)
FALLBACK_REPLY = "这条消息我刚才处理慢了或出错了，请稍后再试一次。"
HELP_REPLY = (
    "我是贾维斯，可以帮你回答文字问题、分析图片、理解微信语音识别文本，也能处理链接和位置消息。\n"
    "发送“清空会话”可以忘掉当前聊天上下文；发送“帮助”可以再次看到这条说明。"
)
HELP_COMMANDS = {"帮助", "help", "/help", "？", "?"}
RESET_COMMANDS = {"清空", "清空会话", "重置", "重置会话", "reset", "/reset"}
DATA_DELETE_COMMANDS = {"删除我的数据", "delete my data"}
RATE_LIMIT_REPLY = "你发得有点快，我需要缓一下再继续处理。请稍后再试。"
MODEL_BUSY_REPLY = "现在同时处理的消息有点多，请稍后再试一次。"
MODEL_CIRCUIT_OPEN_REPLY = "现在模型暂时不太稳定，我先休息一下，请稍后再试。"
MODERATION_BLOCKED_REPLY = "这条回复可能涉及敏感内容，我先不展示了。你可以换个方式问我。"
MENU_KEY_HELP = "MENU_HELP"
MENU_KEY_RESET = "MENU_RESET"
MENU_KEY_ABOUT = "MENU_ABOUT"
WECHAT_ACCESS_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
WECHAT_CUSTOM_MESSAGE_URL = "https://api.weixin.qq.com/cgi-bin/message/custom/send"
# After a failed refresh, hold off this long before anyone tries again, so a
# WeChat outage costs one 5s request per window instead of one per caller.
ACCESS_TOKEN_FAILURE_BACKOFF_SECONDS = 10.0
# Longest a caller waits behind another thread's in-flight refresh.
ACCESS_TOKEN_REFRESH_WAIT_SECONDS = 6.0
STARTED_AT = time.time()
REQUEST_ID_MAX_LENGTH = 64
REQUEST_ID_ALLOWED_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "-_.:/"
)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "X-Permitted-Cross-Domain-Policies": "none",
    "X-Robots-Tag": "noindex, nofollow",
}
METRIC_NAMES = (
    "invalid_signature_total",
    "http_requests_total",
    "payload_too_large_total",
    "invalid_request_id_total",
    "unhandled_errors_total",
    "xml_parse_errors_total",
    "wechat_messages_total",
    "wechat_replies_total",
    "reply_cache_hits_total",
    "reply_inflight_hits_total",
    "rate_limited_total",
    "reply_errors_total",
    "model_text_calls_total",
    "model_image_calls_total",
    "model_call_errors_total",
    "model_busy_total",
    "model_input_truncated_total",
    "moderation_blocked_total",
    "model_circuit_open_total",
    "model_circuit_rejected_total",
    "customer_service_sent_total",
    "customer_service_failed_total",
)


@dataclass(frozen=True)
class Settings:
    zhipu_api_key: str
    wechat_token: str
    admin_token: str = ""
    enable_docs: bool = False
    allow_admin_token_query: bool = False
    text_model: str = "glm-4-flash"
    vision_model: str = "glm-4v-flash"
    bot_name: str = DEFAULT_BOT_NAME
    glm_timeout_seconds: float = 4.0
    model_max_tokens: int = 800
    model_max_concurrent_calls: int = 4
    max_reply_chars: int = 650
    max_model_input_chars: int = 4000
    max_request_bytes: int = 64 * 1024
    signature_tolerance_seconds: int = 600
    duplicate_cache_ttl_seconds: int = 300
    duplicate_cache_max_size: int = 1024
    conversation_memory_ttl_seconds: int = 1800
    conversation_memory_max_turns: int = 6
    conversation_memory_max_users: int = 1024
    rate_limit_window_seconds: int = 60
    rate_limit_max_messages: int = 20
    rate_limit_max_users: int = 2048
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    image_prompt: str = DEFAULT_IMAGE_PROMPT
    moderation_blocklist_file: str = ""
    model_circuit_breaker_threshold: int = 5
    model_circuit_breaker_window_seconds: int = 60
    model_circuit_breaker_cooldown_seconds: int = 30
    alert_webhook_url: str = ""
    alert_min_interval_seconds: int = 300
    wechat_app_id: str = ""
    wechat_app_secret: str = ""
    enable_customer_service_fallback: bool = False


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r, using default %s", name, raw, default)
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r, using default %s", name, raw, default)
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    logger.warning("Invalid %s=%r, using default %s", name, raw, default)
    return default


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        zhipu_api_key=os.getenv("ZHIPU_API_KEY", ""),
        wechat_token=os.getenv("WECHAT_TOKEN", ""),
        admin_token=os.getenv("ADMIN_TOKEN", ""),
        enable_docs=_bool_env("ENABLE_DOCS", False),
        allow_admin_token_query=_bool_env("ALLOW_ADMIN_TOKEN_QUERY", False),
        text_model=os.getenv("TEXT_MODEL", "glm-4-flash"),
        vision_model=os.getenv("VISION_MODEL", "glm-4v-flash"),
        bot_name=os.getenv("BOT_NAME", DEFAULT_BOT_NAME),
        glm_timeout_seconds=max(0.5, _float_env("GLM_TIMEOUT_SECONDS", 4.0)),
        model_max_tokens=max(1, _int_env("MODEL_MAX_TOKENS", 800)),
        model_max_concurrent_calls=max(
            1, _int_env("MODEL_MAX_CONCURRENT_CALLS", 4)
        ),
        max_reply_chars=max(1, _int_env("MAX_REPLY_CHARS", 650)),
        max_model_input_chars=max(0, _int_env("MAX_MODEL_INPUT_CHARS", 4000)),
        max_request_bytes=max(1024, _int_env("MAX_REQUEST_BYTES", 64 * 1024)),
        signature_tolerance_seconds=max(
            0, _int_env("WECHAT_SIGNATURE_TOLERANCE_SECONDS", 600)
        ),
        duplicate_cache_ttl_seconds=max(
            0, _int_env("DUPLICATE_CACHE_TTL_SECONDS", 300)
        ),
        duplicate_cache_max_size=max(1, _int_env("DUPLICATE_CACHE_MAX_SIZE", 1024)),
        conversation_memory_ttl_seconds=max(
            0, _int_env("CONVERSATION_MEMORY_TTL_SECONDS", 1800)
        ),
        conversation_memory_max_turns=max(
            0, _int_env("CONVERSATION_MEMORY_MAX_TURNS", 6)
        ),
        conversation_memory_max_users=max(
            1, _int_env("CONVERSATION_MEMORY_MAX_USERS", 1024)
        ),
        rate_limit_window_seconds=max(0, _int_env("RATE_LIMIT_WINDOW_SECONDS", 60)),
        rate_limit_max_messages=max(0, _int_env("RATE_LIMIT_MAX_MESSAGES", 20)),
        rate_limit_max_users=max(1, _int_env("RATE_LIMIT_MAX_USERS", 2048)),
        system_prompt=os.getenv("SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT),
        image_prompt=os.getenv("IMAGE_PROMPT", DEFAULT_IMAGE_PROMPT),
        moderation_blocklist_file=os.getenv("MODERATION_BLOCKLIST_FILE", ""),
        model_circuit_breaker_threshold=max(
            1, _int_env("MODEL_CIRCUIT_BREAKER_THRESHOLD", 5)
        ),
        model_circuit_breaker_window_seconds=max(
            1, _int_env("MODEL_CIRCUIT_BREAKER_WINDOW_SECONDS", 60)
        ),
        model_circuit_breaker_cooldown_seconds=max(
            1, _int_env("MODEL_CIRCUIT_BREAKER_COOLDOWN_SECONDS", 30)
        ),
        alert_webhook_url=os.getenv("ALERT_WEBHOOK_URL", ""),
        alert_min_interval_seconds=max(0, _int_env("ALERT_MIN_INTERVAL_SECONDS", 300)),
        wechat_app_id=os.getenv("WECHAT_APP_ID", ""),
        wechat_app_secret=os.getenv("WECHAT_APP_SECRET", ""),
        enable_customer_service_fallback=_bool_env(
            "ENABLE_CUSTOMER_SERVICE_FALLBACK", False
        ),
    )


@lru_cache(maxsize=1)
def get_zhipu_client() -> Any:
    settings = get_settings()
    if not settings.zhipu_api_key:
        raise RuntimeError("ZHIPU_API_KEY is not configured")

    try:
        from zhipuai import ZhipuAI
    except ImportError as exc:
        raise RuntimeError(
            "zhipuai is not installed; run `pip install -r requirements.txt`"
        ) from exc

    timeout = httpx.Timeout(
        timeout=settings.glm_timeout_seconds,
        connect=min(3.0, settings.glm_timeout_seconds),
    )
    return ZhipuAI(api_key=settings.zhipu_api_key, timeout=timeout, max_retries=0)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings = get_settings()
    if not settings.wechat_token:
        logger.error("WECHAT_TOKEN is not configured — WeChat signature verification will always fail")
    if not settings.zhipu_api_key:
        logger.error("ZHIPU_API_KEY is not configured — all model calls will fail at runtime")
    if not zhipuai_package_available():
        logger.error("zhipuai package is not installed — run: pip install -r requirements.txt")
    if settings.wechat_token and settings.zhipu_api_key and zhipuai_package_available():
        logger.info(
            "service started text_model=%s vision_model=%s bot_name=%s",
            settings.text_model,
            settings.vision_model,
            settings.bot_name,
        )
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    docs_url = "/docs" if settings.enable_docs else None
    redoc_url = "/redoc" if settings.enable_docs else None
    openapi_url = "/openapi.json" if settings.enable_docs else None
    return FastAPI(
        title="WeChat GLM Assistant",
        version="0.2.0",
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        lifespan=_lifespan,
    )


app = create_app()


class ModelBusyError(RuntimeError):
    pass


class ModelUnavailableError(RuntimeError):
    pass


@dataclass
class InflightReply:
    event: Event
    reply: str = ""


_reply_cache: "OrderedDict[str, Tuple[float, str]]" = OrderedDict()
_reply_cache_lock = Lock()
_inflight_replies: Dict[str, InflightReply] = {}
_inflight_replies_lock = Lock()
_conversation_cache: "OrderedDict[str, Tuple[float, List[Dict[str, str]]]]" = (
    OrderedDict()
)
_conversation_lock = Lock()
_rate_limit_cache: "OrderedDict[str, Tuple[float, List[float]]]" = OrderedDict()
_rate_limit_lock = Lock()
_metrics = {name: 0 for name in METRIC_NAMES}
_message_type_metrics: Dict[str, int] = {}
_model_duration_metrics: Dict[str, Dict[str, float]] = {
    "text": {"total_ms": 0.0, "max_ms": 0.0},
    "image": {"total_ms": 0.0, "max_ms": 0.0},
}
_metrics_lock = Lock()
_model_gate: Optional[BoundedSemaphore] = None
_model_gate_lock = Lock()
_circuit_state: Dict[str, Any] = {"failures": [], "open_until": 0.0}
_circuit_lock = Lock()
_access_token_cache: Dict[str, Any] = {"token": "", "expires_at": 0.0, "retry_after": 0.0}
_access_token_lock = Lock()
# Serializes token refreshes so a burst of callers triggers one WeChat fetch
# instead of N: WeChat invalidates the previous token on each new issue.
_access_token_refresh_lock = Lock()
_last_alert_at = 0.0
_alert_lock = Lock()


@app.middleware("http")
async def add_request_context(request: Request, call_next):
    request_id, invalid_request_id = request_id_from_header(
        request.headers.get("x-request-id")
    )
    if invalid_request_id:
        increment_metric("invalid_request_id_total")
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        increment_metric("unhandled_errors_total")
        logger.exception(
            "unhandled_error request_id=%s method=%s path=%s",
            request_id,
            request.method,
            request.url.path,
        )
        response = JSONResponse(
            {"detail": "internal server error", "request_id": request_id},
            status_code=500,
        )
    duration_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"
    for header, value in SECURITY_HEADERS.items():
        response.headers[header] = value
    increment_metric("http_requests_total")
    logger.info(
        "request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


def request_id_from_header(value: Optional[str]) -> Tuple[str, bool]:
    if not value:
        return uuid.uuid4().hex, False

    candidate = value.strip()
    if (
        candidate
        and len(candidate) <= REQUEST_ID_MAX_LENGTH
        and all(char in REQUEST_ID_ALLOWED_CHARS for char in candidate)
    ):
        return candidate, False

    return uuid.uuid4().hex, True


@app.get("/")
def index() -> JSONResponse:
    settings = get_settings()
    return JSONResponse(
        {
            "ok": True,
            "service": "WeChat GLM Assistant",
            "version": app.version,
            "docs_enabled": settings.enable_docs,
            "endpoints": {
                "wechat": "/wechat",
                "health": "/health",
                "ready": "/ready",
                "metrics": "/metrics",
                "metrics_prometheus": "/metrics/prometheus",
                "docs": "/docs" if settings.enable_docs else None,
            },
        }
    )


@app.get("/health")
def health() -> JSONResponse:
    settings = get_settings()
    return JSONResponse(
        {
            "ok": True,
            "text_model": settings.text_model,
            "vision_model": settings.vision_model,
            "wechat_token_configured": bool(settings.wechat_token),
            "zhipu_key_configured": bool(settings.zhipu_api_key),
            "docs_enabled": settings.enable_docs,
            "max_request_bytes": settings.max_request_bytes,
            "max_model_input_chars": settings.max_model_input_chars,
            "model_max_tokens": settings.model_max_tokens,
            "model_max_concurrent_calls": settings.model_max_concurrent_calls,
            "conversation_memory_enabled": (
                settings.conversation_memory_ttl_seconds > 0
                and settings.conversation_memory_max_turns > 0
            ),
            "conversation_memory_max_turns": settings.conversation_memory_max_turns,
            "rate_limit_enabled": rate_limit_enabled(settings),
            "rate_limit_max_messages": settings.rate_limit_max_messages,
            "rate_limit_window_seconds": settings.rate_limit_window_seconds,
        }
    )


@app.get("/ready")
def ready(request: Request) -> JSONResponse:
    require_diagnostic_auth(request)
    settings = get_settings()
    checks = readiness_checks(settings)
    ok = all(checks.values())
    return JSONResponse({"ok": ok, "checks": checks}, status_code=200 if ok else 503)


@app.get("/metrics")
def metrics(request: Request) -> JSONResponse:
    require_diagnostic_auth(request)
    return JSONResponse(metrics_snapshot())


@app.get("/metrics/prometheus", response_class=PlainTextResponse)
def metrics_prometheus(request: Request) -> PlainTextResponse:
    require_diagnostic_auth(request)
    return PlainTextResponse(
        render_prometheus(metrics_snapshot()), media_type="text/plain; version=0.0.4"
    )


@app.get("/wechat", response_class=PlainTextResponse)
def verify_wechat_server(
    signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
) -> str:
    if not verify_wechat_signature(signature, timestamp, nonce):
        increment_metric("invalid_signature_total")
        raise HTTPException(status_code=403, detail="invalid signature")
    return echostr


@app.post("/wechat")
async def receive_wechat_message(
    request: Request,
    background_tasks: BackgroundTasks,
    signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
) -> Response:
    if not verify_wechat_signature(signature, timestamp, nonce):
        increment_metric("invalid_signature_total")
        raise HTTPException(status_code=403, detail="invalid signature")

    settings = get_settings()
    if content_length_exceeds_limit(request, settings.max_request_bytes):
        logger.warning(
            "Rejecting oversized WeChat payload by Content-Length: %s",
            request.headers.get("content-length"),
        )
        increment_metric("payload_too_large_total")
        raise HTTPException(status_code=413, detail="payload too large")

    body = await request.body()
    if len(body) > settings.max_request_bytes:
        logger.warning("Rejecting oversized WeChat payload: %s bytes", len(body))
        increment_metric("payload_too_large_total")
        raise HTTPException(status_code=413, detail="payload too large")

    try:
        message = parse_wechat_xml(body)
    except (ElementTree.ParseError, DefusedXmlException):
        logger.exception("Failed to parse WeChat XML")
        increment_metric("xml_parse_errors_total")
        return PlainTextResponse("success")

    increment_metric("wechat_messages_total")
    increment_message_type(message.get("MsgType", ""))

    cache_key = message_cache_key(message)
    cached_reply = get_cached_reply(cache_key)
    if cached_reply is not None:
        increment_metric("reply_cache_hits_total")
        if cached_reply:
            increment_metric("wechat_replies_total")
        return xml_or_success(cached_reply)

    owns_inflight, inflight = start_inflight_reply(cache_key)
    if not owns_inflight and inflight is not None:
        increment_metric("reply_inflight_hits_total")
        wait_seconds = min(5.0, settings.glm_timeout_seconds + 0.5)
        completed = await run_in_threadpool(inflight.event.wait, wait_seconds)
        if completed:
            if inflight.reply:
                increment_metric("wechat_replies_total")
            return xml_or_success(inflight.reply)
        logger.warning("Timed out waiting for duplicate WeChat message: %s", cache_key)
        return PlainTextResponse("success")

    reply = ""
    try:
        reply = await build_reply(message, background_tasks)
        remember_reply(cache_key, reply)
    finally:
        finish_inflight_reply(cache_key, inflight, reply)
    if reply:
        increment_metric("wechat_replies_total")
    return xml_or_success(reply)


def verify_wechat_signature(signature: str, timestamp: str, nonce: str) -> bool:
    token = get_settings().wechat_token
    if not token:
        logger.error("WECHAT_TOKEN is not configured")
        return False

    if not timestamp_is_fresh(timestamp):
        logger.warning("Rejected stale or invalid WeChat timestamp: %r", timestamp)
        return False

    raw = "".join(sorted([token, timestamp, nonce]))
    expected = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return hmac.compare_digest(expected, signature)


def readiness_checks(settings: Settings) -> Dict[str, bool]:
    return {
        "wechat_token_configured": bool(settings.wechat_token),
        "zhipu_key_configured": bool(settings.zhipu_api_key),
        "zhipuai_package_available": zhipuai_package_available(),
    }


def zhipuai_package_available() -> bool:
    return importlib.util.find_spec("zhipuai") is not None


def require_diagnostic_auth(request: Request) -> None:
    settings = get_settings()
    expected = settings.admin_token
    if not expected:
        return

    client_host = request.client.host if request.client else "unknown"
    provided = request.headers.get("x-admin-token", "")
    if not provided and settings.allow_admin_token_query:
        provided = request.query_params.get("admin_token", "")
    if not hmac.compare_digest(expected, provided):
        logger.warning(
            "admin_auth_failed path=%s client=%s", request.url.path, client_host
        )
        raise HTTPException(status_code=401, detail="invalid admin token")
    logger.info("admin_auth_ok path=%s client=%s", request.url.path, client_host)


def increment_metric(name: str, amount: int = 1) -> None:
    with _metrics_lock:
        _metrics[name] = _metrics.get(name, 0) + amount


def increment_message_type(msg_type: str) -> None:
    key = msg_type or "unknown"
    with _metrics_lock:
        _message_type_metrics[key] = _message_type_metrics.get(key, 0) + 1


def record_model_duration(kind: str, duration_ms: float) -> None:
    if kind not in _model_duration_metrics:
        return
    safe_duration = max(0.0, duration_ms)
    with _metrics_lock:
        stats = _model_duration_metrics[kind]
        stats["total_ms"] += safe_duration
        stats["max_ms"] = max(stats["max_ms"], safe_duration)


def metrics_snapshot() -> Dict[str, Any]:
    with _metrics_lock:
        counters = dict(_metrics)
        message_types = dict(_message_type_metrics)
        model_durations = {
            kind: {
                "total_ms": round(stats["total_ms"], 2),
                "max_ms": round(stats["max_ms"], 2),
                "average_ms": round(
                    stats["total_ms"] / max(1, counters.get(f"model_{kind}_calls_total", 0)),
                    2,
                ),
            }
            for kind, stats in _model_duration_metrics.items()
        }

    with _reply_cache_lock:
        reply_cache_size = len(_reply_cache)
    with _conversation_lock:
        conversation_users = len(_conversation_cache)
    with _rate_limit_lock:
        rate_limit_users = len(_rate_limit_cache)
    with _inflight_replies_lock:
        inflight_replies = len(_inflight_replies)

    return {
        "ok": True,
        "uptime_seconds": max(0, int(time.time() - STARTED_AT)),
        "counters": counters,
        "message_types": message_types,
        "model_durations_ms": model_durations,
        "cache_sizes": {
            "reply_cache": reply_cache_size,
            "conversation_users": conversation_users,
            "rate_limit_users": rate_limit_users,
            "inflight_replies": inflight_replies,
        },
    }


def render_prometheus(snapshot: Dict[str, Any]) -> str:
    lines = [
        "# TYPE wechat_glm_uptime_seconds gauge",
        f"wechat_glm_uptime_seconds {snapshot['uptime_seconds']}",
    ]

    for name, value in snapshot["counters"].items():
        lines.append(f"# TYPE wechat_glm_{name} counter")
        lines.append(f"wechat_glm_{name} {value}")

    for msg_type, value in snapshot["message_types"].items():
        safe_type = msg_type.replace('"', "")
        lines.append(f'wechat_glm_message_type_total{{type="{safe_type}"}} {value}')

    for kind, stats in snapshot["model_durations_ms"].items():
        lines.append(f'wechat_glm_model_duration_ms_total{{kind="{kind}"}} {stats["total_ms"]}')
        lines.append(f'wechat_glm_model_duration_ms_max{{kind="{kind}"}} {stats["max_ms"]}')

    for name, value in snapshot["cache_sizes"].items():
        lines.append(f"# TYPE wechat_glm_cache_{name} gauge")
        lines.append(f"wechat_glm_cache_{name} {value}")

    return "\n".join(lines) + "\n"


def send_alert(message: str) -> None:
    global _last_alert_at
    settings = get_settings()
    if not settings.alert_webhook_url:
        return

    now = time.time()
    with _alert_lock:
        if now - _last_alert_at < settings.alert_min_interval_seconds:
            return
        _last_alert_at = now
    Thread(target=_post_alert, args=(settings.alert_webhook_url, message), daemon=True).start()


def _post_alert(webhook_url: str, message: str) -> None:
    try:
        httpx.post(
            webhook_url,
            json={"msg_type": "text", "content": {"text": message}},
            timeout=3.0,
        )
    except Exception:
        logger.warning("Failed to deliver alert webhook", exc_info=True)


def timestamp_is_fresh(timestamp: str) -> bool:
    tolerance = get_settings().signature_tolerance_seconds
    if tolerance <= 0:
        return True
    try:
        message_time = int(timestamp)
    except ValueError:
        return False
    return abs(int(time.time()) - message_time) <= tolerance


def content_length_exceeds_limit(request: Request, max_bytes: int) -> bool:
    content_length = request.headers.get("content-length")
    if not content_length:
        return False
    try:
        return int(content_length) > max_bytes
    except ValueError:
        return False


def parse_wechat_xml(body: bytes) -> Dict[str, str]:
    root = ElementTree.fromstring(body)
    return {child.tag: child.text or "" for child in root}


def message_cache_key(message: Dict[str, str]) -> str:
    msg_id = message.get("MsgId", "").strip()
    if msg_id:
        return f"msg:{msg_id}"

    parts = [
        message.get("FromUserName", ""),
        message.get("CreateTime", ""),
        message.get("MsgType", ""),
        message.get("Event", ""),
        message.get("EventKey", ""),
        message.get("Content", ""),
        message.get("PicUrl", ""),
        message.get("MediaId", ""),
    ]
    if not any(parts[:3]):
        return ""

    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"fallback:{digest}"


def start_inflight_reply(cache_key: str) -> Tuple[bool, Optional[InflightReply]]:
    if not cache_key:
        return True, None

    with _inflight_replies_lock:
        existing = _inflight_replies.get(cache_key)
        if existing is not None:
            return False, existing
        inflight = InflightReply(event=Event())
        _inflight_replies[cache_key] = inflight
        return True, inflight


def finish_inflight_reply(
    cache_key: str, inflight: Optional[InflightReply], reply: str
) -> None:
    if not cache_key or inflight is None:
        return

    with _inflight_replies_lock:
        inflight.reply = reply
        inflight.event.set()
        if _inflight_replies.get(cache_key) is inflight:
            _inflight_replies.pop(cache_key, None)


def get_cached_reply(cache_key: str) -> Optional[str]:
    settings = get_settings()
    if not cache_key or settings.duplicate_cache_ttl_seconds <= 0:
        return None

    now = time.time()
    with _reply_cache_lock:
        prune_reply_cache(now, settings)
        entry = _reply_cache.get(cache_key)
        if entry is None:
            return None
        created_at, reply = entry
        if now - created_at > settings.duplicate_cache_ttl_seconds:
            _reply_cache.pop(cache_key, None)
            return None
        _reply_cache.move_to_end(cache_key)
        return reply


def remember_reply(cache_key: str, reply: str) -> None:
    settings = get_settings()
    if not cache_key or settings.duplicate_cache_ttl_seconds <= 0:
        return

    now = time.time()
    with _reply_cache_lock:
        _reply_cache[cache_key] = (now, reply)
        _reply_cache.move_to_end(cache_key)
        prune_reply_cache(now, settings)


def prune_reply_cache(now: float, settings: Settings) -> None:
    expired_keys = [
        key
        for key, (created_at, _) in _reply_cache.items()
        if now - created_at > settings.duplicate_cache_ttl_seconds
    ]
    for key in expired_keys:
        _reply_cache.pop(key, None)

    while len(_reply_cache) > settings.duplicate_cache_max_size:
        _reply_cache.popitem(last=False)


def conversation_memory_enabled(settings: Settings) -> bool:
    return (
        settings.conversation_memory_ttl_seconds > 0
        and settings.conversation_memory_max_turns > 0
    )


def get_conversation_history(user_id: str) -> List[Dict[str, str]]:
    settings = get_settings()
    if not user_id or not conversation_memory_enabled(settings):
        return []

    now = time.time()
    with _conversation_lock:
        prune_conversation_cache(now, settings)
        entry = _conversation_cache.get(user_id)
        if entry is None:
            return []
        created_at, history = entry
        if now - created_at > settings.conversation_memory_ttl_seconds:
            _conversation_cache.pop(user_id, None)
            return []
        _conversation_cache.move_to_end(user_id)
        return list(history)


def remember_conversation_turn(user_id: str, user_text: str, assistant_text: str) -> None:
    settings = get_settings()
    if not user_id or not conversation_memory_enabled(settings):
        return

    now = time.time()
    with _conversation_lock:
        _, history = _conversation_cache.get(user_id, (now, []))
        history = list(history)
        history.extend(
            [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": assistant_text},
            ]
        )
        max_messages = settings.conversation_memory_max_turns * 2
        if len(history) > max_messages:
            history = history[-max_messages:]
        _conversation_cache[user_id] = (now, history)
        _conversation_cache.move_to_end(user_id)
        prune_conversation_cache(now, settings)


def clear_conversation(user_id: str) -> None:
    if not user_id:
        return
    with _conversation_lock:
        _conversation_cache.pop(user_id, None)


def prune_conversation_cache(now: float, settings: Settings) -> None:
    expired_keys = [
        key
        for key, (created_at, _) in _conversation_cache.items()
        if now - created_at > settings.conversation_memory_ttl_seconds
    ]
    for key in expired_keys:
        _conversation_cache.pop(key, None)

    while len(_conversation_cache) > settings.conversation_memory_max_users:
        _conversation_cache.popitem(last=False)


def rate_limit_enabled(settings: Settings) -> bool:
    return settings.rate_limit_window_seconds > 0 and settings.rate_limit_max_messages > 0


def should_rate_limit_message(message: Dict[str, str], msg_type: str) -> bool:
    if msg_type == "text":
        text = message.get("Content", "").strip()
        return bool(text) and text.lower() not in (
            HELP_COMMANDS | RESET_COMMANDS | DATA_DELETE_COMMANDS
        )

    if msg_type == "image":
        return True

    if msg_type == "voice":
        return bool(message.get("Recognition", "").strip())

    if msg_type == "link":
        return bool(message.get("Title", "").strip() or message.get("Description", "").strip())

    return False


def rate_limit_exceeded(user_id: str) -> bool:
    settings = get_settings()
    if not user_id or not rate_limit_enabled(settings):
        return False

    now = time.time()
    window_start = now - settings.rate_limit_window_seconds
    with _rate_limit_lock:
        prune_rate_limit_cache(now, settings)
        _, timestamps = _rate_limit_cache.get(user_id, (now, []))
        recent = [timestamp for timestamp in timestamps if timestamp >= window_start]
        if len(recent) >= settings.rate_limit_max_messages:
            _rate_limit_cache[user_id] = (now, recent)
            _rate_limit_cache.move_to_end(user_id)
            return True

        recent.append(now)
        _rate_limit_cache[user_id] = (now, recent)
        _rate_limit_cache.move_to_end(user_id)
        prune_rate_limit_cache(now, settings)
        return False


def prune_rate_limit_cache(now: float, settings: Settings) -> None:
    expired_keys = [
        key
        for key, (last_seen, _) in _rate_limit_cache.items()
        if now - last_seen > settings.rate_limit_window_seconds
    ]
    for key in expired_keys:
        _rate_limit_cache.pop(key, None)

    while len(_rate_limit_cache) > settings.rate_limit_max_users:
        _rate_limit_cache.popitem(last=False)


def forget_rate_limit(user_id: str) -> None:
    if not user_id:
        return
    with _rate_limit_lock:
        _rate_limit_cache.pop(user_id, None)


def xml_or_success(reply: str) -> Response:
    if not reply:
        return PlainTextResponse("success")
    return Response(content=reply, media_type="application/xml")


async def build_reply(
    message: Dict[str, str], background_tasks: Optional[BackgroundTasks] = None
) -> str:
    msg_type = message.get("MsgType", "")
    from_user = message.get("FromUserName", "")
    to_user = message.get("ToUserName", "")

    if not from_user or not to_user:
        logger.warning("Missing FromUserName or ToUserName in message")
        return ""

    fallback_triggered = False
    try:
        if should_rate_limit_message(message, msg_type) and rate_limit_exceeded(from_user):
            increment_metric("rate_limited_total")
            content = RATE_LIMIT_REPLY
        else:
            content = await content_for_message(message, msg_type)
    except ModelUnavailableError:
        # call_glm already counted this rejection before raising.
        fallback_triggered = True
        content = MODEL_CIRCUIT_OPEN_REPLY
    except ModelBusyError:
        fallback_triggered = True
        content = MODEL_BUSY_REPLY
    except Exception:
        logger.exception("Failed to build GLM reply")
        increment_metric("reply_errors_total")
        fallback_triggered = True
        content = FALLBACK_REPLY

    if not content:
        return ""

    settings = get_settings()
    truncated = len(content.strip()) > settings.max_reply_chars
    reply = render_text_reply(to_user=from_user, from_user=to_user, content=content)

    if background_tasks is not None and settings.enable_customer_service_fallback:
        if fallback_triggered:
            background_tasks.add_task(deliver_fallback_followup, from_user, message, msg_type)
        elif truncated:
            background_tasks.add_task(send_customer_service_text, from_user, content)

    return reply


async def deliver_fallback_followup(
    from_user: str, message: Dict[str, str], msg_type: str
) -> None:
    try:
        content = await content_for_message(message, msg_type)
    except Exception:
        logger.info("Fallback follow-up retry failed for user=%s", from_user)
        return
    if content:
        send_customer_service_text(from_user, content)


async def content_for_message(message: Dict[str, str], msg_type: str) -> str:
    from_user = message.get("FromUserName", "")

    if msg_type == "text":
        user_text = message.get("Content", "").strip()
        return await run_in_threadpool(reply_to_user_text, user_text, from_user)

    if msg_type == "image":
        image_url = message.get("PicUrl", "").strip()
        return await run_in_threadpool(reply_to_user_image, image_url, from_user)

    if msg_type == "voice":
        recognized_text = message.get("Recognition", "").strip()
        if recognized_text:
            return await run_in_threadpool(reply_to_user_text, recognized_text, from_user)
        return "我收到了语音，但没有拿到语音识别文本。你可以再发一段文字给我。"

    if msg_type == "link":
        title = message.get("Title", "").strip()
        description = message.get("Description", "").strip()
        url = message.get("Url", "").strip()
        if title or description:
            prompt = f"用户分享了一个链接：{title}\n{description}\n{url}\n请基于这些信息简要回应。"
            return await run_in_threadpool(reply_to_user_text, prompt, from_user)
        return "我收到了链接，但没有拿到足够的标题或摘要信息。"

    if msg_type == "location":
        label = message.get("Label", "").strip()
        content = f"我收到了位置：{label or '未命名位置'}。如果你需要路线或附近建议，请再补充你的具体问题。"
        remember_conversation_turn(
            from_user,
            f"用户发送了位置：{label or '未命名位置'}",
            content,
        )
        return content

    if msg_type == "event":
        return content_for_event(message)

    return "我现在支持文字、图片、语音识别、位置和链接消息。你可以直接发一句话，或者发一张图片给我看看。"


def content_for_event(message: Dict[str, str]) -> str:
    event = message.get("Event", "").lower()
    if event == "subscribe":
        bot_name = get_settings().bot_name
        return f"你好，我是{bot_name}。可以直接发文字问我，也可以发图片让我分析。"
    if event == "unsubscribe":
        return ""
    if event == "scan":
        return "我看到了你的扫码事件。可以继续发文字或图片给我。"
    if event == "click":
        return content_for_menu_click(message)
    return "事件已收到。你可以直接发文字或图片继续和我聊。"


def content_for_menu_click(message: Dict[str, str]) -> str:
    event_key = message.get("EventKey", "")
    if event_key == MENU_KEY_HELP:
        return HELP_REPLY
    if event_key == MENU_KEY_RESET:
        clear_conversation(message.get("FromUserName", ""))
        return "已清空当前会话上下文。"
    if event_key == MENU_KEY_ABOUT:
        bot_name = get_settings().bot_name
        return f"我是{bot_name}，一个运行在微信里的 GLM 助手。"
    return "收到菜单点击事件。"


def reply_to_user_text(user_text: str, user_id: str = "") -> str:
    normalized_text = (user_text or "").strip()
    if not normalized_text:
        return "你发的是空消息，可以直接问我一个问题。"

    normalized = normalized_text.lower()
    if normalized in HELP_COMMANDS:
        return HELP_REPLY

    if normalized in DATA_DELETE_COMMANDS:
        clear_conversation(user_id)
        forget_rate_limit(user_id)
        return "已清空你的会话记忆和限流记录。"

    if normalized in RESET_COMMANDS:
        clear_conversation(user_id)
        return "已清空当前会话上下文。"

    model_text = trim_model_input(normalized_text)
    history = get_conversation_history(user_id)
    reply = reply_to_text(model_text, history=history)
    remember_conversation_turn(user_id, model_text, reply)
    return reply


def reply_to_user_image(image_url: str, user_id: str = "") -> str:
    reply = reply_to_image(image_url)
    remember_conversation_turn(user_id, "用户发送了一张图片。", reply)
    return reply


def circuit_is_open() -> bool:
    with _circuit_lock:
        return time.time() < _circuit_state["open_until"]


def record_model_failure() -> None:
    settings = get_settings()
    now = time.time()
    opened = False
    with _circuit_lock:
        failures = _circuit_state["failures"]
        open_until = _circuit_state["open_until"]
        if open_until and now < open_until:
            # Breaker is open; this is a request that was already in flight when
            # it tripped. Counting it would let leftovers re-trip the next window.
            return
        if open_until:
            # Cooldown elapsed, so this call was the half-open probe. One failed
            # probe re-opens immediately instead of waiting for a full threshold.
            failures.clear()
            _circuit_state["open_until"] = now + settings.model_circuit_breaker_cooldown_seconds
            opened = True
        else:
            failures.append(now)
            window_start = now - settings.model_circuit_breaker_window_seconds
            while failures and failures[0] < window_start:
                failures.pop(0)
            if len(failures) >= settings.model_circuit_breaker_threshold:
                _circuit_state["open_until"] = (
                    now + settings.model_circuit_breaker_cooldown_seconds
                )
                failures.clear()
                opened = True

    if opened:
        increment_metric("model_circuit_open_total")
        send_alert(
            f"[{settings.bot_name}] GLM 调用连续失败达到阈值，熔断器已打开 "
            f"{settings.model_circuit_breaker_cooldown_seconds} 秒。"
        )


def record_model_success() -> None:
    with _circuit_lock:
        _circuit_state["failures"].clear()
        # A success closes the breaker for good: clearing open_until is what
        # takes us out of the half-open state.
        _circuit_state["open_until"] = 0.0


@lru_cache(maxsize=8)
def get_moderation_blocklist(path: str) -> "frozenset[str]":
    if not path:
        return frozenset()
    try:
        with open(path, "r", encoding="utf-8") as blocklist_file:
            words = {line.strip().lower() for line in blocklist_file if line.strip()}
        return frozenset(words)
    except OSError:
        logger.warning("Could not read moderation blocklist file: %s", path)
        return frozenset()


def moderate_reply(text: str) -> str:
    settings = get_settings()
    blocklist = get_moderation_blocklist(settings.moderation_blocklist_file)
    if not blocklist or not text:
        return text
    lowered = text.lower()
    if any(word in lowered for word in blocklist):
        increment_metric("moderation_blocked_total")
        return MODERATION_BLOCKED_REPLY
    return text


def call_glm(
    kind: str,
    model: str,
    messages: List[Dict[str, Any]],
    temperature: float,
    max_tokens: int,
) -> Any:
    settings = get_settings()
    if circuit_is_open():
        increment_metric("model_circuit_rejected_total")
        raise ModelUnavailableError(MODEL_CIRCUIT_OPEN_REPLY)

    gate = acquire_model_slot(settings)
    increment_metric(f"model_{kind}_calls_total")
    started = time.perf_counter()
    try:
        response = _call_glm_with_retry(model, messages, temperature, max_tokens, kind)
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        gate.release()
        record_model_duration(kind, elapsed_ms)
    logger.debug("model_call kind=%s model=%s duration_ms=%.0f", kind, model, elapsed_ms)
    return response


def _call_glm_with_retry(
    model: str,
    messages: List[Dict[str, Any]],
    temperature: float,
    max_tokens: int,
    kind: str,
) -> Any:
    try:
        response = get_zhipu_client().chat.completions.create(
            model=model, messages=messages, temperature=temperature, max_tokens=max_tokens
        )
    except (httpx.ConnectError, httpx.ConnectTimeout):
        logger.warning("Transient GLM connection error, retrying once (kind=%s)", kind)
        try:
            response = get_zhipu_client().chat.completions.create(
                model=model, messages=messages, temperature=temperature, max_tokens=max_tokens
            )
        except Exception:
            increment_metric("model_call_errors_total")
            record_model_failure()
            raise
    except Exception:
        increment_metric("model_call_errors_total")
        record_model_failure()
        raise

    record_model_success()
    return response


def reply_to_text(user_text: str, history: Optional[List[Dict[str, str]]] = None) -> str:
    settings = get_settings()
    model_text = trim_model_input(user_text)
    if not model_text:
        return "你发的是空消息，可以直接问我一个问题。"

    messages = [{"role": "system", "content": settings.system_prompt}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": model_text})

    response = call_glm(
        "text", settings.text_model, messages, temperature=0.6, max_tokens=settings.model_max_tokens
    )
    reply = completion_content(response, "我没有生成有效回复。")
    return moderate_reply(reply)


def reply_to_image(image_url: str) -> str:
    settings = get_settings()
    if not is_http_url(image_url):
        return "我收到了图片消息，但没有拿到可用的图片地址。"

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": settings.image_prompt},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
    ]
    response = call_glm(
        "image", settings.vision_model, messages, temperature=0.4, max_tokens=settings.model_max_tokens
    )
    reply = completion_content(response, "我没有识别出图片内容。")
    return moderate_reply(reply)


def completion_content(response: Any, fallback: str) -> str:
    try:
        choices = getattr(response, "choices", None) or []
        message = choices[0].message
        content = getattr(message, "content", "")
    except (AttributeError, IndexError, TypeError):
        return fallback

    if isinstance(content, str) and content.strip():
        return content
    return fallback


def acquire_model_slot(settings: Settings) -> BoundedSemaphore:
    global _model_gate

    with _model_gate_lock:
        if _model_gate is None:
            _model_gate = BoundedSemaphore(settings.model_max_concurrent_calls)
        gate = _model_gate

    if not gate.acquire(blocking=False):
        increment_metric("model_busy_total")
        raise ModelBusyError(MODEL_BUSY_REPLY)
    return gate


def trim_model_input(text: str) -> str:
    settings = get_settings()
    normalized = (text or "").strip()
    limit = settings.max_model_input_chars
    if limit <= 0 or len(normalized) <= limit:
        return normalized

    increment_metric("model_input_truncated_total")
    return normalized[:limit].rstrip()


def is_http_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def render_text_reply(to_user: str, from_user: str, content: str) -> str:
    content = trim_wechat_text(content)
    return (
        "<xml>"
        f"<ToUserName>{cdata(to_user)}</ToUserName>"
        f"<FromUserName>{cdata(from_user)}</FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content>{cdata(content)}</Content>"
        "</xml>"
    )


def cdata(value: str) -> str:
    safe = str(value).replace("]]>", "]]]]><![CDATA[>")
    return f"<![CDATA[{safe}]]>"


def trim_wechat_text(text: str) -> str:
    limit = get_settings().max_reply_chars
    normalized = (text or "").strip()
    if len(normalized) <= limit:
        return normalized

    suffix = "\n\n回复太长，我先说到这里。"
    if limit <= len(suffix):
        return normalized[:limit]
    return normalized[: limit - len(suffix)].rstrip() + suffix


def cached_access_token() -> Optional[str]:
    with _access_token_lock:
        if _access_token_cache["token"] and time.time() < _access_token_cache["expires_at"]:
            return str(_access_token_cache["token"])
    return None


def _token_refresh_backoff_active() -> bool:
    with _access_token_lock:
        return time.time() < _access_token_cache["retry_after"]


def get_wechat_access_token() -> Optional[str]:
    settings = get_settings()
    if not settings.wechat_app_id or not settings.wechat_app_secret:
        return None

    cached = cached_access_token()
    if cached:
        return cached
    if _token_refresh_backoff_active():
        return None

    # Bounded wait: this can run on a request path, so a slow or stuck refresh
    # must not pile every other caller up behind it.
    if not _access_token_refresh_lock.acquire(timeout=ACCESS_TOKEN_REFRESH_WAIT_SECONDS):
        logger.warning("Timed out waiting for an in-flight WeChat access_token refresh")
        return cached_access_token()
    try:
        cached = cached_access_token()
        if cached:
            return cached
        if _token_refresh_backoff_active():
            # Whoever held the lock just failed. Repeating their request would
            # only queue another timeout behind the same broken upstream.
            return None
        return refresh_wechat_access_token(settings)
    finally:
        _access_token_refresh_lock.release()


def _record_token_refresh_failure() -> None:
    with _access_token_lock:
        _access_token_cache["retry_after"] = time.time() + ACCESS_TOKEN_FAILURE_BACKOFF_SECONDS


def refresh_wechat_access_token(settings: Settings) -> Optional[str]:
    now = time.time()
    try:
        response = httpx.get(
            WECHAT_ACCESS_TOKEN_URL,
            params={
                "grant_type": "client_credential",
                "appid": settings.wechat_app_id,
                "secret": settings.wechat_app_secret,
            },
            timeout=5.0,
        )
        data = response.json()
    except Exception:
        logger.warning("Failed to fetch WeChat access_token", exc_info=True)
        _record_token_refresh_failure()
        return None

    token = data.get("access_token")
    expires_in = data.get("expires_in")
    if not token or not isinstance(expires_in, int):
        # Never log the raw payload: it may carry a live access_token.
        logger.warning(
            "WeChat access_token response missing fields: errcode=%s errmsg=%s",
            data.get("errcode"),
            data.get("errmsg"),
        )
        _record_token_refresh_failure()
        return None

    with _access_token_lock:
        _access_token_cache["token"] = token
        _access_token_cache["expires_at"] = now + max(0, expires_in - 60)
        _access_token_cache["retry_after"] = 0.0
    return str(token)


def send_customer_service_text(to_openid: str, text: str) -> bool:
    token = get_wechat_access_token()
    if not token or not to_openid or not text:
        return False

    try:
        response = httpx.post(
            WECHAT_CUSTOM_MESSAGE_URL,
            params={"access_token": token},
            json={"touser": to_openid, "msgtype": "text", "text": {"content": text}},
            timeout=5.0,
        )
        data = response.json()
    except Exception:
        logger.warning("Failed to send WeChat customer service message", exc_info=True)
        increment_metric("customer_service_failed_total")
        return False

    if data.get("errcode", 0) != 0:
        logger.warning("WeChat customer service message failed: %s", data)
        increment_metric("customer_service_failed_total")
        return False

    increment_metric("customer_service_sent_total")
    return True
