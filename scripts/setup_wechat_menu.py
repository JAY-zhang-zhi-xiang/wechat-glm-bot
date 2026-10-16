import argparse
import sys
from pathlib import Path
from typing import List

import httpx

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import main  # noqa: E402

WECHAT_MENU_CREATE_URL = "https://api.weixin.qq.com/cgi-bin/menu/create"
WECHAT_MENU_DELETE_URL = "https://api.weixin.qq.com/cgi-bin/menu/delete"


def default_menu_payload() -> dict:
    return {
        "button": [
            {"type": "click", "name": "帮助", "key": main.MENU_KEY_HELP},
            {"type": "click", "name": "清空会话", "key": main.MENU_KEY_RESET},
            {"type": "click", "name": "关于", "key": main.MENU_KEY_ABOUT},
        ]
    }


def create_menu(token: str, timeout: float) -> int:
    try:
        response = httpx.post(
            WECHAT_MENU_CREATE_URL,
            params={"access_token": token},
            json=default_menu_payload(),
            timeout=timeout,
        )
        data = response.json()
    except Exception as exc:
        print(f"menu create failed: {exc}", file=sys.stderr)
        return 1

    if data.get("errcode", 0) != 0:
        print(f"menu create failed: {data}", file=sys.stderr)
        return 1

    print("menu created: 帮助 / 清空会话 / 关于")
    return 0


def delete_menu(token: str, timeout: float) -> int:
    try:
        response = httpx.get(
            WECHAT_MENU_DELETE_URL, params={"access_token": token}, timeout=timeout
        )
        data = response.json()
    except Exception as exc:
        print(f"menu delete failed: {exc}", file=sys.stderr)
        return 1

    if data.get("errcode", 0) != 0:
        print(f"menu delete failed: {data}", file=sys.stderr)
        return 1

    print("menu deleted")
    return 0


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or delete the WeChat official account custom menu."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--create",
        action="store_true",
        help="Create the default custom menu (帮助 / 清空会话 / 关于).",
    )
    group.add_argument(
        "--delete", action="store_true", help="Delete the current custom menu."
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    return parser.parse_args(argv)


def run(argv: List[str]) -> int:
    args = parse_args(argv)
    settings = main.get_settings()
    if not settings.wechat_app_id or not settings.wechat_app_secret:
        print(
            "WECHAT_APP_ID / WECHAT_APP_SECRET is not configured.", file=sys.stderr
        )
        return 2

    token = main.get_wechat_access_token()
    if not token:
        print(
            "Failed to obtain WeChat access_token. "
            "Check WECHAT_APP_ID/WECHAT_APP_SECRET and network connectivity.",
            file=sys.stderr,
        )
        return 1

    if args.create:
        return create_menu(token, args.timeout)
    return delete_menu(token, args.timeout)


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
