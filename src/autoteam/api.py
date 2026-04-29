"""AutoTeam HTTP API."""

from __future__ import annotations

import importlib
import logging
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from autoteam.accounts import (
    REMOTE_STATE_MEMBER,
    STATUS_ACCEPTED,
    STATUS_AUTH_SAVED,
    STATUS_BLOCKED,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_INVITED,
    STATUS_READY,
    STATUS_REMOVED,
    load_accounts,
    update_account_by_id,
)
from autoteam.config import API_KEY
from autoteam.migration import migrate_legacy_data
from autoteam.parents import add_parent, find_parent, load_parents, remove_parent, update_parent

logger = logging.getLogger(__name__)

app = FastAPI(title="AutoTeam API", description="Multi-parent batch invite API", version="2.0.0")

_AUTH_SKIP_PATHS = {"/api/auth/check", "/api/setup/status", "/api/setup/save"}
LOGIN_ALL_DEFAULT_CONCURRENCY = 2
LOGIN_ALL_MAX_CONCURRENCY = 5
LOGIN_ALL_PARENT_TIMEOUT_SECONDS = 360
LOGIN_ALL_POLL_INTERVAL_SECONDS = 0.2


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/") or path in _AUTH_SKIP_PATHS:
        return await call_next(request)
    if not API_KEY:
        return await call_next(request)
    token = ""
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    if token != API_KEY:
        return JSONResponse(status_code=401, content={"detail": "鏈巿鏉冿紝璇锋彁渚涙湁鏁堢殑 API Key"})
    return await call_next(request)


@app.get("/api/auth/check")
def check_auth(request: Request):
    if not API_KEY:
        return {"authenticated": True, "auth_required": False}
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer ") and auth_header[7:] == API_KEY:
        return {"authenticated": True, "auth_required": True}
    return JSONResponse(status_code=401, content={"authenticated": False, "auth_required": True})


class SetupConfig(BaseModel):
    FREEMAIL_BASE_URL: str = ""
    FREEMAIL_ROOT_TOKEN: str = ""
    CPA_URL: str = "http://127.0.0.1:8317"
    CPA_KEY: str = ""
    PLAYWRIGHT_PROXY_URL: str = ""
    PLAYWRIGHT_PROXY_BYPASS: str = ""
    TARGET_CHILDREN_PER_PARENT: str = "4"
    API_KEY: str = ""


def _build_setup_payload(*, include_values: bool) -> dict:
    from autoteam.setup_wizard import REQUIRED_CONFIGS, _read_env

    env = _read_env()
    fields = []
    configured = True
    for key, prompt, default, optional in REQUIRED_CONFIGS:
        value = env.get(key, "") or os.environ.get(key, "")
        ok = bool(value)
        if not ok and not optional:
            configured = False
        item = {"key": key, "prompt": prompt, "default": default, "optional": optional, "configured": ok}
        if include_values:
            item["value"] = value
        fields.append(item)
    return {"configured": configured, "fields": fields}


def _save_setup_config(config: SetupConfig):
    import secrets

    from autoteam.setup_wizard import REQUIRED_CONFIGS, _verify_cpa, _verify_freemail, _write_env

    data = config.model_dump()
    defaults = {key: default for key, _prompt, default, _optional in REQUIRED_CONFIGS}
    if not data.get("CPA_URL"):
        data["CPA_URL"] = defaults.get("CPA_URL", "http://127.0.0.1:8317")
    if not data.get("API_KEY"):
        data["API_KEY"] = secrets.token_urlsafe(24)

    clearable = {"PLAYWRIGHT_PROXY_URL", "PLAYWRIGHT_PROXY_BYPASS", "TARGET_CHILDREN_PER_PARENT"}
    for key, value in data.items():
        if value or key in clearable:
            _write_env(key, value)
            os.environ[key] = value

    import autoteam.config

    importlib.reload(autoteam.config)
    try:
        import autoteam.freemail

        importlib.reload(autoteam.freemail)
    except Exception:
        pass

    errors = []
    if not _verify_freemail():
        errors.append("Freemail 杩炴帴澶辫触")
    if not _verify_cpa():
        errors.append("CPA 杩炴帴澶辫触")
    if errors:
        return JSONResponse(status_code=400, content={"message": " / ".join(errors), "api_key": data["API_KEY"]})

    global API_KEY
    API_KEY = data["API_KEY"]
    return {"message": "閰嶇疆淇濆瓨鎴愬姛", "api_key": data["API_KEY"], "configured": True}


@app.get("/api/setup/status")
def get_setup_status():
    return _build_setup_payload(include_values=False)


@app.get("/api/settings")
def get_settings():
    return _build_setup_payload(include_values=True)


@app.post("/api/settings")
def post_settings_save(config: SetupConfig):
    return _save_setup_config(config)


@app.post("/api/setup/save")
def post_setup_save(config: SetupConfig):
    return _save_setup_config(config)


class ParentCreateParams(BaseModel):
    label: str = ""
    email: str
    default_batch_size: int = 1
    enabled: bool = True


class ParentBulkImportItem(BaseModel):
    label: str = ""
    email: str
    password: str = ""
    workspace_name: str = ""
    session_token: str = ""
    account_id: str = ""
    default_batch_size: int | None = None
    enabled: bool | None = None


class ParentBulkImportParams(BaseModel):
    parents: list[ParentBulkImportItem]
    default_batch_size: int = 1


class ParentLoginAllParams(BaseModel):
    concurrency: Any = None


class ChildHealthCheckParams(BaseModel):
    concurrency: Any = None


class ParentChildLinkRepairParams(BaseModel):
    concurrency: Any = None


class ParentUpdateParams(BaseModel):
    label: str | None = None
    email: str | None = None
    workspace_name: str | None = None
    session_token: str | None = None
    password: str | None = None
    account_id: str | None = None
    default_batch_size: int | None = None
    enabled: bool | None = None


class ParentLoginStartParams(BaseModel):
    email: str | None = None


class ParentSessionParams(BaseModel):
    email: str | None = None
    session_token: str


class PasswordParams(BaseModel):
    password: str


class CodeParams(BaseModel):
    code: str


class WorkspaceParams(BaseModel):
    option_id: str


