#!/usr/bin/env python3
# ruff: noqa: I001
"""CLI entrypoints and batch provisioning flow."""

import autoteam.display  # noqa: F401

import argparse
import getpass
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from rich.console import Console
from rich.table import Table

from autoteam.accounts import (
    ERROR_STAGE_CODEX,
    ERROR_STAGE_CPA,
    ERROR_STAGE_INVITE,
    ERROR_STAGE_RECONCILE,
    ERROR_STAGE_REGISTRATION,
    OCCUPYING_STATUSES,
    REMOTE_STATE_ABSENT,
    REMOTE_STATE_MEMBER,
    REMOTE_STATE_PENDING_INVITE,
    REMOTE_STATE_UNKNOWN,
    STATUS_ACCEPTED,
    STATUS_AUTH_SAVED,
    STATUS_BLOCKED,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_INVITED,
    STATUS_READY,
    STATUS_REMOVED,
    add_account,
    discover_auth_file,
    find_account_by_id,
    load_accounts,
    occupies_slot,
    update_account_by_id,
)
from autoteam.chatgpt_api import ChatGPTTeamAPI
from autoteam.codex_auth import login_codex_via_browser, save_auth_file
from autoteam.cpa_sync import resync_ready_accounts, upload_to_cpa
from autoteam.freemail import FreemailClient
from autoteam.invite import register_with_invite
from autoteam.migration import migrate_legacy_data
from autoteam.parents import add_parent, find_parent, get_enabled_parents, load_parents, remove_parent, update_parent
from autoteam.setup_wizard import check_and_setup

logger = logging.getLogger(__name__)
TARGET_CHILDREN_PER_PARENT = 4
DEFAULT_RELINK_CONCURRENCY = 2
MAX_RELINK_CONCURRENCY = 5
DEFAULT_HEALTH_CONCURRENCY = 5
MAX_HEALTH_CONCURRENCY = 5
STALE_INVITE_ERROR = "invite no longer pending remotely"
REMOTE_MEMBER_MISSING_ERROR = "remote member no longer present"
INVITE_SEND_RETRIES = 2
REGISTRATION_RETRIES = 2
CODEX_RETRIES = 2
CPA_UPLOAD_RETRIES = 3
TEAM_STATE_SETTLE_ATTEMPTS = 6
TEAM_STATE_SETTLE_INTERVAL = 5
INVITE_PROPAGATION_GRACE_SECONDS = TEAM_STATE_SETTLE_ATTEMPTS * TEAM_STATE_SETTLE_INTERVAL + 15


class TeamMemberRemoveError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400, remote_status: int | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.remote_status = remote_status


def _persist_parent_state(parent_id: str, payload: dict):
    update_parent(parent_id, **payload)


def _get_parent_or_die(parent_id: str) -> dict:
    parent = find_parent(load_parents(), parent_id)
    if not parent:
        raise SystemExit(f"母号不存在: {parent_id}")
    return parent


def _require_parent_ready(parent: dict):
    if not parent.get("session_token") or not parent.get("account_id"):
        raise RuntimeError(f"母号未完成登录: {parent.get('label') or parent.get('email')}")


def _extract_invite_id(data) -> str:
    if not isinstance(data, dict):
        return ""
    invites = data.get("account_invites", []) or data.get("invites", [])
    if invites and isinstance(invites[0], dict):
        return str(invites[0].get("id") or "")
    return ""


def _extract_team_invites(data) -> list[dict]:
    if isinstance(data, list):
        invites = data
    elif isinstance(data, dict):
        invites = data.get("account_invites", []) or data.get("invites", [])
    else:
        invites = []
    return [invite for invite in invites if isinstance(invite, dict)]


def _invite_email(invite: dict) -> str:
    return str(invite.get("email_address") or invite.get("email") or "").strip().lower()


def _extract_team_members(data) -> list[dict]:
    if isinstance(data, list):
        members = data
    elif isinstance(data, dict):
        members = data.get("items") or data.get("users") or data.get("members") or []
    else:
        members = []
    return [member for member in members if isinstance(member, dict)]


def _member_email(member: dict) -> str:
    return str(member.get("email") or member.get("email_address") or "").strip().lower()


def _member_user_id(member: dict) -> str:
    return str(member.get("user_id") or member.get("id") or "").strip()


def _member_role(member: dict) -> str:
    return str(member.get("role") or member.get("account_role") or member.get("account_user_role") or "").strip().lower()


def _load_account_by_id(account_id: str) -> dict | None:
    return find_account_by_id(load_accounts(), account_id)


def _match_local_invite(accounts: list[dict], parent_id: str, invite_id: str, email: str) -> dict | None:
    invite_id = str(invite_id or "").strip()
    email = str(email or "").strip().lower()

    if invite_id:
        for account in accounts:
            if account.get("parent_id") == parent_id and str(account.get("invite_id") or "").strip() == invite_id:
                return account

    if email:
        for account in accounts:
            if account.get("parent_id") != parent_id:
                continue
            if str(account.get("email") or "").strip().lower() != email:
                continue
            if account.get("status") not in {STATUS_INVITED, STATUS_CANCELLED}:
                continue
            return account
    return None


def _parent_label(parent: dict) -> str:
    return parent.get("label") or parent.get("workspace_name") or parent.get("email") or parent.get("id") or "-"


def _append_error_once(current_error: str, message: str) -> str:
    current_error = str(current_error or "").strip()
    message = str(message or "").strip()
    if not message:
        return current_error
    if not current_error:
        return message
    parts = [part.strip() for part in current_error.split(";") if part.strip()]
    if message in parts:
        return current_error
    return f"{current_error}; {message}"


def _remove_error_messages(current_error: str, *messages: str) -> str:
    blocked = {str(item or "").strip() for item in messages if str(item or "").strip()}
    parts = [part.strip() for part in str(current_error or "").split(";") if part.strip()]
    kept = [part for part in parts if part not in blocked]
    return "; ".join(kept)


def _repair_account_auth_reference(account: dict) -> dict:
    auth_file = discover_auth_file(account.get("email", ""), account.get("auth_file", ""))
    if not auth_file:
        return account

    updates = {}
    if auth_file != account.get("auth_file"):
        updates["auth_file"] = auth_file
    if account.get("status") in {STATUS_BLOCKED, STATUS_REMOVED}:
        if not updates:
            return account
        return update_account_by_id(account["id"], **updates) or {**account, **updates}
    if not account.get("auth_saved_at"):
        updates["auth_saved_at"] = account.get("completed_at") or time.time()
    expected_status = STATUS_READY if account.get("cpa_uploaded_at") else STATUS_AUTH_SAVED
    if account.get("status") not in {STATUS_READY, STATUS_AUTH_SAVED}:
        updates["status"] = expected_status
    elif account.get("status") == STATUS_AUTH_SAVED and account.get("cpa_uploaded_at"):
        updates["status"] = STATUS_READY

    if not updates:
        return account
    return update_account_by_id(account["id"], **updates) or {**account, **updates}


def _fetch_parent_team_state(parent: dict) -> dict:
    _require_parent_ready(parent)
    chatgpt = ChatGPTTeamAPI(
        session_token=parent["session_token"],
        account_id=parent["account_id"],
        workspace_name=parent.get("workspace_name", ""),
    )
    try:
        chatgpt.start()
        invite_status, invite_payload = chatgpt.list_invites()
        if invite_status != 200:
            raise RuntimeError(f"读取 pending invites 失败: HTTP {invite_status}")
        member_status, member_payload = chatgpt.list_members()
        if member_status != 200:
            raise RuntimeError(f"读取 Team members 失败: HTTP {member_status}")
        members = [
            member
            for member in _extract_team_members(member_payload)
            if _member_email(member) and _member_email(member) != str(parent.get("email") or "").strip().lower()
        ]
        invites = _extract_team_invites(invite_payload)
        return {"members": members, "invites": invites}
    finally:
        chatgpt.stop()


