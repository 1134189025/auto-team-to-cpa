from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

DEFAULT_CONFIG = {
    "controller_url": "http://127.0.0.1:9097",
    "secret": "123123",
    "proxy_group": "",
    "interval_seconds": 300,
    "random": False,
    "include_keywords": [],
    "exclude_keywords": ["DIRECT", "REJECT", "GLOBAL", "剩余流量", "到期"],
    "request_timeout_seconds": 10,
}

GROUP_TYPES = {"selector", "urltest", "fallback", "loadbalance", "load-balance", "relay"}
FORBIDDEN_NODE_NAMES = {"DIRECT", "REJECT", "GLOBAL", "PASS", "COMPATIBLE"}
PREFERRED_GROUP_NAMES = [
    "🚀 节点选择",
    "节点选择",
    "Proxy",
    "PROXY",
    "代理",
    "手动切换",
    "Select",
    "SELECT",
]


class ClashApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class RotatorConfig:
    controller_url: str
    secret: str
    proxy_group: str
    interval_seconds: int
    random: bool
    include_keywords: list[str]
    exclude_keywords: list[str]
    request_timeout_seconds: int

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RotatorConfig:
        merged = {**DEFAULT_CONFIG, **raw}
        interval_seconds = int(merged["interval_seconds"])
        request_timeout_seconds = int(merged["request_timeout_seconds"])
        if interval_seconds < 1:
            raise ValueError("interval_seconds must be >= 1")
        if request_timeout_seconds < 1:
            raise ValueError("request_timeout_seconds must be >= 1")
        return cls(
            controller_url=str(merged["controller_url"]).rstrip("/"),
            secret=str(merged.get("secret") or ""),
            proxy_group=str(merged.get("proxy_group") or ""),
            interval_seconds=interval_seconds,
            random=bool(merged.get("random")),
            include_keywords=[str(item) for item in merged.get("include_keywords") or []],
            exclude_keywords=[str(item) for item in merged.get("exclude_keywords") or []],
            request_timeout_seconds=request_timeout_seconds,
        )


class ClashClient:
    def __init__(self, config: RotatorConfig):
        self.config = config

    def get_proxies(self) -> dict[str, Any]:
        payload = self._request_json("GET", "/proxies")
        proxies = payload.get("proxies")
        if not isinstance(proxies, dict):
            raise ClashApiError("Clash response does not contain a valid proxies object")
        return proxies

    def switch_group(self, group_name: str, node_name: str) -> None:
        path = f"/proxies/{quote(group_name, safe='')}"
        self._request_json("PUT", path, {"name": node_name})

    def _request_json(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.config.controller_url}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if self.config.secret:
            headers["Authorization"] = f"Bearer {self.config.secret}"
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.config.request_timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            if exc.code in {401, 403}:
                raise ClashApiError("Clash controller rejected the request. Check the secret/key.", exc.code)
            if detail:
                raise ClashApiError(f"Clash API returned HTTP {exc.code}: {detail}", exc.code)
            raise ClashApiError(f"Clash API returned HTTP {exc.code}", exc.code)
        except URLError as exc:
            raise ClashApiError(f"Cannot connect to Clash controller: {exc.reason}")
        except TimeoutError:
            raise ClashApiError("Timed out while connecting to Clash controller")

        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            raise ClashApiError("Clash API returned invalid JSON")


def load_config(path: Path) -> RotatorConfig:
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Created default config: {path}")
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("config.json must contain a JSON object")
    return RotatorConfig.from_dict(raw)


def is_group_proxy(proxy: dict[str, Any] | None) -> bool:
    if not isinstance(proxy, dict):
        return False
    return str(proxy.get("type") or "").replace(" ", "").lower() in GROUP_TYPES


def proxy_type(proxies: dict[str, Any], name: str) -> str:
    proxy = proxies.get(name)
    if not isinstance(proxy, dict):
        return ""
    return str(proxy.get("type") or "")


def selectable_groups(proxies: dict[str, Any]) -> list[str]:
    groups = []
    for name, proxy in proxies.items():
        if not isinstance(proxy, dict):
            continue
        candidates = proxy.get("all")
        if isinstance(candidates, list) and len(candidates) > 1:
            groups.append(name)
    return groups


def choose_group(config: RotatorConfig, proxies: dict[str, Any]) -> str:
    if config.proxy_group:
        if config.proxy_group not in proxies:
            available = ", ".join(selectable_groups(proxies)) or "(none)"
            raise ClashApiError(f"Configured proxy_group not found: {config.proxy_group}. Available groups: {available}")
        return config.proxy_group

    groups = selectable_groups(proxies)
    if not groups:
        raise ClashApiError("No selectable proxy group found in Clash /proxies response")

    for preferred in PREFERRED_GROUP_NAMES:
        if preferred in groups:
            return preferred

    selector_groups = [name for name in groups if proxy_type(proxies, name).replace(" ", "").lower() == "selector"]
    non_global_selectors = [name for name in selector_groups if name.upper() != "GLOBAL"]
    if non_global_selectors:
        return non_global_selectors[0]
    if selector_groups:
        return selector_groups[0]

    non_global_groups = [name for name in groups if name.upper() != "GLOBAL"]
    return non_global_groups[0] if non_global_groups else groups[0]


