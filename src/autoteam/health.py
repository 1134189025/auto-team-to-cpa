"""Codex auth health checks for child accounts."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

from autoteam.accounts import (
    ERROR_STAGE_CODEX,
    HEALTH_STATUS_AUTH_ERROR,
    HEALTH_STATUS_CHECK_FAILED,
    HEALTH_STATUS_HEALTHY,
    HEALTH_STATUS_QUOTA_EXHAUSTED,
    REMOTE_STATE_MEMBER,
    STATUS_AUTH_SAVED,
    STATUS_BLOCKED,
    STATUS_READY,
    discover_auth_file,
    update_account_by_id,
)
from autoteam.auth_storage import ensure_auth_file_permissions
from autoteam.codex_auth import check_codex_quota, refresh_access_token
from autoteam.textio import read_text, write_text

logger = logging.getLogger(__name__)

CHECKABLE_STATUSES = {STATUS_READY, STATUS_AUTH_SAVED}
TOKEN_REFRESH_SKEW_SECONDS = 60


def _utc_iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts or time.time()))


def _parse_expired(value) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _compact_error(info, fallback: str) -> str:
    if isinstance(info, dict):
        for key in ("error", "message", "detail", "code"):
            value = str(info.get(key) or "").strip()
            if value:
                return value[:500]
        if info.get("status_code"):
            return f"HTTP {info.get('status_code')}"
    value = str(info or "").strip()
    return (value or fallback)[:500]


def _read_auth_file(path: str) -> dict:
    data = json.loads(read_text(path))
    if not isinstance(data, dict):
        raise ValueError("auth file is not a JSON object")
    return data


def _write_refreshed_auth_file(path: str, auth_data: dict, token_data: dict) -> dict:
    expires_at = time.time() + int(token_data.get("expires_in") or 3600)
    auth_data = dict(auth_data)
    auth_data["access_token"] = token_data.get("access_token") or auth_data.get("access_token", "")
    auth_data["refresh_token"] = token_data.get("refresh_token") or auth_data.get("refresh_token", "")
    if token_data.get("id_token"):
        auth_data["id_token"] = token_data["id_token"]
    auth_data["expired"] = _utc_iso(expires_at)
    auth_data["last_refresh"] = _utc_iso()
    write_text(path, json.dumps(auth_data, indent=2))
    ensure_auth_file_permissions(path)
    return auth_data


def _ensure_fresh_access_token(auth_path: str, auth_data: dict, *, force_refresh: bool = False) -> tuple[str, str, str]:
    access_token = str(auth_data.get("access_token") or "").strip()
    refresh_token = str(auth_data.get("refresh_token") or "").strip()
    expired_at = _parse_expired(auth_data.get("expired"))

    if not force_refresh and access_token and (not expired_at or expired_at > time.time() + TOKEN_REFRESH_SKEW_SECONDS):
        return access_token, "", ""

    if not refresh_token:
        return "", "missing refresh token", "auth_error"

    try:
        token_data = refresh_access_token(refresh_token)
    except Exception as exc:
        return "", f"refresh token check failed: {exc}", "check_failed"
    if not token_data or not token_data.get("access_token"):
        return "", "refresh token failed", "auth_error"

    try:
        refreshed = _write_refreshed_auth_file(auth_path, auth_data, token_data)
    except Exception as exc:
        return "", f"auth file refresh write failed: {exc}", "check_failed"
    return str(refreshed.get("access_token") or "").strip(), "", ""


def _mark_check_failed(account: dict, checked_at: float, error: str) -> dict:
    return update_account_by_id(
        account["id"],
        health_status=HEALTH_STATUS_CHECK_FAILED,
        health_checked_at=checked_at,
        health_error=error,
    ) or {**account, "health_status": HEALTH_STATUS_CHECK_FAILED, "health_checked_at": checked_at, "health_error": error}


def _mark_auth_error(account: dict, checked_at: float, error: str) -> dict:
    return update_account_by_id(
        account["id"],
        status=STATUS_BLOCKED,
        health_status=HEALTH_STATUS_AUTH_ERROR,
        health_checked_at=checked_at,
        health_error=error,
        error=error,
        error_stage=ERROR_STAGE_CODEX,
        completed_at=None,
    ) or {
        **account,
        "status": STATUS_BLOCKED,
        "health_status": HEALTH_STATUS_AUTH_ERROR,
        "health_checked_at": checked_at,
        "health_error": error,
    }


def check_child_account_health(account: dict) -> dict:
    """Check one local child account and persist only the health-related result."""
    if account.get("status") not in CHECKABLE_STATUSES or account.get("remote_state") != REMOTE_STATE_MEMBER:
        return {"outcome": "skipped", "account_id": account.get("id"), "email": account.get("email"), "error": ""}

    checked_at = time.time()
    auth_file = discover_auth_file(account.get("email", ""), account.get("auth_file", ""))
    if not auth_file or not Path(auth_file).exists():
        _mark_check_failed(account, checked_at, "missing auth file")
        return {"outcome": "check_failed", "account_id": account.get("id"), "email": account.get("email"), "error": "missing auth file"}

    try:
        auth_data = _read_auth_file(auth_file)
    except Exception as exc:
        error = f"auth file read failed: {exc}"
        _mark_check_failed(account, checked_at, error)
        return {"outcome": "check_failed", "account_id": account.get("id"), "email": account.get("email"), "error": error}

    access_token, refresh_error, refresh_error_kind = _ensure_fresh_access_token(auth_file, auth_data)
    if refresh_error:
        if refresh_error_kind == "check_failed":
            _mark_check_failed(account, checked_at, refresh_error)
            return {"outcome": "check_failed", "account_id": account.get("id"), "email": account.get("email"), "error": refresh_error}
        _mark_auth_error(account, checked_at, refresh_error)
        return {"outcome": "blocked", "account_id": account.get("id"), "email": account.get("email"), "error": refresh_error}

    account_id = auth_data.get("account_id") or None
    try:
        result, info = check_codex_quota(access_token, account_id)
    except Exception as exc:
        error = f"quota check failed: {exc}"
        _mark_check_failed(account, checked_at, error)
        return {"outcome": "check_failed", "account_id": account.get("id"), "email": account.get("email"), "error": error}

    if result == "auth_error":
        retry_token, refresh_error, refresh_error_kind = _ensure_fresh_access_token(auth_file, auth_data, force_refresh=True)
        if refresh_error:
            if refresh_error_kind == "check_failed":
                _mark_check_failed(account, checked_at, refresh_error)
                return {"outcome": "check_failed", "account_id": account.get("id"), "email": account.get("email"), "error": refresh_error}
            error = refresh_error or _compact_error(info, "Codex auth unauthorized")
            _mark_auth_error(account, checked_at, error)
            return {"outcome": "blocked", "account_id": account.get("id"), "email": account.get("email"), "error": error}
        if retry_token and retry_token != access_token:
            try:
                result, info = check_codex_quota(retry_token, account_id)
            except Exception as exc:
                error = f"quota check failed after refresh: {exc}"
                _mark_check_failed(account, checked_at, error)
                return {"outcome": "check_failed", "account_id": account.get("id"), "email": account.get("email"), "error": error}

    if result == "ok":
        update_account_by_id(
            account["id"],
            auth_file=auth_file,
            health_status=HEALTH_STATUS_HEALTHY,
            health_checked_at=checked_at,
            health_error="",
        )
        return {"outcome": "healthy", "account_id": account.get("id"), "email": account.get("email"), "error": ""}

    if result == "exhausted":
        error = _compact_error(info, "quota exhausted")
        update_account_by_id(
            account["id"],
            auth_file=auth_file,
            health_status=HEALTH_STATUS_QUOTA_EXHAUSTED,
            health_checked_at=checked_at,
            health_error=error,
        )
        return {"outcome": "quota_exhausted", "account_id": account.get("id"), "email": account.get("email"), "error": error}

    if result == "auth_error":
        error = _compact_error(info, "Codex auth unauthorized")
        _mark_auth_error(account, checked_at, error)
        return {"outcome": "blocked", "account_id": account.get("id"), "email": account.get("email"), "error": error}

    error = _compact_error(info, "quota check failed")
    _mark_check_failed(account, checked_at, error)
    return {"outcome": "check_failed", "account_id": account.get("id"), "email": account.get("email"), "error": error}