def reconcile_parent_team_state(parent: dict, *, log_prefix: str = "reconcile") -> dict:
    now = time.time()
    label = _parent_label(parent)
    try:
        remote_state = _fetch_parent_team_state(parent)
    except Exception as exc:
        update_parent(
            parent["id"],
            last_reconciled_at=now,
            last_reconcile_error=str(exc),
        )
        logger.error("[%s] reconcile blocked for %s: %s", log_prefix, label, exc)
        return {
            "ok": False,
            "error": str(exc),
            "members": [],
            "invites": [],
            "remote_pending_count": 0,
            "remote_member_count": 0,
            "remote_occupied_count": 0,
            "settling_invite_count": 0,
            "recoverable_count": 0,
            "drift_count": 0,
        }

    members = remote_state["members"]
    invites = remote_state["invites"]
    members_by_email = {_member_email(member): member for member in members if _member_email(member)}
    invites_by_id = {str(invite.get("id") or "").strip(): invite for invite in invites if str(invite.get("id") or "").strip()}
    invites_by_email = {_invite_email(invite): invite for invite in invites if _invite_email(invite)}

    local_accounts = [account for account in load_accounts() if account.get("parent_id") == parent.get("id")]
    local_emails = {str(account.get("email") or "").strip().lower() for account in local_accounts if account.get("email")}
    matched_remote_member_emails = set()
    matched_remote_invite_keys = set()
    status_drift_count = 0
    settling_invite_count = 0

    for account in local_accounts:
        account = _repair_account_auth_reference(account)
        email = str(account.get("email") or "").strip().lower()
        invite_id = str(account.get("invite_id") or "").strip()
        current_status = account.get("status")
        member = members_by_email.get(email)
        invite = invites_by_id.get(invite_id) if invite_id else None
        if not invite and email:
            invite = invites_by_email.get(email)

        updates = {
            "last_remote_sync_at": now,
            "remote_member_id": "",
        }

        if member:
            matched_remote_member_emails.add(email)
            updates["remote_state"] = REMOTE_STATE_MEMBER
            updates["remote_member_id"] = _member_user_id(member)
            next_status = current_status
            if current_status == STATUS_BLOCKED:
                pass
            elif current_status == STATUS_REMOVED:
                status_drift_count += 1
                updates["health_status"] = "check_failed"
                updates["health_error"] = _append_error_once(
                    account.get("health_error", ""),
                    "removed local record still present remotely",
                )
            elif current_status in {STATUS_READY, STATUS_AUTH_SAVED}:
                pass
            elif discover_auth_file(email, account.get("auth_file", "")):
                next_status = STATUS_READY if account.get("cpa_uploaded_at") else STATUS_AUTH_SAVED
                if not account.get("auth_saved_at"):
                    updates["auth_saved_at"] = now
            else:
                next_status = STATUS_ACCEPTED
                if not account.get("accepted_at"):
                    updates["accepted_at"] = now
            updates["status"] = next_status

            cleaned_error = _remove_error_messages(account.get("error", ""), STALE_INVITE_ERROR, REMOTE_MEMBER_MISSING_ERROR)
            if cleaned_error != account.get("error", ""):
                updates["error"] = cleaned_error
                if not cleaned_error and account.get("error_stage") == ERROR_STAGE_RECONCILE:
                    updates["error_stage"] = ""

        elif invite:
            matched_remote_invite_keys.add(str(invite.get("id") or "").strip())
            invite_email = _invite_email(invite)
            if invite_email:
                matched_remote_invite_keys.add(invite_email)
            updates["remote_state"] = REMOTE_STATE_PENDING_INVITE
            if current_status == STATUS_BLOCKED:
                updates["status"] = STATUS_BLOCKED
            elif current_status == STATUS_REMOVED:
                status_drift_count += 1
                updates["status"] = STATUS_REMOVED
                updates["health_status"] = "check_failed"
                updates["health_error"] = _append_error_once(
                    account.get("health_error", ""),
                    "removed local record has remote pending invite",
                )
            else:
                updates["status"] = STATUS_INVITED

            cleaned_error = _remove_error_messages(account.get("error", ""), STALE_INVITE_ERROR, REMOTE_MEMBER_MISSING_ERROR)
            if cleaned_error != account.get("error", ""):
                updates["error"] = cleaned_error
                if not cleaned_error and account.get("error_stage") == ERROR_STAGE_RECONCILE:
                    updates["error_stage"] = ""

        else:
            updates["remote_state"] = REMOTE_STATE_ABSENT
            current_error = str(account.get("error") or "").strip()

            if current_status == STATUS_BLOCKED:
                updates["status"] = STATUS_REMOVED
                updates["removed_at"] = account.get("removed_at") or now
                updates["removed_reason"] = account.get("removed_reason") or "remote_absent_after_blocked"
                updates["completed_at"] = account.get("completed_at") or now
            elif current_status == STATUS_REMOVED:
                updates["status"] = STATUS_REMOVED
                if not account.get("removed_at"):
                    updates["removed_at"] = now
                if not account.get("removed_reason"):
                    updates["removed_reason"] = "remote_absent"
                if not account.get("completed_at"):
                    updates["completed_at"] = now
            elif current_status == STATUS_INVITED:
                if _invite_is_within_grace(account, now=now):
                    if account.get("enabled", True):
                        settling_invite_count += 1
                    updates["status"] = STATUS_INVITED
                    updates["completed_at"] = None
                    cleaned_error = _remove_error_messages(current_error, STALE_INVITE_ERROR, REMOTE_MEMBER_MISSING_ERROR)
                    if cleaned_error != current_error:
                        updates["error"] = cleaned_error
                    if not cleaned_error and account.get("error_stage") == ERROR_STAGE_RECONCILE:
                        updates["error_stage"] = ""
                else:
                    updates["status"] = STATUS_FAILED
                    updates["error"] = _append_error_once(_remove_error_messages(current_error, STALE_INVITE_ERROR), STALE_INVITE_ERROR)
                    updates["error_stage"] = ERROR_STAGE_RECONCILE
                    updates["completed_at"] = now
            elif current_status == STATUS_FAILED:
                next_error = _append_error_once(current_error, STALE_INVITE_ERROR)
                if next_error != current_error:
                    updates["error"] = next_error
                if not account.get("error_stage"):
                    updates["error_stage"] = ERROR_STAGE_RECONCILE
                if not account.get("completed_at"):
                    updates["completed_at"] = now
            elif current_status in {STATUS_ACCEPTED, STATUS_AUTH_SAVED, STATUS_READY}:
                next_error = _append_error_once(current_error, REMOTE_MEMBER_MISSING_ERROR)
                if next_error != current_error:
                    updates["error"] = next_error
                if account.get("error_stage") in {"", ERROR_STAGE_RECONCILE}:
                    updates["error_stage"] = ERROR_STAGE_RECONCILE

        next_account = {**account, **updates}
        if next_account != account:
            update_account_by_id(account["id"], **updates)

    drift_count = status_drift_count
    for member in members:
        email = _member_email(member)
        if email and email not in local_emails:
            drift_count += 1
    for invite in invites:
        invite_id = str(invite.get("id") or "").strip()
        email = _invite_email(invite)
        if invite_id and invite_id in matched_remote_invite_keys:
            continue
        if email and email in matched_remote_invite_keys:
            continue
        if email and email in local_emails:
            continue
        drift_count += 1

    refreshed_accounts = [account for account in load_accounts() if account.get("parent_id") == parent.get("id")]
    recoverable_count = sum(
        1
        for account in refreshed_accounts
        if account.get("enabled", True) and account.get("status") in {STATUS_ACCEPTED, STATUS_AUTH_SAVED}
    )

    summary = {
        "remote_pending_count": len(invites),
        "remote_member_count": len(members),
        "remote_occupied_count": len(invites) + len(members) + settling_invite_count,
        "settling_invite_count": settling_invite_count,
        "recoverable_count": recoverable_count,
        "drift_count": drift_count,
    }
    update_parent(
        parent["id"],
        remote_pending_count=summary["remote_pending_count"],
        remote_member_count=summary["remote_member_count"],
        recoverable_count=summary["recoverable_count"],
        drift_count=summary["drift_count"],
        last_reconciled_at=now,
        last_reconcile_error="",
    )
    logger.info(
        "[%s] reconciled %s: pending=%d members=%d recoverable=%d drift=%d",
        log_prefix,
        label,
        summary["remote_pending_count"],
        summary["remote_member_count"],
        summary["recoverable_count"],
        summary["drift_count"],
    )
    return {"ok": True, "error": "", "members": members, "invites": invites, **summary}


def _can_relink_account_to_parent(account: dict, parent: dict, active_parent_ids: set[str]) -> bool:
    current_parent_id = str(account.get("parent_id") or "").strip()
    current_parent_email = str(account.get("parent_email") or "").strip().lower()
    parent_id = str(parent.get("id") or "").strip()
    parent_email = str(parent.get("email") or "").strip().lower()
    if current_parent_id == parent_id:
        return True
    if not current_parent_id:
        return True
    if current_parent_id not in active_parent_ids:
        return True
    return bool(parent_email and current_parent_email == parent_email)


def _normalize_relink_concurrency(value) -> int:
    if value in (None, ""):
        return DEFAULT_RELINK_CONCURRENCY
    try:
        concurrency = int(value)
    except Exception:
        return DEFAULT_RELINK_CONCURRENCY
    return min(MAX_RELINK_CONCURRENCY, max(1, concurrency))


def _normalize_health_concurrency(value) -> int:
    if value in (None, ""):
        return DEFAULT_HEALTH_CONCURRENCY
    try:
        concurrency = int(value)
    except Exception:
        return DEFAULT_HEALTH_CONCURRENCY
    return min(MAX_HEALTH_CONCURRENCY, max(1, concurrency))


def _fetch_parent_team_state_for_relink(parent: dict) -> dict:
    label = _parent_label(parent)
    try:
        remote_state = _fetch_parent_team_state(parent)
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "parent": parent,
            "parent_id": parent.get("id"),
            "parent_label": label,
            "members": [],
            "invites": [],
        }
    return {
        "ok": True,
        "error": "",
        "parent": parent,
        "parent_id": parent.get("id"),
        "parent_label": label,
        "members": remote_state["members"],
        "invites": remote_state["invites"],
    }


def _relinked_member_status(account: dict, auth_file: str) -> str:
    current_status = account.get("status")
    if current_status in {STATUS_BLOCKED, STATUS_REMOVED}:
        return current_status
    if current_status in {STATUS_READY, STATUS_AUTH_SAVED}:
        return current_status
    if auth_file:
        return STATUS_READY if account.get("cpa_uploaded_at") else STATUS_AUTH_SAVED
    return STATUS_ACCEPTED


