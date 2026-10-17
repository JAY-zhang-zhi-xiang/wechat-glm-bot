from scripts import smoke_wechat


def test_wechat_signature_matches_expected_sha1():
    signature = smoke_wechat.wechat_signature("token", "123", "abc")

    assert signature == "84501b652458690801074dc0b44e9822dfb63245"


def test_endpoint_normalizes_slashes():
    assert smoke_wechat.endpoint("http://127.0.0.1:8000/", "/wechat") == (
        "http://127.0.0.1:8000/wechat"
    )


def test_cdata_splits_embedded_end_marker():
    assert smoke_wechat.cdata("a]]>b") == "<![CDATA[a]]]]><![CDATA[>b]]>"


def test_text_xml_contains_wechat_text_shape():
    xml = smoke_wechat.text_xml("帮助", "user")

    assert "<MsgType><![CDATA[text]]></MsgType>" in xml
    assert "<Content><![CDATA[帮助]]></Content>" in xml
    assert "<FromUserName><![CDATA[user]]></FromUserName>" in xml


def test_load_env_file_reads_simple_dotenv(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# comment",
                "WECHAT_TOKEN=from-file",
                "ADMIN_TOKEN='admin-from-file'",
                'SMOKE_BASE_URL="http://example.test"',
            ]
        ),
        encoding="utf-8",
    )

    values = smoke_wechat.load_env_file(str(env_file))

    assert values == {
        "WECHAT_TOKEN": "from-file",
        "ADMIN_TOKEN": "admin-from-file",
        "SMOKE_BASE_URL": "http://example.test",
    }


def test_load_env_file_handles_utf8_bom(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("\ufeffWECHAT_TOKEN=from-bom", encoding="utf-8")

    values = smoke_wechat.load_env_file(str(env_file))

    assert values["WECHAT_TOKEN"] == "from-bom"


def test_parse_args_uses_dotenv_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("SMOKE_BASE_URL", raising=False)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "WECHAT_TOKEN=from-file",
                "ADMIN_TOKEN=admin-from-file",
                "SMOKE_BASE_URL=http://from-file.test",
            ]
        ),
        encoding="utf-8",
    )

    args = smoke_wechat.parse_args([])

    assert args.token == "from-file"
    assert args.admin_token == "admin-from-file"
    assert args.base_url == "http://from-file.test"


def test_parse_args_environment_overrides_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WECHAT_TOKEN", "from-env")
    (tmp_path / ".env").write_text("WECHAT_TOKEN=from-file", encoding="utf-8")

    args = smoke_wechat.parse_args([])

    assert args.token == "from-env"


def test_parse_args_cli_overrides_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    (tmp_path / ".env").write_text("WECHAT_TOKEN=from-file", encoding="utf-8")

    args = smoke_wechat.parse_args(["--token", "from-cli"])

    assert args.token == "from-cli"


def test_run_smoke_default_flow_without_real_network(monkeypatch):
    posted_xml = []

    def fake_get(url, params, timeout):
        if url.endswith("/"):
            return smoke_wechat.HttpResult(200, '{"ok": true}')
        if url.endswith("/health"):
            return smoke_wechat.HttpResult(200, '{"ok": true}')
        assert params["echostr"] == "wechat-smoke-ok"
        return smoke_wechat.HttpResult(200, "wechat-smoke-ok")

    def fake_post_xml(url, params, xml, timeout):
        posted_xml.append(xml)
        if "<Content><![CDATA[帮助]]></Content>" in xml:
            return smoke_wechat.HttpResult(200, "清空会话")
        if "<MsgType><![CDATA[location]]></MsgType>" in xml:
            return smoke_wechat.HttpResult(200, "深圳市民中心")
        if "<Content><![CDATA[清空会话]]></Content>" in xml:
            return smoke_wechat.HttpResult(200, "已清空")
        raise AssertionError(f"unexpected xml: {xml}")

    monkeypatch.setattr(smoke_wechat, "get", fake_get)
    monkeypatch.setattr(smoke_wechat, "post_xml", fake_post_xml)

    events = smoke_wechat.run_smoke(
        base_url="http://127.0.0.1:8000",
        token="token",
        timeout=1,
        require_ready=False,
        admin_token="",
        with_model=False,
        model_text="hello",
        image_url="",
    )

    assert events == [
        "index ok",
        "health ok",
        "server verification ok",
        "help message ok",
        "location message ok",
        "reset message ok",
    ]
    assert len(posted_xml) == 3


def test_run_smoke_can_require_readiness_without_real_network(monkeypatch):
    checked_urls = []

    def fake_get(url, params, timeout, headers=None):
        checked_urls.append(url)
        if url.endswith("/"):
            return smoke_wechat.HttpResult(200, '{"ok": true}')
        if url.endswith("/health"):
            return smoke_wechat.HttpResult(200, '{"ok": true}')
        if url.endswith("/ready"):
            assert params is None
            assert headers == {"X-Admin-Token": "admin-secret"}
            return smoke_wechat.HttpResult(200, '{"ok": true}')
        return smoke_wechat.HttpResult(200, "wechat-smoke-ok")

    def fake_post_xml(url, params, xml, timeout):
        if "<Content><![CDATA[帮助]]></Content>" in xml:
            return smoke_wechat.HttpResult(200, "清空会话")
        if "<MsgType><![CDATA[location]]></MsgType>" in xml:
            return smoke_wechat.HttpResult(200, "深圳市民中心")
        if "<Content><![CDATA[清空会话]]></Content>" in xml:
            return smoke_wechat.HttpResult(200, "已清空")
        raise AssertionError(f"unexpected xml: {xml}")

    monkeypatch.setattr(smoke_wechat, "get", fake_get)
    monkeypatch.setattr(smoke_wechat, "post_xml", fake_post_xml)

    events = smoke_wechat.run_smoke(
        base_url="http://127.0.0.1:8000",
        token="token",
        timeout=1,
        require_ready=True,
        admin_token="admin-secret",
        with_model=False,
        model_text="hello",
        image_url="",
    )

    assert "readiness ok" in events
    assert "http://127.0.0.1:8000/ready" in checked_urls


def test_main_requires_token(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)

    exit_code = smoke_wechat.main(["--base-url", "http://127.0.0.1:8000"])

    assert exit_code == 2
    assert "WECHAT_TOKEN is required" in capsys.readouterr().err
