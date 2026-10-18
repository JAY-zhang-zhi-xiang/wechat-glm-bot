import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

import main


@pytest.fixture(autouse=True)
def reset_settings(monkeypatch):
    monkeypatch.setenv("WECHAT_TOKEN", "test-token")
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("ENABLE_DOCS", raising=False)
    monkeypatch.delenv("ALLOW_ADMIN_TOKEN_QUERY", raising=False)
    monkeypatch.delenv("MODEL_MAX_TOKENS", raising=False)
    monkeypatch.delenv("MODEL_MAX_CONCURRENT_CALLS", raising=False)
    monkeypatch.delenv("MAX_REPLY_CHARS", raising=False)
    monkeypatch.delenv("MAX_MODEL_INPUT_CHARS", raising=False)
    monkeypatch.delenv("MAX_REQUEST_BYTES", raising=False)
    monkeypatch.delenv("WECHAT_SIGNATURE_TOLERANCE_SECONDS", raising=False)
    monkeypatch.delenv("DUPLICATE_CACHE_TTL_SECONDS", raising=False)
    monkeypatch.delenv("DUPLICATE_CACHE_MAX_SIZE", raising=False)
    monkeypatch.delenv("CONVERSATION_MEMORY_TTL_SECONDS", raising=False)
    monkeypatch.delenv("CONVERSATION_MEMORY_MAX_TURNS", raising=False)
    monkeypatch.delenv("CONVERSATION_MEMORY_MAX_USERS", raising=False)
    monkeypatch.delenv("RATE_LIMIT_WINDOW_SECONDS", raising=False)
    monkeypatch.delenv("RATE_LIMIT_MAX_MESSAGES", raising=False)
    monkeypatch.delenv("RATE_LIMIT_MAX_USERS", raising=False)
    monkeypatch.delenv("MODERATION_BLOCKLIST_FILE", raising=False)
    monkeypatch.delenv("MODEL_CIRCUIT_BREAKER_THRESHOLD", raising=False)
    monkeypatch.delenv("MODEL_CIRCUIT_BREAKER_WINDOW_SECONDS", raising=False)
    monkeypatch.delenv("MODEL_CIRCUIT_BREAKER_COOLDOWN_SECONDS", raising=False)
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("ALERT_MIN_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("WECHAT_APP_ID", raising=False)
    monkeypatch.delenv("WECHAT_APP_SECRET", raising=False)
    monkeypatch.delenv("ENABLE_CUSTOMER_SERVICE_FALLBACK", raising=False)
    main.get_settings.cache_clear()
    main.get_llm_client.cache_clear()
    main.get_moderation_blocklist.cache_clear()
    with main._reply_cache_lock:
        main._reply_cache.clear()
    with main._conversation_lock:
        main._conversation_cache.clear()
    with main._rate_limit_lock:
        main._rate_limit_cache.clear()
    with main._inflight_replies_lock:
        main._inflight_replies.clear()
    with main._model_gate_lock:
        main._model_gate = None
    with main._metrics_lock:
        for name in main.METRIC_NAMES:
            main._metrics[name] = 0
        main._message_type_metrics.clear()
        for stats in main._model_duration_metrics.values():
            stats["total_ms"] = 0.0
            stats["max_ms"] = 0.0
    with main._circuit_lock:
        main._circuit_state["failures"] = []
        main._circuit_state["open_until"] = 0.0
    with main._access_token_lock:
        main._access_token_cache["token"] = ""
        main._access_token_cache["expires_at"] = 0.0
    with main._alert_lock:
        main._last_alert_at = 0.0
    yield
    main.get_settings.cache_clear()
    if hasattr(main.get_llm_client, "cache_clear"):
        main.get_llm_client.cache_clear()
    main.get_moderation_blocklist.cache_clear()


def sign(timestamp: str, nonce: str, token: str = "test-token") -> str:
    raw = "".join(sorted([token, timestamp, nonce]))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def signed_params(**extra):
    timestamp = str(int(time.time()))
    nonce = "nonce"
    params = {
        "signature": sign(timestamp, nonce),
        "timestamp": timestamp,
        "nonce": nonce,
    }
    params.update(extra)
    return params


def test_parse_log_level_accepts_common_values():
    assert main.parse_log_level("debug") == logging.DEBUG
    assert main.parse_log_level("INFO") == logging.INFO
    assert main.parse_log_level(" warning ") == logging.WARNING


def test_parse_log_level_falls_back_to_info_for_empty_or_invalid_value():
    assert main.parse_log_level("") == logging.INFO
    assert main.parse_log_level(None) == logging.INFO
    assert main.parse_log_level("verbose") == logging.INFO


def text_xml(msg_id: str = "42", content: str = "hello") -> bytes:
    return f"""
    <xml>
      <ToUserName><![CDATA[server]]></ToUserName>
      <FromUserName><![CDATA[user]]></FromUserName>
      <CreateTime>123</CreateTime>
      <MsgType><![CDATA[text]]></MsgType>
      <Content><![CDATA[{content}]]></Content>
      <MsgId>{msg_id}</MsgId>
    </xml>
    """.encode("utf-8")


def image_xml(msg_id: str = "43", pic_url: str = "https://example.com/image.jpg") -> bytes:
    return f"""
    <xml>
      <ToUserName><![CDATA[server]]></ToUserName>
      <FromUserName><![CDATA[user]]></FromUserName>
      <CreateTime>123</CreateTime>
      <MsgType><![CDATA[image]]></MsgType>
      <PicUrl><![CDATA[{pic_url}]]></PicUrl>
      <MediaId><![CDATA[media-id]]></MediaId>
      <MsgId>{msg_id}</MsgId>
    </xml>
    """.encode("utf-8")


def location_xml(msg_id: str = "44", label: str = "深圳湾") -> bytes:
    return f"""
    <xml>
      <ToUserName><![CDATA[server]]></ToUserName>
      <FromUserName><![CDATA[user]]></FromUserName>
      <CreateTime>123</CreateTime>
      <MsgType><![CDATA[location]]></MsgType>
      <Location_X>22.5</Location_X>
      <Location_Y>113.9</Location_Y>
      <Scale>15</Scale>
      <Label><![CDATA[{label}]]></Label>
      <MsgId>{msg_id}</MsgId>
    </xml>
    """.encode("utf-8")


def test_health_does_not_require_zhipuai_package():
    client = TestClient(main.app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["llm_key_configured"] is False
    assert response.json()["conversation_memory_enabled"] is True
    assert response.json()["rate_limit_enabled"] is True
    assert response.json()["max_model_input_chars"] == 4000
    assert response.json()["model_max_tokens"] == 800
    assert response.json()["docs_enabled"] is False
    assert response.json()["model_max_concurrent_calls"] == 4


def test_index_returns_public_service_summary():
    client = TestClient(main.app)

    response = client.get("/")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["service"] == "WeChat GLM Assistant"
    assert data["endpoints"]["wechat"] == "/wechat"
    assert data["endpoints"]["docs"] is None
    assert "zhipu" not in data


def test_response_includes_request_id_and_timing_headers():
    client = TestClient(main.app)

    response = client.get("/", headers={"X-Request-ID": "request-123"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "request-123"
    assert float(response.headers["X-Response-Time-Ms"]) >= 0
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Permitted-Cross-Domain-Policies"] == "none"


def test_request_id_from_header_accepts_safe_values():
    request_id, invalid = main.request_id_from_header("gateway-123_:/trace.1")

    assert request_id == "gateway-123_:/trace.1"
    assert invalid is False


def test_request_id_from_header_rejects_unsafe_values():
    unsafe_id, invalid_unsafe = main.request_id_from_header("bad$id")
    long_id, invalid_long = main.request_id_from_header("a" * 65)

    assert invalid_unsafe is True
    assert len(unsafe_id) == 32
    assert unsafe_id != "bad$id"
    assert invalid_long is True
    assert len(long_id) == 32


def test_response_replaces_invalid_request_id_header():
    client = TestClient(main.app)

    response = client.get("/", headers={"X-Request-ID": "bad$id"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "bad$id"
    assert len(response.headers["X-Request-ID"]) == 32
    assert main.metrics_snapshot()["counters"]["invalid_request_id_total"] == 1


def test_response_generates_request_id_when_missing():
    client = TestClient(main.app)

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert len(response.headers["X-Request-ID"]) == 32


def test_wechat_xml_response_includes_security_headers():
    client = TestClient(main.app)

    response = client.post("/wechat", params=signed_params(), content=text_xml("security", "帮助"))

    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_metrics_track_http_requests_total():
    client = TestClient(main.app)

    client.get("/")
    metrics = client.get("/metrics").json()

    assert metrics["counters"]["http_requests_total"] == 1


def test_unhandled_exception_returns_json_with_request_id():
    app = FastAPI()
    app.middleware("http")(main.add_request_context)

    @app.get("/boom")
    def boom():
        raise RuntimeError("boom")

    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/boom", headers={"X-Request-ID": "boom-123"})

    assert response.status_code == 500
    assert response.json() == {
        "detail": "internal server error",
        "request_id": "boom-123",
    }
    assert response.headers["X-Request-ID"] == "boom-123"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert main.metrics_snapshot()["counters"]["unhandled_errors_total"] == 1


def test_create_app_can_disable_docs(monkeypatch):
    monkeypatch.setenv("ENABLE_DOCS", "false")
    main.get_settings.cache_clear()

    app = main.create_app()

    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None


def test_index_hides_docs_endpoint_when_docs_are_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_DOCS", "false")
    main.get_settings.cache_clear()
    client = TestClient(main.app)

    response = client.get("/")

    assert response.status_code == 200
    assert response.json()["endpoints"]["docs"] is None


def test_create_app_disables_docs_by_default():
    app = main.create_app()

    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None


def test_ready_reports_missing_required_configuration():
    client = TestClient(main.app)

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["ok"] is False
    assert response.json()["checks"]["wechat_token_configured"] is True
    assert response.json()["checks"]["llm_key_configured"] is False


def test_ready_succeeds_when_required_configuration_is_available(monkeypatch):
    monkeypatch.setenv("ZHIPU_API_KEY", "key")
    main.get_settings.cache_clear()
    monkeypatch.setattr(main, "llm_sdk_available", lambda provider: True)
    client = TestClient(main.app)

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "checks": {
            "wechat_token_configured": True,
            "llm_provider": "zhipu",
            "llm_key_configured": True,
            "llm_sdk_available": True,
            "vision_supported": True,
        },
    }


def test_ready_reports_missing_model_sdk(monkeypatch):
    monkeypatch.setenv("ZHIPU_API_KEY", "key")
    main.get_settings.cache_clear()
    monkeypatch.setattr(main, "llm_sdk_available", lambda provider: False)
    client = TestClient(main.app)

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["llm_sdk_available"] is False


def test_ready_requires_admin_token_when_configured(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    main.get_settings.cache_clear()
    client = TestClient(main.app)

    missing = client.get("/ready")
    wrong = client.get("/ready", headers={"X-Admin-Token": "wrong"})
    right = client.get("/ready", headers={"X-Admin-Token": "secret"})

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert right.status_code == 503


def test_metrics_accepts_admin_token_query_when_configured(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    monkeypatch.setenv("ALLOW_ADMIN_TOKEN_QUERY", "true")
    main.get_settings.cache_clear()
    client = TestClient(main.app)

    missing = client.get("/metrics")
    right = client.get("/metrics", params={"admin_token": "secret"})

    assert missing.status_code == 401
    assert right.status_code == 200
    assert right.json()["ok"] is True


def test_metrics_rejects_admin_token_query_by_default(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    main.get_settings.cache_clear()
    client = TestClient(main.app)

    query = client.get("/metrics", params={"admin_token": "secret"})
    header = client.get("/metrics", headers={"X-Admin-Token": "secret"})

    assert query.status_code == 401
    assert header.status_code == 200
    assert header.json()["ok"] is True


def test_metrics_reports_initial_snapshot():
    client = TestClient(main.app)

    response = client.get("/metrics")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["counters"]["wechat_messages_total"] == 0
    assert data["message_types"] == {}
    assert data["model_durations_ms"]["text"] == {
        "total_ms": 0.0,
        "max_ms": 0.0,
        "average_ms": 0.0,
    }
    assert data["cache_sizes"]["reply_cache"] == 0


def test_metrics_track_messages_and_cache_hits():
    client = TestClient(main.app)
    body = text_xml("metric-1", "帮助")

    first = client.post("/wechat", params=signed_params(), content=body)
    second = client.post("/wechat", params=signed_params(), content=body)
    metrics = client.get("/metrics").json()

    assert first.status_code == 200
    assert second.status_code == 200
    assert metrics["counters"]["wechat_messages_total"] == 2
    assert metrics["counters"]["wechat_replies_total"] == 2
    assert metrics["counters"]["reply_cache_hits_total"] == 1
    assert metrics["message_types"]["text"] == 2
    assert metrics["cache_sizes"]["reply_cache"] == 1


def test_metrics_track_invalid_signature_and_parse_error():
    client = TestClient(main.app)
    params = signed_params()
    params["signature"] = "bad"

    bad_signature = client.get("/wechat", params={**params, "echostr": "pong"})
    bad_xml = client.post("/wechat", params=signed_params(), content=b"<xml>")
    metrics = client.get("/metrics").json()

    assert bad_signature.status_code == 403
    assert bad_xml.status_code == 200
    assert metrics["counters"]["invalid_signature_total"] == 1
    assert metrics["counters"]["xml_parse_errors_total"] == 1


def test_wechat_server_verification_accepts_valid_signature():
    client = TestClient(main.app)

    response = client.get("/wechat", params=signed_params(echostr="pong"))

    assert response.status_code == 200
    assert response.text == "pong"


def test_wechat_server_verification_rejects_invalid_signature():
    client = TestClient(main.app)
    params = signed_params(echostr="pong")
    params["signature"] = "bad-signature"

    response = client.get("/wechat", params=params)

    assert response.status_code == 403


def test_wechat_server_verification_rejects_stale_timestamp():
    client = TestClient(main.app)
    timestamp = str(int(time.time()) - 3600)
    nonce = "nonce"

    response = client.get(
        "/wechat",
        params={
            "signature": sign(timestamp, nonce),
            "timestamp": timestamp,
            "nonce": nonce,
            "echostr": "pong",
        },
    )

    assert response.status_code == 403


def test_text_message_duplicate_uses_cached_reply(monkeypatch):
    client = TestClient(main.app)
    calls = {"count": 0}

    def fake_reply_to_user_text(user_text: str, user_id: str = "") -> str:
        calls["count"] += 1
        return f"reply to {user_text}"

    monkeypatch.setattr(main, "reply_to_user_text", fake_reply_to_user_text)

    first = client.post("/wechat", params=signed_params(), content=text_xml())
    second = client.post("/wechat", params=signed_params(), content=text_xml())

    assert first.status_code == 200
    assert second.status_code == 200
    assert "reply to hello" in first.text
    assert second.text == first.text
    assert calls["count"] == 1


def test_concurrent_duplicate_messages_share_inflight_reply(monkeypatch):
    started = Event()
    release = Event()
    calls = {"count": 0}

    def fake_reply_to_user_text(user_text: str, user_id: str = "") -> str:
        calls["count"] += 1
        started.set()
        assert release.wait(timeout=2)
        return f"reply to {user_text}"

    def post_message():
        with TestClient(main.app) as client:
            return client.post("/wechat", params=signed_params(), content=text_xml())

    monkeypatch.setattr(main, "reply_to_user_text", fake_reply_to_user_text)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(post_message)
        assert started.wait(timeout=1)
        second_future = executor.submit(post_message)
        time.sleep(0.05)
        release.set()
        first = first_future.result(timeout=2)
        second = second_future.result(timeout=2)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.text == second.text
    assert calls["count"] == 1
    assert main.metrics_snapshot()["counters"]["reply_inflight_hits_total"] == 1


def test_rate_limit_blocks_repeated_model_messages(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_MAX_MESSAGES", "1")
    main.get_settings.cache_clear()
    client = TestClient(main.app)
    calls = {"count": 0}

    def fake_reply_to_user_text(user_text: str, user_id: str = "") -> str:
        calls["count"] += 1
        return "model reply"

    monkeypatch.setattr(main, "reply_to_user_text", fake_reply_to_user_text)

    first = client.post("/wechat", params=signed_params(), content=text_xml("101", "hello"))
    second = client.post("/wechat", params=signed_params(), content=text_xml("102", "again"))

    assert first.status_code == 200
    assert second.status_code == 200
    assert "model reply" in first.text
    assert "发得有点快" in second.text
    assert calls["count"] == 1
    assert client.get("/metrics").json()["counters"]["rate_limited_total"] == 1


def test_rate_limit_does_not_block_help_or_reset(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_MAX_MESSAGES", "1")
    main.get_settings.cache_clear()
    client = TestClient(main.app)
    monkeypatch.setattr(main, "reply_to_text", lambda user_text, history=None: "model reply")

    client.post("/wechat", params=signed_params(), content=text_xml("201", "hello"))
    help_response = client.post("/wechat", params=signed_params(), content=text_xml("202", "帮助"))
    reset_response = client.post(
        "/wechat",
        params=signed_params(),
        content=text_xml("203", "清空会话"),
    )

    assert "清空会话" in help_response.text
    assert "已清空当前会话上下文" in reset_response.text


def test_rate_limit_can_be_disabled(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_MAX_MESSAGES", "0")
    main.get_settings.cache_clear()
    client = TestClient(main.app)
    calls = {"count": 0}

    def fake_reply_to_user_text(user_text: str, user_id: str = "") -> str:
        calls["count"] += 1
        return "model reply"

    monkeypatch.setattr(main, "reply_to_user_text", fake_reply_to_user_text)

    first = client.post("/wechat", params=signed_params(), content=text_xml("301", "hello"))
    second = client.post("/wechat", params=signed_params(), content=text_xml("302", "again"))

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls["count"] == 2


def test_oversized_payload_is_rejected(monkeypatch):
    monkeypatch.setenv("MAX_REQUEST_BYTES", "1024")
    main.get_settings.cache_clear()
    client = TestClient(main.app)

    response = client.post("/wechat", params=signed_params(), content=b"x" * 1025)

    assert response.status_code == 413


def test_xml_entity_payload_is_ignored_safely():
    client = TestClient(main.app)
    payload = b"""<?xml version="1.0"?>
    <!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
    <xml>
      <ToUserName>&xxe;</ToUserName>
      <FromUserName>user</FromUserName>
      <CreateTime>123</CreateTime>
      <MsgType>text</MsgType>
      <Content>hello</Content>
      <MsgId>99</MsgId>
    </xml>
    """

    response = client.post("/wechat", params=signed_params(), content=payload)

    assert response.status_code == 200
    assert response.text == "success"


def test_content_length_limit_is_checked_before_body_read(monkeypatch):
    monkeypatch.setenv("MAX_REQUEST_BYTES", "1024")
    main.get_settings.cache_clear()
    client = TestClient(main.app)

    response = client.post(
        "/wechat",
        params=signed_params(),
        content=b"<xml />",
        headers={"Content-Length": "1025"},
    )

    assert response.status_code == 413


def test_cdata_splits_embedded_cdata_end_marker():
    rendered = main.cdata("a]]>b")

    assert rendered == "<![CDATA[a]]]]><![CDATA[>b]]>"


def test_trim_wechat_text_handles_tiny_limit(monkeypatch):
    monkeypatch.setenv("MAX_REPLY_CHARS", "8")
    main.get_settings.cache_clear()

    trimmed = main.trim_wechat_text("abcdefghijklmnopqrstuvwxyz")

    assert trimmed == "abcdefgh"


def test_completion_content_uses_fallback_for_empty_response():
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=""))])

    assert main.completion_content(response, "fallback") == "fallback"


def test_reply_to_text_trims_long_model_input(monkeypatch):
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="model reply"))]
            )

    monkeypatch.setenv("ZHIPU_API_KEY", "key")
    monkeypatch.setenv("MAX_MODEL_INPUT_CHARS", "5")
    main.get_settings.cache_clear()
    monkeypatch.setattr(
        main,
        "get_llm_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
    )

    reply = main.reply_to_text("abcdefghijklmnopqrstuvwxyz")

    assert reply == "model reply"
    assert calls[0]["messages"][1] == {"role": "user", "content": "abcde"}
    assert main.metrics_snapshot()["counters"]["model_input_truncated_total"] == 1


def test_reply_to_user_text_remembers_trimmed_input(monkeypatch):
    monkeypatch.setenv("MAX_MODEL_INPUT_CHARS", "4")
    main.get_settings.cache_clear()
    monkeypatch.setattr(main, "reply_to_text", lambda user_text, history=None: f"reply {user_text}")

    reply = main.reply_to_user_text("abcdef", "user")

    assert reply == "reply abcd"
    assert main.get_conversation_history("user") == [
        {"role": "user", "content": "abcd"},
        {"role": "assistant", "content": "reply abcd"},
    ]


def test_model_input_trimming_can_be_disabled(monkeypatch):
    monkeypatch.setenv("MAX_MODEL_INPUT_CHARS", "0")
    main.get_settings.cache_clear()
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="model reply"))]
            )

    monkeypatch.setenv("ZHIPU_API_KEY", "key")
    main.get_settings.cache_clear()
    monkeypatch.setattr(
        main,
        "get_llm_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
    )

    main.reply_to_text("abcdef")

    assert calls[0]["messages"][1]["content"] == "abcdef"
    assert main.metrics_snapshot()["counters"]["model_input_truncated_total"] == 0