def _relink_existing_account_to_member(parent: dict, account: dict, member: dict, *, now: float) -> bool:
    email = _member_email(member) or str(account.get("email") or "").strip().lower()
    auth_file = discover_auth_file(email, account.get("auth_file", ""))
    next_status = _relinked_member_status(account, auth_file)
    updates = {
        "parent_id": parent["id"],
        "parent_email": str(parent.get("email") or "").strip().lower(),
        "workspace_name": parent.get("workspace_name") or account.get("workspace_name", ""),
        "remote_state": REMOTE_STATE_MEMBER,
        "remote_member_id": _member_user_id(member),
        "last_remote_sync_at": now,
        "status": next_status,
    }
    if auth_file:
        updates["auth_file"] = auth_file
        if not account.get("auth_saved_at") and next_status in {STATUS_AUTH_SAVED, STATUS_READY}:
            updates["auth_saved_at"] = now
    if next_status in {STATUS_AUTH_SAVED, STATUS_READY} and account.get("health_status") == "check_failed":
        updates["health_status"] = "unknown"
        updates["health_error"] = ""
    if next_status == STATUS_REMOVED:
        updates["health_status"] = "check_failed"
        updates["health_error"] = _append_error_once(
            account.get("health_error", ""),
            "removed local record still present remotely",
        )
    else:
        updates["removed_at"] = None
        updates["removed_reason"] = ""
    if account.get("error_stage") == ERROR_STAGE_RECONCILE:
        updates["error"] = ""
        updates["error_stage"] = ""
    next_account = {**account, **updates}
    if next_account == account:
        return False
    update_account_by_id(account["id"], **updates)
    return True


def _relink_existing_account_to_invite(parent: dict, account: dict, invite: dict, *, now: float) -> bool:
    next_status = STATUS_REMOVED if account.get("status") == STATUS_REMOVED else STATUS_INVITED
    updates = {
        "parent_id": parent["id"],
        "parent_email": str(parent.get("email") or "").strip().lower(),
        "workspace_name": parent.get("workspace_name") or account.get("workspace_name", ""),
        "remote_state": REMOTE_STATE_PENDING_INVITE,
        "remote_member_id": "",
        "invite_id": str(invite.get("id") or account.get("invite_id") or "").strip(),
        "last_remote_sync_at": now,
        "status": next_status,
    }
    if next_status == STATUS_REMOVED:
        updates["health_status"] = "check_failed"
        updates["health_error"] = _append_error_once(
            account.get("health_error", ""),
            "removed local record has remote pending invite",
        )
    else:
        updates["removed_at"] = None
        updates["removed_reason"] = ""
    if account.get("error_stage") == ERROR_STAGE_RECONCILE:
        updates["error"] = ""
        updates["error_stage"] = ""
    next_account = {**account, **updates}
    if next_account == account:
        return False
    update_account_by_id(account["id"], **updates)
    return True


def _apply_repair_parent_child_links_snapshot(
    parent: dict,
    snapshot: dict,
    active_parent_ids: set[str],
    results: dict,
    duplicate_member_emails: set[str] | None = None,
) -> None:
    label = _parent_label(parent)
    results["processed_parents"] += 1
    now = time.time()
    accounts_by_email = {str(account.get("email") or "").strip().lower(): account for account in load_accounts() if account.get("email")}
    duplicate_member_emails = duplicate_member_emails or set()
    member_emails = {_member_email(member) for member in snapshot["members"] if _member_email(member)}

    for member in snapshot["members"]:
        email = _member_email(member)
        if not email:
            continue
        if email in duplicate_member_emails:
            results["conflicts"] += 1
            results["items"].append({"parent_id": parent.get("id"), "parent": label, "email": email, "status": "conflict"})
            continue
        account = accounts_by_email.get(email)
        if account:
            if not _can_relink_account_to_parent(account, parent, active_parent_ids):
                results["conflicts"] += 1
                results["items"].append({"parent_id": parent.get("id"), "parent": label, "email": email, "status": "conflict"})
                continue
            if _relink_existing_account_to_member(parent, account, member, now=now):
                results["relinked"] += 1
                results["items"].append({"parent_id": parent.get("id"), "parent": label, "email": email, "status": "relinked_member"})
            continue

        auth_file = discover_auth_file(email, "")
        if not auth_file:
            results["remote_only"] += 1
            continue
        add_account(
            email,
            "",
            parent_id=parent["id"],
            parent_email=parent.get("email", ""),
            workspace_name=parent.get("workspace_name", ""),
            mail_provider="freemail",
            mailbox_address=email,
            status=STATUS_AUTH_SAVED,
            auth_file=auth_file,
            remote_state=REMOTE_STATE_MEMBER,
            remote_member_id=_member_user_id(member),
            auth_saved_at=now,
            last_remote_sync_at=now,
        )
        results["created_from_auth"] += 1
        results["items"].append({"parent_id": parent.get("id"), "parent": label, "email": email, "status": "created_from_auth"})

    accounts_by_email = {str(account.get("email") or "").strip().lower(): account for account in load_accounts() if account.get("email")}
    for invite in snapshot["invites"]:
        email = _invite_email(invite)
        if not email:
            continue
        if email in member_emails:
            continue
        account = accounts_by_email.get(email)
        if not account:
            continue
        if not _can_relink_account_to_parent(account, parent, active_parent_ids):
            results["conflicts"] += 1
            results["items"].append({"parent_id": parent.get("id"), "parent": label, "email": email, "status": "conflict"})
            continue
        if _relink_existing_account_to_invite(parent, account, invite, now=now):
            results["relinked"] += 1
            results["items"].append({"parent_id": parent.get("id"), "parent": label, "email": email, "status": "relinked_invite"})

    final_snapshot = reconcile_parent_team_state(parent, log_prefix="relink-final")
    if not final_snapshot["ok"]:
        results["failed"] += 1


def run_repair_parent_child_links(concurrency=DEFAULT_RELINK_CONCURRENCY):
    migrate_legacy_data()
    if not check_and_setup(interactive=False):
        raise RuntimeError("configuration is incomplete; finish setup first")

    parents = load_parents()
    active_parent_ids = {str(parent.get("id") or "").strip() for parent in parents if parent.get("enabled", True)}
    concurrency = _normalize_relink_concurrency(concurrency)
    results = {
        "parents": len(parents),
        "processed_parents": 0,
        "relinked": 0,
        "created_from_auth": 0,
        "remote_only": 0,
        "conflicts": 0,
        "blocked_parents": 0,
        "failed": 0,
        "concurrency": concurrency,
        "items": [],
    }
    targets = []

    for parent in parents:
        label = _parent_label(parent)
        if not parent.get("enabled", True):
            continue
        if not parent.get("session_token") or not parent.get("account_id"):
            results["blocked_parents"] += 1
            results["items"].append({"parent_id": parent.get("id"), "parent": label, "status": "blocked_parent", "detail": "母号未完成登录"})
            continue
        targets.append(dict(parent))

    if targets:
        snapshots = []
        with ThreadPoolExecutor(max_workers=min(concurrency, len(targets))) as executor:
            futures = {executor.submit(_fetch_parent_team_state_for_relink, parent): parent for parent in targets}
            for future in as_completed(futures):
                parent = futures[future]
                label = _parent_label(parent)
                try:
                    snapshot = future.result()
                except Exception as exc:
                    snapshot = {"ok": False, "error": str(exc), "parent": parent, "parent_id": parent.get("id"), "parent_label": label}
                if not snapshot["ok"]:
                    update_parent(parent["id"], last_reconciled_at=time.time(), last_reconcile_error=snapshot["error"])
                    results["blocked_parents"] += 1
                    results["items"].append({"parent_id": parent.get("id"), "parent": label, "status": "blocked_parent", "detail": snapshot["error"]})
                    continue
                snapshots.append((parent, snapshot))

        member_parent_ids_by_email: dict[str, set[str]] = {}
        for parent, snapshot in snapshots:
            for member in snapshot["members"]:
                email = _member_email(member)
                if email:
                    member_parent_ids_by_email.setdefault(email, set()).add(str(parent.get("id") or ""))
        duplicate_member_emails = {email for email, parent_ids in member_parent_ids_by_email.items() if len(parent_ids) > 1}

        for parent, snapshot in snapshots:
            _apply_repair_parent_child_links_snapshot(parent, snapshot, active_parent_ids, results, duplicate_member_emails)

    logger.info(
        "[relink] completed: relinked=%d created_from_auth=%d remote_only=%d conflicts=%d blocked_parents=%d failed=%d",
        results["relinked"],
        results["created_from_auth"],
        results["remote_only"],
        results["conflicts"],
        results["blocked_parents"],
        results["failed"],
    )
    return results


def _generate_local_part():
    return uuid.uuid4().hex[:10]


def _complete_registration(email: str, password: str, invite_link: str, mail_client: FreemailClient):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**autoteam_config())
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        )
        page = context.new_page()
        ok, final_password = register_with_invite(page, invite_link, email, mail_client, password=password)
        browser.close()
    if not ok:
        raise RuntimeError("注册或接受邀请失败")
    return final_password


def autoteam_config():
    from autoteam.config import get_playwright_launch_options

    return get_playwright_launch_options()


def _set_account_error(account: dict, stage: str, error, *, status: str | None = None) -> dict:
    now = time.time()
    target_status = status or account.get("status") or STATUS_FAILED
    updates = {
        "status": target_status,
        "error": str(error),
        "error_stage": stage,
        "retry_count": int(account.get("retry_count") or 0) + 1,
        "completed_at": None if target_status in OCCUPYING_STATUSES else now,
    }
    if target_status == STATUS_ACCEPTED and not account.get("accepted_at"):
        updates["accepted_at"] = now
    if target_status == STATUS_AUTH_SAVED:
        auth_file = discover_auth_file(account.get("email", ""), account.get("auth_file", ""))
        if auth_file:
            updates["auth_file"] = auth_file
        if not account.get("auth_saved_at"):
            updates["auth_saved_at"] = now
    if target_status == STATUS_READY:
        updates["completed_at"] = now
    return update_account_by_id(account["id"], **updates) or {**account, **updates}


