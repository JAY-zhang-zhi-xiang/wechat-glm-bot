import sys
from pathlib import Path

from scripts import run_checks


def make_project(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    (tmp_path / "scripts" / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "tests" / "test_a.py").write_text("", encoding="utf-8")
    return tmp_path


def labels(steps):
    return [step.label for step in steps]


def test_build_steps_defaults_to_compile_lint_and_tests(tmp_path):
    root = make_project(tmp_path)
    args = run_checks.parse_args([])

    steps = run_checks.build_steps(args, root)

    assert labels(steps) == [
        "compile python files",
        "run ruff",
        "run tests",
        "scan dependencies for vulnerabilities",
    ]
    assert steps[0].command[:3] == [sys.executable, "-m", "py_compile"]
    assert "main.py" in steps[0].command
    assert steps[1].command == [sys.executable, "-m", "ruff", "check", "."]
    assert steps[3].command == [
        sys.executable,
        "-m",
        "pip_audit",
        "-r",
        "requirements.txt",
    ]


def test_build_steps_can_skip_audit(tmp_path):
    root = make_project(tmp_path)
    args = run_checks.parse_args(["--skip-audit"])

    steps = run_checks.build_steps(args, root)

    assert "scan dependencies for vulnerabilities" not in labels(steps)


def test_build_steps_checks_config_when_env_exists(tmp_path):
    root = make_project(tmp_path)
    (root / ".env").write_text("WECHAT_TOKEN=token", encoding="utf-8")
    args = run_checks.parse_args([])

    steps = run_checks.build_steps(args, root)

    assert "check configuration" in labels(steps)
    config_step = steps[-1]
    assert "--skip-dependencies" in config_step.command


def test_build_steps_can_require_config_without_env_file(tmp_path):
    root = make_project(tmp_path)
    args = run_checks.parse_args(["--require-config", "--with-dependencies"])

    steps = run_checks.build_steps(args, root)

    assert labels(steps)[-1] == "check configuration"
    assert "--skip-dependencies" not in steps[-1].command


def test_build_steps_can_make_config_warnings_strict(tmp_path):
    root = make_project(tmp_path)
    (root / ".env").write_text("WECHAT_TOKEN=token", encoding="utf-8")
    args = run_checks.parse_args(["--strict-config"])

    steps = run_checks.build_steps(args, root)

    assert labels(steps)[-1] == "check configuration"
    assert "--strict-warnings" in steps[-1].command


def test_build_steps_can_add_smoke(tmp_path):
    root = make_project(tmp_path)
    args = run_checks.parse_args(
        [
            "--smoke",
            "--smoke-base-url",
            "http://example.test",
            "--require-ready",
            "--with-model",
        ]
    )

    steps = run_checks.build_steps(args, root)

    assert labels(steps)[-1] == "run smoke test"
    smoke_command = steps[-1].command
    assert "--base-url" in smoke_command
    assert "http://example.test" in smoke_command
    assert "--require-ready" in smoke_command
    assert "--with-model" in smoke_command


def test_main_stops_on_first_failed_step(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(run_checks, "project_root", lambda: Path(tmp_path))
    monkeypatch.setattr(
        run_checks,
        "build_steps",
        lambda args, root: [
            run_checks.Step("first", ["first"]),
            run_checks.Step("second", ["second"]),
        ],
    )

    def fake_run_step(step, root):
        calls.append(step.label)
        return 7 if step.label == "first" else 0

    monkeypatch.setattr(run_checks, "run_step", fake_run_step)

    exit_code = run_checks.main([])

    assert exit_code == 7
    assert calls == ["first"]


def test_main_returns_zero_when_all_steps_pass(monkeypatch, tmp_path):
    monkeypatch.setattr(run_checks, "project_root", lambda: Path(tmp_path))
    monkeypatch.setattr(
        run_checks,
        "build_steps",
        lambda args, root: [run_checks.Step("first", ["first"])],
    )
    monkeypatch.setattr(run_checks, "run_step", lambda step, root: 0)

    assert run_checks.main([]) == 0
