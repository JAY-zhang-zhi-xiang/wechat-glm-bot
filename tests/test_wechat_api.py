import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture(autouse=True)
def reset_settings(monkeypatch):
    monkeypatch.setenv("WECHAT_TOKEN", "test-token")
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("WECHAT_APP_ID", raising=False)
    monkeypatch.delenv("WECHAT_APP_SECRET", raising=False)
    monkeypatch.delenv("ENABLE_CUSTOMER_SERVICE_FALLBACK", raising=False)
    monkeypatch.delenv("MAX_REPLY_CHARS", raising=False)
    main.get_settings.cache_clear()
    main.get_zhipu_client.cache_clear()
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
        main._access_token_cache["retry_after"] = 0.0
    with main._alert_lock:
        main._last_alert_at = 0.0
    yield
    main.get_settings.cache_clear()
    main.get_zhipu_client.cache_clear()
    main.get_moderation_blocklist.cache_clear()
    with main._access_token_lock:
        main._access_token_cache["token"] = ""
        main._access_token_cache["expires_at"] = 0.0
        main._access_token_cache["retry_after"] = 0.0
    with main._alert_lock:
        main._last_alert_at = 0.0


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


def text_xml(msg_id: str, content: str) -> bytes:
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


def click_xml(msg_id: str, event_key: str) -> bytes:
    return f"""
    <xml>
      <ToUserName><![CDATA[server]]></ToUserName>
      <FromUserName><![CDATA[user]]></FromUserName>
      <CreateTime>123</CreateTime>
      <MsgType><![CDATA[event]]></MsgType>
      <Event><![CDATA[CLICK]]></Event>
      <EventKey><![CDATA[{event_key}]]></EventKey>
      <MsgId>{msg_id}</MsgId>
    </xml>
    """.encode("utf-8")


def test_get_wechat_access_token_returns_none_without_credentials():
    assert main.get_wechat_access_token() is None


def test_get_wechat_access_token_fetches_and_caches(monkeypatch):
    monkeypatch.setenv("WECHAT_APP_ID", "app-id")
    monkeypatch.setenv("WECHAT_APP_SECRET", "app-secret")
    main.get_settings.cache_clear()
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params)
        return SimpleNamespace(
            json=lambda: {"access_token": "token-123", "expires_in": 7200}
        )

    monkeypatch.setattr(main.httpx, "get", fake_get)

    first = main.get_wechat_access_token()
    second = main.get_wechat_access_token()

    assert first == "token-123"
    assert second == "token-123"
    assert len(calls) == 1
    assert calls[0]["appid"] == "app-id"
    assert calls[0]["secret"] == "app-secret"


def test_get_wechat_access_token_refreshes_after_expiry(monkeypatch):
    monkeypatch.setenv("WECHAT_APP_ID", "app-id")
    monkeypatch.setenv("WECHAT_APP_SECRET", "app-secret")
    main.get_settings.cache_clear()
    calls = {"count": 0}

    def fake_get(url, params=None, timeout=None):
        calls["count"] += 1
        return SimpleNamespace(
            json=lambda: {"access_token": f"token-{calls['count']}", "expires_in": 7200}
        )

    monkeypatch.setattr(main.httpx, "get", fake_get)

    first = main.get_wechat_access_token()
    with main._access_token_lock:
        main._access_token_cache["expires_at"] = time.time() - 1
    second = main.get_wechat_access_token()

    assert first == "token-1"
    assert second == "token-2"
    assert calls["count"] == 2


def test_get_wechat_access_token_returns_none_on_network_error(monkeypatch):
    monkeypatch.setenv("WECHAT_APP_ID", "app-id")
    monkeypatch.setenv("WECHAT_APP_SECRET", "app-secret")
    main.get_settings.cache_clear()

    def fake_get(url, params=None, timeout=None):
        raise main.httpx.ConnectError("boom")

    monkeypatch.setattr(main.httpx, "get", fake_get)

    assert main.get_wechat_access_token() is None


def test_get_wechat_access_token_returns_none_on_errcode_response(monkeypatch):
    monkeypatch.setenv("WECHAT_APP_ID", "app-id")
    monkeypatch.setenv("WECHAT_APP_SECRET", "app-secret")
    main.get_settings.cache_clear()

    def fake_get(url, params=None, timeout=None):
        return SimpleNamespace(json=lambda: {"errcode": 40013, "errmsg": "invalid appid"})

    monkeypatch.setattr(main.httpx, "get", fake_get)

    assert main.get_wechat_access_token() is None


def test_get_wechat_access_token_rejects_non_integer_expires_in(monkeypatch):
    monkeypatch.setenv("WECHAT_APP_ID", "app-id")
    monkeypatch.setenv("WECHAT_APP_SECRET", "app-secret")
    main.get_settings.cache_clear()
    monkeypatch.setattr(
        main.httpx,
        "get",
        lambda *a, **k: SimpleNamespace(
            json=lambda: {"access_token": "token-123", "expires_in": "7200"}
        ),
    )

    assert main.get_wechat_access_token() is None