def test_help_command_does_not_call_model(monkeypatch):
    def fail_if_called():
        raise AssertionError("model should not be called for help")

    monkeypatch.setattr(main, "get_llm_client", fail_if_called)

    reply = main.reply_to_user_text("help", "user")

    assert "清空会话" in reply


def test_reset_command_clears_conversation(monkeypatch):
    monkeypatch.setattr(main, "reply_to_text", lambda user_text, history=None: "reply")

    main.reply_to_user_text("第一句", "user")
    assert main.get_conversation_history("user")

    reply = main.reply_to_user_text("清空会话", "user")

    assert reply == "已清空当前会话上下文。"
    assert main.get_conversation_history("user") == []


def test_reply_to_user_text_includes_recent_conversation(monkeypatch):
    calls = []

    def fake_reply_to_text(user_text, history=None):
        calls.append({"user_text": user_text, "history": list(history or [])})
        return f"reply {len(calls)}"

    monkeypatch.setattr(main, "reply_to_text", fake_reply_to_text)

    first = main.reply_to_user_text("第一句", "user")
    second = main.reply_to_user_text("继续说", "user")

    assert first == "reply 1"
    assert second == "reply 2"
    assert calls[0]["history"] == []
    assert calls[1]["history"] == [
        {"role": "user", "content": "第一句"},
        {"role": "assistant", "content": "reply 1"},
    ]