class _PlaywrightExecutor:
    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None

    def _worker(self):
        while True:
            item = self._queue.get()
            if item is None:
                break
            func, args, kwargs, event, holder = item
            try:
                holder["result"] = func(*args, **kwargs)
            except Exception as exc:
                holder["error"] = exc
            finally:
                event.set()

    def ensure_started(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def run(self, func, *args, timeout=300, **kwargs):
        self.ensure_started()
        event = threading.Event()
        holder = {}
        self._queue.put((func, args, kwargs, event, holder))
        if timeout is None:
            event.wait()
        else:
            finished = event.wait(timeout=timeout)
            if not finished:
                raise TimeoutError(f"Playwright task timed out after {timeout}s")
        if "error" in holder:
            raise holder["error"]
        return holder.get("result")


_pw_executor = _PlaywrightExecutor()
_pw_executor.ensure_started()
_playwright_lock = threading.Lock()
_tasks_lock = threading.Lock()

_tasks: dict[str, dict] = {}
MAX_TASK_HISTORY = 50
_current_task_id: str | None = None

_active_login_flow = None
_active_login_parent_id: str | None = None
_active_login_step: str | None = None
_active_login_email: str = ""
_active_login_detail = None
_active_login_message: str = ""
_active_login_auto_mode = False

_active_main_codex_flow = None
_active_main_codex_parent_id: str | None = None
_active_main_codex_step: str | None = None
_active_main_codex_email: str = ""
_active_main_codex_detail = None
_active_main_codex_message: str = ""
_active_main_codex_auto_mode = False

_LOGIN_STEP_MESSAGES = {
    "starting": "正在启动自动登录",
    "submitting_email": "正在提交母号邮箱",
    "waiting_code": "正在等待验证码",
    "submitting_code": "正在提交验证码",
    "selecting_workspace": "正在选择工作空间",
    "password_required": "需要密码继续登录",
    "code_required": "需要验证码继续登录",
    "workspace_required": "需要手动选择工作空间",
    "completed": "登录已完成",
    "error": "登录失败",
}

_MAIN_CODEX_STEP_MESSAGES = {
    "starting": "正在启动主号同步",
    "waiting_code": "正在等待主号验证码",
    "submitting_code": "正在提交主号验证码",
    "uploading_cpa": "正在上传主号 auth 到 CPA",
    "password_required": "需要密码继续主号同步",
    "code_required": "需要验证码继续主号同步",
    "completed": "主号传 CPA 已完成",
    "error": "主号传 CPA 失败",
}


def _sanitize_parent(parent: dict) -> dict:
    data = dict(parent)
    main_auth_file = str(data.get("main_auth_file") or "").strip()
    data["session_present"] = bool(data.get("session_token"))
    data["password_saved"] = bool(data.get("password"))
    data["main_auth_present"] = bool(main_auth_file)
    data["main_auth_filename"] = Path(main_auth_file).name if main_auth_file else ""
    data.pop("session_token", None)
    data.pop("password", None)
    data.pop("main_auth_file", None)
    return data


def _parent_email_key(email) -> str:
    return str(email or "").strip().lower()


def _find_parent_by_email(parents: list[dict], email: str) -> dict | None:
    target = _parent_email_key(email)
    if not target:
        return None
    for parent in parents:
        if _parent_email_key(parent.get("email")) == target:
            return parent
    return None


def _normalize_login_concurrency(value) -> int:
    if value in (None, ""):
        return LOGIN_ALL_DEFAULT_CONCURRENCY
    try:
        concurrency = int(value)
    except Exception:
        return LOGIN_ALL_DEFAULT_CONCURRENCY
    return min(LOGIN_ALL_MAX_CONCURRENCY, max(1, concurrency))


def _default_login_message(step: str | None) -> str:
    return _LOGIN_STEP_MESSAGES.get(step or "", "")


def _default_main_codex_message(step: str | None) -> str:
    return _MAIN_CODEX_STEP_MESSAGES.get(step or "", "")


def _login_status():
    return {
        "in_progress": _active_login_flow is not None,
        "parent_id": _active_login_parent_id,
        "email": _active_login_email,
        "step": _active_login_step,
        "detail": _active_login_detail,
        "message": _active_login_message,
        "auto_mode": _active_login_auto_mode,
        "workspace_options": getattr(_active_login_flow, "workspace_options_cache", []) if _active_login_flow else [],
    }


def _main_codex_status():
    return {
        "in_progress": _active_main_codex_flow is not None,
        "parent_id": _active_main_codex_parent_id,
        "email": _active_main_codex_email,
        "step": _active_main_codex_step,
        "detail": _active_main_codex_detail,
        "message": _active_main_codex_message,
        "auto_mode": _active_main_codex_auto_mode,
    }


def _get_team_parent_or_error(parent_id: str) -> dict:
    migrate_legacy_data()
    parent = find_parent(load_parents(), parent_id)
    if not parent:
        raise HTTPException(status_code=404, detail="母号不存在")
    if not parent.get("session_token") or not parent.get("account_id"):
        raise HTTPException(status_code=400, detail="母号未完成登录")
    return parent


def _acquire_team_action_lock():
    with _tasks_lock:
        task_busy = any(task["status"] in {"pending", "running"} for task in _tasks.values())
    if _active_login_flow or _active_main_codex_flow or task_busy or _playwright_lock.locked():
        raise HTTPException(status_code=409, detail="已有任务正在执行，请稍后再试")
    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="已有任务正在执行，请稍后再试")


def _extract_team_invites(payload) -> list[dict]:
    invites = []
    if isinstance(payload, list):
        invites = payload
    elif isinstance(payload, dict):
        invites = payload.get("account_invites") or payload.get("invites") or []
    return [invite for invite in invites if isinstance(invite, dict)]


def _invite_email(invite: dict) -> str:
    return str(invite.get("email_address") or invite.get("email") or "").strip().lower()


def _match_local_invite_record(
    accounts: list[dict],
    *,
    parent_id: str,
    invite_id: str = "",
    email: str = "",
    fallback_statuses: set[str] | None = None,
) -> dict | None:
    invite_id = str(invite_id or "").strip()
    email = str(email or "").strip().lower()
    if invite_id:
        for account in accounts:
            if account.get("parent_id") == parent_id and str(account.get("invite_id") or "").strip() == invite_id:
                return account

    allowed = fallback_statuses or set()
    if email:
        for account in accounts:
            if account.get("parent_id") != parent_id:
                continue
            if str(account.get("email") or "").strip().lower() != email:
                continue
            if allowed and account.get("status") not in allowed:
                continue
            return account
    return None


def _serialize_parent_invites(parent_id: str, invites: list[dict], accounts: list[dict]) -> list[dict]:
    serialized = []
    for invite in invites:
        invite_id = str(invite.get("id") or "").strip()
        email = _invite_email(invite)
        if not invite_id and not email:
            continue
        local = _match_local_invite_record(
            accounts,
            parent_id=parent_id,
            invite_id=invite_id,
            email=email,
        )
        serialized.append(
            {
                "invite_id": invite_id,
                "email": email,
                "role": str(invite.get("role") or "").strip(),
                "is_local": bool(local),
                "local_child_id": local.get("id") if local else None,
                "local_status": local.get("status") if local else None,
            }
        )
    return sorted(serialized, key=lambda item: (item["email"], item["invite_id"]))


def _serialize_blocked_members(parent_id: str, accounts: list[dict]) -> list[dict]:
    blocked = []
    for account in accounts:
        if account.get("parent_id") != parent_id:
            continue
        if account.get("status") != STATUS_BLOCKED:
            continue
        if account.get("remote_state") != REMOTE_STATE_MEMBER:
            continue
        blocked.append(
            {
                "id": account.get("id"),
                "email": account.get("email"),
                "parent_id": account.get("parent_id"),
                "parent_email": account.get("parent_email"),
                "remote_state": account.get("remote_state"),
                "remote_member_id": account.get("remote_member_id"),
                "health_status": account.get("health_status"),
                "health_checked_at": account.get("health_checked_at"),
                "health_error": account.get("health_error"),
                "error": account.get("error"),
                "error_stage": account.get("error_stage"),
            }
        )
    return sorted(blocked, key=lambda item: (item.get("email") or "", item.get("id") or ""))


def _parent_child_slot_stats(children: list[dict]) -> dict[str, dict[str, int]]:
    stats: dict[str, dict[str, int]] = {}
    for child in children:
        parent_id = str(child.get("parent_id") or "")
        if not parent_id:
            continue
        parent_stats = stats.setdefault(
            parent_id,
            {
                "blocked_member_count": 0,
                "pending_removal_count": 0,
                "quota_exhausted_count": 0,
            },
        )
        if child.get("status") == STATUS_BLOCKED and child.get("remote_state") == REMOTE_STATE_MEMBER:
            parent_stats["blocked_member_count"] += 1
            parent_stats["pending_removal_count"] += 1
        if child.get("health_status") == "quota_exhausted":
            parent_stats["quota_exhausted_count"] += 1
    return stats


