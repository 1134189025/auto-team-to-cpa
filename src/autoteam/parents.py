"""Parent workspace storage."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from autoteam.textio import read_text, write_text

PROJECT_ROOT = Path(__file__).parent.parent.parent
PARENTS_FILE = PROJECT_ROOT / "main_accounts.json"
_PARENTS_LOCK = threading.RLock()


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


def _normalize_int(value, default=1) -> int:
    try:
        return max(1, int(value))
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


def _normalize_path(value) -> str:
    text = _normalize_string(value)
    if not text:
        return ""
    try:
        return str(Path(text).expanduser().resolve())
    except Exception:
        return text


def normalize_parent(data: dict) -> dict:
    return {
        "id": _normalize_string(data.get("id")) or uuid.uuid4().hex,
        "label": _normalize_string(data.get("label")) or _normalize_string(data.get("workspace_name")) or "Untitled",
        "email": _normalize_string(data.get("email")).lower(),
        "session_token": _normalize_string(data.get("session_token")),
        "password": _normalize_string(data.get("password")),
        "account_id": _normalize_string(data.get("account_id")),
        "workspace_name": _normalize_string(data.get("workspace_name")),
        "enabled": _normalize_bool(data.get("enabled"), True),
        "default_batch_size": _normalize_int(data.get("default_batch_size"), 1),
        "updated_at": _normalize_timestamp(data.get("updated_at"), time.time()),
        "last_run_at": _normalize_timestamp(data.get("last_run_at")),
        "last_run_status": _normalize_string(data.get("last_run_status")),
        "last_run_created": int(data.get("last_run_created") or 0),
        "last_run_failed": int(data.get("last_run_failed") or 0),
        "remote_pending_count": int(data.get("remote_pending_count") or 0),
        "remote_member_count": int(data.get("remote_member_count") or 0),
        "recoverable_count": int(data.get("recoverable_count") or 0),
        "drift_count": int(data.get("drift_count") or 0),
        "last_reconciled_at": _normalize_timestamp(data.get("last_reconciled_at")),
        "last_reconcile_error": _normalize_string(data.get("last_reconcile_error")),
        "main_auth_file": _normalize_path(data.get("main_auth_file")),
        "main_auth_plan_type": _normalize_string(data.get("main_auth_plan_type")),
        "main_auth_refreshed_at": _normalize_timestamp(data.get("main_auth_refreshed_at")),
        "main_cpa_uploaded_at": _normalize_timestamp(data.get("main_cpa_uploaded_at")),
        "main_codex_error": _normalize_string(data.get("main_codex_error")),
        "main_codex_error_stage": _normalize_string(data.get("main_codex_error_stage")),
    }


def load_parents() -> list[dict]:
    with _PARENTS_LOCK:
        if not PARENTS_FILE.exists():
            return []
        text = read_text(PARENTS_FILE).strip()
        if not text:
            return []
        items = json.loads(text)
        if not isinstance(items, list):
            return []
        return [normalize_parent(item) for item in items]


def save_parents(parents: list[dict]) -> None:
    with _PARENTS_LOCK:
        normalized = [normalize_parent(item) for item in parents]
        write_text(PARENTS_FILE, json.dumps(normalized, indent=2, ensure_ascii=False))


def find_parent(parents: list[dict], parent_id: str) -> dict | None:
    target = _normalize_string(parent_id)
    for parent in parents:
        if parent.get("id") == target:
            return parent
    return None


def add_parent(
    *,
    label: str,
    email: str,
    default_batch_size: int = 1,
    enabled: bool = True,
    session_token: str = "",
    password: str = "",
    account_id: str = "",
    workspace_name: str = "",
) -> dict:
    with _PARENTS_LOCK:
        parents = load_parents()
        parent = normalize_parent(
            {
                "label": label,
                "email": email,
                "default_batch_size": default_batch_size,
                "enabled": enabled,
                "session_token": session_token,
                "password": password,
                "account_id": account_id,
                "workspace_name": workspace_name,
                "updated_at": time.time(),
            }
        )
        parents.append(parent)
        save_parents(parents)
        return parent


def update_parent(parent_id: str, **kwargs) -> dict | None:
    with _PARENTS_LOCK:
        parents = load_parents()
        parent = find_parent(parents, parent_id)
        if not parent:
            return None
        parent.update(kwargs)
        parent["updated_at"] = time.time()
        save_parents(parents)
        return parent


def remove_parent(parent_id: str) -> bool:
    with _PARENTS_LOCK:
        parents = load_parents()
        before = len(parents)
        parents = [parent for parent in parents if parent.get("id") != _normalize_string(parent_id)]
        if len(parents) == before:
            return False
        save_parents(parents)
        return True


def get_enabled_parents() -> list[dict]:
    return [parent for parent in load_parents() if parent.get("enabled")]