def test_image_reply_is_remembered_for_followup(monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(main, "reply_to_image", lambda image_url: "这张图里有一只杯子")
    calls = []

    response = client.post("/wechat", params=signed_params(), content=image_xml())
    assert response.status_code == 200

    def fake_reply_to_text(user_text, history=None):
        calls.append(list(history or []))
        return "followup reply"

    monkeypatch.setattr(main, "reply_to_text", fake_reply_to_text)

    reply = main.reply_to_user_text("刚才那张图有什么？", "user")

    assert reply == "followup reply"
    assert calls[0] == [
        {"role": "user", "content": "用户发送了一张图片。"},
        {"role": "assistant", "content": "这张图里有一只杯子"},
    ]


def test_location_message_is_remembered_for_followup():
    client = TestClient(main.app)

    response = client.post("/wechat", params=signed_params(), content=location_xml())

    assert response.status_code == 200
    assert main.get_conversation_history("user") == [
        {"role": "user", "content": "用户发送了位置：深圳湾"},
        {
            "role": "assistant",
            "content": "我收到了位置：深圳湾。如果你需要路线或附近建议，请再补充你的具体问题。",
        },
    ]


def test_conversation_memory_can_be_disabled(monkeypatch):
    monkeypatch.setenv("CONVERSATION_MEMORY_MAX_TURNS", "0")
    main.get_settings.cache_clear()
    calls = []

    def fake_reply_to_text(user_text, history=None):
        calls.append(list(history or []))
        return "reply"

    monkeypatch.setattr(main, "reply_to_text", fake_reply_to_text)

    main.reply_to_user_text("第一句", "user")
    main.reply_to_user_text("继续说", "user")

    assert calls == [[], []]
    assert main.get_conversation_history("user") == []


def test_conversation_memory_keeps_configured_turn_count(monkeypatch):
    monkeypatch.setenv("CONVERSATION_MEMORY_MAX_TURNS", "1")
    main.get_settings.cache_clear()
    monkeypatch.setattr(main, "reply_to_text", lambda user_text, history=None: f"reply {user_text}")

    main.reply_to_user_text("第一句", "user")
    main.reply_to_user_text("第二句", "user")

    assert main.get_conversation_history("user") == [
        {"role": "user", "content": "第二句"},
        {"role": "assistant", "content": "reply 第二句"},
    ]


def test_reply_to_text_calls_configured_text_model(monkeypatch):
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="model reply"))]
            )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    monkeypatch.setenv("ZHIPU_API_KEY", "key")
    monkeypatch.setenv("TEXT_MODEL", "text-model")
    main.get_settings.cache_clear()
    monkeypatch.setattr(main, "get_llm_client", lambda: fake_client)

    reply = main.reply_to_text("hello")

    assert reply == "model reply"
    assert calls[0]["model"] == "text-model"
    assert calls[0]["max_tokens"] == 800
    assert calls[0]["messages"][1] == {"role": "user", "content": "hello"}
    assert main.metrics_snapshot()["counters"]["model_text_calls_total"] == 1
    assert main.metrics_snapshot()["counters"]["model_image_calls_total"] == 0