def _send_invite_for_account(parent: dict, account: dict) -> dict:
    last_exc = None
    for attempt in range(1, INVITE_SEND_RETRIES + 1):
        try:
            chatgpt = ChatGPTTeamAPI(
                session_token=parent["session_token"],
                account_id=parent["account_id"],
                workspace_name=parent.get("workspace_name", ""),
            )
            try:
                chatgpt.start()
                status, data = chatgpt.invite_member(account["email"])
            finally:
                chatgpt.stop()
            if status != 200:
                raise RuntimeError(f"邀请失败: HTTP {status}")
            invite_id = _extract_invite_id(data)
            return update_account_by_id(
                account["id"],
                invite_id=invite_id,
                invite_sent_at=time.time(),
                status=STATUS_INVITED,
                remote_state=REMOTE_STATE_PENDING_INVITE,
                remote_member_id="",
                error="",
                error_stage="",
                completed_at=None,
            ) or account
        except Exception as exc:
            last_exc = exc
            if attempt < INVITE_SEND_RETRIES:
                logger.warning("[invite] send invite retry %d/%d for %s: %s", attempt + 1, INVITE_SEND_RETRIES, account["email"], exc)
    _set_account_error(account, ERROR_STAGE_INVITE, last_exc or "邀请失败", status=STATUS_FAILED)
    raise last_exc or RuntimeError("邀请失败")


def _wait_for_remote_membership(parent: dict, account_id: str) -> dict:
    account = _load_account_by_id(account_id)
    for attempt in range(TEAM_STATE_SETTLE_ATTEMPTS):
        snapshot = reconcile_parent_team_state(parent, log_prefix="membership")
        if not snapshot["ok"]:
            raise RuntimeError(snapshot["error"])
        account = _load_account_by_id(account_id)
        if not account:
            raise RuntimeError("账号记录不存在")
        if account.get("remote_state") == REMOTE_STATE_MEMBER:
            return account
        should_wait = account.get("remote_state") == REMOTE_STATE_PENDING_INVITE or (
            account.get("status") == STATUS_INVITED and _invite_is_within_grace(account)
        )
        if attempt < TEAM_STATE_SETTLE_ATTEMPTS - 1 and should_wait:
            time.sleep(TEAM_STATE_SETTLE_INTERVAL)
            continue
        return account
    return account


def _invite_is_within_grace(account: dict, *, now: float | None = None) -> bool:
    invite_sent_at = account.get("invite_sent_at")
    if not invite_sent_at:
        return False
    current_time = now or time.time()
    return current_time - float(invite_sent_at) <= INVITE_PROPAGATION_GRACE_SECONDS


def _load_invite_detail(account: dict, mail_client: FreemailClient) -> dict:
    invite_email = mail_client.wait_for_email(account.get("mailbox_address") or account["email"], sender_keyword="openai")
    invite_detail = invite_email
    if invite_email.get("id"):
        try:
            invite_detail = mail_client.get_email(invite_email["id"])
        except Exception:
            invite_detail = invite_email
    return invite_detail


def _accept_invite_and_register(parent: dict, account: dict, mail_client: FreemailClient) -> dict:
    last_exc = None
    for attempt in range(1, REGISTRATION_RETRIES + 1):
        current = _load_account_by_id(account["id"]) or account
        try:
            invite_detail = _load_invite_detail(current, mail_client)
            invite_link = mail_client.extract_invite_link(invite_detail)
            if not invite_link:
                raise RuntimeError("未能从邀请邮件中提取邀请链接")
            final_password = _complete_registration(current["email"], current["password"], invite_link, mail_client)
            update_account_by_id(current["id"], password=final_password, error="", error_stage="")
            settled = _wait_for_remote_membership(parent, current["id"])
            if settled.get("remote_state") == REMOTE_STATE_MEMBER:
                if settled.get("status") not in {STATUS_ACCEPTED, STATUS_AUTH_SAVED, STATUS_READY}:
                    settled = update_account_by_id(
                        settled["id"],
                        status=STATUS_ACCEPTED,
                        accepted_at=settled.get("accepted_at") or time.time(),
                        completed_at=None,
                    ) or settled
                return settled
            raise RuntimeError("注册成功后未确认到远端 member 状态")
        except Exception as exc:
            last_exc = exc
            reconcile_parent_team_state(parent, log_prefix="registration")
            refreshed = _load_account_by_id(current["id"]) or current
            if refreshed.get("remote_state") == REMOTE_STATE_MEMBER:
                target_status = refreshed.get("status")
                if target_status not in {STATUS_ACCEPTED, STATUS_AUTH_SAVED, STATUS_READY}:
                    target_status = STATUS_ACCEPTED
                return _set_account_error(refreshed, ERROR_STAGE_REGISTRATION, exc, status=target_status)
            if refreshed.get("remote_state") == REMOTE_STATE_PENDING_INVITE or refreshed.get("status") == STATUS_INVITED:
                refreshed = _set_account_error(refreshed, ERROR_STAGE_REGISTRATION, exc, status=STATUS_INVITED)
                if attempt < REGISTRATION_RETRIES:
                    logger.warning(
                        "[invite] registration retry %d/%d for %s: %s",
                        attempt + 1,
                        REGISTRATION_RETRIES,
                        refreshed["email"],
                        exc,
                    )
                    continue
                raise exc
            _set_account_error(refreshed, ERROR_STAGE_REGISTRATION, exc, status=STATUS_FAILED)
            raise exc
    raise last_exc or RuntimeError("注册或接受邀请失败")


def _generate_codex_auth(parent: dict, account: dict, mail_client: FreemailClient) -> dict:
    current = _repair_account_auth_reference(_load_account_by_id(account["id"]) or account)
    if current.get("status") in {STATUS_AUTH_SAVED, STATUS_READY}:
        return current
    if current.get("status") != STATUS_ACCEPTED:
        raise RuntimeError(f"账号当前不可进入 Codex 阶段: {current.get('status')}")

    last_exc = None
    for attempt in range(1, CODEX_RETRIES + 1):
        try:
            bundle = login_codex_via_browser(
                current["email"],
                current["password"],
                mail_client=mail_client,
                account_id=parent["account_id"],
                workspace_name=parent.get("workspace_name", ""),
            )
            if not bundle:
                raise RuntimeError("Codex OAuth 登录失败")
            auth_file = save_auth_file(bundle)
            return update_account_by_id(
                current["id"],
                auth_file=auth_file,
                status=STATUS_AUTH_SAVED,
                auth_saved_at=time.time(),
                error="",
                error_stage="",
                completed_at=None,
            ) or current
        except Exception as exc:
            last_exc = exc
            refreshed = _repair_account_auth_reference(_load_account_by_id(current["id"]) or current)
            if discover_auth_file(refreshed.get("email", ""), refreshed.get("auth_file", "")):
                return _set_account_error(refreshed, ERROR_STAGE_CODEX, exc, status=STATUS_AUTH_SAVED)
            refreshed = _set_account_error(refreshed, ERROR_STAGE_CODEX, exc, status=STATUS_ACCEPTED)
            if attempt < CODEX_RETRIES:
                logger.warning("[codex] auth retry %d/%d for %s: %s", attempt + 1, CODEX_RETRIES, refreshed["email"], exc)
                current = refreshed
                continue
            raise exc
    raise last_exc or RuntimeError("Codex OAuth 登录失败")


def _upload_account_to_cpa(account: dict, *, force: bool = False) -> dict:
    current = _repair_account_auth_reference(_load_account_by_id(account["id"]) or account)
    auth_file = discover_auth_file(current.get("email", ""), current.get("auth_file", ""))
    if not auth_file:
        raise RuntimeError("本地 auth 文件缺失")
    if current.get("status") == STATUS_READY and current.get("cpa_uploaded_at") and not force:
        return current

    last_exc = None
    for attempt in range(1, CPA_UPLOAD_RETRIES + 1):
        try:
            if not upload_to_cpa(auth_file):
                raise RuntimeError("上传到 CLIProxyAPI 失败")
            return update_account_by_id(
                current["id"],
                auth_file=auth_file,
                status=STATUS_READY,
                auth_saved_at=current.get("auth_saved_at") or time.time(),
                cpa_uploaded_at=time.time(),
                error="",
                error_stage="",
                completed_at=time.time(),
            ) or current
        except Exception as exc:
            last_exc = exc
            current = _set_account_error(current, ERROR_STAGE_CPA, exc, status=STATUS_AUTH_SAVED)
            current = update_account_by_id(
                current["id"],
                auth_file=auth_file,
                auth_saved_at=current.get("auth_saved_at") or time.time(),
                completed_at=None,
            ) or current
            if attempt < CPA_UPLOAD_RETRIES:
                logger.warning("[cpa] upload retry %d/%d for %s: %s", attempt + 1, CPA_UPLOAD_RETRIES, current["email"], exc)
                continue
            raise exc
    raise last_exc or RuntimeError("上传到 CLIProxyAPI 失败")