def _attach_parent_slot_stats(parent: dict, stats: dict[str, int]) -> dict:
    data = _sanitize_parent(parent)
    remote_member_count = max(0, int(data.get("remote_member_count") or 0))
    blocked_member_count = int(stats.get("blocked_member_count") or 0)
    pending_removal_count = int(stats.get("pending_removal_count") or 0)
    data["blocked_member_count"] = blocked_member_count
    data["pending_removal_count"] = pending_removal_count
    data["usable_member_count"] = max(0, remote_member_count - blocked_member_count)
    data["refill_gap_after_removal"] = pending_removal_count
    data["quota_exhausted_count"] = int(stats.get("quota_exhausted_count") or 0)
    return data


def _status_payload():
    from autoteam.config import get_target_children_per_parent

    migrate_legacy_data()
    raw_parents = load_parents()
    children = [{key: value for key, value in child.items() if key != "password"} for child in load_accounts()]
    parent_slot_stats = _parent_child_slot_stats(children)
    parents = [_attach_parent_slot_stats(parent, parent_slot_stats.get(str(parent.get("id") or ""), {})) for parent in raw_parents]
    pending_removal_children = sum(int(item.get("pending_removal_count") or 0) for item in parent_slot_stats.values())
    summary = {
        "enabled_parents": sum(1 for parent in parents if parent.get("enabled")),
        "accepted_children": sum(1 for child in children if child.get("status") == STATUS_ACCEPTED),
        "auth_saved_children": sum(1 for child in children if child.get("status") == STATUS_AUTH_SAVED),
        "ready_children": sum(1 for child in children if child.get("status") == STATUS_READY),
        "blocked_children": sum(1 for child in children if child.get("status") == STATUS_BLOCKED),
        "pending_removal_children": pending_removal_children,
        "removed_children": sum(1 for child in children if child.get("status") == STATUS_REMOVED),
        "quota_exhausted_children": sum(1 for child in children if child.get("health_status") == "quota_exhausted"),
        "failed_children": sum(1 for child in children if child.get("status") == STATUS_FAILED),
        "invited_children": sum(1 for child in children if child.get("status") == STATUS_INVITED),
        "recoverable_children": sum(1 for child in children if child.get("status") in {STATUS_ACCEPTED, STATUS_AUTH_SAVED}),
        "drift_parents": sum(1 for parent in raw_parents if int(parent.get("drift_count") or 0) > 0),
        "target_children_per_parent": get_target_children_per_parent(),
        "last_batch_at": max((parent.get("last_run_at") or 0 for parent in raw_parents), default=0) or None,
    }
    return {"parents": parents, "children": children, "summary": summary}


def _prune_tasks():
    if len(_tasks) <= MAX_TASK_HISTORY:
        return
    completed = sorted((task for task in _tasks.values() if task["status"] in {"completed", "failed"}), key=lambda t: t["created_at"])
    while len(_tasks) > MAX_TASK_HISTORY and completed:
        task = completed.pop(0)
        _tasks.pop(task["task_id"], None)


def _run_task(task_id: str, func, *args, **kwargs):
    global _current_task_id
    task = _tasks[task_id]
    _playwright_lock.acquire()
    _current_task_id = task_id
    with _tasks_lock:
        task["status"] = "running"
        task["started_at"] = time.time()
    try:
        result = func(*args, **kwargs)
        with _tasks_lock:
            task["result"] = result
            task["status"] = "completed"
    except Exception as exc:
        with _tasks_lock:
            task["status"] = "failed"
            task["error"] = str(exc)
        logger.error("[API] 浠诲姟澶辫触: %s", exc)
    finally:
        with _tasks_lock:
            task["finished_at"] = time.time()
        _current_task_id = None
        _playwright_lock.release()


def _start_task(command: str, func, params: dict):
    with _tasks_lock:
        if _playwright_lock.locked() or any(task["status"] in {"pending", "running"} for task in _tasks.values()):
            raise HTTPException(status_code=409, detail="已有任务正在执行，请稍后再试")
        task_id = uuid.uuid4().hex[:12]
        task = {
            "task_id": task_id,
            "command": command,
            "params": params,
            "status": "pending",
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
        }
        _tasks[task_id] = task
        _prune_tasks()
    thread = threading.Thread(target=_run_task, args=(task_id, func), daemon=True)
    thread.start()
    return task


@app.get("/api/status")
def get_status():
    return _status_payload()


@app.get("/api/tasks")
def get_tasks():
    return sorted(_tasks.values(), key=lambda task: task["created_at"], reverse=True)


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@app.get("/api/parents")
def get_parents():
    return {"parents": _status_payload()["parents"], "login": _login_status()}


@app.post("/api/parents")
def post_parent(params: ParentCreateParams):
    migrate_legacy_data()
    parent = add_parent(
        label=params.label or params.email,
        email=params.email,
        default_batch_size=params.default_batch_size,
        enabled=params.enabled,
    )
    return {"parent": _sanitize_parent(parent)}


@app.post("/api/parents/bulk-import")
def post_parents_bulk_import(params: ParentBulkImportParams):
    migrate_legacy_data()
    if not params.parents:
        raise HTTPException(status_code=400, detail="没有可导入的母号")

    created = 0
    updated = 0
    skipped = 0
    failed = 0
    seen_emails: set[str] = set()
    items = []

    for index, item in enumerate(params.parents, start=1):
        email = _parent_email_key(item.email)
        row = {"index": index, "email": email, "status": "", "parent_id": "", "error": ""}
        if not email:
            skipped += 1
            row.update(status="skipped", error="缺少邮箱")
            items.append(row)
            continue
        if email in seen_emails:
            skipped += 1
            row.update(status="skipped", error="重复邮箱")
            items.append(row)
            continue
        seen_emails.add(email)

        try:
            existing = _find_parent_by_email(load_parents(), email)
            common_updates = {
                "email": email,
            }
            provided_fields = getattr(item, "model_fields_set", set())
            if "default_batch_size" in provided_fields and item.default_batch_size is not None:
                common_updates["default_batch_size"] = item.default_batch_size
            if "enabled" in provided_fields and item.enabled is not None:
                common_updates["enabled"] = item.enabled
            if item.label.strip():
                common_updates["label"] = item.label.strip()
            for key in ("password", "workspace_name", "session_token", "account_id"):
                value = str(getattr(item, key) or "").strip()
                if value:
                    common_updates[key] = value

            if existing:
                parent = update_parent(existing["id"], **common_updates)
                if not parent:
                    raise RuntimeError("母号更新失败")
                updated += 1
                row.update(status="updated", parent_id=parent.get("id", ""))
            else:
                parent = add_parent(
                    label=item.label.strip() or email,
                    email=email,
                    default_batch_size=item.default_batch_size or params.default_batch_size,
                    enabled=True if item.enabled is None else item.enabled,
                    session_token=item.session_token,
                    password=item.password,
                    account_id=item.account_id,
                    workspace_name=item.workspace_name,
                )
                created += 1
                row.update(status="created", parent_id=parent.get("id", ""))
        except Exception as exc:
            failed += 1
            row.update(status="failed", error=str(exc))
        items.append(row)

    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "items": items,
        "parents": [_sanitize_parent(parent) for parent in load_parents()],
    }