def test_get_wechat_access_token_does_not_log_the_token_on_malformed_response(
    monkeypatch, caplog
):
    monkeypatch.setenv("WECHAT_APP_ID", "app-id")
    monkeypatch.setenv("WECHAT_APP_SECRET", "app-secret")
    main.get_settings.cache_clear()
    monkeypatch.setattr(
        main.httpx,
        "get",
        lambda *a, **k: SimpleNamespace(
            json=lambda: {"access_token": "super-secret-token", "expires_in": None}
        ),
    )

    with caplog.at_level(logging.WARNING, logger="wechat_glm_assistant"):
        assert main.get_wechat_access_token() is None

    assert "missing fields" in caplog.text
    assert "super-secret-token" not in caplog.text


def test_get_wechat_access_token_clamps_zero_expires_in(monkeypatch):
    monkeypatch.setenv("WECHAT_APP_ID", "app-id")
    monkeypatch.setenv("WECHAT_APP_SECRET", "app-secret")
    main.get_settings.cache_clear()
    calls = {"count": 0}

    def fake_get(url, params=None, timeout=None):
        calls["count"] += 1
        return SimpleNamespace(
            json=lambda: {"access_token": f"token-{calls['count']}", "expires_in": 0}
        )

    monkeypatch.setattr(main.httpx, "get", fake_get)

    first = main.get_wechat_access_token()
    second = main.get_wechat_access_token()

    assert first == "token-1"
    # expires_at clamps to now, so the token is never served from a stale cache.
    assert second == "token-2"
    assert calls["count"] == 2


def test_get_wechat_access_token_clamps_negative_expires_in(monkeypatch):
    monkeypatch.setenv("WECHAT_APP_ID", "app-id")
    monkeypatch.setenv("WECHAT_APP_SECRET", "app-secret")
    main.get_settings.cache_clear()
    monkeypatch.setattr(
        main.httpx,
        "get",
        lambda *a, **k: SimpleNamespace(
            json=lambda: {"access_token": "token-123", "expires_in": -100}
        ),
    )

    assert main.get_wechat_access_token() == "token-123"
    with main._access_token_lock:
        assert main._access_token_cache["expires_at"] <= time.time()


def test_concurrent_token_refresh_issues_a_single_fetch(monkeypatch):
    monkeypatch.setenv("WECHAT_APP_ID", "app-id")
    monkeypatch.setenv("WECHAT_APP_SECRET", "app-secret")
    main.get_settings.cache_clear()
    calls = {"count": 0}
    started = Barrier(4)

    def slow_get(url, params=None, timeout=None):
        calls["count"] += 1
        time.sleep(0.05)
        return SimpleNamespace(
            json=lambda: {"access_token": "token-shared", "expires_in": 7200}
        )

    monkeypatch.setattr(main.httpx, "get", slow_get)

    def worker():
        started.wait(timeout=5)
        return main.get_wechat_access_token()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [future.result(timeout=5) for future in [pool.submit(worker) for _ in range(4)]]

    assert results == ["token-shared"] * 4
    assert calls["count"] == 1


def test_send_customer_service_text_succeeds(monkeypatch):
    monkeypatch.setenv("WECHAT_APP_ID", "app-id")
    monkeypatch.setenv("WECHAT_APP_SECRET", "app-secret")
    main.get_settings.cache_clear()
    monkeypatch.setattr(main, "get_wechat_access_token", lambda: "token-123")
    calls = []

    def fake_post(url, params=None, json=None, timeout=None):
        calls.append((url, params, json))
        return SimpleNamespace(json=lambda: {"errcode": 0})

    monkeypatch.setattr(main.httpx, "post", fake_post)

    result = main.send_customer_service_text("openid-1", "hello there")

    assert result is True
    assert calls[0][1] == {"access_token": "token-123"}
    assert calls[0][2] == {
        "touser": "openid-1",
        "msgtype": "text",
        "text": {"content": "hello there"},
    }
    assert main.metrics_snapshot()["counters"]["customer_service_sent_total"] == 1


def test_send_customer_service_text_fails_without_token(monkeypatch):
    monkeypatch.setattr(main, "get_wechat_access_token", lambda: None)

    result = main.send_customer_service_text("openid-1", "hello")

    assert result is False
    assert main.metrics_snapshot()["counters"]["customer_service_failed_total"] == 0


def test_send_customer_service_text_fails_on_errcode(monkeypatch):
    monkeypatch.setattr(main, "get_wechat_access_token", lambda: "token-123")
    monkeypatch.setattr(
        main.httpx,
        "post",
        lambda *a, **k: SimpleNamespace(json=lambda: {"errcode": 45015, "errmsg": "no session"}),
    )

    result = main.send_customer_service_text("openid-1", "hello")

    assert result is False
    assert main.metrics_snapshot()["counters"]["customer_service_failed_total"] == 1


