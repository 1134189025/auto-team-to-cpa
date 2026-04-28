"""Child account storage for invited and provisioned accounts."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from autoteam.auth_storage import AUTH_DIR
from autoteam.textio import read_text, write_text

PROJECT_ROOT = Path(__file__).parent.parent.parent
ACCOUNTS_FILE = PROJECT_ROOT / "accounts.json"
_ACCOUNTS_LOCK = threading.RLock()

STATUS_INVITED = "invited"
STATUS_ACCEPTED = "accepted"
STATUS_AUTH_SAVED = "auth_saved"
STATUS_READY = "ready"
STATUS_BLOCKED = "blocked"
STATUS_REMOVED = "removed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
VALID_STATUSES = {
    STATUS_INVITED,
    STATUS_ACCEPTED,
    STATUS_AUTH_SAVED,
    STATUS_READY,
    STATUS_BLOCKED,
    STATUS_REMOVED,
    STATUS_FAILED,
    STATUS_CANCELLED,
}
OCCUPYING_STATUSES = {STATUS_INVITED, STATUS_ACCEPTED, STATUS_AUTH_SAVED, STATUS_READY, STATUS_BLOCKED}
RECOVERABLE_STATUSES = {STATUS_INVITED, STATUS_ACCEPTED, STATUS_AUTH_SAVED}

HEALTH_STATUS_UNKNOWN = "unknown"
HEALTH_STATUS_HEALTHY = "healthy"
HEALTH_STATUS_QUOTA_EXHAUSTED = "quota_exhausted"
HEALTH_STATUS_AUTH_ERROR = "auth_error"
HEALTH_STATUS_CHECK_FAILED = "check_failed"
VALID_HEALTH_STATUSES = {
    HEALTH_STATUS_UNKNOWN,
    HEALTH_STATUS_HEALTHY,
    HEALTH_STATUS_QUOTA_EXHAUSTED,
    HEALTH_STATUS_AUTH_ERROR,
    HEALTH_STATUS_CHECK_FAILED,
}

REMOTE_STATE_PENDING_INVITE = "pending_invite"
REMOTE_STATE_MEMBER = "member"
REMOTE_STATE_ABSENT = "absent"
REMOTE_STATE_UNKNOWN = "unknown"
VALID_REMOTE_STATES = {
    REMOTE_STATE_PENDING_INVITE,
    REMOTE_STATE_MEMBER,
    REMOTE_STATE_ABSENT,
    REMOTE_STATE_UNKNOWN,
}

ERROR_STAGE_INVITE = "invite"
ERROR_STAGE_REGISTRATION = "registration"
ERROR_STAGE_CODEX = "codex"
ERROR_STAGE_CPA = "cpa"
ERROR_STAGE_RECONCILE = "reconcile"
ERROR_STAGE_UNKNOWN = "unknown"
VALID_ERROR_STAGES = {
    "",
    ERROR_STAGE_INVITE,
    ERROR_STAGE_REGISTRATION,
    ERROR_STAGE_CODEX,
    ERROR_STAGE_CPA,
    ERROR_STAGE_RECONCILE,
    ERROR_STAGE_UNKNOWN,
}


def _normalize_string(value) -> str:
    return str(value or "").strip()


def _normalize_bool(value, default=True) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _normalize_int(value, default=0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return default


def _normalize_timestamp(value, default=None):
    if value in (None, ""):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except Exception:
        return default


def _normalize_remote_state(value) -> str:
    remote_state = _normalize_string(value) or REMOTE_STATE_UNKNOWN
    if remote_state not in VALID_REMOTE_STATES:
        return REMOTE_STATE_UNKNOWN
    return remote_state


def _normalize_error_stage(value, *, has_error: bool) -> str:
    error_stage = _normalize_string(value)
    if not error_stage and has_error:
        return ERROR_STAGE_UNKNOWN
    if error_stage not in VALID_ERROR_STAGES:
        return ERROR_STAGE_UNKNOWN if has_error else ""
    return error_stage


def _normalize_health_status(value) -> str:
    health_status = _normalize_string(value) or HEALTH_STATUS_UNKNOWN
    if health_status not in VALID_HEALTH_STATUSES:
        return HEALTH_STATUS_UNKNOWN
    return health_status


def discover_auth_file(email: str, auth_file: str = "") -> str:
    candidate = _normalize_string(auth_file)
    if candidate and Path(candidate).exists():
        return str(Path(candidate).resolve())

    email_l = _normalize_string(email).lower()
    if not email_l or not AUTH_DIR.exists():
        return ""

    for path in sorted(AUTH_DIR.glob(f"codex-{email_l}-*.json")):
        if path.is_file():
            return str(path.resolve())
    return ""


def has_auth_file(account: dict) -> bool:
    return bool(discover_auth_file(account.get("email", ""), account.get("auth_file", "")))


def occupies_slot(account_or_status) -> bool:
    if isinstance(account_or_status, dict):
        status = _normalize_string(account_or_status.get("status"))
    else:
        status = _normalize_string(account_or_status)
    return status in OCCUPYING_STATUSES


def is_recoverable_status(account_or_status) -> bool:
    if isinstance(account_or_status, dict):
        status = _normalize_string(account_or_status.get("status"))
    else:
        status = _normalize_string(account_or_status)
    return status in RECOVERABLE_STATUSES


def normalize_account(data: dict) -> dict:
    now = time.time()
    status = _normalize_string(data.get("status")) or STATUS_INVITED
    if status not in VALID_STATUSES:
        status = STATUS_INVITED

    created_at = _normalize_timestamp(data.get("created_at"), now)
    completed_at = _normalize_timestamp(data.get("completed_at"))
    invite_sent_at = _normalize_timestamp(data.get("invite_sent_at"))
    cpa_uploaded_at = _normalize_timestamp(data.get("cpa_uploaded_at"))
    accepted_at = _normalize_timestamp(data.get("accepted_at"))
    auth_saved_at = _normalize_timestamp(data.get("auth_saved_at"))
    last_remote_sync_at = _normalize_timestamp(data.get("last_remote_sync_at"))
    health_checked_at = _normalize_timestamp(data.get("health_checked_at"))
    removed_at = _normalize_timestamp(data.get("removed_at"))
    error = _normalize_string(data.get("error"))
    error_stage = _normalize_error_stage(data.get("error_stage"), has_error=bool(error))
    auth_file = discover_auth_file(data.get("email"), data.get("auth_file", ""))

    return {
        "id": _normalize_string(data.get("id")) or uuid.uuid4().hex,
        "email": _normalize_string(data.get("email")).lower(),
        "password": _normalize_string(data.get("password")),
        "parent_id": _normalize_string(data.get("parent_id")),
        "parent_email": _normalize_string(data.get("parent_email")).lower(),
        "workspace_name": _normalize_string(data.get("workspace_name")),
        "mail_provider": _normalize_string(data.get("mail_provider")) or "freemail",
        "mailbox_address": _normalize_string(data.get("mailbox_address")).lower()
        or _normalize_string(data.get("email")).lower(),
        "invite_id": _normalize_string(data.get("invite_id")),
        "invite_sent_at": invite_sent_at,
        "status": status,
        "remote_state": _normalize_remote_state(data.get("remote_state")),
        "remote_member_id": _normalize_string(data.get("remote_member_id")),
        "accepted_at": accepted_at,
        "auth_saved_at": auth_saved_at,
        "auth_file": auth_file,
        "cpa_uploaded_at": cpa_uploaded_at,
        "error": error,
        "error_stage": error_stage,
        "retry_count": _normalize_int(data.get("retry_count"), 0),
        "last_remote_sync_at": last_remote_sync_at,
        "health_status": _normalize_health_status(data.get("health_status")),
        "health_checked_at": health_checked_at,
        "health_error": _normalize_string(data.get("health_error")),
        "removed_at": removed_at,
        "removed_reason": _normalize_string(data.get("removed_reason")),
        "created_at": created_at,
        "completed_at": completed_at,
        "enabled": _normalize_bool(data.get("enabled"), True),
    }


def load_accounts() -> list[dict]:
    with _ACCOUNTS_LOCK:
        if not ACCOUNTS_FILE.exists():
            return []
        text = read_text(ACCOUNTS_FILE).strip()
        if not text:
            return []
        items = json.loads(text)
        if not isinstance(items, list):
            return []
        return [normalize_account(item) for item in items]


def save_accounts(accounts: list[dict]) -> None:
    with _ACCOUNTS_LOCK:
        normalized = [normalize_account(item) for item in accounts]
        write_text(ACCOUNTS_FILE, json.dumps(normalized, indent=2, ensure_ascii=False))


def find_account(accounts: list[dict], email: str) -> dict | None:
    email = _normalize_string(email).lower()
    for account in accounts:
        if account.get("email", "").lower() == email:
            return account
    return None


def find_account_by_id(accounts: list[dict], account_id: str) -> dict | None:
    target = _normalize_string(account_id)
    for account in accounts:
        if account.get("id") == target:
            return account
    return None


def add_account(
    email: str,
    password: str,
    *,
    parent_id: str,
    parent_email: str,
    workspace_name: str,
    mail_provider: str,
    mailbox_address: str,
    invite_id: str = "",
    status: str = STATUS_INVITED,
    auth_file: str = "",
    error: str = "",
    remote_state: str = REMOTE_STATE_UNKNOWN,
    remote_member_id: str = "",
    accepted_at=None,
    auth_saved_at=None,
    cpa_uploaded_at=None,
    error_stage: str = "",
    retry_count: int = 0,
    last_remote_sync_at=None,
    health_status: str | None = None,
    health_checked_at=None,
    health_error: str = "",
    removed_at=None,
    removed_reason: str = "",
) -> dict:
    with _ACCOUNTS_LOCK:
        accounts = load_accounts()
        existing = find_account(accounts, email)
        if existing:
            existing.update(
                normalize_account(
                    {
                        **existing,
                        "password": password or existing.get("password", ""),
                        "parent_id": parent_id or existing.get("parent_id", ""),
                        "parent_email": parent_email or existing.get("parent_email", ""),
                        "workspace_name": workspace_name or existing.get("workspace_name", ""),
                        "mail_provider": mail_provider or existing.get("mail_provider", "freemail"),
                        "mailbox_address": mailbox_address or existing.get("mailbox_address", ""),
                        "invite_id": invite_id or existing.get("invite_id", ""),
                        "status": status or existing.get("status", STATUS_INVITED),
                        "remote_state": remote_state or existing.get("remote_state", REMOTE_STATE_UNKNOWN),
                        "remote_member_id": remote_member_id or existing.get("remote_member_id", ""),
                        "accepted_at": accepted_at if accepted_at is not None else existing.get("accepted_at"),
                        "auth_saved_at": auth_saved_at if auth_saved_at is not None else existing.get("auth_saved_at"),
                        "auth_file": auth_file or existing.get("auth_file", ""),
                        "cpa_uploaded_at": cpa_uploaded_at if cpa_uploaded_at is not None else existing.get("cpa_uploaded_at"),
                        "error": error or existing.get("error", ""),
                        "error_stage": error_stage or existing.get("error_stage", ""),
                        "retry_count": retry_count if retry_count is not None else existing.get("retry_count", 0),
                        "last_remote_sync_at": last_remote_sync_at if last_remote_sync_at is not None else existing.get("last_remote_sync_at"),
                        "health_status": health_status if health_status is not None else existing.get("health_status", HEALTH_STATUS_UNKNOWN),
                        "health_checked_at": health_checked_at if health_checked_at is not None else existing.get("health_checked_at"),
                        "health_error": health_error or existing.get("health_error", ""),
                        "removed_at": removed_at if removed_at is not None else existing.get("removed_at"),
                        "removed_reason": removed_reason or existing.get("removed_reason", ""),
                        "invite_sent_at": time.time(),
                    }
                )
            )
            save_accounts(accounts)
            return existing

        account = normalize_account(
            {
                "email": email,
                "password": password,
                "parent_id": parent_id,
                "parent_email": parent_email,
                "workspace_name": workspace_name,
                "mail_provider": mail_provider,
                "mailbox_address": mailbox_address,
                "invite_id": invite_id,
                "status": status,
                "remote_state": remote_state,
                "remote_member_id": remote_member_id,
                "accepted_at": accepted_at,
                "auth_saved_at": auth_saved_at,
                "auth_file": auth_file,
                "cpa_uploaded_at": cpa_uploaded_at,
                "error": error,
                "error_stage": error_stage,
                "retry_count": retry_count,
                "last_remote_sync_at": last_remote_sync_at,
                "health_status": health_status or HEALTH_STATUS_UNKNOWN,
                "health_checked_at": health_checked_at,
                "health_error": health_error,
                "removed_at": removed_at,
                "removed_reason": removed_reason,
                "invite_sent_at": time.time(),
                "created_at": time.time(),
            }
        )
        accounts.append(account)
        save_accounts(accounts)
        return account


def update_account(email: str, **kwargs) -> dict | None:
    with _ACCOUNTS_LOCK:
        accounts = load_accounts()
        account = find_account(accounts, email)
        if not account:
            return None
        account.update(kwargs)
        save_accounts(accounts)
        return account


def update_account_by_id(account_id: str, **kwargs) -> dict | None:
    with _ACCOUNTS_LOCK:
        accounts = load_accounts()
        account = find_account_by_id(accounts, account_id)
        if not account:
            return None
        account.update(kwargs)
        save_accounts(accounts)
        return account


def get_ready_accounts() -> list[dict]:
    return [account for account in load_accounts() if account["status"] == STATUS_READY]


def get_accounts_by_parent(parent_id: str) -> list[dict]:
    target = _normalize_string(parent_id)
    return [account for account in load_accounts() if account.get("parent_id") == target]