def keyword_allowed(name: str, include_keywords: list[str], exclude_keywords: list[str]) -> bool:
    lowered = name.casefold()
    if include_keywords and not any(keyword.casefold() in lowered for keyword in include_keywords):
        return False
    return not any(keyword.casefold() in lowered for keyword in exclude_keywords)


def candidate_nodes(config: RotatorConfig, proxies: dict[str, Any], group_name: str) -> list[str]:
    group = proxies.get(group_name)
    if not isinstance(group, dict):
        raise ClashApiError(f"Proxy group is not valid: {group_name}")

    raw_candidates = group.get("all")
    if not isinstance(raw_candidates, list):
        raise ClashApiError(f"Proxy group is not selectable: {group_name}")

    filtered = []
    for item in raw_candidates:
        name = str(item)
        if name.upper() in FORBIDDEN_NODE_NAMES:
            continue
        if keyword_allowed(name, config.include_keywords, config.exclude_keywords):
            filtered.append(name)

    leaf_nodes = [name for name in filtered if not is_group_proxy(proxies.get(name))]
    return leaf_nodes or filtered


def pick_next_node(config: RotatorConfig, candidates: list[str], current: str) -> str | None:
    if not candidates:
        return None
    alternatives = [name for name in candidates if name != current]
    if not alternatives:
        return None
    if config.random:
        return random.choice(alternatives)
    if current in candidates:
        current_index = candidates.index(current)
        return candidates[(current_index + 1) % len(candidates)]
    return candidates[0]


def switch_once(config: RotatorConfig, client: ClashClient, list_groups: bool = True) -> bool:
    proxies = client.get_proxies()
    groups = selectable_groups(proxies)
    if list_groups:
        print("Selectable groups: " + (", ".join(groups) if groups else "(none)"))

    group_name = choose_group(config, proxies)
    group = proxies[group_name]
    current = str(group.get("now") or "")
    candidates = candidate_nodes(config, proxies, group_name)
    next_node = pick_next_node(config, candidates, current)

    if not next_node:
        print(f"[{timestamp()}] No alternative node for group '{group_name}'. Current: {current or '-'}")
        return False

    client.switch_group(group_name, next_node)
    print(f"[{timestamp()}] Switched '{group_name}': {current or '-'} -> {next_node}")
    return True


def print_status(config: RotatorConfig) -> None:
    group = config.proxy_group or "(auto)"
    mode = "random" if config.random else "sequential"
    print("Clash rotator started")
    print(f"Controller: {config.controller_url}")
    print(f"Proxy group: {group}")
    print(f"Interval: {config.interval_seconds}s")
    print(f"Mode: {mode}")
    print("Stop: press Ctrl+C or close this window")
    print()


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_forever(config: RotatorConfig) -> int:
    client = ClashClient(config)
    print_status(config)
    first_run = True
    while True:
        try:
            switch_once(config, client, list_groups=first_run)
        except ClashApiError as exc:
            print(f"[{timestamp()}] ERROR: {exc}")
        except KeyboardInterrupt:
            print("\nStopped.")
            return 0

        first_run = False
        next_time = datetime.now() + timedelta(seconds=config.interval_seconds)
        print(f"Next switch at {next_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print()
        try:
            time.sleep(config.interval_seconds)
        except KeyboardInterrupt:
            print("\nStopped.")
            return 0


def list_groups(config: RotatorConfig) -> int:
    client = ClashClient(config)
    proxies = client.get_proxies()
    groups = selectable_groups(proxies)
    if not groups:
        print("No selectable proxy groups found.")
        return 1

    print("Selectable proxy groups:")
    for name in groups:
        proxy = proxies.get(name) or {}
        now = proxy.get("now") or "-"
        kind = proxy.get("type") or "-"
        candidates = candidate_nodes(config, proxies, name)
        print(f"- {name} | type: {kind} | current: {now} | candidates after filter: {len(candidates)}")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    default_config = Path(__file__).with_name("config.json")
    parser = argparse.ArgumentParser(description="Rotate Clash proxy nodes on a fixed interval.")
    parser.add_argument("--config", default=str(default_config), help="Path to config.json")
    parser.add_argument("--once", action="store_true", help="Switch once and exit")
    parser.add_argument("--list-groups", action="store_true", help="List selectable Clash proxy groups and exit")
    return parser.parse_args(argv)


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        config = load_config(Path(args.config))
        if args.list_groups:
            return list_groups(config)
        if args.once:
            return 0 if switch_once(config, ClashClient(config)) else 1
        return run_forever(config)
    except (ClashApiError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
