"""Configuration loader."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

from autoteam.textio import parse_env_line, parse_env_value, read_text

PROJECT_ROOT = Path(__file__).parent.parent.parent

_env_file = PROJECT_ROOT / ".env"
if _env_file.exists():
    for line in read_text(_env_file).splitlines():
        parsed = parse_env_line(line)
        if parsed:
            key, value = parsed
            os.environ.setdefault(key, value)


def _get_int_env(name: str, default: int) -> int:
    return int(parse_env_value(os.environ.get(name, str(default))))


def _get_bounded_int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(parse_env_value(os.environ.get(name, str(default))))
    except Exception:
        value = default
    return min(maximum, max(minimum, value))


def get_target_children_per_parent() -> int:
    return _get_bounded_int_env("TARGET_CHILDREN_PER_PARENT", 4, minimum=1, maximum=50)


FREEMAIL_BASE_URL = os.environ.get("FREEMAIL_BASE_URL", "").strip()
FREEMAIL_ROOT_TOKEN = os.environ.get("FREEMAIL_ROOT_TOKEN", "").strip()

CPA_URL = os.environ.get("CPA_URL", "").strip()
CPA_KEY = os.environ.get("CPA_KEY", "").strip()

API_KEY = os.environ.get("API_KEY", "").strip()

EMAIL_POLL_INTERVAL = _get_int_env("EMAIL_POLL_INTERVAL", 3)
EMAIL_POLL_TIMEOUT = _get_int_env("EMAIL_POLL_TIMEOUT", 300)

PLAYWRIGHT_PROXY_URL = os.environ.get("PLAYWRIGHT_PROXY_URL", "").strip()
PLAYWRIGHT_PROXY_SERVER = os.environ.get("PLAYWRIGHT_PROXY_SERVER", "").strip()
PLAYWRIGHT_PROXY_USERNAME = os.environ.get("PLAYWRIGHT_PROXY_USERNAME", "").strip()
PLAYWRIGHT_PROXY_PASSWORD = os.environ.get("PLAYWRIGHT_PROXY_PASSWORD", "").strip()
PLAYWRIGHT_PROXY_BYPASS = os.environ.get("PLAYWRIGHT_PROXY_BYPASS", "").strip()


def _format_proxy_host(hostname: str) -> str:
    if ":" in hostname and not hostname.startswith("["):
        return f"[{hostname}]"
    return hostname


def _parse_proxy_url(proxy_url: str):
    if "://" not in proxy_url:
        return {"server": proxy_url}

    parsed = urlsplit(proxy_url)
    if not parsed.scheme or not parsed.hostname:
        return {"server": proxy_url}

    host = _format_proxy_host(parsed.hostname)
    server = f"{parsed.scheme}://{host}"
    if parsed.port:
        server = f"{server}:{parsed.port}"

    proxy = {"server": server}
    if parsed.username:
        proxy["username"] = unquote(parsed.username)
    if parsed.password:
        proxy["password"] = unquote(parsed.password)
    return proxy


def get_playwright_launch_options():
    options = {
        "headless": False,
        "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    }

    proxy = None
    if PLAYWRIGHT_PROXY_URL:
        proxy = _parse_proxy_url(PLAYWRIGHT_PROXY_URL)
    elif PLAYWRIGHT_PROXY_SERVER:
        proxy = {"server": PLAYWRIGHT_PROXY_SERVER}
        if PLAYWRIGHT_PROXY_USERNAME:
            proxy["username"] = PLAYWRIGHT_PROXY_USERNAME
        if PLAYWRIGHT_PROXY_PASSWORD:
            proxy["password"] = PLAYWRIGHT_PROXY_PASSWORD

    if proxy:
        if PLAYWRIGHT_PROXY_BYPASS:
            proxy["bypass"] = PLAYWRIGHT_PROXY_BYPASS
        options["proxy"] = proxy

    return options