@app.put("/api/parents/{parent_id}")
def put_parent(parent_id: str, params: ParentUpdateParams):
    migrate_legacy_data()
    updates = {key: value for key, value in params.model_dump().items() if value is not None}
    parent = update_parent(parent_id, **updates)
    if not parent:
        raise HTTPException(status_code=404, detail="母号不存在")
    return {"parent": _sanitize_parent(parent)}


@app.delete("/api/parents/{parent_id}")
def delete_parent(parent_id: str):
    migrate_legacy_data()
    if not remove_parent(parent_id):
        raise HTTPException(status_code=404, detail="母号不存在")
    return {"success": True}


@app.get("/api/parents/login/status")
def get_parent_login_status():
    return _login_status()


@app.get("/api/parents/main-codex/status")
def get_parent_main_codex_status():
    return _main_codex_status()


def _set_login_flow(parent_id: str, api_obj, step: str, email: str, *, detail=None, message: str = "", auto_mode=False):
    global _active_login_flow, _active_login_parent_id, _active_login_step, _active_login_email
    global _active_login_detail, _active_login_message, _active_login_auto_mode
    _active_login_flow = api_obj
    _active_login_parent_id = parent_id
    _active_login_step = step
    _active_login_email = email
    _active_login_detail = detail
    _active_login_message = message or _default_login_message(step)
    _active_login_auto_mode = bool(auto_mode)


def _clear_login_flow():
    global _active_login_flow, _active_login_parent_id, _active_login_step, _active_login_email
    global _active_login_detail, _active_login_message, _active_login_auto_mode
    flow = _active_login_flow
    _active_login_flow = None
    _active_login_parent_id = None
    _active_login_step = None
    _active_login_email = ""
    _active_login_detail = None
    _active_login_message = ""
    _active_login_auto_mode = False
    return flow


def _set_main_codex_flow(parent_id: str, flow, step: str, email: str, *, detail=None, message: str = "", auto_mode=False):
    global _active_main_codex_flow, _active_main_codex_parent_id, _active_main_codex_step, _active_main_codex_email
    global _active_main_codex_detail, _active_main_codex_message, _active_main_codex_auto_mode
    _active_main_codex_flow = flow
    _active_main_codex_parent_id = parent_id
    _active_main_codex_step = step
    _active_main_codex_email = email
    _active_main_codex_detail = detail
    _active_main_codex_message = message or _default_main_codex_message(step)
    _active_main_codex_auto_mode = bool(auto_mode)


def _clear_main_codex_flow():
    global _active_main_codex_flow, _active_main_codex_parent_id, _active_main_codex_step, _active_main_codex_email
    global _active_main_codex_detail, _active_main_codex_message, _active_main_codex_auto_mode
    flow = _active_main_codex_flow
    _active_main_codex_flow = None
    _active_main_codex_parent_id = None
    _active_main_codex_step = None
    _active_main_codex_email = ""
    _active_main_codex_detail = None
    _active_main_codex_message = ""
    _active_main_codex_auto_mode = False
    return flow


def _release_playwright_lock():
    if _playwright_lock.locked():
        _playwright_lock.release()


def _stop_login_flow(flow):
    if not flow:
        return
    try:
        _pw_executor.run(flow.stop)
    except Exception:
        pass


def _stop_main_codex_flow(flow):
    if not flow:
        return
    try:
        _pw_executor.run(flow.stop)
    except Exception:
        pass


def _abort_login_flow():
    flow = _clear_login_flow()
    _stop_login_flow(flow)
    _release_playwright_lock()


def _abort_main_codex_flow():
    flow = _clear_main_codex_flow()
    _stop_main_codex_flow(flow)
    _release_playwright_lock()


def _complete_login(parent_id: str):
    flow = _active_login_flow
    result = {"status": "completed", "info": None, "login": None}
    try:
        info = _pw_executor.run(flow.complete_admin_login)
        update_parent(parent_id, **{k: v for k, v in info.items() if k in {"email", "password", "session_token", "account_id", "workspace_name"}})
        result["info"] = info
    finally:
        _stop_login_flow(flow)
        _clear_login_flow()
        _release_playwright_lock()
    result["login"] = _login_status()
    return result


def _stop_login_all_flow(flow):
    if not flow:
        return
    try:
        flow.stop()
    except Exception:
        pass


def _track_login_all_flow(parent_id: str, flow, active_flows: dict | None, active_lock: threading.Lock | None):
    if active_flows is None or active_lock is None or not parent_id:
        return
    with active_lock:
        active_flows[parent_id] = flow


def _untrack_login_all_flow(parent_id: str, active_flows: dict | None, active_lock: threading.Lock | None):
    if active_flows is None or active_lock is None or not parent_id:
        return
    with active_lock:
        active_flows.pop(parent_id, None)


def _stop_tracked_login_all_flow(parent_id: str, active_flows: dict, active_lock: threading.Lock):
    flow = None
    with active_lock:
        flow = active_flows.pop(parent_id, None)
    _stop_login_all_flow(flow)


def _login_all_timeout_item(parent: dict, timeout_seconds: float) -> dict:
    timeout_label = int(timeout_seconds) if float(timeout_seconds).is_integer() else timeout_seconds
    return {
        "parent_id": str(parent.get("id") or ""),
        "email": _parent_email_key(parent.get("email")),
        "status": "failed",
        "step": "timeout",
        "detail": f"单个母号登录超过 {timeout_label} 秒，已主动关闭浏览器",
        "_parent_update": {
            "last_run_at": time.time(),
            "last_run_status": "login_failed_timeout",
            "last_run_failed": 1,
        },
    }


def _login_all_error_item(parent: dict, exc: Exception) -> dict:
    return {
        "parent_id": str(parent.get("id") or ""),
        "email": _parent_email_key(parent.get("email")),
        "status": "failed",
        "step": "error",
        "detail": str(exc),
        "_parent_update": {
            "last_run_at": time.time(),
            "last_run_status": "login_failed",
            "last_run_failed": 1,
        },
    }


def _run_saved_parent_login(parent: dict, active_flows: dict | None = None, active_lock: threading.Lock | None = None) -> dict:
    from autoteam.chatgpt_api import ChatGPTTeamAPI

    parent_id = str(parent.get("id") or "")
    email = _parent_email_key(parent.get("email"))
    item = {
        "parent_id": parent_id,
        "email": email,
        "status": "",
        "step": "",
        "detail": "",
        "workspace_name": "",
        "account_id": "",
    }
    if not parent_id or not email:
        item.update(status="failed", step="invalid_parent", detail="缺少母号 ID 或邮箱")
        return item

    api_obj = None
    try:
        api_obj = ChatGPTTeamAPI(workspace_name=parent.get("workspace_name") or "")
        _track_login_all_flow(parent_id, api_obj, active_flows, active_lock)
        result = api_obj.auto_admin_login(email, password=parent.get("password") or "")
        step = str(result.get("step") or "error")
        detail = str(result.get("detail") or "")
        item.update(step=step, detail=detail)

        if step == "completed":
            info = api_obj.complete_admin_login()
            item["_parent_update"] = {
                key: value for key, value in info.items() if key in {"email", "password", "session_token", "account_id", "workspace_name"}
            }
            item["_parent_update"].update(last_run_at=time.time(), last_run_status="login_completed", last_run_failed=0)
            item.update(
                status="logged_in",
                workspace_name=info.get("workspace_name") or "",
                account_id=info.get("account_id") or "",
            )
            return item

        if step in {"password_required", "code_required", "workspace_required"}:
            item["_parent_update"] = {
                "last_run_at": time.time(),
                "last_run_status": "login_manual_required",
                "last_run_failed": 1,
            }
            item["status"] = "manual_required"
            return item

        item["_parent_update"] = {
            "last_run_at": time.time(),
            "last_run_status": "login_failed",
            "last_run_failed": 1,
        }
        item["status"] = "failed"
        return item
    except Exception as exc:
        item["_parent_update"] = {
            "last_run_at": time.time(),
            "last_run_status": "login_failed",
            "last_run_failed": 1,
        }
        item.update(status="failed", step="error", detail=str(exc))
        return item
    finally:
        _untrack_login_all_flow(parent_id, active_flows, active_lock)
        if api_obj:
            _stop_login_all_flow(api_obj)


