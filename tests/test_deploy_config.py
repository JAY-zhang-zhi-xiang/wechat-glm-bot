from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_nginx_uses_exact_locations_for_public_endpoints():
    config = (ROOT / "deploy" / "nginx-wechat-glm.conf").read_text(encoding="utf-8")

    for path in ["/", "/wechat", "/health", "/ready", "/metrics", "/metrics/prometheus"]:
        assert f"location = {path} " in config

    assert "client_max_body_size 64k;" in config


def test_nginx_forwards_request_id_to_app():
    config = (ROOT / "deploy" / "nginx-wechat-glm.conf").read_text(encoding="utf-8")

    assert config.count("proxy_set_header X-Request-ID $request_id;") == 6


def test_nginx_access_log_includes_request_correlation_fields():
    config = (ROOT / "deploy" / "nginx-wechat-glm.conf").read_text(encoding="utf-8")

    assert "log_format wechat_glm" in config
    assert '"$request_method $uri $server_protocol"' in config
    assert "request_id=$request_id" in config
    assert "upstream_request_id=$upstream_http_x_request_id" in config
    assert "upstream_status=$upstream_status" in config
    assert "request_time=$request_time" in config
    assert "upstream_response_time=$upstream_response_time" in config
    assert "access_log /var/log/nginx/wechat-glm-access.log wechat_glm;" in config
    assert "error_log /var/log/nginx/wechat-glm-error.log warn;" in config


def test_nginx_access_log_does_not_record_query_strings():
    config = (ROOT / "deploy" / "nginx-wechat-glm.conf").read_text(encoding="utf-8")

    assert '"$request" ' not in config
    assert "$request_uri" not in config
    assert "$args" not in config


def test_nginx_adds_security_headers_at_proxy_layer():
    config = (ROOT / "deploy" / "nginx-wechat-glm.conf").read_text(encoding="utf-8")

    for header in [
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
    ]:
        assert f"proxy_hide_header {header};" in config

    assert 'add_header X-Content-Type-Options "nosniff" always;' in config
    assert 'add_header X-Frame-Options "DENY" always;' in config
    assert 'add_header Referrer-Policy "no-referrer" always;' in config


def test_systemd_service_loads_env_and_uses_proxy_headers():
    service = (ROOT / "deploy" / "wechat-glm-assistant.service").read_text(
        encoding="utf-8"
    )

    assert "EnvironmentFile=-/opt/wechat-glm-assistant/.env" in service
    assert "--proxy-headers" in service
    assert "NoNewPrivileges=true" in service
    assert "PrivateTmp=true" in service