def test_model_concurrency_limit_rejects_excess_call(monkeypatch):
    started = Event()
    release = Event()
    calls = {"count": 0}

    class BlockingCompletions:
        def create(self, **kwargs):
            calls["count"] += 1
            started.set()
            assert release.wait(timeout=2)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="model reply"))]
            )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=BlockingCompletions())
    )
    monkeypatch.setenv("ZHIPU_API_KEY", "key")
    monkeypatch.setenv("MODEL_MAX_CONCURRENT_CALLS", "1")
    main.get_settings.cache_clear()
    monkeypatch.setattr(main, "get_llm_client", lambda: fake_client)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(main.reply_to_text, "first")
        assert started.wait(timeout=1)
        second_future = executor.submit(main.reply_to_text, "second")
        time.sleep(0.05)
        release.set()

        assert first_future.result(timeout=2) == "model reply"
        with pytest.raises(main.ModelBusyError):
            second_future.result(timeout=2)

    assert calls["count"] == 1
    assert main.metrics_snapshot()["counters"]["model_busy_total"] == 1


def test_reply_to_text_uses_configured_model_max_tokens(monkeypatch):
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="model reply"))]
            )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    monkeypatch.setenv("ZHIPU_API_KEY", "key")
    monkeypatch.setenv("MODEL_MAX_TOKENS", "120")
    main.get_settings.cache_clear()
    monkeypatch.setattr(main, "get_llm_client", lambda: fake_client)

    reply = main.reply_to_text("hello")

    assert reply == "model reply"
    assert calls[0]["max_tokens"] == 120


