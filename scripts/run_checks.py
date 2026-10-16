import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass(frozen=True)
class Step:
    label: str
    command: List[str]


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def python_files(root: Path) -> List[str]:
    files = [root / "main.py"]
    files.extend(sorted((root / "scripts").glob("*.py")))
    files.extend(sorted((root / "tests").glob("*.py")))
    return [str(path.relative_to(root)) for path in files if path.exists()]


def build_steps(args: argparse.Namespace, root: Path) -> List[Step]:
    steps = [
        Step(
            "compile python files",
            [sys.executable, "-m", "py_compile", *python_files(root)],
        ),
        Step("run ruff", [sys.executable, "-m", "ruff", "check", "."]),
        Step("run tests", [sys.executable, "-m", "pytest"]),
    ]

    if not args.skip_audit:
        steps.append(
            Step(
                "scan dependencies for vulnerabilities",
                [sys.executable, "-m", "pip_audit", "-r", "requirements.txt"],
            )
        )

    env_file = root / args.env_file
    should_check_config = args.require_config or (env_file.exists() and not args.skip_config)
    if should_check_config:
        command = [
            sys.executable,
            "scripts/check_config.py",
            "--env-file",
            args.env_file,
        ]
        if not args.with_dependencies:
            command.append("--skip-dependencies")
        if args.strict_config:
            command.append("--strict-warnings")
        steps.append(Step("check configuration", command))

    if args.smoke:
        command = [
            sys.executable,
            "scripts/smoke_wechat.py",
            "--base-url",
            args.smoke_base_url,
            "--timeout",
            str(args.smoke_timeout),
        ]
        if args.require_ready:
            command.append("--require-ready")
        if args.with_model:
            command.append("--with-model")
        steps.append(Step("run smoke test", command))

    return steps


def run_step(step: Step, root: Path) -> int:
    print(f"==> {step.label}")
    completed = subprocess.run(step.command, cwd=root)
    if completed.returncode == 0:
        print(f"ok: {step.label}")
    else:
        print(f"failed: {step.label} exited {completed.returncode}")
    return completed.returncode


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local project checks.")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument(
        "--skip-audit",
        action="store_true",
        help="Skip the pip-audit dependency vulnerability scan.",
    )
    parser.add_argument(
        "--require-config",
        action="store_true",
        help="Fail if config check fails even when the env file is missing.",
    )
    parser.add_argument(
        "--skip-config",
        action="store_true",
        help="Do not run config checks automatically.",
    )
    parser.add_argument(
        "--with-dependencies",
        action="store_true",
        help="Include dependency availability in config checks.",
    )
    parser.add_argument(
        "--strict-config",
        action="store_true",
        help="Fail config checks when warnings are present.",
    )
    parser.add_argument("--smoke", action="store_true", help="Run smoke_wechat.py too.")
    parser.add_argument("--smoke-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--smoke-timeout", type=float, default=8.0)
    parser.add_argument("--require-ready", action="store_true")
    parser.add_argument("--with-model", action="store_true")
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    root = project_root()
    for step in build_steps(args, root):
        code = run_step(step, root)
        if code != 0:
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
