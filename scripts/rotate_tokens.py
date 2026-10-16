import argparse
import secrets
import sys
from pathlib import Path
from typing import Dict, List

try:
    from scripts.check_config import MIN_ADMIN_TOKEN_LENGTH, MIN_WECHAT_TOKEN_LENGTH
except ModuleNotFoundError:
    from check_config import MIN_ADMIN_TOKEN_LENGTH, MIN_WECHAT_TOKEN_LENGTH

TOKEN_URLSAFE_BYTES = 32


def generate_token(min_length: int) -> str:
    token = secrets.token_urlsafe(TOKEN_URLSAFE_BYTES)
    while len(token) < min_length:
        token += secrets.token_urlsafe(TOKEN_URLSAFE_BYTES)
    return token


def apply_to_env_file(env_file: Path, updates: Dict[str, str]) -> None:
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    applied = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                # Rewrite every occurrence: stopping at the first would leave a
                # duplicate line holding the old secret.
                new_lines.append(f"{key}={updates[key]}")
                applied.add(key)
                continue
        new_lines.append(line)
    for key, value in updates.items():
        if key not in applied:
            new_lines.append(f"{key}={value}")
    env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate new WECHAT_TOKEN/ADMIN_TOKEN values, optionally applying them to an env file."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the new tokens into --env-file in place instead of just printing them.",
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument(
        "--wechat-token", action="store_true", help="Only rotate WECHAT_TOKEN."
    )
    parser.add_argument(
        "--admin-token", action="store_true", help="Only rotate ADMIN_TOKEN."
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    rotate_both = not args.wechat_token and not args.admin_token
    updates: Dict[str, str] = {}
    if args.wechat_token or rotate_both:
        updates["WECHAT_TOKEN"] = generate_token(MIN_WECHAT_TOKEN_LENGTH)
    if args.admin_token or rotate_both:
        updates["ADMIN_TOKEN"] = generate_token(MIN_ADMIN_TOKEN_LENGTH)

    for key, value in updates.items():
        print(f"{key}={value}")

    if args.apply:
        apply_to_env_file(Path(args.env_file), updates)
        print(
            f"\nApplied to {args.env_file}. Remember to also update the Token in the "
            "WeChat official account backend to match the new WECHAT_TOKEN, and restart the service."
        )
    else:
        print("\nRun again with --apply --env-file <path> to write these values in place.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