def test_reply_to_text_records_model_duration(monkeypatch):
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="model reply"))]
            )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    times = iter([100.0, 100.2])
    monkeypatch.setenv("ZHIPU_API_KEY", "key")
    main.get_settings.cache_clear()
    monkeypatch.setattr(main, "get_llm_client", lambda: fake_client)
    monkeypatch.setattr(main.time, "perf_counter", lambda: next(times))

    reply = main.reply_to_text("hello")

    assert reply == "model reply"
    assert calls
    assert main.metrics_snapshot()["model_durations_ms"]["text"] == {
        "total_ms": 200.0,
        "max_ms": 200.0,
        "average_ms": 200.0,
    }


def test_reply_to_text_tracks_model_call_errors(monkeypatch):
    class FailingCompletions:
        def create(self, **kwargs):
            raise RuntimeError("model failed")

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FailingCompletions())
    )
    monkeypatch.setenv("ZHIPU_API_KEY", "key")
    main.get_settings.cache_clear()
    monkeypatch.setattr(main, "get_llm_client", lambda: fake_client)

    with pytest.raises(RuntimeError, match="model failed"):
        main.reply_to_text("hello")

    counters = main.metrics_snapshot()["counters"]
    assert counters["model_text_calls_total"] == 1
    assert counters["model_call_errors_total"] == 1


def test_reply_to_image_calls_configured_vision_model(monkeypatch):
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="image reply"))]
            )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    monkeypatch.setenv("ZHIPU_API_KEY", "key")
    monkeypatch.setenv("VISION_MODEL", "vision-model")
    main.get_settings.cache_clear()
    monkeypatch.setattr(main, "get_llm_client", lambda: fake_client)

    reply = main.reply_to_image("https://example.com/image.jpg")

    assert reply == "image reply"
    assert calls[0]["model"] == "vision-model"
    assert calls[0]["max_tokens"] == 800
    assert calls[0]["messages"][0]["content"][1]["image_url"]["url"] == (
        "https://example.com/image.jpg"
    )
    counters = main.metrics_snapshot()["counters"]
    assert counters["model_text_calls_total"] == 0
    assert counters["model_image_calls_total"] == 1