def resume_account(parent: dict, account_id: str, mail_client: FreemailClient) -> dict:
    current = _repair_account_auth_reference(_load_account_by_id(account_id) or {})
    if not current:
        raise RuntimeError(f"账号不存在: {account_id}")

    if current.get("status") == STATUS_READY:
        return current
    if current.get("status") == STATUS_AUTH_SAVED:
        return _upload_account_to_cpa(current)
    if current.get("status") == STATUS_ACCEPTED:
        generated = _generate_codex_auth(parent, current, mail_client)
        if generated.get("status") == STATUS_READY:
            return generated
        return _upload_account_to_cpa(generated)
    if current.get("status") == STATUS_INVITED:
        if current.get("remote_state") not in {REMOTE_STATE_PENDING_INVITE, REMOTE_STATE_MEMBER}:
            snapshot = reconcile_parent_team_state(parent, log_prefix="resume")
            if not snapshot["ok"]:
                raise RuntimeError(snapshot["error"])
            current = _load_account_by_id(account_id) or current

        if current.get("remote_state") == REMOTE_STATE_MEMBER:
            return resume_account(parent, account_id, mail_client)

        if current.get("remote_state") == REMOTE_STATE_ABSENT and not _invite_is_within_grace(current):
            raise RuntimeError(STALE_INVITE_ERROR)

        if current.get("remote_state") == REMOTE_STATE_ABSENT:
            logger.info(
                "[resume] invite for %s not yet visible remotely; continue via email within grace window",
                current["email"],
            )

        accepted = _accept_invite_and_register(parent, current, mail_client)
        return resume_account(parent, accepted["id"], mail_client)
    raise RuntimeError(f"账号当前不可恢复: {current.get('status')}")


def provision_single_account(parent: dict, mail_client: FreemailClient) -> dict:
    _require_parent_ready(parent)

    mailbox = mail_client.create_temp_email(prefix=_generate_local_part())
    email = mailbox["email"]
    password = f"Tmp_{uuid.uuid4().hex[:12]}!"
    child = add_account(
        email,
        password,
        parent_id=parent["id"],
        parent_email=parent["email"],
        workspace_name=parent.get("workspace_name", ""),
        mail_provider="freemail",
        mailbox_address=email,
        status=STATUS_FAILED,
        remote_state=REMOTE_STATE_UNKNOWN,
    )
    child = _send_invite_for_account(parent, child)
    return resume_account(parent, child["id"], mail_client)


def _count_active_children(accounts: list[dict], parent_id: str) -> int:
    return sum(
        1
        for account in accounts
        if account.get("parent_id") == parent_id and account.get("enabled", True) and occupies_slot(account)
    )


def _recoverable_accounts_for_parent(parent_id: str) -> list[dict]:
    priority = {
        STATUS_AUTH_SAVED: 0,
        STATUS_ACCEPTED: 1,
        STATUS_INVITED: 2,
    }
    candidates = []
    for account in load_accounts():
        if account.get("parent_id") != parent_id or not account.get("enabled", True):
            continue
        status = account.get("status")
        remote_state = account.get("remote_state")
        if status == STATUS_AUTH_SAVED and remote_state == REMOTE_STATE_MEMBER:
            candidates.append(account)
        elif status == STATUS_ACCEPTED and remote_state == REMOTE_STATE_MEMBER:
            candidates.append(account)
        elif status == STATUS_INVITED and remote_state == REMOTE_STATE_PENDING_INVITE:
            candidates.append(account)
    candidates.sort(
        key=lambda account: (
            priority.get(account.get("status"), 99),
            account.get("invite_sent_at") or account.get("accepted_at") or account.get("created_at") or 0,
        )
    )
    return candidates


def _run_parent_fill_plan(
    parent: dict,
    *,
    target_per_parent: int,
    mail_client: FreemailClient,
    log_prefix: str,
    allow_create: bool,
) -> dict:
    result = {
        "created": 0,
        "recovered": 0,
        "failed": 0,
        "blocked": False,
        "planned": 0,
        "remote_occupied_count": 0,
        "remote_pending_count": 0,
        "remote_member_count": 0,
    }
    label = _parent_label(parent)
    now = time.time()

    try:
        _require_parent_ready(parent)
    except Exception as exc:
        logger.error("[%s] parent not ready for %s: %s", log_prefix, label, exc)
        update_parent(
            parent["id"],
            last_run_at=now,
            last_run_status="blocked",
            last_run_created=0,
            last_run_failed=1,
        )
        result["blocked"] = True
        result["failed"] = 1
        return result

    create_attempts = 0
    attempted_recovery_ids: set[str] = set()
    max_create_attempts = max(1, target_per_parent * 2) if allow_create else 0
    planned_set = False

    while True:
        snapshot = reconcile_parent_team_state(parent, log_prefix=log_prefix)
        if not snapshot["ok"]:
            result["blocked"] = True
            result["failed"] += 1
            update_parent(
                parent["id"],
                last_run_at=time.time(),
                last_run_status="blocked",
                last_run_created=result["created"],
                last_run_failed=result["failed"],
            )
            return result

        result["remote_occupied_count"] = snapshot["remote_occupied_count"]
        result["remote_pending_count"] = snapshot["remote_pending_count"]
        result["remote_member_count"] = snapshot["remote_member_count"]
        if not planned_set:
            result["planned"] = max(0, target_per_parent - snapshot["remote_occupied_count"])
            planned_set = True

        candidate = next(
            (
                account
                for account in _recoverable_accounts_for_parent(parent["id"])
                if account["id"] not in attempted_recovery_ids
            ),
            None,
        )
        if candidate:
            attempted_recovery_ids.add(candidate["id"])
            logger.info("[%s] recover %s (%s)", log_prefix, candidate["email"], candidate.get("status"))
            try:
                resume_account(parent, candidate["id"], mail_client)
                result["recovered"] += 1
            except Exception as exc:
                result["failed"] += 1
                logger.error("[%s] recover failed for %s: %s", log_prefix, candidate["email"], exc)
            continue

        missing = max(0, target_per_parent - snapshot["remote_occupied_count"])
        if not allow_create or missing <= 0:
            break

        if create_attempts >= max_create_attempts:
            logger.error(
                "[%s] stop creating for %s: create attempts reached %d while still missing %d",
                log_prefix,
                label,
                max_create_attempts,
                missing,
            )
            result["failed"] += 1
            break

        create_attempts += 1
        logger.info(
            "[%s] create child for %s (%d/%d), remote occupied=%d target=%d",
            log_prefix,
            label,
            create_attempts,
            max_create_attempts,
            snapshot["remote_occupied_count"],
            target_per_parent,
        )
        try:
            provision_single_account(parent, mail_client)
            result["created"] += 1
        except Exception as exc:
            result["failed"] += 1
            logger.error("[%s] create failed for %s: %s", log_prefix, label, exc)

    final_status = "success"
    if result["blocked"]:
        final_status = "blocked"
    elif result["failed"] and (result["created"] or result["recovered"]):
        final_status = "partial"
    elif result["failed"]:
        final_status = "failed"

    update_parent(
        parent["id"],
        last_run_at=time.time(),
        last_run_status=final_status,
        last_run_created=result["created"],
        last_run_failed=result["failed"],
    )
    return result


def _run_parent_plan(parent: dict, planned_count: int, mail_client: FreemailClient, log_prefix: str) -> tuple[int, int]:
    created = 0
    failed = 0

    if planned_count <= 0:
        logger.info("[%s] %s already full, skipping", log_prefix, parent["label"])
        update_parent(
            parent["id"],
            last_run_at=time.time(),
            last_run_status="success",
            last_run_created=0,
            last_run_failed=0,
        )
        return created, failed

    logger.info("[%s] start parent %s (%s), planned=%d", log_prefix, parent["label"], parent["workspace_name"] or parent["email"], planned_count)
    try:
        _require_parent_ready(parent)
    except Exception as exc:
        logger.error("[%s] parent not ready: %s", log_prefix, exc)
        failed = planned_count
        update_parent(
            parent["id"],
            last_run_at=time.time(),
            last_run_status="failed",
            last_run_created=0,
            last_run_failed=failed,
        )
        return created, failed

    for index in range(planned_count):
        logger.info("[%s] %s child %d/%d", log_prefix, parent["label"], index + 1, planned_count)
        try:
            provision_single_account(parent, mail_client)
            created += 1
        except Exception as exc:
            failed += 1
            logger.error("[%s] create failed: %s", log_prefix, exc)

    status = "success" if failed == 0 else "partial" if created else "failed"
    update_parent(
        parent["id"],
        last_run_at=time.time(),
        last_run_status=status,
        last_run_created=created,
        last_run_failed=failed,
    )
    return created, failed


def _run_batch_plan(parents: list[dict], plan_by_parent: dict[str, int], log_prefix: str, extra: dict | None = None) -> dict:
    results = {"created": 0, "failed": 0, "parents": len(parents)}
    if extra:
        results.update(extra)

    if not parents:
        logger.warning("[%s] no enabled parents", log_prefix)
        return results

    mail_client = FreemailClient()
    for parent in parents:
        planned_count = max(0, int(plan_by_parent.get(parent["id"], 0) or 0))
        created, failed = _run_parent_plan(parent, planned_count, mail_client, log_prefix)
        results["created"] += created
        results["failed"] += failed

    logger.info("[%s] completed: created %d, failed %d, parents %d", log_prefix, results["created"], results["failed"], results["parents"])
    return results


def run_batch():
    migrate_legacy_data()
    if not check_and_setup(interactive=False):
        raise RuntimeError("配置不完整，请先完成初始化配置")

    parents = get_enabled_parents()
    plan_by_parent = {parent["id"]: max(0, int(parent.get("default_batch_size", 1) or 1)) for parent in parents}
    return _run_batch_plan(parents, plan_by_parent, "batch")