def run_login_all_parents(
    concurrency=LOGIN_ALL_DEFAULT_CONCURRENCY,
    parent_timeout_seconds=LOGIN_ALL_PARENT_TIMEOUT_SECONDS,
    poll_interval_seconds=LOGIN_ALL_POLL_INTERVAL_SECONDS,
):
    migrate_legacy_data()
    parents = load_parents()
    concurrency = _normalize_login_concurrency(concurrency)
    parent_timeout_seconds = max(0.01, float(parent_timeout_seconds or LOGIN_ALL_PARENT_TIMEOUT_SECONDS))
    poll_interval_seconds = max(0.01, float(poll_interval_seconds or LOGIN_ALL_POLL_INTERVAL_SECONDS))
    result = {
        "parents": len(parents),
        "attempted": 0,
        "logged_in": 0,
        "manual_required": 0,
        "failed": 0,
        "timed_out": 0,
        "skipped": 0,
        "concurrency": concurrency,
        "parent_timeout_seconds": parent_timeout_seconds,
        "items": [],
    }
    login_targets = []

    def _record_item(item: dict):
        parent_update = item.pop("_parent_update", None)
        parent_id = item.get("parent_id") or ""
        if parent_update and parent_id:
            update_parent(parent_id, **parent_update)

        status = item.get("status")
        if status == "logged_in":
            result["logged_in"] += 1
        elif status == "manual_required":
            result["manual_required"] += 1
        elif status == "skipped":
            result["skipped"] += 1
        else:
            result["failed"] += 1
            if item.get("step") == "timeout":
                result["timed_out"] += 1
        result["items"].append(item)

    for parent in parents:
        parent_id = str(parent.get("id") or "")
        email = _parent_email_key(parent.get("email"))
        if not parent.get("enabled", True):
            _record_item({"parent_id": parent_id, "email": email, "status": "skipped", "step": "disabled", "detail": "母号已停用"})
            continue
        if parent.get("session_token") and parent.get("account_id"):
            _record_item(
                {
                    "parent_id": parent_id,
                    "email": email,
                    "status": "skipped",
                    "step": "already_logged_in",
                    "detail": "已保存登录态",
                }
            )
            continue

        login_targets.append(dict(parent))

    result["attempted"] = len(login_targets)
    if not login_targets:
        return result

    active_flows: dict[str, Any] = {}
    active_lock = threading.Lock()
    completed_queue: queue.Queue = queue.Queue()
    pending = list(login_targets)
    running: dict[str, dict] = {}

    def _run_thread(run_key: str, parent: dict):
        try:
            item = _run_saved_parent_login(parent, active_flows, active_lock)
        except Exception as exc:
            item = _login_all_error_item(parent, exc)
        completed_queue.put((run_key, item))

    def _start_next():
        if not pending or len(running) >= concurrency:
            return
        parent = pending.pop(0)
        parent_id = str(parent.get("id") or "")
        run_key = uuid.uuid4().hex
        thread = threading.Thread(target=_run_thread, args=(run_key, parent), daemon=True)
        running[run_key] = {
            "parent": parent,
            "parent_id": parent_id,
            "thread": thread,
            "started_at": time.monotonic(),
        }
        thread.start()

    for _ in range(min(concurrency, len(pending))):
        _start_next()

    while running:
        try:
            run_key, item = completed_queue.get(timeout=poll_interval_seconds)
        except queue.Empty:
            run_key = ""
            item = None

        if run_key in running:
            running.pop(run_key, None)
            _record_item(item)
            while pending and len(running) < concurrency:
                _start_next()

        now = time.monotonic()
        for run_key, meta in list(running.items()):
            if now - meta["started_at"] < parent_timeout_seconds:
                continue
            parent = meta["parent"]
            parent_id = meta["parent_id"]
            logger.warning(
                "[API] login-all parent timed out; closing browser | parent_id=%s | email=%s | timeout=%ss",
                parent_id,
                _parent_email_key(parent.get("email")),
                parent_timeout_seconds,
            )
            _stop_tracked_login_all_flow(parent_id, active_flows, active_lock)
            running.pop(run_key, None)
            _record_item(_login_all_timeout_item(parent, parent_timeout_seconds))
            while pending and len(running) < concurrency:
                _start_next()

    return result


