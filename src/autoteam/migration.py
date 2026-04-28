"""Legacy single-parent data migration."""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

from autoteam.auth_storage import AUTH_DIR
from autoteam.parents import PARENTS_FILE, save_parents
from autoteam.textio import read_text, write_text

PROJECT_ROOT = Path(__file__).parent.parent.parent
LEGACY_STATE_FILE = PROJECT_ROOT / "state.json"
LEGACY_ACCOUNTS_FILE = PROJECT_ROOT / "accounts.json"

_MIGRATED = False


def _backup_once(path: Path):
    if not path.exists():
        return
    backup = path.with_suffix(path.suffix + ".legacy.bak")
    if backup.exists():
        return
    shutil.copy2(path, backup)


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        text = read_text(path).strip()
        if not text:
            return default
        return json.loads(text)
    except Exception:
        return default


def _looks_new_parent(parent: dict) -> bool:
    return "default_batch_size" in parent and "enabled" in parent


def _looks_new_account(account: dict) -> bool:
    return account.get("status") in {"invited", "accepted", "auth_saved", "ready", "blocked", "removed", "failed", "cancelled"} and "parent_id" in account


def migrate_legacy_data():
    global _MIGRATED
    if _MIGRATED:
        return
    _MIGRATED = True

    parent_id = None
    parent_email = ""
    workspace_name = ""
    if not PARENTS_FILE.exists():
        legacy_state = _load_json(LEGACY_STATE_FILE, {})
        if isinstance(legacy_state, dict) and legacy_state.get("session_token") and legacy_state.get("account_id"):
            _backup_once(LEGACY_STATE_FILE)
            parent = {
                "id": uuid.uuid4().hex,
                "label": legacy_state.get("workspace_name") or legacy_state.get("email") or "Migrated Parent",
                "email": legacy_state.get("email", ""),
                "session_token": legacy_state.get("session_token", ""),
                "password": legacy_state.get("password", ""),
                "account_id": legacy_state.get("account_id", ""),
                "workspace_name": legacy_state.get("workspace_name", ""),
                "enabled": True,
                "default_batch_size": 1,
                "updated_at": legacy_state.get("updated_at") or time.time(),
                "last_run_at": None,
                "last_run_status": "",
                "last_run_created": 0,
                "last_run_failed": 0,
            }
            save_parents([parent])
            parent_id = parent["id"]
            parent_email = parent["email"]
            workspace_name = parent["workspace_name"]
    else:
        parents = _load_json(PARENTS_FILE, [])
        if parents and isinstance(parents, list) and _looks_new_parent(parents[0]):
            parent_id = parents[0].get("id")
            parent_email = parents[0].get("email", "")
            workspace_name = parents[0].get("workspace_name", "")

    if not LEGACY_ACCOUNTS_FILE.exists():
        return

    legacy_accounts = _load_json(LEGACY_ACCOUNTS_FILE, [])
    if not isinstance(legacy_accounts, list) or not legacy_accounts:
        return
    if all(_looks_new_account(account) for account in legacy_accounts):
        return

    _backup_once(LEGACY_ACCOUNTS_FILE)

    migrated = []
    for item in legacy_accounts:
        if not isinstance(item, dict):
            continue
        email = str(item.get("email") or "").strip().lower()
        if not email:
            continue
        auth_file = str(item.get("auth_file") or "").strip()
        auth_exists = bool(auth_file and Path(auth_file).exists())
        if not auth_exists and AUTH_DIR.exists():
            for candidate in AUTH_DIR.glob(f"codex-{email}-*.json"):
                auth_file = str(candidate.resolve())
                auth_exists = True
                break

        migrated.append(
            {
                "id": uuid.uuid4().hex,
                "email": email,
                "password": str(item.get("password") or "").strip(),
                "parent_id": parent_id or "",
                "parent_email": parent_email,
                "workspace_name": workspace_name,
                "mail_provider": "legacy-cloudmail",
                "mailbox_address": email,
                "invite_id": "",
                "invite_sent_at": item.get("created_at"),
                "remote_state": "unknown",
                "remote_member_id": "",
                "accepted_at": None,
                "auth_saved_at": item.get("last_active_at") if auth_exists else None,
                "status": "ready" if auth_exists else "failed",
                "auth_file": auth_file if auth_exists else "",
                "cpa_uploaded_at": None,
                "error": "" if auth_exists else "Migrated legacy record without valid auth file",
                "error_stage": "" if auth_exists else "unknown",
                "retry_count": 0,
                "last_remote_sync_at": None,
                "created_at": item.get("created_at") or time.time(),
                "completed_at": item.get("last_active_at") or item.get("created_at") or time.time(),
            }
        )

    write_text(LEGACY_ACCOUNTS_FILE, json.dumps(migrated, indent=2, ensure_ascii=False))