def run_fill_all(target_per_parent: int = TARGET_CHILDREN_PER_PARENT):
    migrate_legacy_data()
    if not check_and_setup(interactive=False):
        raise RuntimeError("配置不完整，请先完成初始化配置")

    parents = get_enabled_parents()
    results = {
        "created": 0,
        "recovered": 0,
        "failed": 0,
        "parents": len(parents),
        "planned": 0,
        "target_per_parent": target_per_parent,
        "blocked_parents": 0,
        "remote_occupied": 0,
    }

    if not parents:
        logger.warning("[fill-all] no enabled parents")
        return results

    mail_client = FreemailClient()
    for parent in parents:
        parent_result = _run_parent_fill_plan(
            parent,
            target_per_parent=target_per_parent,
            mail_client=mail_client,
            log_prefix="fill-all",
            allow_create=True,
        )
        results["created"] += parent_result["created"]
        results["recovered"] += parent_result["recovered"]
        results["failed"] += parent_result["failed"]
        results["planned"] += parent_result["planned"]
        results["remote_occupied"] += parent_result["remote_occupied_count"]
        if parent_result["blocked"]:
            results["blocked_parents"] += 1

    logger.info(
        "[fill-all] completed: created=%d recovered=%d failed=%d blocked=%d parents=%d",
        results["created"],
        results["recovered"],
        results["failed"],
        results["blocked_parents"],
        results["parents"],
    )
    return results


def run_repair_stuck_accounts():
    migrate_legacy_data()
    if not check_and_setup(interactive=False):
        raise RuntimeError("配置不完整，请先完成初始化配置")

    parents = get_enabled_parents()
    results = {
        "created": 0,
        "recovered": 0,
        "failed": 0,
        "parents": len(parents),
        "blocked_parents": 0,
        "remote_occupied": 0,
    }

    if not parents:
        logger.warning("[repair] no enabled parents")
        return results

    mail_client = FreemailClient()
    for parent in parents:
        parent_result = _run_parent_fill_plan(
            parent,
            target_per_parent=int(parent.get("remote_pending_count") or 0) + int(parent.get("remote_member_count") or 0),
            mail_client=mail_client,
            log_prefix="repair",
            allow_create=False,
        )
        results["recovered"] += parent_result["recovered"]
        results["failed"] += parent_result["failed"]
        results["remote_occupied"] += parent_result["remote_occupied_count"]
        if parent_result["blocked"]:
            results["blocked_parents"] += 1

    logger.info(
        "[repair] completed: recovered=%d failed=%d blocked=%d parents=%d",
        results["recovered"],
        results["failed"],
        results["blocked_parents"],
        results["parents"],
    )
    return results


def run_check_child_health(concurrency=DEFAULT_HEALTH_CONCURRENCY):
    migrate_legacy_data()
    if not check_and_setup(interactive=False):
        raise RuntimeError("configuration is incomplete; finish setup first")

    from autoteam.health import check_child_account_health

    parents = get_enabled_parents()
    concurrency = _normalize_health_concurrency(concurrency)
    results = {
        "parents": len(parents),
        "checked": 0,
        "healthy": 0,
        "quota_exhausted": 0,
        "blocked": 0,
        "check_failed": 0,
        "skipped": 0,
        "blocked_parents": 0,
        "concurrency": concurrency,
    }

    def _reconcile_parent_for_health(parent: dict) -> dict:
        snapshot = reconcile_parent_team_state(parent, log_prefix="health")
        if not snapshot["ok"]:
            return {"ok": False, "parent": parent, "error": snapshot.get("error", ""), "accounts": []}

        accounts = [
            account
            for account in load_accounts()
            if account.get("parent_id") == parent.get("id")
            and account.get("enabled", True)
            and account.get("status") in {STATUS_READY, STATUS_AUTH_SAVED}
            and account.get("remote_state") == REMOTE_STATE_MEMBER
        ]
        return {"ok": True, "parent": parent, "error": "", "accounts": accounts}

    check_targets = []
    if parents:
        with ThreadPoolExecutor(max_workers=min(concurrency, len(parents))) as executor:
            futures = {executor.submit(_reconcile_parent_for_health, parent): parent for parent in parents}
            for future in as_completed(futures):
                parent = futures[future]
                try:
                    parent_result = future.result()
                except Exception as exc:
                    logger.error("[health] reconcile blocked for %s: %s", _parent_label(parent), exc)
                    results["blocked_parents"] += 1
                    continue
                if not parent_result["ok"]:
                    results["blocked_parents"] += 1
                    continue
                accounts = parent_result["accounts"]
                if not accounts:
                    results["skipped"] += 1
                    continue
                check_targets.extend(accounts)

    def _check_account_for_health(account: dict) -> dict:
        try:
            return check_child_account_health(account)
        except Exception as exc:
            update_account_by_id(
                account["id"],
                health_status="check_failed",
                health_checked_at=time.time(),
                health_error=str(exc),
            )
            return {"outcome": "check_failed", "account_id": account.get("id"), "email": account.get("email"), "error": str(exc)}

    if check_targets:
        with ThreadPoolExecutor(max_workers=min(concurrency, len(check_targets))) as executor:
            futures = {executor.submit(_check_account_for_health, account): account for account in check_targets}
            for future in as_completed(futures):
                outcome = future.result()
                key = outcome.get("outcome") or "check_failed"
                if key == "skipped":
                    results["skipped"] += 1
                    continue
                results["checked"] += 1
                if key in {"healthy", "quota_exhausted", "blocked", "check_failed"}:
                    results[key] += 1
                else:
                    results["check_failed"] += 1

    logger.info(
        "[health] completed: checked=%d healthy=%d exhausted=%d blocked=%d failed=%d skipped=%d blocked_parents=%d concurrency=%d",
        results["checked"],
        results["healthy"],
        results["quota_exhausted"],
        results["blocked"],
        results["check_failed"],
        results["skipped"],
        results["blocked_parents"],
        results["concurrency"],
    )
    return results


def remove_blocked_child_from_team(parent_id: str, child_id: str) -> dict:
    migrate_legacy_data()
    parent = find_parent(load_parents(), parent_id)
    if not parent:
        raise TeamMemberRemoveError("母号不存在", status_code=404)
    try:
        _require_parent_ready(parent)
    except Exception as exc:
        raise TeamMemberRemoveError(str(exc), status_code=400)

    account = find_account_by_id(load_accounts(), child_id)
    if not account or account.get("parent_id") != parent_id:
        raise TeamMemberRemoveError("子号不存在或不属于该母号", status_code=404)
    if account.get("status") != STATUS_BLOCKED or account.get("remote_state") != REMOTE_STATE_MEMBER:
        raise TeamMemberRemoveError("子号不是疑似封禁且已加入 Team 的成员", status_code=400)

    snapshot = reconcile_parent_team_state(parent, log_prefix="remove-blocked")
    if not snapshot["ok"]:
        update_account_by_id(account["id"], health_error=snapshot["error"], error_stage=ERROR_STAGE_RECONCILE)
        raise TeamMemberRemoveError(f"删除前对账失败: {snapshot['error']}", status_code=502)

    account = find_account_by_id(load_accounts(), child_id) or account
    if account.get("status") == STATUS_REMOVED and account.get("remote_state") == REMOTE_STATE_ABSENT:
        return {"removed": True, "already_absent": True, "child": account}
    if account.get("status") != STATUS_BLOCKED or account.get("remote_state") != REMOTE_STATE_MEMBER:
        raise TeamMemberRemoveError("子号当前不是疑似封禁且已加入 Team 的成员", status_code=400)

    email = str(account.get("email") or "").strip().lower()
    remote_member_id = str(account.get("remote_member_id") or "").strip()
    if not remote_member_id:
        raise TeamMemberRemoveError("缺少远端成员 ID，请先重新检测或对账", status_code=400)

    chatgpt = ChatGPTTeamAPI(
        session_token=parent["session_token"],
        account_id=parent["account_id"],
        workspace_name=parent.get("workspace_name", ""),
    )
    try:
        chatgpt.start()
        list_status, member_payload = chatgpt.list_members()
        if list_status != 200:
            update_account_by_id(account["id"], health_error=f"list members failed: HTTP {list_status}", error_stage=ERROR_STAGE_RECONCILE)
            raise TeamMemberRemoveError(f"读取 Team members 失败: HTTP {list_status}", status_code=502, remote_status=list_status)

        members = _extract_team_members(member_payload)
        member = next(
            (
                item
                for item in members
                if _member_user_id(item) == remote_member_id and _member_email(item) == email
            ),
            None,
        )
        if not member:
            update_account_by_id(account["id"], health_error="remote member mismatch before removal", error_stage=ERROR_STAGE_RECONCILE)
            raise TeamMemberRemoveError("远端成员不存在或与本地记录不匹配", status_code=404)

        parent_email = str(parent.get("email") or "").strip().lower()
        role = _member_role(member)
        if email == parent_email or role in {"account-owner", "owner"}:
            raise TeamMemberRemoveError("禁止移出母号自身或 owner 成员", status_code=400)

        remove_status, remove_payload = chatgpt.remove_member(remote_member_id)
        if remove_status in {404, 405}:
            error = f"remove member endpoint needs verification: HTTP {remove_status}"
            update_account_by_id(account["id"], health_error=error, error_stage=ERROR_STAGE_RECONCILE)
            raise TeamMemberRemoveError(error, status_code=502, remote_status=remove_status)
        if remove_status not in {200, 204}:
            error = f"remove member failed: HTTP {remove_status}"
            update_account_by_id(account["id"], health_error=error, error_stage=ERROR_STAGE_RECONCILE)
            raise TeamMemberRemoveError(error, status_code=502, remote_status=remove_status)
    finally:
        chatgpt.stop()

    now = time.time()
    updated = update_account_by_id(
        account["id"],
        status=STATUS_REMOVED,
        remote_state=REMOTE_STATE_ABSENT,
        remote_member_id="",
        removed_at=now,
        removed_reason="blocked_auth",
        completed_at=now,
        last_remote_sync_at=now,
    ) or {**account, "status": STATUS_REMOVED, "remote_state": REMOTE_STATE_ABSENT, "removed_at": now, "removed_reason": "blocked_auth"}
    update_parent(
        parent_id,
        remote_pending_count=snapshot.get("remote_pending_count", 0),
        remote_member_count=max(0, int(snapshot.get("remote_member_count") or 0) - 1),
        recoverable_count=snapshot.get("recoverable_count", 0),
        drift_count=snapshot.get("drift_count", 0),
        last_reconciled_at=now,
        last_reconcile_error="",
    )
    return {
        "removed": True,
        "already_absent": False,
        "parent_id": parent_id,
        "child_id": child_id,
        "email": email,
        "remote_member_id": remote_member_id,
        "remote_member_count": max(0, int(snapshot.get("remote_member_count") or 0) - 1),
        "remote_status": remove_status,
        "remote_payload": remove_payload,
        "child": updated,
    }