def _complete_main_codex(parent_id: str):
    flow = _active_main_codex_flow
    result = {"status": "completed", "info": None, "sync": None, "main_codex": None}
    auth_saved = None
    refreshed_at = time.time()

    try:
        info = _pw_executor.run(flow.complete)
        auth_saved = info
        result["info"] = {
            "parent_id": parent_id,
            "email": info.get("email"),
            "auth_file": info.get("auth_file"),
            "plan_type": info.get("plan_type"),
        }
        _set_main_codex_flow(
            parent_id,
            flow,
            "uploading_cpa",
            info.get("email") or _active_main_codex_email,
            detail=Path(str(info.get("auth_file") or "")).name or None,
            message="正在上传主号 auth 到 CPA",
            auto_mode=_active_main_codex_auto_mode,
        )

        from autoteam.cpa_sync import sync_parent_main_auth_to_cpa

        sync_result = sync_parent_main_auth_to_cpa(parent_id, info["auth_file"])
        result["sync"] = sync_result
        update_parent(
            parent_id,
            main_auth_file=info.get("auth_file"),
            main_auth_plan_type=info.get("plan_type"),
            main_auth_refreshed_at=refreshed_at,
            main_cpa_uploaded_at=time.time(),
            main_codex_error="",
            main_codex_error_stage="",
        )
    except Exception as exc:
        if auth_saved and auth_saved.get("auth_file"):
            update_parent(
                parent_id,
                main_auth_file=auth_saved.get("auth_file"),
                main_auth_plan_type=auth_saved.get("plan_type"),
                main_auth_refreshed_at=refreshed_at,
                main_codex_error=str(exc),
                main_codex_error_stage="cpa",
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        update_parent(
            parent_id,
            main_codex_error=str(exc),
            main_codex_error_stage="codex",
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        _stop_main_codex_flow(flow)
        _clear_main_codex_flow()
        _release_playwright_lock()

    result["main_codex"] = _main_codex_status()
    return result


@app.post("/api/parents/{parent_id}/login/start")
def post_parent_login_start(parent_id: str, params: ParentLoginStartParams):
    migrate_legacy_data()
    parent = find_parent(load_parents(), parent_id)
    if not parent:
        raise HTTPException(status_code=404, detail="母号不存在")
    if _active_login_flow or _active_main_codex_flow:
        raise HTTPException(status_code=409, detail="已有登录流程正在进行")
    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="已有任务正在执行，请稍后再试")

    from autoteam.chatgpt_api import ChatGPTTeamAPI

    email = (params.email or parent.get("email") or "").strip()
    if not email:
        _playwright_lock.release()
        raise HTTPException(status_code=400, detail="缺少母号邮箱")

    def _do_start():
        api_obj = ChatGPTTeamAPI(workspace_name=parent.get("workspace_name") or "")

        def _progress_callback(*, step, detail=None, message=""):
            _set_login_flow(
                parent_id,
                api_obj,
                step,
                email,
                detail=detail,
                message=message,
                auto_mode=True,
            )

        api_obj.progress_callback = _progress_callback
        try:
            result = api_obj.auto_admin_login(
                email,
                password=parent.get("password") or "",
            )
            return api_obj, result
        except Exception:
            try:
                api_obj.stop()
            except Exception:
                pass
            raise

    try:
        api_obj, result = _pw_executor.run(_do_start, timeout=420)
    except Exception as exc:
        _abort_login_flow()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    step = result.get("step")
    if step == "completed":
        _set_login_flow(
            parent_id,
            api_obj,
            step,
            email,
            detail=result.get("detail"),
            message="登录已完成",
            auto_mode=True,
        )
        return _complete_login(parent_id)
    if step in {"password_required", "code_required", "workspace_required"}:
        _set_login_flow(
            parent_id,
            api_obj,
            step,
            email,
            detail=result.get("detail"),
            message=_active_login_message,
            auto_mode=True,
        )
        return {"status": step, "login": _login_status()}

    _pw_executor.run(api_obj.stop)
    _clear_login_flow()
    if _playwright_lock.locked():
        _playwright_lock.release()
    raise HTTPException(status_code=400, detail=result.get("detail") or "无法识别登录状态")


@app.post("/api/parents/{parent_id}/login/session")
def post_parent_login_session(parent_id: str, params: ParentSessionParams):
    migrate_legacy_data()
    parent = find_parent(load_parents(), parent_id)
    if not parent:
        raise HTTPException(status_code=404, detail="母号不存在")
    if _active_login_flow or _active_main_codex_flow:
        raise HTTPException(status_code=409, detail="已有登录流程正在进行")
    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="已有任务正在执行，请稍后再试")

    from autoteam.chatgpt_api import ChatGPTTeamAPI

    email = (params.email or parent.get("email") or "").strip()
    if not email:
        _playwright_lock.release()
        raise HTTPException(status_code=400, detail="缺少母号邮箱")

    flow = ChatGPTTeamAPI()
    try:
        info = _pw_executor.run(flow.import_admin_session, email, params.session_token)
        update_parent(parent_id, **{k: v for k, v in info.items() if k in {"email", "password", "session_token", "account_id", "workspace_name"}})
        return {"status": "completed", "info": info, "login": _login_status()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        try:
            _pw_executor.run(flow.stop)
        finally:
            if _playwright_lock.locked():
                _playwright_lock.release()


def _ensure_login_flow(parent_id: str):
    if not _active_login_flow or _active_login_parent_id != parent_id:
        raise HTTPException(status_code=400, detail="当前没有对应母号的登录流程")


def _ensure_main_codex_flow(parent_id: str):
    if not _active_main_codex_flow or _active_main_codex_parent_id != parent_id:
        raise HTTPException(status_code=400, detail="当前没有对应母号的主号同步流程")


@app.post("/api/parents/{parent_id}/login/password")
def post_parent_login_password(parent_id: str, params: PasswordParams):
    _ensure_login_flow(parent_id)
    try:
        result = _pw_executor.run(_active_login_flow.submit_admin_password, params.password)
    except Exception as exc:
        _abort_login_flow()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    step = result.get("step")
    if step == "completed":
        return _complete_login(parent_id)
    _set_login_flow(
        parent_id,
        _active_login_flow,
        step,
        _active_login_email,
        detail=result.get("detail"),
        auto_mode=False,
    )
    return {"status": step, "login": _login_status()}


@app.post("/api/parents/{parent_id}/login/code")
def post_parent_login_code(parent_id: str, params: CodeParams):
    _ensure_login_flow(parent_id)
    try:
        result = _pw_executor.run(_active_login_flow.submit_admin_code, params.code)
    except Exception as exc:
        _abort_login_flow()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    step = result.get("step")
    if step == "completed":
        return _complete_login(parent_id)
    _set_login_flow(
        parent_id,
        _active_login_flow,
        step,
        _active_login_email,
        detail=result.get("detail"),
        auto_mode=False,
    )
    return {"status": step, "login": _login_status()}


@app.post("/api/parents/{parent_id}/login/workspace")
def post_parent_login_workspace(parent_id: str, params: WorkspaceParams):
    _ensure_login_flow(parent_id)
    try:
        result = _pw_executor.run(_active_login_flow.select_workspace_option, params.option_id)
    except Exception as exc:
        _abort_login_flow()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    step = result.get("step")
    if step == "completed":
        return _complete_login(parent_id)
    _set_login_flow(
        parent_id,
        _active_login_flow,
        step,
        _active_login_email,
        detail=result.get("detail"),
        auto_mode=False,
    )
    return {"status": step, "login": _login_status()}


@app.post("/api/parents/{parent_id}/login/cancel")
def post_parent_login_cancel(parent_id: str):
    _ensure_login_flow(parent_id)
    _abort_login_flow()
    return {"status": "cancelled", "login": _login_status()}


@app.post("/api/parents/{parent_id}/main-codex/start")
def post_parent_main_codex_start(parent_id: str):
    migrate_legacy_data()
    parent = find_parent(load_parents(), parent_id)
    if not parent:
        raise HTTPException(status_code=404, detail="母号不存在")
    if not parent.get("session_token") or not parent.get("account_id"):
        raise HTTPException(status_code=400, detail="母号未完成登录")
    if _active_login_flow or _active_main_codex_flow:
        raise HTTPException(status_code=409, detail="已有流程正在进行")
    with _tasks_lock:
        task_busy = any(task["status"] in {"pending", "running"} for task in _tasks.values())
    if task_busy or not _playwright_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="已有任务正在执行，请稍后再试")

    email = str(parent.get("email") or "").strip().lower()
    if not email:
        _release_playwright_lock()
        raise HTTPException(status_code=400, detail="母号缺少邮箱")

    mail_client = None
    mail_client_ready = False
    latest_email_id = 0
    try:
        from autoteam.freemail import FreemailClient

        mail_client = FreemailClient()
        latest_email_id = int(mail_client.get_latest_email_id(email, sender_keyword="openai") or 0)
        mail_client_ready = True
    except Exception as exc:
        logger.warning("[API] main codex freemail unavailable for %s: %s", email, exc)

    def _do_start():
        from autoteam.codex_auth import ParentMainCodexSyncFlow

        flow = ParentMainCodexSyncFlow(
            parent_id=parent_id,
            email=email,
            session_token=parent.get("session_token") or "",
            account_id=parent.get("account_id") or "",
            workspace_name=parent.get("workspace_name") or "",
            password=parent.get("password") or "",
        )
        result = flow.start()
        return flow, result

    try:
        _set_main_codex_flow(parent_id, None, "starting", email, auto_mode=True)
        flow, result = _pw_executor.run(_do_start, timeout=420)
    except Exception as exc:
        _abort_main_codex_flow()
        update_parent(parent_id, main_codex_error=str(exc), main_codex_error_stage="codex")
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _set_main_codex_flow(
        parent_id,
        flow,
        result.get("step") or "starting",
        email,
        detail=result.get("detail"),
        auto_mode=True,
    )

    for _ in range(12):
        step = result.get("step")
        detail = result.get("detail")

        if step == "completed":
            return _complete_main_codex(parent_id)

        if step == "code_required":
            if not mail_client_ready or not mail_client:
                detail = "Freemail 无法读取或当前邮箱不在托管域名中，请手动输入验证码"
                _set_main_codex_flow(
                    parent_id,
                    flow,
                    "code_required",
                    email,
                    detail=detail,
                    message="自动收验证码不可用",
                    auto_mode=True,
                )
                return {"status": "code_required", "main_codex": _main_codex_status()}

            _set_main_codex_flow(
                parent_id,
                flow,
                "waiting_code",
                email,
                detail=email,
                message="正在等待 Freemail 新验证码",
                auto_mode=True,
            )
            try:
                code_payload = mail_client.wait_for_verification_code(
                    email,
                    since_id=latest_email_id,
                    sender_keyword="openai",
                )
            except TimeoutError as exc:
                detail = str(exc)
                _set_main_codex_flow(
                    parent_id,
                    flow,
                    "code_required",
                    email,
                    detail=detail,
                    message="自动等待验证码超时",
                    auto_mode=True,
                )
                return {"status": "code_required", "main_codex": _main_codex_status()}
            except Exception as exc:
                detail = str(exc)
                _set_main_codex_flow(
                    parent_id,
                    flow,
                    "code_required",
                    email,
                    detail=detail,
                    message="自动读取验证码失败",
                    auto_mode=True,
                )
                return {"status": "code_required", "main_codex": _main_codex_status()}

            code_value = str((code_payload or {}).get("code") or "").strip()
            latest_email_id = max(latest_email_id, int((code_payload or {}).get("email_id") or 0))
            _set_main_codex_flow(
                parent_id,
                flow,
                "submitting_code",
                email,
                detail=f"email_id>{latest_email_id}",
                message="正在提交主号验证码",
                auto_mode=True,
            )
            try:
                result = _pw_executor.run(flow.submit_code, code_value)
            except Exception as exc:
                _abort_main_codex_flow()
                update_parent(parent_id, main_codex_error=str(exc), main_codex_error_stage="codex")
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            _set_main_codex_flow(
                parent_id,
                flow,
                result.get("step") or "starting",
                email,
                detail=result.get("detail"),
                auto_mode=True,
            )
            continue

        if step == "password_required":
            detail = detail or "当前页面需要密码继续主号同步"
            _set_main_codex_flow(
                parent_id,
                flow,
                "password_required",
                email,
                detail=detail,
                message="需要密码继续主号同步",
                auto_mode=True,
            )
            return {"status": "password_required", "main_codex": _main_codex_status()}

        _abort_main_codex_flow()
        update_parent(
            parent_id,
            main_codex_error=detail or f"无法识别主号同步状态: {step}",
            main_codex_error_stage="codex",
        )
        raise HTTPException(status_code=400, detail=detail or f"无法识别主号同步状态: {step}")

    _abort_main_codex_flow()
    update_parent(parent_id, main_codex_error="主号同步超时", main_codex_error_stage="codex")
    raise HTTPException(status_code=400, detail="主号同步超时")


