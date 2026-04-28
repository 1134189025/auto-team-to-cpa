"""Freemail API client."""

from __future__ import annotations

import html
import json
import logging
import re
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import requests

import autoteam.config as config
from autoteam.textio import read_text, write_text

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
PROVIDER_STATE_FILE = PROJECT_ROOT / "freemail_state.json"

_VERIFICATION_CODE_PATTERNS = (
    r"(?:temporary\s+(?:openai|chatgpt)\s+login\s+code(?:\s+is)?|verification\s+code(?:\s+is)?|login\s+code(?:\s+is)?|code(?:\s+is)?|验证码(?:为|是)?)\D{0,24}(\d{6})",
    r"\b(\d{6})\b",
)


def _normalize_text(value) -> str:
    return str(value or "").strip()


class FreemailClient:
    def __init__(self):
        self.base_url = config.FREEMAIL_BASE_URL.rstrip("/")
        self.token = config.FREEMAIL_ROOT_TOKEN.strip()
        self.session = requests.Session()

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _get(self, path, params=None):
        resp = self.session.get(f"{self.base_url}{path}", headers=self._headers(), params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path, data=None):
        resp = self.session.post(f"{self.base_url}{path}", headers=self._headers(), json=data or {}, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path, params=None):
        resp = self.session.delete(f"{self.base_url}{path}", headers=self._headers(), params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def verify(self):
        domains = self.get_domains()
        if not domains:
            raise RuntimeError("Freemail 未返回任何可用域名")
        return domains

    def get_domains(self) -> list[str]:
        data = self._get("/api/domains")
        if not isinstance(data, list):
            raise RuntimeError("Freemail 域名列表格式无效")
        return [str(item).strip() for item in data if str(item or "").strip()]

    def _load_state(self) -> dict:
        if not PROVIDER_STATE_FILE.exists():
            return {"next_domain_index": 0}
        try:
            return json.loads(read_text(PROVIDER_STATE_FILE))
        except Exception:
            return {"next_domain_index": 0}

    def _save_state(self, state: dict):
        write_text(PROVIDER_STATE_FILE, json.dumps(state, indent=2, ensure_ascii=False))

    def _next_domain_index(self, domains: list[str]) -> int:
        if not domains:
            raise RuntimeError("Freemail 没有可用域名")
        state = self._load_state()
        current = int(state.get("next_domain_index") or 0)
        selected = current % len(domains)
        state["next_domain_index"] = (selected + 1) % len(domains)
        self._save_state(state)
        return selected

    def create_temp_email(self, prefix=None):
        domains = self.get_domains()
        domain_index = self._next_domain_index(domains)
        local = (prefix or f"tmp-{uuid.uuid4().hex[:10]}").strip().lower()
        result = self._post("/api/create", {"local": local, "domainIndex": domain_index})
        email = _normalize_text(result.get("email")).lower()
        if not email:
            raise RuntimeError("Freemail 创建邮箱失败：未返回邮箱地址")
        logger.info("[Freemail] 临时邮箱已创建: %s", email)
        return {"email": email, "expires": result.get("expires"), "domain_index": domain_index}

    def list_emails(self, mailbox: str, size=20):
        mailbox = _normalize_text(mailbox).lower()
        data = self._get("/api/emails", {"mailbox": mailbox, "limit": min(max(1, int(size)), 50)})
        if not isinstance(data, list):
            return []
        return data

    def get_email(self, email_id: int | str):
        return self._get(f"/api/email/{quote(str(email_id))}")

    def search_emails_by_recipient(self, to_email, size=10, account_id=None):
        del account_id
        return self.list_emails(to_email, size=size)

    @staticmethod
    def _email_id(email_data) -> int:
        try:
            return int((email_data or {}).get("id") or 0)
        except Exception:
            return 0

    def get_latest_email_id(self, to_email, sender_keyword=None):
        target = _normalize_text(to_email).lower()
        latest_id = 0
        for email in self.search_emails_by_recipient(target, size=20):
            sender = _normalize_text(email.get("sender")).lower()
            if sender_keyword and sender_keyword.lower() not in sender:
                continue
            latest_id = max(latest_id, self._email_id(email))
        return latest_id

    @staticmethod
    def _html_to_visible_text(value):
        content = str(value or "")
        if not content:
            return ""

        content = re.sub(r"(?is)<(script|style)\b.*?>.*?</\1>", " ", content)
        content = re.sub(r"(?is)<!--.*?-->", " ", content)
        content = re.sub(r"(?i)<br\s*/?>", "\n", content)
        content = re.sub(r"(?i)</(?:p|div|tr|table|h[1-6]|li|td|section|article)>", "\n", content)
        content = re.sub(r"(?s)<[^>]+>", " ", content)
        content = html.unescape(content)
        content = re.sub(r"[\t\r\f\v ]+", " ", content)
        content = re.sub(r"\n\s+", "\n", content)
        content = re.sub(r"\n{2,}", "\n", content)
        return content.strip()

    def extract_verification_code(self, email_data):
        direct = _normalize_text(email_data.get("verification_code"))
        if re.fullmatch(r"\d{6}", direct):
            return direct

        sources = []
        for key in ("content", "html_content", "preview", "subject"):
            value = _normalize_text(email_data.get(key))
            if value and value not in sources:
                sources.append(value)
        visible_html = self._html_to_visible_text(email_data.get("html_content"))
        if visible_html and visible_html not in sources:
            sources.append(visible_html)

        for source in sources:
            for pattern in _VERIFICATION_CODE_PATTERNS:
                match = re.search(pattern, source, re.IGNORECASE)
                if match:
                    return match.group(1)
        return None

    def wait_for_email(self, to_email, timeout=None, sender_keyword=None, since_id=0):
        timeout = timeout or config.EMAIL_POLL_TIMEOUT
        target = _normalize_text(to_email).lower()
        logger.info("[Freemail] 等待邮件到达 %s... (超时 %ds)", target, timeout)
        started_at = time.time()
        seen_highest = int(since_id or 0)

        while time.time() - started_at < timeout:
            emails = self.search_emails_by_recipient(target, size=10)
            for email in emails:
                email_id = int(email.get("id") or 0)
                sender = _normalize_text(email.get("sender")).lower()
                if email_id <= seen_highest:
                    continue
                if sender_keyword and sender_keyword.lower() not in sender:
                    continue
                return email
            time.sleep(config.EMAIL_POLL_INTERVAL)

        raise TimeoutError(f"等待邮件超时: {target}")

    def wait_for_verification_code(self, to_email, timeout=None, since_id=0, sender_keyword="openai"):
        timeout = timeout or config.EMAIL_POLL_TIMEOUT
        target = _normalize_text(to_email).lower()
        logger.info("[Freemail] 绛夊緟楠岃瘉鐮侀偖浠? %s... (瓒呮椂 %ds, since_id=%s)", target, timeout, int(since_id or 0))
        started_at = time.time()
        seen_ids: set[int] = set()

        while time.time() - started_at < timeout:
            for email_meta in self.search_emails_by_recipient(target, size=10):
                email_id = self._email_id(email_meta)
                if email_id and (email_id <= int(since_id or 0) or email_id in seen_ids):
                    continue

                if email_id:
                    seen_ids.add(email_id)

                sender = _normalize_text(email_meta.get("sender")).lower()
                subject = _normalize_text(email_meta.get("subject")).lower()
                if sender_keyword and sender_keyword.lower() not in sender:
                    continue
                if "invited" in subject or "invitation" in subject:
                    continue

                detail = email_meta
                if email_id:
                    try:
                        detail = self.get_email(email_id)
                    except Exception:
                        detail = email_meta

                code = self.extract_verification_code(detail)
                if code:
                    return {"code": code, "email_id": email_id, "email": detail}

            time.sleep(config.EMAIL_POLL_INTERVAL)

        raise TimeoutError(f"绛夊緟楠岃瘉鐮佽秴鏃? {target}")

    def extract_invite_link(self, email_data):
        sources = []
        for key in ("html_content", "content", "preview", "subject"):
            value = _normalize_text(email_data.get(key))
            if value and value not in sources:
                sources.append(value)

        patterns = (
            r'href="(https://chatgpt\.com/auth/login\?[^"]+)"',
            r"(https://chatgpt\.com/auth/login\?[^\s<>\"]+)",
            r"(https?://[^\s<>\"]*(?:invite|accept|join|workspace)[^\s<>\"]*)",
        )
        for source in sources:
            for pattern in patterns:
                match = re.search(pattern, source, re.IGNORECASE)
                if match:
                    return match.group(1)
        return None

    def delete_emails_for(self, to_email):
        mailbox = _normalize_text(to_email).lower()
        emails = self.search_emails_by_recipient(mailbox, size=50)
        deleted = 0
        for item in emails:
            email_id = item.get("id")
            if not email_id:
                continue
            try:
                self._delete(f"/api/email/{quote(str(email_id))}")
                deleted += 1
            except Exception:
                continue
        return deleted

    def delete_account(self, mailbox_address):
        mailbox = _normalize_text(mailbox_address).lower()
        result = self._delete("/api/mailboxes", {"address": mailbox})
        logger.info("[Freemail] 已删除邮箱: %s", mailbox)
        return result