def test_reply_to_image_uses_configured_model_max_tokens(monkeypatch):
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="image reply"))]
            )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    monkeypatch.setenv("ZHIPU_API_KEY", "key")
    monkeypatch.setenv("MODEL_MAX_TOKENS", "120")
    main.get_settings.cache_clear()
    monkeypatch.setattr(main, "get_llm_client", lambda: fake_client)

    reply = main.reply_to_image("https://example.com/image.jpg")

    assert reply == "image reply"
    assert calls[0]["max_tokens"] == 120


def test_reply_to_image_records_model_duration(monkeypatch):
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="image reply"))]
            )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    times = iter([50.0, 50.075])
    monkeypatch.setenv("ZHIPU_API_KEY", "key")
    main.get_settings.cache_clear()
    monkeypatch.setattr(main, "get_llm_client", lambda: fake_client)
    monkeypatch.setattr(main.time, "perf_counter", lambda: next(times))

    reply = main.reply_to_image("https://example.com/image.jpg")

    assert reply == "image reply"
    assert calls
    assert main.metrics_snapshot()["model_durations_ms"]["image"] == {
        "total_ms": 75.0,
        "max_ms": 75.0,
        "average_ms": 75.0,
    }


def test_reply_to_image_rejects_non_http_url():
    assert "可用的图片地址" in main.reply_to_image("file:///tmp/image.jpg")
    assert main.metrics_snapshot()["counters"]["model_image_calls_total"] == 0