@app.post("/api/parents/{parent_id}/main-codex/password")
def post_parent_main_codex_password(parent_id: str, params: PasswordParams):
    _ensure_main_codex_flow(parent_id)
    update_parent(parent_id, password=params.password)
    try:
        result = _pw_executor.run(_active_main_codex_flow.submit_password, params.password)
    except Exception as exc:
        _abort_main_codex_flow()
        update_parent(parent_id, main_codex_error=str(exc), main_codex_error_stage="codex")
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    step = result.get("step")
    if step == "completed":
        return _complete_main_codex(parent_id)
    _set_main_codex_flow(
        parent_id,
        _active_main_codex_flow,
        step,
        _active_main_codex_email,
        detail=result.get("detail"),
        auto_mode=False,
    )
    return {"status": step, "main_codex": _main_codex_status()}


@app.post("/api/parents/{parent_id}/main-codex/code")
def post_parent_main_codex_code(parent_id: str, params: CodeParams):
    _ensure_main_codex_flow(parent_id)
    try:
        result = _pw_executor.run(_active_main_codex_flow.submit_code, params.code)
    except Exception as exc:
        _abort_main_codex_flow()
        update_parent(parent_id, main_codex_error=str(exc), main_codex_error_stage="codex")
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    step = result.get("step")
    if step == "completed":
        return _complete_main_codex(parent_id)
    _set_main_codex_flow(
        parent_id,
        _active_main_codex_flow,
        step,
        _active_main_codex_email,
        detail=result.get("detail"),
        auto_mode=False,
    )
    return {"status": step, "main_codex": _main_codex_status()}


@app.post("/api/parents/{parent_id}/main-codex/cancel")
def post_parent_main_codex_cancel(parent_id: str):
    _ensure_main_codex_flow(parent_id)
    _abort_main_codex_flow()
    return {"status": "cancelled", "main_codex": _main_codex_status()}


@app.get("/api/parents/{parent_id}/team/invites")
def get_parent_team_invites(parent_id: str):
    parent = _get_team_parent_or_error(parent_id)
    _acquire_team_action_lock()
    try:
        def _load_invites():
            from autoteam.chatgpt_api import ChatGPTTeamAPI

            chatgpt = ChatGPTTeamAPI(
                session_token=parent["session_token"],
                account_id=parent["account_id"],
                workspace_name=parent.get("workspace_name", ""),
            )
            chatgpt.start()
            try:
                return chatgpt.list_invites()
            finally:
                chatgpt.stop()

        status_code, payload = _pw_executor.run(_load_invites)
        if status_code != 200:
            raise HTTPException(status_code=500, detail=f"读取待邀请列表失败: HTTP {status_code}")

        accounts = load_accounts()
        invites = _serialize_parent_invites(parent_id, _extract_team_invites(payload), accounts)
        return {"parent_id": parent_id, "invites": invites}
    finally:
        _release_playwright_lock()


@app.get("/api/parents/{parent_id}/team/blocked-members")
def get_parent_team_blocked_members(parent_id: str):
    _get_team_parent_or_error(parent_id)
    return {"parent_id": parent_id, "members": _serialize_blocked_members(parent_id, load_accounts())}


@app.post("/api/parents/{parent_id}/team/members/{child_id}/remove")
def post_parent_team_member_remove(parent_id: str, child_id: str):
    _get_team_parent_or_error(parent_id)
    _acquire_team_action_lock()
    try:
        from autoteam.manager import TeamMemberRemoveError, remove_blocked_child_from_team

        try:
            result = _pw_executor.run(remove_blocked_child_from_team, parent_id, child_id, timeout=None)
        except TeamMemberRemoveError as exc:
            detail = str(exc)
            if exc.remote_status:
                detail = {"message": detail, "remote_status": exc.remote_status}
            raise HTTPException(status_code=exc.status_code, detail=detail)

        child = {key: value for key, value in (result.get("child") or {}).items() if key != "password"}
        return {**{key: value for key, value in result.items() if key != "child"}, "child": child}
    finally:
        _release_playwright_lock()


@app.post("/api/parents/{parent_id}/team/blocked-members/remove-all")
def post_parent_team_blocked_members_remove_all(parent_id: str):
    _get_team_parent_or_error(parent_id)
    _acquire_team_action_lock()
    try:
        from autoteam.manager import TeamMemberRemoveError, remove_all_blocked_children_from_team

        try:
            return _pw_executor.run(remove_all_blocked_children_from_team, parent_id, timeout=None)
        except TeamMemberRemoveError as exc:
            detail = str(exc)
            if exc.remote_status:
                detail = {"message": detail, "remote_status": exc.remote_status}
            raise HTTPException(status_code=exc.status_code, detail=detail)
    finally:
        _release_playwright_lock()


