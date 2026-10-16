import argparse
import hashlib
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class HttpResult:
    status: int
    body: str


def load_env_file(path: str = ".env") -> Dict[str, str]:
    env_path = Path(path)
    if not env_path.exists():
        return {}

    values: Dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def env_default(name: str, env_file_values: Dict[str, str], default: str = "") -> str:
    return os.getenv(name) or env_file_values.get(name, default)


def wechat_signature(token: str, timestamp: str, nonce: str) -> str:
    raw = "".join(sorted([token, timestamp, nonce]))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def signed_params(token: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(8)
    params = {
        "signature": wechat_signature(token, timestamp, nonce),
        "timestamp": timestamp,
        "nonce": nonce,
    }
    if extra:
        params.update(extra)
    return params


def cdata(value: str) -> str:
    return f"<![CDATA[{str(value).replace(']]>', ']]]]><![CDATA[>')}]]>"


def text_xml(content: str, from_user: str, to_user: str = "server") -> str:
    return (
        "<xml>"
        f"<ToUserName>{cdata(to_user)}</ToUserName>"
        f"<FromUserName>{cdata(from_user)}</FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content>{cdata(content)}</Content>"
        f"<MsgId>{int(time.time() * 1000)}</MsgId>"
        "</xml>"
    )


def location_xml(label: str, from_user: str, to_user: str = "server") -> str:
    return (
        "<xml>"
        f"<ToUserName>{cdata(to_user)}</ToUserName>"
        f"<FromUserName>{cdata(from_user)}</FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType><![CDATA[location]]></MsgType>"
        "<Location_X>22.5431</Location_X>"
        "<Location_Y>114.0579</Location_Y>"
        "<Scale>15</Scale>"
        f"<Label>{cdata(label)}</Label>"
        f"<MsgId>{int(time.time() * 1000)}</MsgId>"
        "</xml>"
    )


def image_xml(image_url: str, from_user: str, to_user: str = "server") -> str:
    return (
        "<xml>"
        f"<ToUserName>{cdata(to_user)}</ToUserName>"
        f"<FromUserName>{cdata(from_user)}</FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType><![CDATA[image]]></MsgType>"
        f"<PicUrl>{cdata(image_url)}</PicUrl>"
        "<MediaId><![CDATA[smoke-media-id]]></MediaId>"
        f"<MsgId>{int(time.time() * 1000)}</MsgId>"
        "</xml>"
    )


def endpoint(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def get(
    url: str,
    params: Optional[Dict[str, str]],
    timeout: float,
    headers: Optional[Dict[str, str]] = None,
) -> HttpResult:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    return send(request, timeout)


def post_xml(url: str, params: Dict[str, str], xml: str, timeout: float) -> HttpResult:
    url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        data=xml.encode("utf-8"),
        headers={"Content-Type": "application/xml; charset=utf-8"},
        method="POST",
    )
    return send(request, timeout)


def send(request: urllib.request.Request, timeout: float) -> HttpResult:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return HttpResult(status=response.status, body=body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return HttpResult(status=exc.code, body=body)


def assert_contains(result: HttpResult, expected: str, label: str) -> None:
    if result.status != 200:
        raise RuntimeError(f"{label} failed: HTTP {result.status}: {result.body[:200]}")
    if expected not in result.body:
        raise RuntimeError(f"{label} failed: expected {expected!r}, got {result.body[:200]!r}")


def run_smoke(
    base_url: str,
    token: str,
    timeout: float,
    require_ready: bool,
    admin_token: str,
    with_model: bool,
    model_text: str,
    image_url: str,
) -> List[str]:
    wechat_url = endpoint(base_url, "/wechat")
    index_url = endpoint(base_url, "/")
    health_url = endpoint(base_url, "/health")
    ready_url = endpoint(base_url, "/ready")
    from_user = f"smoke-{secrets.token_hex(4)}"
    events: List[str] = []

    index = get(index_url, None, timeout)
    if index.status == 200:
        events.append("index ok")
    else:
        events.append(f"index skipped: HTTP {index.status}")

    health = get(health_url, None, timeout)
    if health.status == 200:
        events.append("health ok")
    else:
        events.append(f"health skipped: HTTP {health.status}")

    if require_ready:
        ready_headers = {"X-Admin-Token": admin_token} if admin_token else None
        ready = get(ready_url, None, timeout, ready_headers)
        if ready.status != 200:
            raise RuntimeError(f"readiness failed: HTTP {ready.status}: {ready.body[:200]}")
        events.append("readiness ok")

    verify = get(
        wechat_url,
        signed_params(token, {"echostr": "wechat-smoke-ok"}),
        timeout,
    )
    assert_contains(verify, "wechat-smoke-ok", "server verification")
    events.append("server verification ok")

    help_reply = post_xml(
        wechat_url,
        signed_params(token),
        text_xml("帮助", from_user),
        timeout,
    )
    assert_contains(help_reply, "清空会话", "help message")
    events.append("help message ok")

    location_reply = post_xml(
        wechat_url,
        signed_params(token),
        location_xml("深圳市民中心", from_user),
        timeout,
    )
    assert_contains(location_reply, "深圳市民中心", "location message")
    events.append("location message ok")

    reset_reply = post_xml(
        wechat_url,
        signed_params(token),
        text_xml("清空会话", from_user),
        timeout,
    )
    assert_contains(reset_reply, "已清空", "reset message")
    events.append("reset message ok")

    if with_model:
        text_reply = post_xml(
            wechat_url,
            signed_params(token),
            text_xml(model_text, from_user),
            timeout,
        )
        assert_contains(text_reply, "<MsgType><![CDATA[text]]></MsgType>", "model text")
        events.append("model text ok")

        if image_url:
            image_reply = post_xml(
                wechat_url,
                signed_params(token),
                image_xml(image_url, from_user),
                timeout,
            )
            assert_contains(
                image_reply,
                "<MsgType><![CDATA[text]]></MsgType>",
                "model image",
            )
            events.append("model image ok")

    return events


def parse_args(argv: List[str]) -> argparse.Namespace:
    env_file_values = load_env_file()
    parser = argparse.ArgumentParser(
        description="Smoke-test a WeChat callback endpoint with signed requests."
    )
    parser.add_argument(
        "--base-url",
        default=env_default("SMOKE_BASE_URL", env_file_values, "http://127.0.0.1:8000"),
        help="Base URL for the FastAPI/Nginx service.",
    )
    parser.add_argument(
        "--token",
        default=env_default("WECHAT_TOKEN", env_file_values),
        help="WeChat token. Defaults to WECHAT_TOKEN from the environment or .env.",
    )
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Require /ready to return 200 before running callback checks.",
    )
    parser.add_argument(
        "--admin-token",
        default=env_default("ADMIN_TOKEN", env_file_values),
        help="Admin token for protected diagnostic endpoints.",
    )
    parser.add_argument(
        "--with-model",
        action="store_true",
        help="Also send messages that call GLM and may consume API quota.",
    )
    parser.add_argument("--model-text", default="你好，请用一句话介绍你自己。")
    parser.add_argument("--image-url", default="")
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    if not args.token:
        print("WECHAT_TOKEN is required. Pass --token or set WECHAT_TOKEN.", file=sys.stderr)
        return 2

    try:
        events = run_smoke(
            base_url=args.base_url,
            token=args.token,
            timeout=args.timeout,
            require_ready=args.require_ready,
            admin_token=args.admin_token,
            with_model=args.with_model,
            model_text=args.model_text,
            image_url=args.image_url,
        )
    except Exception as exc:
        print(f"smoke failed: {exc}", file=sys.stderr)
        return 1

    for event in events:
        print(f"ok: {event}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