def test_send_customer_service_text_fails_on_network_error(monkeypatch):
    monkeypatch.setattr(main, "get_wechat_access_token", lambda: "token-123")

    def fake_post(*args, **kwargs):
        raise main.httpx.ConnectError("boom")

    monkeypatch.setattr(main.httpx, "post", fake_post)

    result = main.send_customer_service_text("openid-1", "hello")

    assert result is False
    assert main.metrics_snapshot()["counters"]["customer_service_failed_total"] == 1


def test_wechat_route_schedules_followup_for_truncated_reply(monkeypatch):
    monkeypatch.setenv("WECHAT_APP_ID", "app-id")
    monkeypatch.setenv("WECHAT_APP_SECRET", "app-secret")
    monkeypatch.setenv("ENABLE_CUSTOMER_SERVICE_FALLBACK", "true")
    monkeypatch.setenv("MAX_REPLY_CHARS", "10")
    main.get_settings.cache_clear()

    full_reply = "这是一段很长的回复内容，超过了最大回复字符数限制。"
    monkeypatch.setattr(main, "reply_to_user_text", lambda user_text, user_id="": full_reply)
    sent = []
    monkeypatch.setattr(
        main, "send_customer_service_text", lambda to_openid, text: sent.append((to_openid, text))
    )

    client = TestClient(main.app)
    response = client.post("/wechat", params=signed_params(), content=text_xml("700", "hello"))

    assert response.status_code == 200
    assert sent == [("user", full_reply)]


def test_wechat_route_schedules_followup_for_fallback_reply(monkeypatch):
    monkeypatch.setenv("WECHAT_APP_ID", "app-id")
    monkeypatch.setenv("WECHAT_APP_SECRET", "app-secret")
    monkeypatch.setenv("ENABLE_CUSTOMER_SERVICE_FALLBACK", "true")
    main.get_settings.cache_clear()

    call_count = {"count": 0}

    def flaky_reply_to_user_text(user_text, user_id=""):
        call_count["count"] += 1
        if call_count["count"] == 1:
            raise main.ModelBusyError("busy")
        return "real answer"

    monkeypatch.setattr(main, "reply_to_user_text", flaky_reply_to_user_text)
    sent = []
    monkeypatch.setattr(
        main, "send_customer_service_text", lambda to_openid, text: sent.append((to_openid, text))
    )

    client = TestClient(main.app)
    response = client.post("/wechat", params=signed_params(), content=text_xml("701", "hello"))

    assert response.status_code == 200
    assert main.MODEL_BUSY_REPLY in response.text
    assert sent == [("user", "real answer")]
    assert call_count["count"] == 2


def test_wechat_route_does_not_schedule_followup_when_fallback_disabled(monkeypatch):
    monkeypatch.setenv("WECHAT_APP_ID", "app-id")
    monkeypatch.setenv("WECHAT_APP_SECRET", "app-secret")
    monkeypatch.setenv("MAX_REPLY_CHARS", "10")
    main.get_settings.cache_clear()

    monkeypatch.setattr(
        main, "reply_to_user_text", lambda user_text, user_id="": "回复内容超过了最大回复字符数限制。"
    )
    sent = []
    monkeypatch.setattr(
        main, "send_customer_service_text", lambda to_openid, text: sent.append((to_openid, text))
    )

    client = TestClient(main.app)
    response = client.post("/wechat", params=signed_params(), content=text_xml("702", "hello"))

    assert response.status_code == 200
    assert sent == []


def test_menu_click_help_key_returns_help_reply():
    client = TestClient(main.app)

    response = client.post(
        "/wechat", params=signed_params(), content=click_xml("800", main.MENU_KEY_HELP)
    )

    assert response.status_code == 200
    assert "清空会话" in response.text


def test_menu_click_reset_key_clears_conversation(monkeypatch):
    monkeypatch.setattr(main, "reply_to_text", lambda user_text, history=None: "reply")
    main.reply_to_user_text("第一句", "user")
    assert main.get_conversation_history("user")
    client = TestClient(main.app)

    response = client.post(
        "/wechat", params=signed_params(), content=click_xml("801", main.MENU_KEY_RESET)
    )

    assert response.status_code == 200
    assert "已清空当前会话上下文" in response.text
    assert main.get_conversation_history("user") == []


def test_menu_click_about_key_returns_about_text():
    client = TestClient(main.app)

    response = client.post(
        "/wechat", params=signed_params(), content=click_xml("802", main.MENU_KEY_ABOUT)
    )

    assert response.status_code == 200
    assert "贾维斯" in response.text


def test_menu_click_unknown_key_returns_generic_reply():
    client = TestClient(main.app)

    response = client.post(
        "/wechat", params=signed_params(), content=click_xml("803", "SOME_OTHER_KEY")
    )

    assert response.status_code == 200
    assert "收到菜单点击事件" in response.text
