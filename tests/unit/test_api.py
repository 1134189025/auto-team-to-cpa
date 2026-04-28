import pytest
from fastapi import HTTPException

from autoteam import api, chatgpt_api, codex_auth, freemail, manager, setup_wizard


def test_status_payload_uses_new_parent_child_summary(monkeypatch):
    monkeypatch.setattr(
        api,
        "load_parents",
        lambda: [
            {
                "id": "p1",
                "enabled": True,
                "email": "owner@example.com",
                "label": "A",
                "session_token": "s",
                "password": "",
                "account_id": "acc",
                "workspace_name": "WS",
                "default_batch_size": 1,
                "updated_at": 1,
                "last_run_at": 2,
                "last_run_status": "success",
                "last_run_created": 1,
                "last_run_failed": 0,
                "remote_member_count": 4,
                "drift_count": 2,
                "main_auth_file": "D:/autoteam/auths/codex-main-parent-1.json",
                "main_auth_plan_type": "team",
                "main_auth_refreshed_at": 10,
                "main_cpa_uploaded_at": 20,
                "main_codex_error": "",
                "main_codex_error_stage": "",
            },
        ],
    )
    monkeypatch.setattr(
        api,
        "load_accounts",
        lambda: [
            {"id": "c1", "parent_id": "p1", "status": "ready"},
            {"id": "c2", "status": "failed"},
            {"id": "c3", "status": "invited"},
            {"id": "c4", "status": "accepted"},
            {"id": "c5", "status": "auth_saved"},
            {"id": "c6", "parent_id": "p1", "status": "blocked", "remote_state": "member", "remote_member_id": "user-1"},
            {"id": "c7", "status": "removed"},
            {"id": "c8", "parent_id": "p1", "status": "ready", "health_status": "quota_exhausted"},
        ],
    )
    monkeypatch.setattr(api, "migrate_legacy_data", lambda: None)

    payload = api.get_status()

    assert payload["summary"] == {
        "enabled_parents": 1,
        "accepted_children": 1,
        "auth_saved_children": 1,
        "ready_children": 2,
        "blocked_children": 1,
        "pending_removal_children": 1,
        "removed_children": 1,
        "quota_exhausted_children": 1,
        "failed_children": 1,
        "invited_children": 1,
        "recoverable_children": 2,
        "drift_parents": 1,
        "last_batch_at": 2,
    }
    assert payload["parents"][0]["session_present"] is True
    assert payload["parents"][0]["blocked_member_count"] == 1
    assert payload["parents"][0]["pending_removal_count"] == 1
    assert payload["parents"][0]["usable_member_count"] == 3
    assert payload["parents"][0]["refill_gap_after_removal"] == 1
    assert payload["parents"][0]["quota_exhausted_count"] == 1
    assert payload["parents"][0]["main_auth_present"] is True
    assert payload["parents"][0]["main_auth_filename"] == "codex-main-parent-1.json"
    assert "main_auth_file" not in payload["parents"][0]


def test_setup_status_uses_freemail_keys(monkeypatch):
    monkeypatch.setattr(api, "API_KEY", "")
    result = api.get_setup_status()
    keys = {field["key"] for field in result["fields"]}
    assert "FREEMAIL_BASE_URL" in keys
    assert "FREEMAIL_ROOT_TOKEN" in keys
    assert all("value" not in field for field in result["fields"])


def test_get_settings_exposes_current_values(monkeypatch):
    monkeypatch.setattr(setup_wizard, "_read_env", lambda: {"FREEMAIL_BASE_URL": "http://freemail.local", "CPA_KEY": "secret-key"})
    monkeypatch.setenv("CPA_URL", "http://127.0.0.1:8317")

    result = api.get_settings()
    fields = {field["key"]: field for field in result["fields"]}

    assert fields["FREEMAIL_BASE_URL"]["value"] == "http://freemail.local"
    assert fields["CPA_KEY"]["value"] == "secret-key"
    assert fields["CPA_URL"]["value"] == "http://127.0.0.1:8317"


