from scripts import check_config


def write_env(tmp_path, content: str):
    env_file = tmp_path / ".env"
    env_file.write_text(content, encoding="utf-8")
    return env_file


def messages(results):
    return [result.message for result in results]


def levels(results):
    return [result.level for result in results]


def test_run_checks_accepts_valid_env_without_dependency_check(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    env_file = write_env(
        tmp_path,
        "\n".join(
            [
                "ZHIPU_API_KEY=key",
                "WECHAT_TOKEN=token",
                "ADMIN_TOKEN=admin",
                "TEXT_MODEL=glm-text",
                "VISION_MODEL=glm-vision",
                "GLM_TIMEOUT_SECONDS=4",
                "MAX_REQUEST_BYTES=65536",
            ]
        ),
    )

    results = check_config.run_checks(str(env_file), skip_dependencies=True)

    assert "error" not in levels(results)
    assert "ADMIN_TOKEN is configured" in messages(results)


def test_run_checks_rejects_missing_and_placeholder_values(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    env_file = write_env(tmp_path, "ZHIPU_API_KEY=replace-with-your-zhipu-api-key")

    results = check_config.run_checks(str(env_file), skip_dependencies=True)

    assert "ZHIPU_API_KEY still uses a placeholder value" in messages(results)
    assert "WECHAT_TOKEN is required" in messages(results)


def test_run_checks_warns_for_short_tokens(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    env_file = write_env(
        tmp_path,
        "\n".join(
            [
                "ZHIPU_API_KEY=key",
                "WECHAT_TOKEN=short",
                "ADMIN_TOKEN=admin",
            ]
        ),
    )

    results = check_config.run_checks(str(env_file), skip_dependencies=True)

    assert "WECHAT_TOKEN is shorter than 16 characters" in messages(results)
    assert "ADMIN_TOKEN is shorter than 24 characters" in messages(results)
    assert "error" not in levels(results)


def test_run_checks_accepts_strong_tokens(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    env_file = write_env(
        tmp_path,
        "\n".join(
            [
                "ZHIPU_API_KEY=key",
                "WECHAT_TOKEN=wechat-token-1234567890",
                "ADMIN_TOKEN=admin-token-123456789012345",
            ]
        ),
    )

    results = check_config.run_checks(str(env_file), skip_dependencies=True)

    assert "WECHAT_TOKEN length is acceptable" in messages(results)
    assert "ADMIN_TOKEN length is acceptable" in messages(results)
    assert "WECHAT_TOKEN is shorter than 16 characters" not in messages(results)
    assert "ADMIN_TOKEN is shorter than 24 characters" not in messages(results)


def test_run_checks_reports_invalid_numbers(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    env_file = write_env(
        tmp_path,
        "\n".join(
            [
                "ZHIPU_API_KEY=key",
                "WECHAT_TOKEN=token",
                "MODEL_MAX_TOKENS=0",
                "MAX_REQUEST_BYTES=12",
                "GLM_TIMEOUT_SECONDS=slow",
            ]
        ),
    )

    results = check_config.run_checks(str(env_file), skip_dependencies=True)

    assert "MAX_REQUEST_BYTES must be >= 1024" in messages(results)
    assert "MODEL_MAX_TOKENS must be >= 1" in messages(results)
    assert "GLM_TIMEOUT_SECONDS must be a number" in messages(results)


def test_run_checks_reports_invalid_boolean(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    env_file = write_env(
        tmp_path,
        "\n".join(
            [
                "ZHIPU_API_KEY=key",
                "WECHAT_TOKEN=token",
                "ENABLE_DOCS=maybe",
                "ALLOW_ADMIN_TOKEN_QUERY=maybe",
            ]
        ),
    )

    results = check_config.run_checks(str(env_file), skip_dependencies=True)

    assert "ENABLE_DOCS must be a boolean" in messages(results)
    assert "ALLOW_ADMIN_TOKEN_QUERY must be a boolean" in messages(results)


def test_run_checks_accepts_boolean_values(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    env_file = write_env(
        tmp_path,
        "\n".join(
            [
                "ZHIPU_API_KEY=key",
                "WECHAT_TOKEN=token",
                "ENABLE_DOCS=false",
                "ALLOW_ADMIN_TOKEN_QUERY=false",
            ]
        ),
    )

    results = check_config.run_checks(str(env_file), skip_dependencies=True)

    assert "ENABLE_DOCS=false" in messages(results)
    assert "ALLOW_ADMIN_TOKEN_QUERY=false" in messages(results)
    assert "ENABLE_DOCS must be a boolean" not in messages(results)
    assert "ALLOW_ADMIN_TOKEN_QUERY must be a boolean" not in messages(results)


def test_run_checks_warns_when_admin_token_query_is_enabled(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("ALLOW_ADMIN_TOKEN_QUERY", raising=False)
    env_file = write_env(
        tmp_path,
        "\n".join(
            [
                "ZHIPU_API_KEY=key",
                "WECHAT_TOKEN=token",
                "ADMIN_TOKEN=admin-token-123456789012345",
                "ALLOW_ADMIN_TOKEN_QUERY=true",
            ]
        ),
    )

    results = check_config.run_checks(str(env_file), skip_dependencies=True)

    assert (
        "ALLOW_ADMIN_TOKEN_QUERY is enabled; set ALLOW_ADMIN_TOKEN_QUERY=false on public deployments"
        in messages(results)
    )


def test_run_checks_reports_when_admin_token_query_is_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("ALLOW_ADMIN_TOKEN_QUERY", raising=False)
    env_file = write_env(
        tmp_path,
        "\n".join(
            [
                "ZHIPU_API_KEY=key",
                "WECHAT_TOKEN=token",
                "ADMIN_TOKEN=admin-token-123456789012345",
                "ALLOW_ADMIN_TOKEN_QUERY=false",
            ]
        ),
    )

    results = check_config.run_checks(str(env_file), skip_dependencies=True)

    assert "ALLOW_ADMIN_TOKEN_QUERY is disabled" in messages(results)


def test_run_checks_warns_when_docs_are_enabled(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    monkeypatch.delenv("ENABLE_DOCS", raising=False)
    env_file = write_env(
        tmp_path,
        "\n".join(
            [
                "ZHIPU_API_KEY=key",
                "WECHAT_TOKEN=token",
                "ENABLE_DOCS=true",
            ]
        ),
    )

    results = check_config.run_checks(str(env_file), skip_dependencies=True)

    assert "ENABLE_DOCS is enabled; set ENABLE_DOCS=false on public deployments" in messages(
        results
    )


def test_run_checks_uses_secure_default_when_docs_setting_is_omitted(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    monkeypatch.delenv("ENABLE_DOCS", raising=False)
    env_file = write_env(
        tmp_path,
        "\n".join(
            [
                "ZHIPU_API_KEY=key",
                "WECHAT_TOKEN=token",
            ]
        ),
    )

    results = check_config.run_checks(str(env_file), skip_dependencies=True)

    assert "ENABLE_DOCS is disabled" in messages(results)


def test_run_checks_reports_when_docs_are_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    monkeypatch.delenv("ENABLE_DOCS", raising=False)
    env_file = write_env(
        tmp_path,
        "\n".join(
            [
                "ZHIPU_API_KEY=key",
                "WECHAT_TOKEN=token",
                "ENABLE_DOCS=false",
            ]
        ),
    )

    results = check_config.run_checks(str(env_file), skip_dependencies=True)

    assert "ENABLE_DOCS is disabled" in messages(results)
    assert "ENABLE_DOCS is enabled; set ENABLE_DOCS=false on public deployments" not in messages(
        results
    )


def test_run_checks_accepts_log_level_values(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    env_file = write_env(
        tmp_path,
        "\n".join(
            [
                "ZHIPU_API_KEY=key",
                "WECHAT_TOKEN=token",
                "LOG_LEVEL=debug",
            ]
        ),
    )

    results = check_config.run_checks(str(env_file), skip_dependencies=True)

    assert "LOG_LEVEL=DEBUG" in messages(results)
    assert "LOG_LEVEL must be one of: CRITICAL, DEBUG, ERROR, INFO, WARNING" not in messages(
        results
    )


def test_run_checks_rejects_invalid_log_level(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    env_file = write_env(
        tmp_path,
        "\n".join(
            [
                "ZHIPU_API_KEY=key",
                "WECHAT_TOKEN=token",
                "LOG_LEVEL=verbose",
            ]
        ),
    )

    results = check_config.run_checks(str(env_file), skip_dependencies=True)

    assert "LOG_LEVEL must be one of: CRITICAL, DEBUG, ERROR, INFO, WARNING" in messages(
        results
    )


def test_environment_overrides_env_file(tmp_path, monkeypatch):
    env_file = write_env(
        tmp_path,
        "\n".join(
            [
                "ZHIPU_API_KEY=replace-with-your-zhipu-api-key",
                "WECHAT_TOKEN=file-token",
            ]
        ),
    )
    monkeypatch.setenv("ZHIPU_API_KEY", "env-key")

    results = check_config.run_checks(str(env_file), skip_dependencies=True)

    assert "ZHIPU_API_KEY is configured" in messages(results)
    assert "ZHIPU_API_KEY still uses a placeholder value" not in messages(results)


def test_run_checks_ignores_customer_service_fallback_when_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    monkeypatch.delenv("ENABLE_CUSTOMER_SERVICE_FALLBACK", raising=False)
    env_file = write_env(
        tmp_path,
        "\n".join(
            [
                "ZHIPU_API_KEY=key",
                "WECHAT_TOKEN=token",
            ]
        ),
    )

    results = check_config.run_checks(str(env_file), skip_dependencies=True)

    assert not any("ENABLE_CUSTOMER_SERVICE_FALLBACK" in message for message in messages(results))


def test_run_checks_warns_when_customer_service_fallback_missing_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    monkeypatch.delenv("WECHAT_APP_ID", raising=False)
    monkeypatch.delenv("WECHAT_APP_SECRET", raising=False)
    env_file = write_env(
        tmp_path,
        "\n".join(
            [
                "ZHIPU_API_KEY=key",
                "WECHAT_TOKEN=token",
                "ENABLE_CUSTOMER_SERVICE_FALLBACK=true",
            ]
        ),
    )

    results = check_config.run_checks(str(env_file), skip_dependencies=True)

    assert (
        "ENABLE_CUSTOMER_SERVICE_FALLBACK is enabled but missing: WECHAT_APP_ID, WECHAT_APP_SECRET"
        in messages(results)
    )


def test_run_checks_accepts_customer_service_fallback_with_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("WECHAT_TOKEN", raising=False)
    env_file = write_env(
        tmp_path,
        "\n".join(
            [
                "ZHIPU_API_KEY=key",
                "WECHAT_TOKEN=token",
                "ENABLE_CUSTOMER_SERVICE_FALLBACK=true",
                "WECHAT_APP_ID=app-id",
                "WECHAT_APP_SECRET=app-secret",
            ]
        ),
    )

    results = check_config.run_checks(str(env_file), skip_dependencies=True)

    assert "ENABLE_CUSTOMER_SERVICE_FALLBACK has required app credentials" in messages(results)


def test_dependency_check_reports_missing_package(monkeypatch):
    monkeypatch.setattr(check_config.importlib.util, "find_spec", lambda name: None)

    results = check_config.check_dependencies()

    assert all(result.level == "error" for result in results)
    assert "Python package missing: zhipuai" in messages(results)


def test_main_returns_nonzero_on_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_env(tmp_path, "")

    exit_code = check_config.main(["--skip-dependencies"])

    assert exit_code == 1
    assert "WECHAT_TOKEN is required" in capsys.readouterr().out


def test_main_can_treat_warnings_as_failures(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_env(
        tmp_path,
        "\n".join(
            [
                "ZHIPU_API_KEY=key",
                "WECHAT_TOKEN=short",
                "ENABLE_DOCS=false",
            ]
        ),
    )

    exit_code = check_config.main(["--skip-dependencies", "--strict-warnings"])

    assert exit_code == 1
    assert "WECHAT_TOKEN is shorter than 16 characters" in capsys.readouterr().out


def _clear_model_env(monkeypatch):
    for name in (
        "LLM_API_KEY",
        "LLM_PROVIDER",
        "ZHIPU_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_run_checks_accepts_llm_api_key_for_deepseek(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    env_file = write_env(
        tmp_path,
        "\n".join(
            [
                "LLM_PROVIDER=deepseek",
                "LLM_API_KEY=sk-deepseek-key",
                "TEXT_MODEL=deepseek-chat",
                "WECHAT_TOKEN=a-long-enough-wechat-token",
            ]
        ),
    )

    results = check_config.run_checks(str(env_file), skip_dependencies=True)

    assert "LLM_API_KEY is configured" in messages(results)
    assert "LLM_PROVIDER=deepseek" in messages(results)


def test_run_checks_accepts_deepseek_key_alias(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    env_file = write_env(
        tmp_path,
        "\n".join(
            [
                "LLM_PROVIDER=deepseek",
                "DEEPSEEK_API_KEY=sk-deepseek-key",
                "WECHAT_TOKEN=a-long-enough-wechat-token",
            ]
        ),
    )

    results = check_config.run_checks(str(env_file), skip_dependencies=True)

    assert "DEEPSEEK_API_KEY is configured" in messages(results)


def test_run_checks_rejects_missing_model_key(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    env_file = write_env(
        tmp_path,
        "\n".join(
            [
                "LLM_PROVIDER=deepseek",
                "WECHAT_TOKEN=a-long-enough-wechat-token",
            ]
        ),
    )

    results = check_config.run_checks(str(env_file), skip_dependencies=True)

    assert "model API key is required: LLM_API_KEY or DEEPSEEK_API_KEY" in messages(results)


def test_run_checks_rejects_unknown_provider(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    env_file = write_env(
        tmp_path,
        "\n".join(
            [
                "LLM_PROVIDER=gemini",
                "LLM_API_KEY=some-key",
                "WECHAT_TOKEN=a-long-enough-wechat-token",
            ]
        ),
    )

    results = check_config.run_checks(str(env_file), skip_dependencies=True)

    assert "LLM_PROVIDER must be one of: DEEPSEEK, OPENAI, ZHIPU" in messages(results)

