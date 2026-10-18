import argparse
import importlib.util
import os
import sys
from dataclasses import dataclass
from typing import Dict, List

try:
    from scripts.smoke_wechat import load_env_file
except ModuleNotFoundError:
    from smoke_wechat import load_env_file


SUPPORTED_PROVIDERS = {"zhipu", "deepseek", "openai"}
DEFAULT_PROVIDER = "zhipu"
PROVIDER_KEY_ENV_NAMES = {
    "zhipu": ("ZHIPU_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
}
REQUIRED_PACKAGES = ("fastapi", "uvicorn", "dotenv", "defusedxml", "httpx")
PROVIDER_PACKAGES = {"zhipu": ("zhipuai",)}
OPTIONAL_SETTINGS = (
    "ADMIN_TOKEN",
    "LLM_PROVIDER",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "ZHIPU_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "TEXT_MODEL",
    "VISION_MODEL",
    "SYSTEM_PROMPT",
    "IMAGE_PROMPT",
    "MODERATION_BLOCKLIST_FILE",
    "ALERT_WEBHOOK_URL",
    "WECHAT_APP_ID",
    "WECHAT_APP_SECRET",
)
MIN_WECHAT_TOKEN_LENGTH = 16
MIN_ADMIN_TOKEN_LENGTH = 24
BOOL_RULES = ("ENABLE_DOCS", "ALLOW_ADMIN_TOKEN_QUERY", "ENABLE_CUSTOMER_SERVICE_FALLBACK")
BOOL_TRUE_VALUES = {"1", "true", "yes", "on"}
BOOL_FALSE_VALUES = {"0", "false", "no", "off"}
BOOL_VALUES = BOOL_TRUE_VALUES | BOOL_FALSE_VALUES
ENUM_RULES = {
    "LOG_LEVEL": {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"},
    "LLM_PROVIDER": {"ZHIPU", "DEEPSEEK", "OPENAI"},
}
INT_RULES = {
    "MODEL_MAX_TOKENS": 1,
    "MODEL_MAX_CONCURRENT_CALLS": 1,
    "MAX_REPLY_CHARS": 1,
    "MAX_MODEL_INPUT_CHARS": 0,
    "MAX_REQUEST_BYTES": 1024,
    "WECHAT_SIGNATURE_TOLERANCE_SECONDS": 0,
    "DUPLICATE_CACHE_TTL_SECONDS": 0,
    "DUPLICATE_CACHE_MAX_SIZE": 1,
    "CONVERSATION_MEMORY_TTL_SECONDS": 0,
    "CONVERSATION_MEMORY_MAX_TURNS": 0,
    "CONVERSATION_MEMORY_MAX_USERS": 1,
    "RATE_LIMIT_WINDOW_SECONDS": 0,
    "RATE_LIMIT_MAX_MESSAGES": 0,
    "RATE_LIMIT_MAX_USERS": 1,
    "MODEL_CIRCUIT_BREAKER_THRESHOLD": 1,
    "MODEL_CIRCUIT_BREAKER_WINDOW_SECONDS": 1,
    "MODEL_CIRCUIT_BREAKER_COOLDOWN_SECONDS": 1,
    "ALERT_MIN_INTERVAL_SECONDS": 0,
}
FLOAT_RULES = {"GLM_TIMEOUT_SECONDS": 0.5}
MODEL_API_KEY_NAMES = ("LLM_API_KEY", "ZHIPU_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY")


@dataclass(frozen=True)
class CheckResult:
    level: str
    message: str


def merged_config(env_file: str) -> Dict[str, str]:
    values = load_env_file(env_file)
    for key, value in os.environ.items():
        if (
            key in values
            or key == "WECHAT_TOKEN"
            or key in MODEL_API_KEY_NAMES
            or key in OPTIONAL_SETTINGS
            or key in BOOL_RULES
            or key in ENUM_RULES
            or key in INT_RULES
            or key in FLOAT_RULES
        ):
            values[key] = value
    return values


def provider_from_values(values: Dict[str, str]) -> str:
    raw = values.get("LLM_PROVIDER", "").strip().lower()
    if not raw:
        return DEFAULT_PROVIDER
    return raw


def is_placeholder(value: str) -> bool:
    return value.startswith("replace-with-") or value in {"your-token", "your-api-key"}


def check_required(values: Dict[str, str]) -> List[CheckResult]:
    results: List[CheckResult] = []

    wechat_token = values.get("WECHAT_TOKEN", "").strip()
    if not wechat_token:
        results.append(CheckResult("error", "WECHAT_TOKEN is required"))
    elif is_placeholder(wechat_token):
        results.append(CheckResult("error", "WECHAT_TOKEN still uses a placeholder value"))
    else:
        results.append(CheckResult("ok", "WECHAT_TOKEN is configured"))

    provider = provider_from_values(values)
    names = ("LLM_API_KEY",) + PROVIDER_KEY_ENV_NAMES.get(provider, ())
    for name in names:
        value = values.get(name, "").strip()
        if not value:
            continue
        if is_placeholder(value):
            results.append(CheckResult("error", f"{name} still uses a placeholder value"))
        else:
            results.append(CheckResult("ok", f"{name} is configured"))
            results.append(CheckResult("ok", f"LLM_PROVIDER={provider}"))
        break
    else:
        expected = " or ".join(names)
        results.append(CheckResult("error", f"model API key is required: {expected}"))
    return results


def check_models(values: Dict[str, str]) -> List[CheckResult]:
    results: List[CheckResult] = []
    provider = provider_from_values(values)
    for key in ("TEXT_MODEL", "VISION_MODEL"):
        value = values.get(key, "").strip()
        if value:
            results.append(CheckResult("ok", f"{key}={value}"))
        else:
            results.append(CheckResult("warn", f"{key} is empty; the app will use its default"))
    if provider == "deepseek":
        results.append(
            CheckResult(
                "warn",
                "DeepSeek's API is text-only; image messages will get a friendly "
                "text reply instead of a vision answer",
            )
        )
    return results


def check_token_strength(values: Dict[str, str]) -> List[CheckResult]:
    results: List[CheckResult] = []
    wechat_token = values.get("WECHAT_TOKEN", "").strip()
    if wechat_token and not is_placeholder(wechat_token):
        if len(wechat_token) < MIN_WECHAT_TOKEN_LENGTH:
            results.append(
                CheckResult(
                    "warn",
                    f"WECHAT_TOKEN is shorter than {MIN_WECHAT_TOKEN_LENGTH} characters",
                )
            )
        else:
            results.append(CheckResult("ok", "WECHAT_TOKEN length is acceptable"))

    admin_token = values.get("ADMIN_TOKEN", "").strip()
    if admin_token:
        if len(admin_token) < MIN_ADMIN_TOKEN_LENGTH:
            results.append(
                CheckResult(
                    "warn",
                    f"ADMIN_TOKEN is shorter than {MIN_ADMIN_TOKEN_LENGTH} characters",
                )
            )
        else:
            results.append(CheckResult("ok", "ADMIN_TOKEN length is acceptable"))
    return results


def check_numbers(values: Dict[str, str]) -> List[CheckResult]:
    results: List[CheckResult] = []
    for key, minimum in INT_RULES.items():
        raw = values.get(key, "").strip()
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            results.append(CheckResult("error", f"{key} must be an integer"))
            continue
        if value < minimum:
            results.append(CheckResult("error", f"{key} must be >= {minimum}"))
        else:
            results.append(CheckResult("ok", f"{key}={value}"))

    for key, minimum in FLOAT_RULES.items():
        raw = values.get(key, "").strip()
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            results.append(CheckResult("error", f"{key} must be a number"))
            continue
        if value < minimum:
            results.append(CheckResult("error", f"{key} must be >= {minimum}"))
        else:
            results.append(CheckResult("ok", f"{key}={value:g}"))

    return results


def check_booleans(values: Dict[str, str]) -> List[CheckResult]:
    results: List[CheckResult] = []
    for key in BOOL_RULES:
        raw = values.get(key, "").strip()
        if not raw:
            continue
        if raw.lower() not in BOOL_VALUES:
            results.append(CheckResult("error", f"{key} must be a boolean"))
        else:
            results.append(CheckResult("ok", f"{key}={raw}"))
    return results


def check_public_surface(values: Dict[str, str]) -> List[CheckResult]:
    raw_docs = values.get("ENABLE_DOCS", "").strip().lower()
    if not raw_docs:
        docs_enabled = False
    elif raw_docs in BOOL_TRUE_VALUES:
        docs_enabled = True
    elif raw_docs in BOOL_FALSE_VALUES:
        docs_enabled = False
    else:
        return []

    if docs_enabled:
        return [
            CheckResult(
                "warn",
                "ENABLE_DOCS is enabled; set ENABLE_DOCS=false on public deployments",
            )
        ]
    return [CheckResult("ok", "ENABLE_DOCS is disabled")]


def check_admin_token_query(values: Dict[str, str]) -> List[CheckResult]:
    if not values.get("ADMIN_TOKEN", "").strip():
        return []

    raw = values.get("ALLOW_ADMIN_TOKEN_QUERY", "").strip().lower()
    if not raw:
        enabled = False
    elif raw in BOOL_TRUE_VALUES:
        enabled = True
    elif raw in BOOL_FALSE_VALUES:
        enabled = False
    else:
        return []

    if enabled:
        return [
            CheckResult(
                "warn",
                "ALLOW_ADMIN_TOKEN_QUERY is enabled; set ALLOW_ADMIN_TOKEN_QUERY=false on public deployments",
            )
        ]
    return [CheckResult("ok", "ALLOW_ADMIN_TOKEN_QUERY is disabled")]


def check_enums(values: Dict[str, str]) -> List[CheckResult]:
    results: List[CheckResult] = []
    for key, allowed_values in ENUM_RULES.items():
        raw = values.get(key, "").strip()
        if not raw:
            continue
        normalized = raw.upper()
        if normalized not in allowed_values:
            allowed = ", ".join(sorted(allowed_values))
            results.append(CheckResult("error", f"{key} must be one of: {allowed}"))
        else:
            results.append(CheckResult("ok", f"{key}={normalized}"))
    return results


def check_admin_token(values: Dict[str, str]) -> List[CheckResult]:
    if values.get("ADMIN_TOKEN", "").strip():
        return [CheckResult("ok", "ADMIN_TOKEN is configured")]
    return [
        CheckResult(
            "warn",
            "ADMIN_TOKEN is empty; /ready and /metrics will be public",
        )
    ]


def check_customer_service_fallback(values: Dict[str, str]) -> List[CheckResult]:
    raw = values.get("ENABLE_CUSTOMER_SERVICE_FALLBACK", "").strip().lower()
    if raw not in BOOL_TRUE_VALUES:
        return []

    missing = [
        key
        for key in ("WECHAT_APP_ID", "WECHAT_APP_SECRET")
        if not values.get(key, "").strip()
    ]
    if missing:
        return [
            CheckResult(
                "warn",
                "ENABLE_CUSTOMER_SERVICE_FALLBACK is enabled but missing: "
                + ", ".join(missing),
            )
        ]
    return [CheckResult("ok", "ENABLE_CUSTOMER_SERVICE_FALLBACK has required app credentials")]


def check_dependencies(provider: str = DEFAULT_PROVIDER) -> List[CheckResult]:
    results: List[CheckResult] = []
    packages = REQUIRED_PACKAGES + PROVIDER_PACKAGES.get(provider, ())
    for package in packages:
        if importlib.util.find_spec(package) is None:
            results.append(CheckResult("error", f"Python package missing: {package}"))
        else:
            results.append(CheckResult("ok", f"Python package available: {package}"))
    return results


def run_checks(env_file: str, skip_dependencies: bool) -> List[CheckResult]:
    values = merged_config(env_file)
    results: List[CheckResult] = []
    results.extend(check_required(values))
    results.extend(check_models(values))
    results.extend(check_token_strength(values))
    results.extend(check_numbers(values))
    results.extend(check_booleans(values))
    results.extend(check_public_surface(values))
    results.extend(check_admin_token_query(values))
    results.extend(check_enums(values))
    results.extend(check_admin_token(values))
    results.extend(check_customer_service_fallback(values))
    if not skip_dependencies:
        results.extend(check_dependencies(provider_from_values(values)))
    return results


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate local deployment configuration.")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--skip-dependencies", action="store_true")
    parser.add_argument(
        "--strict-warnings",
        action="store_true",
        help="Return a non-zero exit code when warnings are present.",
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    results = run_checks(args.env_file, args.skip_dependencies)
    has_error = False
    has_warning = False
    for result in results:
        print(f"{result.level}: {result.message}")
        has_error = has_error or result.level == "error"
        has_warning = has_warning or result.level == "warn"
    return 1 if has_error or (args.strict_warnings and has_warning) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
