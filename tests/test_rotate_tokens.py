from scripts import check_config, rotate_tokens


def test_generate_token_meets_minimum_length():
    token = rotate_tokens.generate_token(check_config.MIN_ADMIN_TOKEN_LENGTH)

    assert len(token) >= check_config.MIN_ADMIN_TOKEN_LENGTH


def test_generate_token_produces_unique_values():
    tokens = {rotate_tokens.generate_token(16) for _ in range(20)}

    assert len(tokens) == 20


def test_parse_args_defaults_to_rotating_both(tmp_path):
    args = rotate_tokens.parse_args([])

    assert args.apply is False
    assert args.env_file == ".env"
    assert args.wechat_token is False
    assert args.admin_token is False


def test_main_prints_both_tokens_without_apply(capsys):
    exit_code = rotate_tokens.main([])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "WECHAT_TOKEN=" in out
    assert "ADMIN_TOKEN=" in out
    assert "Run again with --apply" in out


def test_main_can_rotate_only_wechat_token(capsys):
    exit_code = rotate_tokens.main(["--wechat-token"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "WECHAT_TOKEN=" in out
    assert "ADMIN_TOKEN=" not in out


def test_main_can_rotate_only_admin_token(capsys):
    exit_code = rotate_tokens.main(["--admin-token"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "ADMIN_TOKEN=" in out
    assert "WECHAT_TOKEN=" not in out


def test_apply_to_env_file_replaces_existing_keys(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ZHIPU_API_KEY=key\nWECHAT_TOKEN=old-token\nADMIN_TOKEN=old-admin\n",
        encoding="utf-8",
    )

    rotate_tokens.apply_to_env_file(
        env_file, {"WECHAT_TOKEN": "new-token", "ADMIN_TOKEN": "new-admin"}
    )

    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert "ZHIPU_API_KEY=key" in lines
    assert "WECHAT_TOKEN=new-token" in lines
    assert "ADMIN_TOKEN=new-admin" in lines
    assert "WECHAT_TOKEN=old-token" not in lines
    assert "ADMIN_TOKEN=old-admin" not in lines


def test_apply_to_env_file_appends_missing_keys(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("ZHIPU_API_KEY=key\n", encoding="utf-8")

    rotate_tokens.apply_to_env_file(env_file, {"WECHAT_TOKEN": "new-token"})

    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert "ZHIPU_API_KEY=key" in lines
    assert "WECHAT_TOKEN=new-token" in lines


def test_apply_to_env_file_creates_file_when_missing(tmp_path):
    env_file = tmp_path / ".env"

    rotate_tokens.apply_to_env_file(env_file, {"WECHAT_TOKEN": "new-token"})

    assert env_file.read_text(encoding="utf-8").strip() == "WECHAT_TOKEN=new-token"


def test_apply_to_env_file_preserves_comments_and_blank_lines(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n\nWECHAT_TOKEN=old-token\n",
        encoding="utf-8",
    )

    rotate_tokens.apply_to_env_file(env_file, {"WECHAT_TOKEN": "new-token"})

    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert "# comment" in lines
    assert "" in lines
    assert "WECHAT_TOKEN=new-token" in lines


def test_apply_to_env_file_rewrites_every_duplicate_occurrence(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "WECHAT_TOKEN=old-token\nZHIPU_API_KEY=key\nWECHAT_TOKEN=old-token-again\n",
        encoding="utf-8",
    )

    rotate_tokens.apply_to_env_file(env_file, {"WECHAT_TOKEN": "new-token"})

    content = env_file.read_text(encoding="utf-8")
    assert "old-token" not in content
    assert content.count("WECHAT_TOKEN=new-token") == 2
    assert "ZHIPU_API_KEY=key" in content


def test_apply_to_env_file_leaves_malformed_lines_untouched(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "BARE_KEY_NO_EQUALS\n# WECHAT_TOKEN=commented-out\nWECHAT_TOKEN=old-token\n",
        encoding="utf-8",
    )

    rotate_tokens.apply_to_env_file(env_file, {"WECHAT_TOKEN": "new-token"})

    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert "BARE_KEY_NO_EQUALS" in lines
    assert "# WECHAT_TOKEN=commented-out" in lines
    assert "WECHAT_TOKEN=new-token" in lines
    assert "WECHAT_TOKEN=old-token" not in lines


def test_main_with_apply_writes_env_file(tmp_path, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text("ZHIPU_API_KEY=key\nWECHAT_TOKEN=old-token\n", encoding="utf-8")

    exit_code = rotate_tokens.main(
        ["--apply", "--env-file", str(env_file), "--wechat-token"]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Applied to" in out
    content = env_file.read_text(encoding="utf-8")
    assert "WECHAT_TOKEN=old-token" not in content
    assert "ZHIPU_API_KEY=key" in content