class ImmediateThread:
    """Runs the target synchronously so alert-thread tests stay deterministic."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


def test_get_moderation_blocklist_returns_empty_set_for_missing_file():
    assert main.get_moderation_blocklist("") == frozenset()
    assert main.get_moderation_blocklist("/no/such/file.txt") == frozenset()


def test_moderate_reply_passes_through_without_blocklist():
    assert main.moderate_reply("hello") == "hello"


def test_reply_to_text_applies_moderation_blocklist(monkeypatch, tmp_path):
    blocklist_file = tmp_path / "blocklist.txt"
    blocklist_file.write_text("敏感词\n", encoding="utf-8")
    monkeypatch.setenv("ZHIPU_API_KEY", "key")
    monkeypatch.setenv("MODERATION_BLOCKLIST_FILE", str(blocklist_file))
    main.get_settings.cache_clear()

    class FakeCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="含有敏感词的回复"))]
            )

    monkeypatch.setattr(
        main,
        "get_llm_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
    )

    reply = main.reply_to_text("测试")

    assert reply == main.MODERATION_BLOCKED_REPLY
    assert main.metrics_snapshot()["counters"]["moderation_blocked_total"] == 1


def test_reply_to_text_ignores_non_matching_blocklist(monkeypatch, tmp_path):
    blocklist_file = tmp_path / "blocklist.txt"
    blocklist_file.write_text("敏感词\n", encoding="utf-8")
    monkeypatch.setenv("ZHIPU_API_KEY", "key")
    monkeypatch.setenv("MODERATION_BLOCKLIST_FILE", str(blocklist_file))
    main.get_settings.cache_clear()

    class FakeCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="正常回复"))]
            )

    monkeypatch.setattr(
        main,
        "get_llm_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
    )

    reply = main.reply_to_text("测试")

    assert reply == "正常回复"
    assert main.metrics_snapshot()["counters"]["moderation_blocked_total"] == 0


def test_call_glm_retries_once_on_connect_error(monkeypatch):
    calls = {"count": 0}

    class FlakyCompletions:
        def create(self, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise httpx.ConnectError("boom")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FlakyCompletions()))
    monkeypatch.setenv("ZHIPU_API_KEY", "key")
    main.get_settings.cache_clear()
    monkeypatch.setattr(main, "get_llm_client", lambda: fake_client)

    response = main.call_glm(
        "text", "model", [{"role": "user", "content": "hi"}], temperature=0.5, max_tokens=100
    )

    assert main.completion_content(response, "") == "ok"
    assert calls["count"] == 2
    assert main.metrics_snapshot()["counters"]["model_call_errors_total"] == 0


def test_call_glm_does_not_retry_on_read_timeout(monkeypatch):
    calls = {"count": 0}

    class TimeoutCompletions:
        def create(self, **kwargs):
            calls["count"] += 1
            raise httpx.ReadTimeout("timeout")

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=TimeoutCompletions()))
    monkeypatch.setenv("ZHIPU_API_KEY", "key")
    main.get_settings.cache_clear()
    monkeypatch.setattr(main, "get_llm_client", lambda: fake_client)

    with pytest.raises(httpx.ReadTimeout):
        main.call_glm(
            "text", "model", [{"role": "user", "content": "hi"}], temperature=0.5, max_tokens=100
        )

    assert calls["count"] == 1
    assert main.metrics_snapshot()["counters"]["model_call_errors_total"] == 1


def test_call_glm_retry_failure_is_not_retried_again(monkeypatch):
    calls = {"count": 0}

    class AlwaysFailingCompletions:
        def create(self, **kwargs):
            calls["count"] += 1
            raise httpx.ConnectError("still down")

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=AlwaysFailingCompletions())
    )
    monkeypatch.setenv("ZHIPU_API_KEY", "key")
    main.get_settings.cache_clear()
    monkeypatch.setattr(main, "get_llm_client", lambda: fake_client)

    with pytest.raises(httpx.ConnectError):
        main.call_glm("text", "model", [], temperature=0.5, max_tokens=100)

    assert calls["count"] == 2
    assert main.metrics_snapshot()["counters"]["model_call_errors_total"] == 1


def test_call_glm_raises_when_circuit_open(monkeypatch):
    monkeypatch.setenv("ZHIPU_API_KEY", "key")
    main.get_settings.cache_clear()
    with main._circuit_lock:
        main._circuit_state["open_until"] = time.time() + 30

    with pytest.raises(main.ModelUnavailableError):
        main.call_glm("text", "model", [], temperature=0.5, max_tokens=100)

    assert main.metrics_snapshot()["counters"]["model_circuit_rejected_total"] == 1


def test_record_model_failure_opens_circuit_after_threshold(monkeypatch):
    monkeypatch.setenv("MODEL_CIRCUIT_BREAKER_THRESHOLD", "2")
    monkeypatch.setenv("MODEL_CIRCUIT_BREAKER_COOLDOWN_SECONDS", "30")
    main.get_settings.cache_clear()

    main.record_model_failure()
    assert main.circuit_is_open() is False

    main.record_model_failure()

    assert main.circuit_is_open() is True
    assert main.metrics_snapshot()["counters"]["model_circuit_open_total"] == 1


def test_record_model_success_clears_failure_history(monkeypatch):
    monkeypatch.setenv("MODEL_CIRCUIT_BREAKER_THRESHOLD", "2")
    main.get_settings.cache_clear()

    main.record_model_failure()
    main.record_model_success()
    main.record_model_failure()

    assert main.circuit_is_open() is False
    assert main.metrics_snapshot()["counters"]["model_circuit_open_total"] == 0


def test_failures_recorded_while_open_are_ignored(monkeypatch):
    monkeypatch.setenv("MODEL_CIRCUIT_BREAKER_THRESHOLD", "2")
    monkeypatch.setenv("MODEL_CIRCUIT_BREAKER_WINDOW_SECONDS", "600")
    monkeypatch.setenv("MODEL_CIRCUIT_BREAKER_COOLDOWN_SECONDS", "30")
    main.get_settings.cache_clear()

    main.record_model_failure()
    main.record_model_failure()
    assert main.circuit_is_open() is True

    # Requests that were already in flight when the breaker tripped land here.
    main.record_model_failure()
    main.record_model_failure()

    with main._circuit_lock:
        assert main._circuit_state["failures"] == []
    # They must not extend the cooldown or count toward reopening either.
    assert main.metrics_snapshot()["counters"]["model_circuit_open_total"] == 1


def test_failed_half_open_probe_reopens_immediately(monkeypatch):
    monkeypatch.setenv("MODEL_CIRCUIT_BREAKER_THRESHOLD", "2")
    monkeypatch.setenv("MODEL_CIRCUIT_BREAKER_WINDOW_SECONDS", "600")
    monkeypatch.setenv("MODEL_CIRCUIT_BREAKER_COOLDOWN_SECONDS", "30")
    main.get_settings.cache_clear()

    main.record_model_failure()
    main.record_model_failure()
    assert main.circuit_is_open() is True

    with main._circuit_lock:
        main._circuit_state["open_until"] = time.time() - 1
    assert main.circuit_is_open() is False

    # The one request admitted after cooldown is the probe; it failing means the
    # upstream is still down, so reopen without waiting for another threshold.
    main.record_model_failure()

    assert main.circuit_is_open() is True
    assert main.metrics_snapshot()["counters"]["model_circuit_open_total"] == 2


def test_successful_half_open_probe_closes_the_circuit(monkeypatch):
    monkeypatch.setenv("MODEL_CIRCUIT_BREAKER_THRESHOLD", "2")
    monkeypatch.setenv("MODEL_CIRCUIT_BREAKER_COOLDOWN_SECONDS", "30")
    main.get_settings.cache_clear()

    main.record_model_failure()
    main.record_model_failure()
    with main._circuit_lock:
        main._circuit_state["open_until"] = time.time() - 1

    main.record_model_success()

    with main._circuit_lock:
        assert main._circuit_state["open_until"] == 0.0
    # Back in the closed state: a single failure starts a fresh window.
    main.record_model_failure()
    assert main.circuit_is_open() is False
    assert main.metrics_snapshot()["counters"]["model_circuit_open_total"] == 1


def test_circuit_open_rejection_is_counted_once_end_to_end(monkeypatch):
    monkeypatch.setenv("ZHIPU_API_KEY", "key")
    main.get_settings.cache_clear()
    with main._circuit_lock:
        main._circuit_state["open_until"] = time.time() + 30
    client = TestClient(main.app)

    response = client.post("/wechat", params=signed_params(), content=text_xml("910", "在吗"))

    assert response.status_code == 200
    assert main.MODEL_CIRCUIT_OPEN_REPLY in response.text
    # call_glm counts the rejection; build_reply must not count it again.
    assert main.metrics_snapshot()["counters"]["model_circuit_rejected_total"] == 1


def test_record_model_failure_opening_circuit_sends_alert(monkeypatch):
    monkeypatch.setenv("MODEL_CIRCUIT_BREAKER_THRESHOLD", "1")
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://open.feishu.cn/webhook/abc")
    main.get_settings.cache_clear()
    calls = []
    monkeypatch.setattr(main, "Thread", ImmediateThread)
    monkeypatch.setattr(
        main.httpx, "post", lambda url, json=None, timeout=None: calls.append((url, json))
    )

    main.record_model_failure()

    assert len(calls) == 1
    assert calls[0][0] == "https://open.feishu.cn/webhook/abc"
    assert calls[0][1]["msg_type"] == "text"


def test_send_alert_is_noop_without_webhook_configured(monkeypatch):
    calls = []
    monkeypatch.setattr(main.httpx, "post", lambda *a, **k: calls.append(1))

    main.send_alert("no webhook configured")

    assert calls == []


def test_send_alert_throttles_repeated_alerts(monkeypatch):
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://open.feishu.cn/webhook/abc")
    monkeypatch.setenv("ALERT_MIN_INTERVAL_SECONDS", "300")
    main.get_settings.cache_clear()
    calls = []
    monkeypatch.setattr(main, "Thread", ImmediateThread)
    monkeypatch.setattr(main.httpx, "post", lambda *a, **k: calls.append(1))

    main.send_alert("first")
    main.send_alert("second")

    assert len(calls) == 1


def test_wechat_reply_shows_circuit_open_message_when_model_unavailable(monkeypatch):
    def raise_unavailable(user_text, user_id=""):
        raise main.ModelUnavailableError("circuit open")

    monkeypatch.setattr(main, "reply_to_user_text", raise_unavailable)
    client = TestClient(main.app)

    response = client.post("/wechat", params=signed_params(), content=text_xml("500", "hello"))

    assert response.status_code == 200
    assert main.MODEL_CIRCUIT_OPEN_REPLY in response.text
    # This path bypasses call_glm, which is what owns the rejection counter, so
    # the count stays 0 here; the end-to-end test above covers the real path.
    assert main.metrics_snapshot()["counters"]["model_circuit_rejected_total"] == 0


def test_delete_my_data_command_clears_conversation_and_rate_limit(monkeypatch):
    monkeypatch.setattr(main, "reply_to_text", lambda user_text, history=None: "reply")
    main.reply_to_user_text("第一句", "user")
    main.rate_limit_exceeded("user")
    assert main.get_conversation_history("user")
    with main._rate_limit_lock:
        assert "user" in main._rate_limit_cache

    reply = main.reply_to_user_text("删除我的数据", "user")

    assert reply == "已清空你的会话记忆和限流记录。"
    assert main.get_conversation_history("user") == []
    with main._rate_limit_lock:
        assert "user" not in main._rate_limit_cache


def test_delete_my_data_command_accepts_english_phrase(monkeypatch):
    monkeypatch.setattr(main, "reply_to_text", lambda user_text, history=None: "reply")
    main.reply_to_user_text("hi", "user")

    reply = main.reply_to_user_text("Delete My Data", "user")

    assert reply == "已清空你的会话记忆和限流记录。"
    assert main.get_conversation_history("user") == []


def test_require_diagnostic_auth_logs_success_and_failure(monkeypatch, caplog):
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    main.get_settings.cache_clear()
    client = TestClient(main.app)

    with caplog.at_level(logging.INFO, logger="wechat_glm_assistant"):
        client.get("/ready", headers={"X-Admin-Token": "wrong"})
        client.get("/ready", headers={"X-Admin-Token": "secret"})

    assert "admin_auth_failed" in caplog.text
    assert "admin_auth_ok" in caplog.text


def test_metrics_prometheus_requires_admin_token_when_configured(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    main.get_settings.cache_clear()
    client = TestClient(main.app)

    missing = client.get("/metrics/prometheus")
    right = client.get("/metrics/prometheus", headers={"X-Admin-Token": "secret"})

    assert missing.status_code == 401
    assert right.status_code == 200


def test_metrics_prometheus_returns_exposition_format():
    client = TestClient(main.app)

    response = client.get("/metrics/prometheus")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "wechat_glm_uptime_seconds" in response.text
    assert "wechat_glm_http_requests_total" in response.text
    assert "wechat_glm_cache_reply_cache" in response.text


def test_metrics_prometheus_includes_message_type_and_model_duration_lines():
    client = TestClient(main.app)
    client.post("/wechat", params=signed_params(), content=text_xml("600", "帮助"))

    response = client.get("/metrics/prometheus")

    assert 'wechat_glm_message_type_total{type="text"}' in response.text
    assert 'wechat_glm_model_duration_ms_total{kind="text"}' in response.text


def test_deepseek_provider_resolves_key_and_default_base_url(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    main.get_settings.cache_clear()
    main.get_llm_client.cache_clear()

    settings = main.get_settings()
    client = main.get_llm_client()

    assert settings.llm_provider == "deepseek"
    assert settings.llm_api_key == "ds-key"
    assert isinstance(client, main.OpenAICompatibleClient)
    assert client._base_url == "https://api.deepseek.com/v1"


def test_llm_api_key_wins_over_provider_alias(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "generic-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    main.get_settings.cache_clear()

    assert main.get_settings().llm_api_key == "generic-key"


def test_unknown_provider_falls_back_to_zhipu(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    main.get_settings.cache_clear()

    assert main.get_settings().llm_provider == "zhipu"


def test_openai_compatible_client_posts_chat_completions_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["Authorization"]
        captured["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "你好，我是 DeepSeek"}}]},
        )

    client = main.OpenAICompatibleClient(
        "ds-key",
        "https://api.deepseek.com/v1",
        httpx.Timeout(4.0, connect=3.0),
        transport=httpx.MockTransport(handler),
    )

    reply = main.completion_content(
        client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": "你好"}],
            temperature=0.6,
            max_tokens=100,
        ),
        "fallback",
    )

    assert reply == "你好，我是 DeepSeek"
    assert captured["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert captured["auth"] == "Bearer ds-key"
    assert '"model":"deepseek-chat"' in captured["body"].replace(" ", "")


def test_openai_compatible_client_raises_on_error_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid key"}})

    client = main.OpenAICompatibleClient(
        "bad-key",
        "https://api.deepseek.com/v1",
        httpx.Timeout(4.0, connect=3.0),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(main.ModelCallError):
        client.create_completion("deepseek-chat", [], 0.5, 10)


def test_deepseek_image_message_returns_friendly_reply_without_model_call(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    main.get_settings.cache_clear()

    def fail_if_called():
        raise AssertionError("the model must not be called for images on deepseek")

    monkeypatch.setattr(main, "get_llm_client", fail_if_called)

    assert main.reply_to_image("https://example.com/a.jpg") == main.IMAGE_UNSUPPORTED_REPLY


def test_ready_passes_for_text_only_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "ds-key")
    main.get_settings.cache_clear()
    monkeypatch.setattr(main, "llm_sdk_available", lambda provider: True)
    client = TestClient(main.app)

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["checks"]["vision_supported"] is False



def test_placeholder_api_key_counts_as_not_configured(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "replace-with-your-deepseek-api-key")
    main.get_settings.cache_clear()
    client = TestClient(main.app)

    assert main.get_settings().llm_api_key == ""
    assert client.get("/ready").status_code == 503
    assert client.get("/ready").json()["checks"]["llm_key_configured"] is False