def remove_all_blocked_children_from_team(parent_id: str) -> dict:
    migrate_legacy_data()
    parent = find_parent(load_parents(), parent_id)
    if not parent:
        raise TeamMemberRemoveError("母号不存在", status_code=404)
    try:
        _require_parent_ready(parent)
    except Exception as exc:
        raise TeamMemberRemoveError(str(exc), status_code=400)

    targets = sorted(
        [
            account
            for account in load_accounts()
            if account.get("parent_id") == parent_id
            and account.get("status") == STATUS_BLOCKED
            and account.get("remote_state") == REMOTE_STATE_MEMBER
        ],
        key=lambda account: (str(account.get("email") or ""), str(account.get("id") or "")),
    )
    result = {
        "parent_id": parent_id,
        "attempted": len(targets),
        "removed": 0,
        "failed": 0,
        "items": [],
    }

    for account in targets:
        child_id = str(account.get("id") or "")
        email = str(account.get("email") or "")
        try:
            item = remove_blocked_child_from_team(parent_id, child_id)
        except TeamMemberRemoveError as exc:
            result["failed"] += 1
            result["items"].append(
                {
                    "child_id": child_id,
                    "email": email,
                    "status": "failed",
                    "detail": str(exc),
                    "remote_status": exc.remote_status,
                    "http_status": exc.status_code,
                }
            )
            continue

        result["removed"] += 1
        result["items"].append(
            {
                "child_id": child_id,
                "email": email,
                "status": "removed",
                "already_absent": bool(item.get("already_absent")),
                "remote_status": item.get("remote_status"),
            }
        )

    return result


def run_delete_401_children(concurrency=DEFAULT_HEALTH_CONCURRENCY):
    concurrency = _normalize_health_concurrency(concurrency)
    health_result = run_check_child_health(concurrency=concurrency)

    enabled_parent_ids = {str(parent.get("id") or "") for parent in get_enabled_parents()}
    parent_ids = sorted(
        {
            str(account.get("parent_id") or "")
            for account in load_accounts()
            if str(account.get("parent_id") or "") in enabled_parent_ids
            and account.get("status") == STATUS_BLOCKED
            and account.get("remote_state") == REMOTE_STATE_MEMBER
        }
    )
    parent_ids = [parent_id for parent_id in parent_ids if parent_id]

    results = {
        "parents": health_result.get("parents", 0),
        "checked": health_result.get("checked", 0),
        "healthy": health_result.get("healthy", 0),
        "quota_exhausted": health_result.get("quota_exhausted", 0),
        "blocked": health_result.get("blocked", 0),
        "check_failed": health_result.get("check_failed", 0),
        "skipped": health_result.get("skipped", 0),
        "blocked_parents": health_result.get("blocked_parents", 0),
        "concurrency": concurrency,
        "remove_parents": len(parent_ids),
        "remove_attempted": 0,
        "removed": 0,
        "remove_failed": 0,
        "items": [],
    }

    if not parent_ids:
        logger.info("[delete-401] no blocked Team members to remove")
        return results

    def _remove_parent_blocked(parent_id: str) -> dict:
        try:
            return remove_all_blocked_children_from_team(parent_id)
        except TeamMemberRemoveError as exc:
            return {
                "parent_id": parent_id,
                "attempted": 0,
                "removed": 0,
                "failed": 1,
                "items": [
                    {
                        "child_id": "",
                        "email": "",
                        "status": "failed",
                        "detail": str(exc),
                        "remote_status": exc.remote_status,
                        "http_status": exc.status_code,
                    }
                ],
            }
        except Exception as exc:
            return {
                "parent_id": parent_id,
                "attempted": 0,
                "removed": 0,
                "failed": 1,
                "items": [{"child_id": "", "email": "", "status": "failed", "detail": str(exc)}],
            }

    with ThreadPoolExecutor(max_workers=min(concurrency, len(parent_ids))) as executor:
        futures = {executor.submit(_remove_parent_blocked, parent_id): parent_id for parent_id in parent_ids}
        for future in as_completed(futures):
            parent_result = future.result()
            results["remove_attempted"] += int(parent_result.get("attempted") or 0)
            results["removed"] += int(parent_result.get("removed") or 0)
            results["remove_failed"] += int(parent_result.get("failed") or 0)
            results["items"].extend(parent_result.get("items") or [])

    logger.info(
        "[delete-401] completed: checked=%d blocked=%d removed=%d remove_failed=%d parents=%d concurrency=%d",
        results["checked"],
        results["blocked"],
        results["removed"],
        results["remove_failed"],
        results["remove_parents"],
        results["concurrency"],
    )
    return results


def run_clear_pending_invites():
    migrate_legacy_data()

    parents = load_parents()
    results = {
        "parents": len(parents),
        "processed_parents": 0,
        "found": 0,
        "cancelled": 0,
        "failed": 0,
        "skipped_unready": 0,
        "stale_local": 0,
        "stale_failed": 0,
        "recovered": 0,
        "remote_members": 0,
    }

    if not parents:
        logger.warning("[clear-pending] no parents configured")
        return results

    for parent in parents:
        label = parent.get("label") or parent.get("workspace_name") or parent.get("email") or parent.get("id")
        if not parent.get("session_token") or not parent.get("account_id"):
            results["skipped_unready"] += 1
            logger.warning("[clear-pending] skip parent %s: missing session/account", label)
            continue

        logger.info("[clear-pending] start parent %s", label)
        results["processed_parents"] += 1
        before_accounts = {
            account["id"]: account
            for account in load_accounts()
            if account.get("parent_id") == parent["id"]
        }
        local_cancelled_ids: set[str] = set()
        chatgpt = ChatGPTTeamAPI(
            session_token=parent["session_token"],
            account_id=parent["account_id"],
            workspace_name=parent.get("workspace_name", ""),
        )
        try:
            chatgpt.start()
            status, payload = chatgpt.list_invites()
            if status != 200:
                results["failed"] += 1
                logger.error("[clear-pending] list invites failed for %s: HTTP %s", label, status)
                continue

            invites = _extract_team_invites(payload)
            results["found"] += len(invites)
            logger.info("[clear-pending] %s pending invites found for %s", len(invites), label)

            for invite in invites:
                invite_id = str(invite.get("id") or "").strip()
                email = _invite_email(invite)
                if not invite_id:
                    results["failed"] += 1
                    logger.error("[clear-pending] invite missing id for %s (%s)", label, email or "-")
                    continue

                cancel_status, _cancel_payload = chatgpt.cancel_invite(invite_id)
                if cancel_status not in {200, 204}:
                    results["failed"] += 1
                    logger.error("[clear-pending] cancel invite failed for %s (%s): HTTP %s", label, email or invite_id, cancel_status)
                    continue

                results["cancelled"] += 1
                local = _match_local_invite(load_accounts(), parent["id"], invite_id, email)
                if local:
                    local_cancelled_ids.add(local["id"])
                    update_account_by_id(
                        local["id"],
                        status=STATUS_CANCELLED,
                        remote_state=REMOTE_STATE_ABSENT,
                        error="",
                        error_stage="",
                        completed_at=time.time(),
                        last_remote_sync_at=time.time(),
                    )
        except Exception as exc:
            results["failed"] += 1
            logger.error("[clear-pending] parent %s failed: %s", label, exc)
        finally:
            chatgpt.stop()

        snapshot = reconcile_parent_team_state(parent, log_prefix="clear-pending")
        if not snapshot["ok"]:
            results["failed"] += 1
            continue

        results["remote_members"] += snapshot["remote_member_count"]
        after_accounts = {
            account["id"]: account
            for account in load_accounts()
            if account.get("parent_id") == parent["id"]
        }
        for account_id, before in before_accounts.items():
            if account_id in local_cancelled_ids:
                continue
            after = after_accounts.get(account_id)
            if not after:
                continue
            changed = (
                before.get("status") != after.get("status")
                or before.get("remote_state") != after.get("remote_state")
                or str(before.get("error") or "") != str(after.get("error") or "")
            )
            if not changed:
                continue
            results["stale_local"] += 1
            if before.get("status") == STATUS_FAILED:
                results["stale_failed"] += 1
            if after.get("status") in {STATUS_ACCEPTED, STATUS_AUTH_SAVED, STATUS_READY}:
                results["recovered"] += 1

    logger.info(
        "[clear-pending] completed: cancelled %d, stale_local %d, stale_failed %d, recovered %d, remote_members %d, failed %d, found %d, processed parents %d, skipped %d",
        results["cancelled"],
        results["stale_local"],
        results["stale_failed"],
        results["recovered"],
        results["remote_members"],
        results["failed"],
        results["found"],
        results["processed_parents"],
        results["skipped_unready"],
    )
    return results