def test_parent_bulk_import_creates_and_updates_without_leaking_secrets(monkeypatch):
    parents = [
        {
            "id": "parent-1",
            "label": "Old",
            "email": "owner@example.com",
            "password": "old-pass",
            "session_token": "",
            "account_id": "",
            "workspace_name": "",
            "enabled": True,
            "default_batch_size": 1,
        }
    ]

    def fake_add_parent(**kwargs):
        parent = {"id": f"parent-{len(parents) + 1}", **kwargs}
        parents.append(parent)
        return parent

    def fake_update_parent(parent_id, **kwargs):
        for parent in parents:
            if parent["id"] == parent_id:
                parent.update(kwargs)
                return parent
        return None

    monkeypatch.setattr(api, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(api, "load_parents", lambda: parents)
    monkeypatch.setattr(api, "add_parent", fake_add_parent)
    monkeypatch.setattr(api, "update_parent", fake_update_parent)

    result = api.post_parents_bulk_import(
        api.ParentBulkImportParams(
            parents=[
                api.ParentBulkImportItem(
                    label="Updated",
                    email="OWNER@example.com",
                    password="new-pass",
                    default_batch_size=3,
                ),
                api.ParentBulkImportItem(
                    label="New",
                    email="new@example.com",
                    password="new-account-pass",
                    workspace_name="Team B",
                ),
                api.ParentBulkImportItem(email="new@example.com"),
            ]
        )
    )

    assert result["created"] == 1
    assert result["updated"] == 1
    assert result["skipped"] == 1
    assert result["failed"] == 0
    assert parents[0]["label"] == "Updated"
    assert parents[0]["password"] == "new-pass"
    assert parents[0]["default_batch_size"] == 3
    assert parents[1]["email"] == "new@example.com"
    assert parents[1]["workspace_name"] == "Team B"
    assert all("password" not in parent for parent in result["parents"])
    assert all("session_token" not in parent for parent in result["parents"])


def test_parent_bulk_import_preserves_existing_enabled_and_batch_when_omitted(monkeypatch):
    parents = [
        {
            "id": "parent-1",
            "label": "Old",
            "email": "owner@example.com",
            "password": "old-pass",
            "enabled": False,
            "default_batch_size": 9,
        }
    ]

    def fake_update_parent(parent_id, **kwargs):
        parents[0].update(kwargs)
        return parents[0]

    monkeypatch.setattr(api, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(api, "load_parents", lambda: parents)
    monkeypatch.setattr(api, "update_parent", fake_update_parent)

    result = api.post_parents_bulk_import(
        api.ParentBulkImportParams(
            parents=[
                api.ParentBulkImportItem(
                    email="owner@example.com",
                    password="new-pass",
                )
            ],
            default_batch_size=2,
        )
    )

    assert result["updated"] == 1
    assert parents[0]["password"] == "new-pass"
    assert parents[0]["enabled"] is False
    assert parents[0]["default_batch_size"] == 9


def test_start_task_rejects_when_pending_task_exists(monkeypatch):
    saved_tasks = dict(api._tasks)
    try:
        api._tasks.clear()

        class DummyThread:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                return None

        monkeypatch.setattr(api.threading, "Thread", DummyThread)

        first = api._start_task("batch-run", lambda: None, {})
        assert first["status"] == "pending"

        with pytest.raises(HTTPException) as exc_info:
            api._start_task("batch-run", lambda: None, {})
        assert exc_info.value.status_code == 409
    finally:
        api._tasks.clear()
        api._tasks.update(saved_tasks)


def test_post_fill_all_starts_fill_all_task(monkeypatch):
    captured = {}

    def fake_start_task(command, func, params):
        captured["command"] = command
        captured["func_name"] = func.__name__
        captured["params"] = params
        return {"task_id": "task-1", "command": command, "params": params}

    monkeypatch.setattr(api, "_start_task", fake_start_task)

    result = api.post_fill_all()

    assert captured == {
        "command": "fill-all",
        "func_name": "run_fill_all",
        "params": {"target_per_parent": 4},
    }
    assert result["task_id"] == "task-1"


def test_post_check_child_health_starts_task(monkeypatch):
    captured = {}

    def fake_start_task(command, func, params):
        captured["command"] = command
        captured["func_name"] = func.__name__
        captured["params"] = params
        return {"task_id": "task-health", "command": command, "params": params}

    monkeypatch.setattr(api, "_start_task", fake_start_task)

    result = api.post_check_child_health(api.ChildHealthCheckParams(concurrency="4"))

    assert captured == {
        "command": "check-child-health",
        "func_name": "run_check_child_health",
        "params": {"concurrency": 4},
    }
    assert result["task_id"] == "task-health"


def test_post_delete_401_children_starts_task(monkeypatch):
    captured = {}

    def fake_start_task(command, func, params):
        captured["command"] = command
        captured["func_name"] = func.__name__
        captured["params"] = params
        return {"task_id": "task-delete-401", "command": command, "params": params}

    monkeypatch.setattr(api, "_start_task", fake_start_task)

    result = api.post_delete_401_children(api.ChildHealthCheckParams(concurrency="4"))

    assert captured == {
        "command": "delete-401-children",
        "func_name": "run_delete_401_children",
        "params": {"concurrency": 4},
    }
    assert result["task_id"] == "task-delete-401"


def test_post_login_all_parents_starts_task(monkeypatch):
    captured = {}

    def fake_start_task(command, func, params):
        captured["command"] = command
        captured["func_name"] = func.__name__
        captured["params"] = params
        return {"task_id": "task-login-all", "command": command, "params": params}

    monkeypatch.setattr(api, "_start_task", fake_start_task)

    result = api.post_login_all_parents(api.ParentLoginAllParams(concurrency="3"))

    assert captured == {
        "command": "login-all-parents",
        "func_name": "run_login_all_parents",
        "params": {"concurrency": 3},
    }
    assert result["task_id"] == "task-login-all"


def test_login_all_concurrency_is_normalized():
    assert api._normalize_login_concurrency(None) == 2
    assert api._normalize_login_concurrency("bad") == 2
    assert api._normalize_login_concurrency(0) == 1
    assert api._normalize_login_concurrency(9) == 5
    assert api._normalize_login_concurrency("4") == 4


def test_run_login_all_parents_logs_in_completed_accounts_and_skips_manual_steps(monkeypatch):
    parents = [
        {
            "id": "parent-ok",
            "email": "ok@example.com",
            "password": "saved-pass",
            "workspace_name": "Team A",
            "enabled": True,
            "session_token": "",
            "account_id": "",
        },
        {
            "id": "parent-manual",
            "email": "manual@example.com",
            "password": "",
            "workspace_name": "",
            "enabled": True,
            "session_token": "",
            "account_id": "",
        },
        {
            "id": "parent-disabled",
            "email": "disabled@example.com",
            "enabled": False,
            "session_token": "",
            "account_id": "",
        },
        {
            "id": "parent-saved",
            "email": "saved@example.com",
            "enabled": True,
            "session_token": "session",
            "account_id": "acct-saved",
        },
    ]
    updates = []
    active = {"count": 0, "max": 0}
    active_lock = api.threading.Lock()
    main_thread_id = api.threading.get_ident()

    class DummyChatGPTTeamAPI:
        def __init__(self, workspace_name=""):
            self.workspace_name = workspace_name
            self.email = ""

        def auto_admin_login(self, email, password=""):
            self.email = email
            with active_lock:
                active["count"] += 1
                active["max"] = max(active["max"], active["count"])
            try:
                api.time.sleep(0.03)
                if email == "ok@example.com":
                    assert password == "saved-pass"
                    return {"step": "completed", "detail": ""}
                return {"step": "code_required", "detail": "需要验证码"}
            finally:
                with active_lock:
                    active["count"] -= 1

        def complete_admin_login(self):
            return {
                "email": self.email,
                "password": "saved-pass",
                "session_token": "new-session",
                "account_id": "acct-ok",
                "workspace_name": "Team A",
            }

        def stop(self):
            return None

    def fake_update_parent(parent_id, **kwargs):
        assert api.threading.get_ident() == main_thread_id
        updates.append((parent_id, kwargs))
        for parent in parents:
            if parent["id"] == parent_id:
                parent.update(kwargs)
                return parent
        return None

    monkeypatch.setattr(api, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(api, "load_parents", lambda: parents)
    monkeypatch.setattr(api, "update_parent", fake_update_parent)
    monkeypatch.setattr(chatgpt_api, "ChatGPTTeamAPI", DummyChatGPTTeamAPI)

    result = api.run_login_all_parents(concurrency=2)

    assert result["parents"] == 4
    assert result["attempted"] == 2
    assert result["logged_in"] == 1
    assert result["manual_required"] == 1
    assert result["failed"] == 0
    assert result["skipped"] == 2
    assert result["concurrency"] == 2
    assert active["max"] == 2
    assert {item["status"] for item in result["items"]} == {"logged_in", "manual_required", "skipped"}
    updates_by_parent = {parent_id: payload for parent_id, payload in updates}
    assert updates_by_parent["parent-ok"]["session_token"] == "new-session"
    assert updates_by_parent["parent-ok"]["last_run_status"] == "login_completed"
    assert updates_by_parent["parent-manual"]["last_run_status"] == "login_manual_required"


def test_run_login_all_parents_times_out_stuck_parent_and_continues(monkeypatch):
    parents = [
        {
            "id": "parent-stuck",
            "email": "stuck@example.com",
            "password": "",
            "workspace_name": "",
            "enabled": True,
            "session_token": "",
            "account_id": "",
        },
        {
            "id": "parent-ok",
            "email": "ok@example.com",
            "password": "",
            "workspace_name": "Team A",
            "enabled": True,
            "session_token": "",
            "account_id": "",
        },
    ]
    updates = []
    stop_calls = []
    started_stuck = api.threading.Event()

    class DummyChatGPTTeamAPI:
        def __init__(self, workspace_name=""):
            self.workspace_name = workspace_name
            self.email = ""
            self.stop_event = api.threading.Event()

        def auto_admin_login(self, email, password=""):
            del password
            self.email = email
            if email == "stuck@example.com":
                started_stuck.set()
                self.stop_event.wait(timeout=2)
                return {"step": "code_required", "detail": "late manual step"}
            return {"step": "completed", "detail": ""}

        def complete_admin_login(self):
            return {
                "email": self.email,
                "password": "",
                "session_token": "new-session",
                "account_id": "acct-ok",
                "workspace_name": "Team A",
            }

        def stop(self):
            stop_calls.append(self.email)
            self.stop_event.set()

    def fake_update_parent(parent_id, **kwargs):
        updates.append((parent_id, kwargs))
        for parent in parents:
            if parent["id"] == parent_id:
                parent.update(kwargs)
                return parent
        return None

    monkeypatch.setattr(api, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(api, "load_parents", lambda: parents)
    monkeypatch.setattr(api, "update_parent", fake_update_parent)
    monkeypatch.setattr(chatgpt_api, "ChatGPTTeamAPI", DummyChatGPTTeamAPI)

    result = api.run_login_all_parents(concurrency=1, parent_timeout_seconds=0.05, poll_interval_seconds=0.01)

    assert started_stuck.is_set()
    assert result["attempted"] == 2
    assert result["logged_in"] == 1
    assert result["failed"] == 1
    assert result["timed_out"] == 1
    items_by_parent = {item["parent_id"]: item for item in result["items"]}
    assert items_by_parent["parent-stuck"]["step"] == "timeout"
    assert items_by_parent["parent-ok"]["status"] == "logged_in"
    assert "stuck@example.com" in stop_calls
    updates_by_parent = {parent_id: payload for parent_id, payload in updates}
    assert updates_by_parent["parent-stuck"]["last_run_status"] == "login_failed_timeout"
    assert updates_by_parent["parent-ok"]["last_run_status"] == "login_completed"


def test_post_clear_pending_invites_starts_task(monkeypatch):
    captured = {}

    def fake_start_task(command, func, params):
        captured["command"] = command
        captured["func_name"] = func.__name__
        captured["params"] = params
        return {"task_id": "task-2", "command": command, "params": params}

    monkeypatch.setattr(api, "_start_task", fake_start_task)

    result = api.post_clear_pending_invites()

    assert captured == {
        "command": "clear-pending-invites",
        "func_name": "run_clear_pending_invites",
        "params": {},
    }
    assert result["task_id"] == "task-2"


def test_post_repair_stuck_accounts_starts_task(monkeypatch):
    captured = {}

    def fake_start_task(command, func, params):
        captured["command"] = command
        captured["func_name"] = func.__name__
        captured["params"] = params
        return {"task_id": "task-3", "command": command, "params": params}

    monkeypatch.setattr(api, "_start_task", fake_start_task)

    result = api.post_repair_stuck_accounts()

    assert captured == {
        "command": "repair-stuck-accounts",
        "func_name": "run_repair_stuck_accounts",
        "params": {},
    }
    assert result["task_id"] == "task-3"


def test_post_repair_parent_child_links_starts_task(monkeypatch):
    captured = {}

    def fake_start_task(command, func, params):
        captured["command"] = command
        captured["func_name"] = func.__name__
        captured["params"] = params
        return {"task_id": "task-links", "command": command, "params": params}

    monkeypatch.setattr(api, "_start_task", fake_start_task)

    result = api.post_repair_parent_child_links(api.ParentChildLinkRepairParams(concurrency="4"))

    assert captured == {
        "command": "repair-parent-child-links",
        "func_name": "run_repair_parent_child_links",
        "params": {"concurrency": 4},
    }
    assert result["task_id"] == "task-links"


def test_login_password_error_clears_flow_and_releases_lock(monkeypatch):
    saved_flow = api._active_login_flow
    saved_parent_id = api._active_login_parent_id
    saved_step = api._active_login_step
    saved_email = api._active_login_email
    saved_detail = api._active_login_detail
    saved_message = api._active_login_message
    saved_auto_mode = api._active_login_auto_mode

    class DummyFlow:
        def __init__(self):
            self.stopped = False

        def submit_admin_password(self, _password):
            raise RuntimeError("boom")

        def stop(self):
            self.stopped = True

    flow = DummyFlow()
    api._set_login_flow("parent-1", flow, "password_required", "owner@example.com")
    api._playwright_lock.acquire()
    monkeypatch.setattr(api._pw_executor, "run", lambda func, *args, **kwargs: func(*args))

    try:
        with pytest.raises(HTTPException) as exc_info:
            api.post_parent_login_password("parent-1", api.PasswordParams(password="bad"))
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "boom"
        assert api._active_login_flow is None
        assert api._active_login_parent_id is None
        assert api._active_login_step is None
        assert api._active_login_email == ""
        assert flow.stopped is True
        assert not api._playwright_lock.locked()
    finally:
        if api._playwright_lock.locked():
            api._playwright_lock.release()
        api._active_login_flow = saved_flow
        api._active_login_parent_id = saved_parent_id
        api._active_login_step = saved_step
        api._active_login_email = saved_email
        api._active_login_detail = saved_detail
        api._active_login_message = saved_message
        api._active_login_auto_mode = saved_auto_mode


def test_verify_cpa_requires_key(monkeypatch):
    monkeypatch.setenv("CPA_URL", "http://127.0.0.1:8317")
    monkeypatch.delenv("CPA_KEY", raising=False)
    assert setup_wizard._verify_cpa() is False


def test_login_start_surfaces_executor_errors(monkeypatch):
    saved_flow = api._active_login_flow
    saved_parent_id = api._active_login_parent_id
    saved_step = api._active_login_step
    saved_email = api._active_login_email
    saved_detail = api._active_login_detail
    saved_message = api._active_login_message
    saved_auto_mode = api._active_login_auto_mode

    monkeypatch.setattr(api, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(api, "load_parents", lambda: [{"id": "parent-1", "email": "owner@example.com"}])
    monkeypatch.setattr(api, "find_parent", lambda parents, parent_id: parents[0] if parent_id == "parent-1" else None)
    monkeypatch.setattr(api._pw_executor, "run", lambda func, *args, **kwargs: (_ for _ in ()).throw(RuntimeError("launch failed")))

    try:
        if api._playwright_lock.locked():
            api._playwright_lock.release()
        with pytest.raises(HTTPException) as exc_info:
            api.post_parent_login_start("parent-1", api.ParentLoginStartParams(email="owner@example.com"))
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "launch failed"
        assert api._active_login_flow is None
        assert not api._playwright_lock.locked()
    finally:
        if api._playwright_lock.locked():
            api._playwright_lock.release()
        api._active_login_flow = saved_flow
        api._active_login_parent_id = saved_parent_id
        api._active_login_step = saved_step
        api._active_login_email = saved_email
        api._active_login_detail = saved_detail
        api._active_login_message = saved_message
        api._active_login_auto_mode = saved_auto_mode


def test_get_parent_team_invites_uses_parent_session_and_marks_local_invites(monkeypatch):
    saved_tasks = dict(api._tasks)
    captured = {}

    class DummyChatGPTTeamAPI:
        def __init__(self, *, session_token="", account_id="", workspace_name="", persist_callback=None):
            captured["session_token"] = session_token
            captured["account_id"] = account_id
            captured["workspace_name"] = workspace_name

        def start(self):
            captured["started"] = True

        def list_invites(self):
            return (
                200,
                {
                    "account_invites": [
                        {"id": "invite-local", "email_address": "local@example.com", "role": "standard-user"},
                        {"id": "invite-external", "email_address": "external@example.com", "role": "standard-user"},
                    ]
                },
            )

        def stop(self):
            captured["stopped"] = True

    monkeypatch.setattr(api, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(api, "load_parents", lambda: [{"id": "parent-1", "session_token": "session-1", "account_id": "acct-1", "workspace_name": "WS"}])
    monkeypatch.setattr(api, "find_parent", lambda parents, parent_id: parents[0] if parent_id == "parent-1" else None)
    monkeypatch.setattr(
        api,
        "load_accounts",
        lambda: [
            {"id": "child-1", "parent_id": "parent-1", "email": "local@example.com", "invite_id": "invite-local", "status": "invited"},
        ],
    )
    monkeypatch.setattr(api._pw_executor, "run", lambda func, *args, **kwargs: func(*args))
    monkeypatch.setattr(chatgpt_api, "ChatGPTTeamAPI", DummyChatGPTTeamAPI)

    try:
        api._tasks.clear()
        if api._playwright_lock.locked():
            api._playwright_lock.release()

        result = api.get_parent_team_invites("parent-1")

        assert captured["session_token"] == "session-1"
        assert captured["account_id"] == "acct-1"
        assert captured["started"] is True
        assert captured["stopped"] is True
        local = next(invite for invite in result["invites"] if invite["email"] == "local@example.com")
        external = next(invite for invite in result["invites"] if invite["email"] == "external@example.com")
        assert local["is_local"] is True
        assert local["local_child_id"] == "child-1"
        assert local["local_status"] == "invited"
        assert external["is_local"] is False
        assert external["local_child_id"] is None
    finally:
        if api._playwright_lock.locked():
            api._playwright_lock.release()
        api._tasks.clear()
        api._tasks.update(saved_tasks)


def test_cancel_parent_team_invite_updates_local_child_status(monkeypatch):
    saved_tasks = dict(api._tasks)
    updates = []
    parent_updates = []

    class DummyChatGPTTeamAPI:
        def __init__(self, *, session_token="", account_id="", workspace_name="", persist_callback=None):
            self.session_token = session_token

        def start(self):
            return None

        def list_invites(self):
            return (
                200,
                {"account_invites": [{"id": "invite-local", "email_address": "local@example.com", "role": "standard-user"}]},
            )

        def cancel_invite(self, invite_id):
            assert invite_id == "invite-local"
            return 204, ""

        def stop(self):
            return None

    monkeypatch.setattr(api, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(api, "load_parents", lambda: [{"id": "parent-1", "session_token": "session-1", "account_id": "acct-1", "workspace_name": "WS"}])
    monkeypatch.setattr(api, "find_parent", lambda parents, parent_id: parents[0] if parent_id == "parent-1" else None)
    monkeypatch.setattr(
        api,
        "load_accounts",
        lambda: [
            {"id": "child-1", "parent_id": "parent-1", "email": "local@example.com", "invite_id": "invite-local", "status": "invited"},
        ],
    )
    monkeypatch.setattr(
        api,
        "update_account_by_id",
        lambda account_id, **kwargs: updates.append((account_id, kwargs)) or {"id": account_id, **kwargs},
    )
    monkeypatch.setattr(api, "update_parent", lambda parent_id, **kwargs: parent_updates.append((parent_id, kwargs)))
    monkeypatch.setattr(api._pw_executor, "run", lambda func, *args, **kwargs: func(*args))
    monkeypatch.setattr(chatgpt_api, "ChatGPTTeamAPI", DummyChatGPTTeamAPI)

    try:
        api._tasks.clear()
        if api._playwright_lock.locked():
            api._playwright_lock.release()

        result = api.post_parent_team_invite_cancel("parent-1", "invite-local")

        assert result["cancelled"] is True
        assert result["local_child_id"] == "child-1"
        assert result["local_status"] == "cancelled"
        assert updates[0][0] == "child-1"
        assert updates[0][1]["status"] == "cancelled"
        assert "completed_at" in updates[0][1]
        assert parent_updates[-1][1]["remote_pending_count"] == 0
    finally:
        if api._playwright_lock.locked():
            api._playwright_lock.release()
        api._tasks.clear()
        api._tasks.update(saved_tasks)


def test_cancel_parent_team_invite_does_not_create_local_record_for_external_invite(monkeypatch):
    saved_tasks = dict(api._tasks)
    update_called = {"value": False}
    parent_updates = []

    class DummyChatGPTTeamAPI:
        def __init__(self, *, session_token="", account_id="", workspace_name="", persist_callback=None):
            return None

        def start(self):
            return None

        def list_invites(self):
            return (
                200,
                {"account_invites": [{"id": "invite-external", "email_address": "external@example.com", "role": "standard-user"}]},
            )

        def cancel_invite(self, invite_id):
            assert invite_id == "invite-external"
            return 204, ""

        def stop(self):
            return None

    monkeypatch.setattr(api, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(api, "load_parents", lambda: [{"id": "parent-1", "session_token": "session-1", "account_id": "acct-1", "workspace_name": "WS"}])
    monkeypatch.setattr(api, "find_parent", lambda parents, parent_id: parents[0] if parent_id == "parent-1" else None)
    monkeypatch.setattr(api, "load_accounts", lambda: [])
    monkeypatch.setattr(api, "update_account_by_id", lambda *args, **kwargs: update_called.update(value=True))
    monkeypatch.setattr(api, "update_parent", lambda parent_id, **kwargs: parent_updates.append((parent_id, kwargs)))
    monkeypatch.setattr(api._pw_executor, "run", lambda func, *args, **kwargs: func(*args))
    monkeypatch.setattr(chatgpt_api, "ChatGPTTeamAPI", DummyChatGPTTeamAPI)

    try:
        api._tasks.clear()
        if api._playwright_lock.locked():
            api._playwright_lock.release()

        result = api.post_parent_team_invite_cancel("parent-1", "invite-external")

        assert result == {
            "parent_id": "parent-1",
            "invite_id": "invite-external",
            "email": "external@example.com",
            "cancelled": True,
        }
        assert update_called["value"] is False
        assert parent_updates[-1][1]["remote_pending_count"] == 0
    finally:
        if api._playwright_lock.locked():
            api._playwright_lock.release()
        api._tasks.clear()
        api._tasks.update(saved_tasks)


def test_get_parent_team_invites_returns_409_when_system_busy(monkeypatch):
    saved_tasks = dict(api._tasks)
    monkeypatch.setattr(api, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(api, "load_parents", lambda: [{"id": "parent-1", "session_token": "session-1", "account_id": "acct-1"}])
    monkeypatch.setattr(api, "find_parent", lambda parents, parent_id: parents[0] if parent_id == "parent-1" else None)

    try:
        api._tasks.clear()
        api._playwright_lock.acquire()
        with pytest.raises(HTTPException) as exc_info:
            api.get_parent_team_invites("parent-1")
        assert exc_info.value.status_code == 409
    finally:
        if api._playwright_lock.locked():
            api._playwright_lock.release()
        api._tasks.clear()
        api._tasks.update(saved_tasks)


def test_get_parent_team_invites_requires_logged_in_parent(monkeypatch):
    monkeypatch.setattr(api, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(api, "load_parents", lambda: [{"id": "parent-1", "session_token": "", "account_id": ""}])
    monkeypatch.setattr(api, "find_parent", lambda parents, parent_id: parents[0] if parent_id == "parent-1" else None)

    with pytest.raises(HTTPException) as exc_info:
        api.get_parent_team_invites("parent-1")

    assert exc_info.value.status_code == 400


def test_get_parent_team_blocked_members_lists_only_parent_blocked(monkeypatch):
    monkeypatch.setattr(api, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(api, "load_parents", lambda: [{"id": "parent-1", "session_token": "session-1", "account_id": "acct-1"}])
    monkeypatch.setattr(api, "find_parent", lambda parents, parent_id: parents[0] if parent_id == "parent-1" else None)
    monkeypatch.setattr(
        api,
        "load_accounts",
        lambda: [
            {
                "id": "child-1",
                "parent_id": "parent-1",
                "email": "blocked@example.com",
                "status": "blocked",
                "remote_state": "member",
                "remote_member_id": "user-1",
                "health_status": "auth_error",
            },
            {"id": "child-2", "parent_id": "parent-1", "email": "ready@example.com", "status": "ready"},
            {"id": "child-3", "parent_id": "parent-2", "email": "other@example.com", "status": "blocked"},
            {"id": "child-4", "parent_id": "parent-1", "email": "absent@example.com", "status": "blocked", "remote_state": "absent"},
        ],
    )

    result = api.get_parent_team_blocked_members("parent-1")

    assert [member["id"] for member in result["members"]] == ["child-1"]
    assert result["members"][0]["health_status"] == "auth_error"


def test_post_parent_team_member_remove_maps_manager_error(monkeypatch):
    saved_tasks = dict(api._tasks)
    captured = {}

    def fake_remove(parent_id, child_id):
        assert parent_id == "parent-1"
        assert child_id == "child-1"
        raise manager.TeamMemberRemoveError("remote failed", status_code=502, remote_status=405)

    monkeypatch.setattr(api, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(api, "load_parents", lambda: [{"id": "parent-1", "session_token": "session-1", "account_id": "acct-1"}])
    monkeypatch.setattr(api, "find_parent", lambda parents, parent_id: parents[0] if parent_id == "parent-1" else None)

    def fake_run(func, *args, **kwargs):
        captured["timeout"] = kwargs.get("timeout", "missing")
        return fake_remove(*args)

    monkeypatch.setattr(api._pw_executor, "run", fake_run)
    monkeypatch.setattr(manager, "remove_blocked_child_from_team", fake_remove)

    try:
        api._tasks.clear()
        if api._playwright_lock.locked():
            api._playwright_lock.release()

        with pytest.raises(HTTPException) as exc_info:
            api.post_parent_team_member_remove("parent-1", "child-1")

        assert exc_info.value.status_code == 502
        assert exc_info.value.detail == {"message": "remote failed", "remote_status": 405}
        assert captured["timeout"] is None
    finally:
        if api._playwright_lock.locked():
            api._playwright_lock.release()
        api._tasks.clear()
        api._tasks.update(saved_tasks)


def test_post_parent_team_blocked_members_remove_all_runs_manager(monkeypatch):
    saved_tasks = dict(api._tasks)
    captured = {}

    def fake_remove_all(parent_id):
        assert parent_id == "parent-1"
        return {"parent_id": parent_id, "attempted": 2, "removed": 2, "failed": 0, "items": []}

    monkeypatch.setattr(api, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(api, "load_parents", lambda: [{"id": "parent-1", "session_token": "session-1", "account_id": "acct-1"}])
    monkeypatch.setattr(api, "find_parent", lambda parents, parent_id: parents[0] if parent_id == "parent-1" else None)

    def fake_run(func, *args, **kwargs):
        captured["timeout"] = kwargs.get("timeout", "missing")
        return fake_remove_all(*args)

    monkeypatch.setattr(api._pw_executor, "run", fake_run)
    monkeypatch.setattr(manager, "remove_all_blocked_children_from_team", fake_remove_all)

    try:
        api._tasks.clear()
        if api._playwright_lock.locked():
            api._playwright_lock.release()

        result = api.post_parent_team_blocked_members_remove_all("parent-1")

        assert result["attempted"] == 2
        assert result["removed"] == 2
        assert captured["timeout"] is None
    finally:
        if api._playwright_lock.locked():
            api._playwright_lock.release()
        api._tasks.clear()
        api._tasks.update(saved_tasks)


def test_codex_auth_mail_helpers_support_freemail_fields():
    item = {"id": "12", "sender": "team@openai.com"}
    assert codex_auth._mail_item_id(item) == 12
    assert codex_auth._mail_item_sender(item) == "team@openai.com"


def test_check_codex_quota_treats_429_as_exhausted(monkeypatch):
    class DummyResponse:
        status_code = 429
        text = '{"code":"usage_limit_reached"}'

        def json(self):
            return {"code": "usage_limit_reached"}

    class DummyRequests:
        @staticmethod
        def get(*args, **kwargs):
            return DummyResponse()

    monkeypatch.setitem(__import__("sys").modules, "requests", DummyRequests)

    status, info = codex_auth.check_codex_quota("token", account_id="acct-1")

    assert status == "exhausted"
    assert info["status_code"] == 429
    assert info["code"] == "usage_limit_reached"


def test_codex_auth_fill_otp_input_prefers_multi_cell_inputs():
    class FakeField:
        def __init__(self):
            self.value = None

        def is_visible(self, timeout=0):
            return True

        def click(self, timeout=0):
            return None

        def fill(self, value):
            self.value = value

    class FakeCollection:
        def __init__(self, fields):
            self.fields = fields

        def count(self):
            return len(self.fields)

        def nth(self, index):
            return self.fields[index]

        @property
        def first(self):
            return self.fields[0]

    class FakePage:
        def __init__(self, fields):
            self.fields = fields

        def locator(self, selector):
            if selector == 'input[maxlength="1"]':
                return FakeCollection(self.fields)
            return FakeCollection([])

    fields = [FakeField() for _ in range(6)]
    page = FakePage(fields)

    first = codex_auth._fill_otp_input(page, "123456")

    assert first is fields[0]
    assert [field.value for field in fields] == list("123456")


def test_login_status_exposes_detail_message_and_auto_mode():
    saved_flow = api._active_login_flow
    saved_parent_id = api._active_login_parent_id
    saved_step = api._active_login_step
    saved_email = api._active_login_email
    saved_detail = api._active_login_detail
    saved_message = api._active_login_message
    saved_auto_mode = api._active_login_auto_mode

    try:
        api._set_login_flow(
            "parent-1",
            object(),
            "waiting_code",
            "owner@example.com",
            detail="waiting for mail",
            message="正在等待验证码",
            auto_mode=True,
        )

        status = api._login_status()

        assert status["detail"] == "waiting for mail"
        assert status["message"] == "正在等待验证码"
        assert status["auto_mode"] is True
    finally:
        api._active_login_flow = saved_flow
        api._active_login_parent_id = saved_parent_id
        api._active_login_step = saved_step
        api._active_login_email = saved_email
        api._active_login_detail = saved_detail
        api._active_login_message = saved_message
        api._active_login_auto_mode = saved_auto_mode


def test_post_parent_login_start_auto_login_completes(monkeypatch):
    saved_flow = api._active_login_flow
    saved_parent_id = api._active_login_parent_id
    saved_step = api._active_login_step
    saved_email = api._active_login_email
    saved_detail = api._active_login_detail
    saved_message = api._active_login_message
    saved_auto_mode = api._active_login_auto_mode
    updates = []
    captured = {}

    class DummyChatGPTTeamAPI:
        def __init__(self, *, session_token="", account_id="", workspace_name="", persist_callback=None):
            del session_token, account_id, persist_callback
            self.workspace_name = workspace_name
            self.progress_callback = None

        def auto_admin_login(self, email, password="", mail_client=None, code_timeout=None):
            del mail_client, code_timeout
            captured["email"] = email
            captured["password"] = password
            captured["workspace_name"] = self.workspace_name
            self.progress_callback(step="waiting_code", detail=email, message="正在等待验证码")
            return {"step": "completed", "detail": "done"}

        def complete_admin_login(self):
            return {
                "email": captured["email"],
                "password": captured["password"],
                "session_token": "session-1",
                "account_id": "acct-1",
                "workspace_name": "Team Alpha",
            }

        def stop(self):
            captured["stopped"] = True

    monkeypatch.setattr(api, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(
        api,
        "load_parents",
        lambda: [{"id": "parent-1", "email": "owner@example.com", "password": "saved-pass", "workspace_name": "Team Alpha"}],
    )
    monkeypatch.setattr(api, "find_parent", lambda parents, parent_id: parents[0] if parent_id == "parent-1" else None)
    monkeypatch.setattr(api, "update_parent", lambda parent_id, **kwargs: updates.append((parent_id, kwargs)) or {"id": parent_id, **kwargs})
    monkeypatch.setattr(api._pw_executor, "run", lambda func, *args, **kwargs: func(*args))
    monkeypatch.setattr(chatgpt_api, "ChatGPTTeamAPI", DummyChatGPTTeamAPI)

    try:
        if api._playwright_lock.locked():
            api._playwright_lock.release()

        result = api.post_parent_login_start("parent-1", api.ParentLoginStartParams(email="owner@example.com"))

        assert result["status"] == "completed"
        assert result["info"]["session_token"] == "session-1"
        assert captured["email"] == "owner@example.com"
        assert captured["password"] == "saved-pass"
        assert captured["workspace_name"] == "Team Alpha"
        assert updates == [
            (
                "parent-1",
                {
                    "email": "owner@example.com",
                    "password": "saved-pass",
                    "session_token": "session-1",
                    "account_id": "acct-1",
                    "workspace_name": "Team Alpha",
                },
            )
        ]
        assert result["login"]["in_progress"] is False
        assert captured["stopped"] is True
    finally:
        if api._playwright_lock.locked():
            api._playwright_lock.release()
        api._active_login_flow = saved_flow
        api._active_login_parent_id = saved_parent_id
        api._active_login_step = saved_step
        api._active_login_email = saved_email
        api._active_login_detail = saved_detail
        api._active_login_message = saved_message
        api._active_login_auto_mode = saved_auto_mode


def test_post_parent_login_start_returns_blocking_status_with_progress(monkeypatch):
    saved_flow = api._active_login_flow
    saved_parent_id = api._active_login_parent_id
    saved_step = api._active_login_step
    saved_email = api._active_login_email
    saved_detail = api._active_login_detail
    saved_message = api._active_login_message
    saved_auto_mode = api._active_login_auto_mode

    class DummyChatGPTTeamAPI:
        def __init__(self, *, session_token="", account_id="", workspace_name="", persist_callback=None):
            del session_token, account_id, workspace_name, persist_callback
            self.progress_callback = None
            self.workspace_options_cache = [{"id": "w1", "label": "Team One", "kind": "preferred"}]

        def auto_admin_login(self, email, password="", mail_client=None, code_timeout=None):
            del email, password, mail_client, code_timeout
            self.progress_callback(step="waiting_code", detail="owner@example.com", message="正在等待验证码")
            self.progress_callback(step="code_required", detail="Freemail 无法读取验证码", message="需要手动输入验证码")
            return {"step": "code_required", "detail": "Freemail 无法读取验证码"}

        def stop(self):
            return None

    monkeypatch.setattr(api, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(api, "load_parents", lambda: [{"id": "parent-1", "email": "owner@example.com", "password": ""}])
    monkeypatch.setattr(api, "find_parent", lambda parents, parent_id: parents[0] if parent_id == "parent-1" else None)
    monkeypatch.setattr(api._pw_executor, "run", lambda func, *args, **kwargs: func(*args))
    monkeypatch.setattr(chatgpt_api, "ChatGPTTeamAPI", DummyChatGPTTeamAPI)

    try:
        if api._playwright_lock.locked():
            api._playwright_lock.release()

        result = api.post_parent_login_start("parent-1", api.ParentLoginStartParams(email="owner@example.com"))

        assert result["status"] == "code_required"
        assert result["login"]["step"] == "code_required"
        assert result["login"]["detail"] == "Freemail 无法读取验证码"
        assert result["login"]["message"] == "需要手动输入验证码"
        assert result["login"]["auto_mode"] is True
        assert result["login"]["workspace_options"] == [{"id": "w1", "label": "Team One", "kind": "preferred"}]
    finally:
        api._abort_login_flow()
        if api._playwright_lock.locked():
            api._playwright_lock.release()
        api._active_login_flow = saved_flow
        api._active_login_parent_id = saved_parent_id
        api._active_login_step = saved_step
        api._active_login_email = saved_email
        api._active_login_detail = saved_detail
        api._active_login_message = saved_message
        api._active_login_auto_mode = saved_auto_mode


def test_post_parent_main_codex_start_completes_and_updates_parent(monkeypatch):
    saved_flow = api._active_main_codex_flow
    saved_parent_id = api._active_main_codex_parent_id
    saved_step = api._active_main_codex_step
    saved_email = api._active_main_codex_email
    saved_detail = api._active_main_codex_detail
    saved_message = api._active_main_codex_message
    saved_auto_mode = api._active_main_codex_auto_mode
    captured = {}
    updates = []

    class DummyFreemailClient:
        def get_latest_email_id(self, email, sender_keyword=None):
            captured["freemail_email"] = email
            captured["sender_keyword"] = sender_keyword
            return 0

    class DummyFlow:
        def __init__(self, *, parent_id, email, session_token, account_id, workspace_name="", password=""):
            captured["flow"] = {
                "parent_id": parent_id,
                "email": email,
                "session_token": session_token,
                "account_id": account_id,
                "workspace_name": workspace_name,
                "password": password,
            }

        def start(self):
            return {"step": "completed", "detail": None}

        def complete(self):
            return {
                "parent_id": "parent-1",
                "email": "owner@example.com",
                "auth_file": "D:/autoteam/auths/codex-main-parent-1.json",
                "plan_type": "team",
            }

        def stop(self):
            captured["stopped"] = True

    monkeypatch.setattr(api, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(
        api,
        "load_parents",
        lambda: [
            {
                "id": "parent-1",
                "email": "owner@example.com",
                "session_token": "session-1",
                "account_id": "acct-1",
                "workspace_name": "Team Alpha",
                "password": "saved-pass",
            }
        ],
    )
    monkeypatch.setattr(api, "find_parent", lambda parents, parent_id: parents[0] if parent_id == "parent-1" else None)
    monkeypatch.setattr(api._pw_executor, "run", lambda func, *args, **kwargs: func(*args))
    monkeypatch.setattr(codex_auth, "ParentMainCodexSyncFlow", DummyFlow)
    monkeypatch.setattr(freemail, "FreemailClient", DummyFreemailClient)
    monkeypatch.setattr(
        "autoteam.cpa_sync.sync_parent_main_auth_to_cpa",
        lambda parent_id, auth_file: {
            "parent_id": parent_id,
            "auth_file": auth_file,
            "filename": "codex-main-parent-1.json",
            "deleted_existing": False,
            "uploaded": True,
        },
    )
    monkeypatch.setattr(
        api,
        "update_parent",
        lambda parent_id, **kwargs: updates.append((parent_id, kwargs)) or {"id": parent_id, **kwargs},
    )

    try:
        if api._playwright_lock.locked():
            api._playwright_lock.release()

        result = api.post_parent_main_codex_start("parent-1")

        assert result["status"] == "completed"
        assert captured["flow"] == {
            "parent_id": "parent-1",
            "email": "owner@example.com",
            "session_token": "session-1",
            "account_id": "acct-1",
            "workspace_name": "Team Alpha",
            "password": "saved-pass",
        }
        assert result["sync"]["filename"] == "codex-main-parent-1.json"
        assert updates[-1][0] == "parent-1"
        assert updates[-1][1]["main_auth_file"] == "D:/autoteam/auths/codex-main-parent-1.json"
        assert updates[-1][1]["main_auth_plan_type"] == "team"
        assert updates[-1][1]["main_codex_error"] == ""
        assert captured["stopped"] is True
    finally:
        if api._playwright_lock.locked():
            api._playwright_lock.release()
        api._active_main_codex_flow = saved_flow
        api._active_main_codex_parent_id = saved_parent_id
        api._active_main_codex_step = saved_step
        api._active_main_codex_email = saved_email
        api._active_main_codex_detail = saved_detail
        api._active_main_codex_message = saved_message
        api._active_main_codex_auto_mode = saved_auto_mode


def test_post_parent_main_codex_start_returns_code_required_when_mail_unavailable(monkeypatch):
    saved_flow = api._active_main_codex_flow
    saved_parent_id = api._active_main_codex_parent_id
    saved_step = api._active_main_codex_step
    saved_email = api._active_main_codex_email
    saved_detail = api._active_main_codex_detail
    saved_message = api._active_main_codex_message
    saved_auto_mode = api._active_main_codex_auto_mode

    class DummyFlow:
        def __init__(self, *, parent_id, email, session_token, account_id, workspace_name="", password=""):
            del parent_id, email, session_token, account_id, workspace_name, password

        def start(self):
            return {"step": "code_required", "detail": "需要新的 OpenAI 验证码"}

        def stop(self):
            return None

    monkeypatch.setattr(api, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(
        api,
        "load_parents",
        lambda: [{"id": "parent-1", "email": "owner@example.com", "session_token": "session-1", "account_id": "acct-1"}],
    )
    monkeypatch.setattr(api, "find_parent", lambda parents, parent_id: parents[0] if parent_id == "parent-1" else None)
    monkeypatch.setattr(api._pw_executor, "run", lambda func, *args, **kwargs: func(*args))
    monkeypatch.setattr(codex_auth, "ParentMainCodexSyncFlow", DummyFlow)
    monkeypatch.setattr(freemail, "FreemailClient", lambda: (_ for _ in ()).throw(RuntimeError("freemail down")))

    try:
        if api._playwright_lock.locked():
            api._playwright_lock.release()

        result = api.post_parent_main_codex_start("parent-1")

        assert result["status"] == "code_required"
        assert result["main_codex"]["step"] == "code_required"
        assert result["main_codex"]["auto_mode"] is True
        assert "Freemail" in result["main_codex"]["detail"]
    finally:
        api._abort_main_codex_flow()
        if api._playwright_lock.locked():
            api._playwright_lock.release()
        api._active_main_codex_flow = saved_flow
        api._active_main_codex_parent_id = saved_parent_id
        api._active_main_codex_step = saved_step
        api._active_main_codex_email = saved_email
        api._active_main_codex_detail = saved_detail
        api._active_main_codex_message = saved_message
        api._active_main_codex_auto_mode = saved_auto_mode
