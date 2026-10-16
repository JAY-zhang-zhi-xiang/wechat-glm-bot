from types import SimpleNamespace

import pytest

from scripts import setup_wechat_menu


@pytest.fixture(autouse=True)
def reset_settings(monkeypatch):
    monkeypatch.setenv("WECHAT_TOKEN", "test-token")
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("WECHAT_APP_ID", raising=False)
    monkeypatch.delenv("WECHAT_APP_SECRET", raising=False)
    setup_wechat_menu.main.get_settings.cache_clear()
    with setup_wechat_menu.main._access_token_lock:
        setup_wechat_menu.main._access_token_cache["token"] = ""
        setup_wechat_menu.main._access_token_cache["expires_at"] = 0.0
    yield
    setup_wechat_menu.main.get_settings.cache_clear()


def test_parse_args_requires_create_or_delete():
    with pytest.raises(SystemExit):
        setup_wechat_menu.parse_args([])


def test_parse_args_rejects_both_create_and_delete():
    with pytest.raises(SystemExit):
        setup_wechat_menu.parse_args(["--create", "--delete"])


def test_parse_args_accepts_create():
    args = setup_wechat_menu.parse_args(["--create"])

    assert args.create is True
    assert args.delete is False
    assert args.timeout == 5.0


def test_run_returns_error_when_credentials_missing(capsys):
    exit_code = setup_wechat_menu.run(["--create"])

    assert exit_code == 2
    assert "WECHAT_APP_ID" in capsys.readouterr().err


def test_run_returns_error_when_access_token_unavailable(monkeypatch, capsys):
    monkeypatch.setenv("WECHAT_APP_ID", "app-id")
    monkeypatch.setenv("WECHAT_APP_SECRET", "app-secret")
    setup_wechat_menu.main.get_settings.cache_clear()
    monkeypatch.setattr(setup_wechat_menu.main, "get_wechat_access_token", lambda: None)

    exit_code = setup_wechat_menu.run(["--create"])

    assert exit_code == 1
    assert "access_token" in capsys.readouterr().err


def test_run_create_calls_create_menu(monkeypatch):
    monkeypatch.setenv("WECHAT_APP_ID", "app-id")
    monkeypatch.setenv("WECHAT_APP_SECRET", "app-secret")
    setup_wechat_menu.main.get_settings.cache_clear()
    monkeypatch.setattr(setup_wechat_menu.main, "get_wechat_access_token", lambda: "token-123")
    calls = []
    monkeypatch.setattr(
        setup_wechat_menu, "create_menu", lambda token, timeout: calls.append((token, timeout)) or 0
    )

    exit_code = setup_wechat_menu.run(["--create"])

    assert exit_code == 0
    assert calls == [("token-123", 5.0)]


def test_run_delete_calls_delete_menu(monkeypatch):
    monkeypatch.setenv("WECHAT_APP_ID", "app-id")
    monkeypatch.setenv("WECHAT_APP_SECRET", "app-secret")
    setup_wechat_menu.main.get_settings.cache_clear()
    monkeypatch.setattr(setup_wechat_menu.main, "get_wechat_access_token", lambda: "token-123")
    calls = []
    monkeypatch.setattr(
        setup_wechat_menu, "delete_menu", lambda token, timeout: calls.append((token, timeout)) or 0
    )

    exit_code = setup_wechat_menu.run(["--delete"])

    assert exit_code == 0
    assert calls == [("token-123", 5.0)]


def test_create_menu_posts_default_buttons(monkeypatch):
    calls = []

    def fake_post(url, params=None, json=None, timeout=None):
        calls.append((url, params, json))
        return SimpleNamespace(json=lambda: {"errcode": 0})

    monkeypatch.setattr(setup_wechat_menu.httpx, "post", fake_post)

    exit_code = setup_wechat_menu.create_menu("token-123", 5.0)

    assert exit_code == 0
    assert calls[0][0] == setup_wechat_menu.WECHAT_MENU_CREATE_URL
    assert calls[0][1] == {"access_token": "token-123"}
    keys = [button["key"] for button in calls[0][2]["button"]]
    assert keys == [
        setup_wechat_menu.main.MENU_KEY_HELP,
        setup_wechat_menu.main.MENU_KEY_RESET,
        setup_wechat_menu.main.MENU_KEY_ABOUT,
    ]


def test_create_menu_reports_failure_on_errcode(monkeypatch, capsys):
    monkeypatch.setattr(
        setup_wechat_menu.httpx,
        "post",
        lambda *a, **k: SimpleNamespace(json=lambda: {"errcode": 40001, "errmsg": "bad token"}),
    )

    exit_code = setup_wechat_menu.create_menu("token-123", 5.0)

    assert exit_code == 1
    assert "menu create failed" in capsys.readouterr().err


def test_create_menu_reports_failure_on_network_error(monkeypatch, capsys):
    def fake_post(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(setup_wechat_menu.httpx, "post", fake_post)

    exit_code = setup_wechat_menu.create_menu("token-123", 5.0)

    assert exit_code == 1
    assert "menu create failed" in capsys.readouterr().err


def test_delete_menu_succeeds(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params))
        return SimpleNamespace(json=lambda: {"errcode": 0})

    monkeypatch.setattr(setup_wechat_menu.httpx, "get", fake_get)

    exit_code = setup_wechat_menu.delete_menu("token-123", 5.0)

    assert exit_code == 0
    assert calls[0][0] == setup_wechat_menu.WECHAT_MENU_DELETE_URL
    assert calls[0][1] == {"access_token": "token-123"}


def test_delete_menu_reports_failure_on_errcode(monkeypatch, capsys):
    monkeypatch.setattr(
        setup_wechat_menu.httpx,
        "get",
        lambda *a, **k: SimpleNamespace(json=lambda: {"errcode": 40001, "errmsg": "bad token"}),
    )

    exit_code = setup_wechat_menu.delete_menu("token-123", 5.0)

    assert exit_code == 1
    assert "menu delete failed" in capsys.readouterr().err
