"""Interactive setup wizard."""

from __future__ import annotations

import importlib
import logging
import os
import re
import secrets
import sys

from autoteam.config import PROJECT_ROOT
from autoteam.textio import parse_env_line, read_text, write_text

logger = logging.getLogger(__name__)

ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"

REQUIRED_CONFIGS = [
    ("FREEMAIL_BASE_URL", "Freemail API 地址", "", False),
    ("FREEMAIL_ROOT_TOKEN", "Freemail Root Token", "", False),
    ("CPA_URL", "CPA (CLIProxyAPI) 地址", "http://127.0.0.1:8317", False),
    ("CPA_KEY", "CPA 管理密钥", "", False),
    ("PLAYWRIGHT_PROXY_URL", "Playwright 浏览器代理 URL（可选）", "", True),
    ("PLAYWRIGHT_PROXY_BYPASS", "Playwright 代理绕过列表（可选）", "", True),
    ("API_KEY", "API 鉴权密钥（回车自动生成）", "", False),
]


def _read_env() -> dict[str, str]:
    result = {}
    if ENV_FILE.exists():
        for line in read_text(ENV_FILE).splitlines():
            parsed = parse_env_line(line)
            if parsed:
                key, value = parsed
                result[key] = value
    return result


def _write_env(key: str, value: str):
    if ENV_FILE.exists():
        content = read_text(ENV_FILE)
        pattern = rf"^{re.escape(key)}=.*$"
        if re.search(pattern, content, re.MULTILINE):
            content = re.sub(pattern, f"{key}={value}", content, flags=re.MULTILINE)
        else:
            content = content.rstrip() + f"\n{key}={value}\n"
        write_text(ENV_FILE, content)
        return

    if ENV_EXAMPLE.exists():
        content = read_text(ENV_EXAMPLE)
        pattern = rf"^{re.escape(key)}=.*$"
        if re.search(pattern, content, re.MULTILINE):
            content = re.sub(pattern, f"{key}={value}", content, flags=re.MULTILINE)
            write_text(ENV_FILE, content)
            return
    write_text(ENV_FILE, f"{key}={value}\n")


def _is_interactive() -> bool:
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def check_and_setup(interactive: bool = True) -> bool:
    interactive = interactive and _is_interactive()
    env = _read_env()
    missing = []

    for key, prompt, default, optional in REQUIRED_CONFIGS:
        value = env.get(key, "") or os.environ.get(key, "")
        if not value and not optional:
            missing.append((key, prompt, default, optional))

    if not missing:
        if not _verify_freemail():
            logger.error("[验证] Freemail 配置有误，请修改 .env 后重新启动")
            sys.exit(1)
        if not _verify_cpa():
            logger.error("[验证] CPA 配置有误，请修改 .env 后重新启动")
            sys.exit(1)
        return True

    if not interactive:
        for key, prompt, _default, _optional in missing:
            logger.warning("[配置] 缺少必填项: %s (%s)", key, prompt)
        logger.warning("[配置] 请通过 Web 面板或编辑 .env 文件补全配置")
        return False

    print("\n=== AutoTeam 首次配置 ===\n")
    print("请填写以下配置项，直接回车使用默认值（如有）：\n")

    for key, prompt, default, optional in missing:
        hint = f" [{default}]" if default else ""
        if key == "API_KEY":
            hint = " [回车自动生成]"
        try:
            value = input(f"  {prompt}{hint}: ").strip()
        except KeyboardInterrupt:
            print("\n\n已取消配置。")
            raise SystemExit(130)

        if not value:
            if key == "API_KEY":
                value = secrets.token_urlsafe(24)
                print(f"    -> 已自动生成: {value}")
            elif default:
                value = default
                print(f"    -> 使用默认值: {value}")
            elif not optional:
                print("    -> 跳过（必填项，可稍后编辑 .env）")
                continue

        if value:
            _write_env(key, value)
            os.environ[key] = value

    print("\n配置已保存到 .env\n")

    import autoteam.config

    importlib.reload(autoteam.config)
    try:
        import autoteam.freemail

        importlib.reload(autoteam.freemail)
    except Exception:
        pass

    if not _verify_freemail():
        logger.error("[验证] Freemail 配置有误，请修改 .env 后重新启动")
        sys.exit(1)
    if not _verify_cpa():
        logger.error("[验证] CPA 配置有误，请修改 .env 后重新启动")
        sys.exit(1)
    return True


def _verify_freemail():
    base_url = os.environ.get("FREEMAIL_BASE_URL", "")
    token = os.environ.get("FREEMAIL_ROOT_TOKEN", "")
    if not base_url or not token:
        return False

    logger.info("[验证] Freemail 配置...")
    try:
        from autoteam.freemail import FreemailClient

        client = FreemailClient()
        domains = client.verify()
        logger.info("[验证] Freemail 域名数量: %d", len(domains))
        mailbox = client.create_temp_email(prefix=f"at-test-{secrets.token_hex(3)}")
        logger.info("[验证] Freemail 创建测试邮箱成功: %s", mailbox["email"])
        try:
            client.delete_account(mailbox["email"])
            logger.info("[验证] Freemail 测试邮箱已清理")
        except Exception as exc:
            logger.warning("[验证] Freemail 测试邮箱清理失败: %s（不影响使用）", exc)
        return True
    except Exception as exc:
        logger.error("[验证] Freemail 连接失败: %s", exc)
        logger.error("[验证] 请检查 FREEMAIL_BASE_URL 和 FREEMAIL_ROOT_TOKEN")
        return False


def _verify_cpa():
    cpa_url = os.environ.get("CPA_URL", "")
    cpa_key = os.environ.get("CPA_KEY", "")
    if not cpa_url or not cpa_key:
        return False

    logger.info("[验证] CPA 配置...")
    try:
        import requests

        resp = requests.get(
            f"{cpa_url}/v0/management/auth-files",
            headers={"Authorization": f"Bearer {cpa_key}"},
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info("[验证] CPA 连接成功")
            return True
        if resp.status_code == 401:
            logger.error("[验证] CPA 连接失败: 密钥无效 (401)")
            return False
        logger.error("[验证] CPA 连接失败: HTTP %d", resp.status_code)
        return False
    except Exception as exc:
        logger.error("[验证] CPA 连接失败: %s", exc)
        return False