@app.post("/api/parents/{parent_id}/team/invites/{invite_id}/cancel")
def post_parent_team_invite_cancel(parent_id: str, invite_id: str):
    parent = _get_team_parent_or_error(parent_id)
    invite_id = str(invite_id or "").strip()
    if not invite_id:
        raise HTTPException(status_code=400, detail="缺少 invite_id")

    _acquire_team_action_lock()
    try:
        def _cancel_invite():
            from autoteam.chatgpt_api import ChatGPTTeamAPI

            chatgpt = ChatGPTTeamAPI(
                session_token=parent["session_token"],
                account_id=parent["account_id"],
                workspace_name=parent.get("workspace_name", ""),
            )
            chatgpt.start()
            try:
                list_status, list_payload = chatgpt.list_invites()
                if list_status != 200:
                    return list_status, list_payload, None, None, None
                invites = _extract_team_invites(list_payload)
                remote_invite = next(
                    (
                        invite
                        for invite in invites
                        if str(invite.get("id") or "").strip() == invite_id
                    ),
                    None,
                )
                if not remote_invite:
                    return list_status, list_payload, None, None, len(invites)
                cancel_status, cancel_payload = chatgpt.cancel_invite(invite_id)
                pending_after = max(0, len(invites) - 1) if cancel_status in {200, 204} else len(invites)
                return list_status, remote_invite, cancel_status, cancel_payload, pending_after
            finally:
                chatgpt.stop()

        list_status, remote_invite, cancel_status, _cancel_payload, pending_after = _pw_executor.run(_cancel_invite)
        if list_status != 200:
            raise HTTPException(status_code=500, detail=f"读取待邀请列表失败: HTTP {list_status}")
        if not remote_invite:
            raise HTTPException(status_code=404, detail="待邀请记录不存在")
        if cancel_status not in {200, 204}:
            raise HTTPException(status_code=500, detail=f"取消邀请失败: HTTP {cancel_status}")
        update_parent(parent_id, remote_pending_count=pending_after or 0, last_reconciled_at=time.time(), last_reconcile_error="")

        email = _invite_email(remote_invite)
        accounts = load_accounts()
        local = _match_local_invite_record(
            accounts,
            parent_id=parent_id,
            invite_id=invite_id,
            email=email,
        )

        response = {
            "parent_id": parent_id,
            "invite_id": invite_id,
            "email": email,
            "cancelled": True,
        }
        if local:
            updated = (
                update_account_by_id(
                    local["id"],
                    status=STATUS_CANCELLED,
                    remote_state="absent",
                    error="",
                    error_stage="",
                    completed_at=time.time(),
                    last_remote_sync_at=time.time(),
                )
                or local
            )
            response["local_child_id"] = updated.get("id")
            response["local_status"] = updated.get("status")
        return response
    finally:
        _release_playwright_lock()


@app.post("/api/tasks/batch-run", status_code=202)
def post_batch_run():
    from autoteam.manager import run_batch

    return _start_task("batch-run", run_batch, {})


@app.post("/api/tasks/fill-all", status_code=202)
def post_fill_all():
    from autoteam.config import get_target_children_per_parent
    from autoteam.manager import run_fill_all

    return _start_task("fill-all", run_fill_all, {"target_per_parent": get_target_children_per_parent()})


@app.post("/api/tasks/check-child-health", status_code=202)
def post_check_child_health(params: ChildHealthCheckParams | None = None):
    from autoteam.manager import _normalize_health_concurrency, run_check_child_health

    concurrency = _normalize_health_concurrency(params.concurrency if params else None)

    def _run_check_child_health_task():
        return run_check_child_health(concurrency=concurrency)

    _run_check_child_health_task.__name__ = "run_check_child_health"
    return _start_task("check-child-health", _run_check_child_health_task, {"concurrency": concurrency})


@app.post("/api/tasks/delete-401-children", status_code=202)
def post_delete_401_children(params: ChildHealthCheckParams | None = None):
    from autoteam.manager import _normalize_health_concurrency, run_delete_401_children

    concurrency = _normalize_health_concurrency(params.concurrency if params else None)

    def _run_delete_401_children_task():
        return run_delete_401_children(concurrency=concurrency)

    _run_delete_401_children_task.__name__ = "run_delete_401_children"
    return _start_task("delete-401-children", _run_delete_401_children_task, {"concurrency": concurrency})


@app.post("/api/tasks/login-all-parents", status_code=202)
def post_login_all_parents(params: ParentLoginAllParams | None = None):
    concurrency = _normalize_login_concurrency(params.concurrency if params else None)

    def _run_login_all_parents_task():
        return run_login_all_parents(concurrency=concurrency)

    _run_login_all_parents_task.__name__ = "run_login_all_parents"
    return _start_task("login-all-parents", _run_login_all_parents_task, {"concurrency": concurrency})


@app.post("/api/tasks/clear-pending-invites", status_code=202)
def post_clear_pending_invites():
    from autoteam.manager import run_clear_pending_invites

    return _start_task("clear-pending-invites", run_clear_pending_invites, {})


@app.post("/api/tasks/repair-stuck-accounts", status_code=202)
def post_repair_stuck_accounts():
    from autoteam.manager import run_repair_stuck_accounts

    return _start_task("repair-stuck-accounts", run_repair_stuck_accounts, {})


@app.post("/api/tasks/repair-parent-child-links", status_code=202)
def post_repair_parent_child_links(params: ParentChildLinkRepairParams | None = None):
    from autoteam.manager import run_repair_parent_child_links

    concurrency = _normalize_login_concurrency(params.concurrency if params else None)

    def _run_repair_parent_child_links_task():
        return run_repair_parent_child_links(concurrency=concurrency)

    _run_repair_parent_child_links_task.__name__ = "run_repair_parent_child_links"
    return _start_task("repair-parent-child-links", _run_repair_parent_child_links_task, {"concurrency": concurrency})


@app.post("/api/cpa/resync")
def post_cpa_resync():
    from autoteam.cpa_sync import resync_ready_accounts

    return resync_ready_accounts()


_log_buffer: list[dict] = []
_LOG_BUFFER_MAX = 500


class _LogCollector(logging.Handler):
    def emit(self, record):
        entry = {"time": record.created, "level": record.levelname, "message": self.format(record)}
        _log_buffer.append(entry)
        if len(_log_buffer) > _LOG_BUFFER_MAX:
            del _log_buffer[: len(_log_buffer) - _LOG_BUFFER_MAX]


_log_collector = _LogCollector()
_log_collector.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger().addHandler(_log_collector)


@app.get("/api/logs")
def get_logs(limit: int = 100, since: float = 0):
    if since > 0:
        logs = [entry for entry in _log_buffer if entry["time"] > since]
    else:
        logs = _log_buffer[-limit:]
    return {"logs": logs, "total": len(_log_buffer)}


DIST_DIR = Path(__file__).parent / "web" / "dist"

if DIST_DIR.exists():
    assets_dir = DIST_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{path:path}")
    def serve_frontend(path: str):
        file = DIST_DIR / path
        if file.is_file() and ".." not in path:
            return FileResponse(str(file))
        return FileResponse(
            str(DIST_DIR / "index.html"),
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )


def start_server(host: str = "0.0.0.0", port: int = 8787):
    import uvicorn

    migrate_legacy_data()
    from autoteam.setup_wizard import check_and_setup

    check_and_setup(interactive=False)

    global API_KEY
    from autoteam.config import API_KEY as fresh_api_key

    API_KEY = fresh_api_key or os.environ.get("API_KEY", "")
    logger.info("[API] AutoTeam API started at http://%s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")