def _print_parents():
    parents = load_parents()
    console = Console()
    table = Table(title="母号列表", show_header=True)
    table.add_column("ID", style="cyan")
    table.add_column("标签")
    table.add_column("邮箱")
    table.add_column("Workspace")
    table.add_column("启用")
    table.add_column("默认批量")
    table.add_column("最近运行")
    for parent in parents:
        table.add_row(
            parent["id"][:8],
            parent["label"],
            parent["email"],
            parent.get("workspace_name") or "-",
            "yes" if parent.get("enabled") else "no",
            str(parent.get("default_batch_size", 1)),
            parent.get("last_run_status") or "-",
        )
    console.print(table)


def _print_children():
    accounts = load_accounts()
    console = Console()
    table = Table(title="子号记录", show_header=True)
    table.add_column("邮箱")
    table.add_column("母号")
    table.add_column("Workspace")
    table.add_column("状态")
    table.add_column("邮件后端")
    table.add_column("CPA")
    for account in accounts[-50:]:
        table.add_row(
            account["email"],
            account.get("parent_email") or "-",
            account.get("workspace_name") or "-",
            account["status"],
            account.get("mail_provider") or "-",
            "yes" if account.get("cpa_uploaded_at") else "no",
        )
    console.print(table)


def cmd_status():
    migrate_legacy_data()
    _print_parents()
    _print_children()


def cmd_parent_add(args):
    migrate_legacy_data()
    parent = add_parent(
        label=args.label or args.email,
        email=args.email,
        default_batch_size=args.default_batch_size,
        enabled=not args.disabled,
    )
    logger.info("[母号] 已添加: %s (%s)", parent["label"], parent["id"])


def cmd_parent_update(args):
    migrate_legacy_data()
    updates = {}
    for key in ("label", "email", "workspace_name", "session_token", "password", "account_id"):
        value = getattr(args, key, None)
        if value not in (None, ""):
            updates[key] = value
    if args.default_batch_size is not None:
        updates["default_batch_size"] = args.default_batch_size
    if args.enabled is not None:
        updates["enabled"] = args.enabled
    parent = update_parent(args.parent_id, **updates)
    if not parent:
        raise SystemExit(f"母号不存在: {args.parent_id}")
    logger.info("[母号] 已更新: %s", parent["label"])


def cmd_parent_remove(args):
    migrate_legacy_data()
    if not remove_parent(args.parent_id):
        raise SystemExit(f"母号不存在: {args.parent_id}")
    logger.info("[母号] 已删除: %s", args.parent_id)


def _interactive_login(parent_id: str, email: str):
    flow = ChatGPTTeamAPI(persist_callback=lambda payload: _persist_parent_state(parent_id, payload))
    try:
        result = flow.begin_admin_login(email)
        step = result.get("step")
        while True:
            if step == "completed":
                info = flow.complete_admin_login()
                logger.info("[母号] 登录完成: %s", info.get("workspace_name") or info.get("email"))
                return info
            if step == "password_required":
                password = getpass.getpass("密码（留空取消）: ")
                if not password:
                    return None
                result = flow.submit_admin_password(password)
                step = result.get("step")
                continue
            if step == "code_required":
                code = input("验证码（留空取消）: ").strip()
                if not code:
                    return None
                result = flow.submit_admin_code(code)
                step = result.get("step")
                continue
            if step == "workspace_required":
                options = flow.list_workspace_options()
                for index, option in enumerate(options, 1):
                    logger.info("%d. %s", index, option["label"])
                choice = input("选择序号（留空取消）: ").strip()
                if not choice:
                    return None
                result = flow.select_workspace_option(options[int(choice) - 1]["id"])
                step = result.get("step")
                continue
            raise RuntimeError(result.get("detail") or f"未知登录步骤: {step}")
    finally:
        flow.stop()


def cmd_parent_login(args):
    migrate_legacy_data()
    parent = _get_parent_or_die(args.parent_id)
    check_and_setup(interactive=True)
    email = args.email or parent.get("email")
    if not email:
        raise SystemExit("请提供母号邮箱")
    info = _interactive_login(parent["id"], email)
    if info:
        update_parent(parent["id"], email=info.get("email") or email, workspace_name=info.get("workspace_name") or parent.get("workspace_name", ""))


def cmd_parent_import_session(args):
    migrate_legacy_data()
    parent = _get_parent_or_die(args.parent_id)
    check_and_setup(interactive=True)
    email = args.email or parent.get("email")
    if not email:
        raise SystemExit("请提供母号邮箱")
    session_token = args.session_token or getpass.getpass("session_token（留空取消）: ").strip()
    if not session_token:
        return
    flow = ChatGPTTeamAPI(persist_callback=lambda payload: _persist_parent_state(parent["id"], payload))
    try:
        info = flow.import_admin_session(email, session_token)
        update_parent(parent["id"], label=parent["label"] or info.get("workspace_name") or email, email=info.get("email") or email)
        logger.info("[母号] session_token 已导入: %s", info.get("workspace_name") or info.get("email"))
    finally:
        flow.stop()


def cmd_parent_list(_args):
    migrate_legacy_data()
    _print_parents()


def main():
    parser = argparse.ArgumentParser(prog="autoteam", description="AutoTeam 多母号批量邀请管理器")
    sub = parser.add_subparsers(dest="command")

    parent_parser = sub.add_parser("parent", help="母号管理")
    parent_sub = parent_parser.add_subparsers(dest="parent_command")

    parent_sub.add_parser("list", help="列出母号")

    parent_add = parent_sub.add_parser("add", help="添加母号")
    parent_add.add_argument("--label", default="")
    parent_add.add_argument("--email", required=True)
    parent_add.add_argument("--default-batch-size", type=int, default=1)
    parent_add.add_argument("--disabled", action="store_true")

    parent_update = parent_sub.add_parser("update", help="更新母号")
    parent_update.add_argument("parent_id")
    parent_update.add_argument("--label")
    parent_update.add_argument("--email")
    parent_update.add_argument("--workspace-name")
    parent_update.add_argument("--session-token")
    parent_update.add_argument("--password")
    parent_update.add_argument("--account-id")
    parent_update.add_argument("--default-batch-size", type=int)
    enable_group = parent_update.add_mutually_exclusive_group()
    enable_group.add_argument("--enable", dest="enabled", action="store_true")
    enable_group.add_argument("--disable", dest="enabled", action="store_false")
    parent_update.set_defaults(enabled=None)

    parent_remove = parent_sub.add_parser("remove", help="删除母号")
    parent_remove.add_argument("parent_id")

    parent_enable = parent_sub.add_parser("enable", help="启用母号")
    parent_enable.add_argument("parent_id")

    parent_disable = parent_sub.add_parser("disable", help="停用母号")
    parent_disable.add_argument("parent_id")

    parent_login = parent_sub.add_parser("login", help="交互式登录母号")
    parent_login.add_argument("parent_id")
    parent_login.add_argument("--email")

    parent_session = parent_sub.add_parser("import-session", help="导入母号 session_token")
    parent_session.add_argument("parent_id")
    parent_session.add_argument("--email")
    parent_session.add_argument("--session-token")

    sub.add_parser("batch-run", help="遍历全部启用母号批量创建子号")
    fill_all_parser = sub.add_parser("fill-all", help="将全部启用母号按目标数量补满子号")
    fill_all_parser.add_argument("--target-per-parent", type=int, default=TARGET_CHILDREN_PER_PARENT)
    sub.add_parser("repair-stuck-accounts", help="对账并修复卡住子号，不创建新邀请")
    sub.add_parser("status", help="查看母号和子号状态")
    sub.add_parser("cpa-resync", help="补传 auth_saved/ready 子号到 CLIProxyAPI")

    api_parser = sub.add_parser("api", help="启动 Web/API 服务")
    api_parser.add_argument("--host", default="0.0.0.0")
    api_parser.add_argument("--port", type=int, default=8787)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    if args.command == "parent":
        if args.parent_command == "list":
            cmd_parent_list(args)
        elif args.parent_command == "add":
            cmd_parent_add(args)
        elif args.parent_command == "update":
            cmd_parent_update(args)
        elif args.parent_command == "remove":
            cmd_parent_remove(args)
        elif args.parent_command == "enable":
            update_parent(args.parent_id, enabled=True)
        elif args.parent_command == "disable":
            update_parent(args.parent_id, enabled=False)
        elif args.parent_command == "login":
            cmd_parent_login(args)
        elif args.parent_command == "import-session":
            cmd_parent_import_session(args)
        else:
            parent_parser.print_help()
        return

    if args.command == "batch-run":
        run_batch()
        return

    if args.command == "fill-all":
        run_fill_all(target_per_parent=args.target_per_parent)
        return

    if args.command == "repair-stuck-accounts":
        run_repair_stuck_accounts()
        return

    if args.command == "status":
        cmd_status()
        return

    if args.command == "cpa-resync":
        migrate_legacy_data()
        if not check_and_setup(interactive=False):
            raise SystemExit("配置不完整，请先完成初始化配置")
        resync_ready_accounts()
        return

    if args.command == "api":
        from autoteam.api import start_server

        start_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
