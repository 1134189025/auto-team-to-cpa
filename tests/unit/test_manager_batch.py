import threading

import pytest

from autoteam import manager


def test_generate_local_part_uses_plain_hex_without_prefix():
    local = manager._generate_local_part()

    assert len(local) == 10
    assert local.isalnum()
    assert local == local.lower()
    assert "-" not in local
    int(local, 16)


def test_run_batch_iterates_all_enabled_parents(monkeypatch):
    parents = [
        {"id": "p1", "label": "A", "default_batch_size": 2, "session_token": "s", "account_id": "a", "workspace_name": "A"},
        {"id": "p2", "label": "B", "default_batch_size": 1, "session_token": "s", "account_id": "b", "workspace_name": "B"},
    ]
    calls = []
    updates = []

    monkeypatch.setattr(manager, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(manager, "check_and_setup", lambda interactive=False: True)
    monkeypatch.setattr(manager, "get_enabled_parents", lambda: parents)
    monkeypatch.setattr(manager, "FreemailClient", lambda: object())
    monkeypatch.setattr(manager, "provision_single_account", lambda parent, mail_client: calls.append(parent["id"]) or {"id": parent["id"]})
    monkeypatch.setattr(manager, "update_parent", lambda parent_id, **kwargs: updates.append((parent_id, kwargs)))

    result = manager.run_batch()

    assert calls == ["p1", "p1", "p2"]
    assert result == {"created": 3, "failed": 0, "parents": 2}
    assert updates[0][0] == "p1"
    assert updates[1][0] == "p2"


def test_reconcile_parent_team_state_upgrades_failed_member_to_accepted(monkeypatch):
    parent = {"id": "p1", "label": "A", "email": "owner@example.com"}
    state_accounts = [
        {
            "id": "child-1",
            "parent_id": "p1",
            "email": "member@example.com",
            "status": manager.STATUS_FAILED,
            "error": "registration failed",
            "remote_state": manager.REMOTE_STATE_UNKNOWN,
            "remote_member_id": "",
            "enabled": True,
        }
    ]
    parent_updates = []

    def fake_load_accounts():
        return [dict(account) for account in state_accounts]

    def fake_update_account_by_id(account_id, **kwargs):
        for account in state_accounts:
            if account["id"] == account_id:
                account.update(kwargs)
                return dict(account)
        return None

    monkeypatch.setattr(manager, "load_accounts", fake_load_accounts)
    monkeypatch.setattr(manager, "update_account_by_id", fake_update_account_by_id)
    monkeypatch.setattr(
        manager,
        "_fetch_parent_team_state",
        lambda _parent: {
            "members": [{"email": "member@example.com", "user_id": "member-1"}],
            "invites": [],
        },
    )
    monkeypatch.setattr(manager, "update_parent", lambda parent_id, **kwargs: parent_updates.append((parent_id, kwargs)))

    result = manager.reconcile_parent_team_state(parent, log_prefix="test")

    assert result["ok"] is True
    assert result["remote_member_count"] == 1
    assert result["recoverable_count"] == 1
    assert state_accounts[0]["status"] == manager.STATUS_ACCEPTED
    assert state_accounts[0]["remote_state"] == manager.REMOTE_STATE_MEMBER
    assert state_accounts[0]["remote_member_id"] == "member-1"
    assert state_accounts[0]["accepted_at"] is not None
    assert parent_updates[-1][1]["remote_member_count"] == 1


def test_reconcile_parent_team_state_keeps_blocked_member_occupying_slot(monkeypatch):
    parent = {"id": "p1", "label": "A", "email": "owner@example.com"}
    state_accounts = [
        {
            "id": "child-blocked",
            "parent_id": "p1",
            "email": "blocked@example.com",
            "status": manager.STATUS_BLOCKED,
            "remote_state": manager.REMOTE_STATE_MEMBER,
            "remote_member_id": "old-id",
            "enabled": True,
        }
    ]

    monkeypatch.setattr(manager, "load_accounts", lambda: [dict(account) for account in state_accounts])

    def fake_update_account_by_id(account_id, **kwargs):
        state_accounts[0].update(kwargs)
        return dict(state_accounts[0])

    monkeypatch.setattr(manager, "update_account_by_id", fake_update_account_by_id)
    monkeypatch.setattr(
        manager,
        "_fetch_parent_team_state",
        lambda _parent: {"members": [{"email": "blocked@example.com", "user_id": "member-1"}], "invites": []},
    )
    monkeypatch.setattr(manager, "update_parent", lambda parent_id, **kwargs: None)

    result = manager.reconcile_parent_team_state(parent, log_prefix="test")

    assert result["remote_member_count"] == 1
    assert state_accounts[0]["status"] == manager.STATUS_BLOCKED
    assert state_accounts[0]["remote_state"] == manager.REMOTE_STATE_MEMBER
    assert state_accounts[0]["remote_member_id"] == "member-1"


def test_reconcile_parent_team_state_marks_blocked_absent_as_removed(monkeypatch):
    parent = {"id": "p1", "label": "A", "email": "owner@example.com"}
    state_accounts = [
        {
            "id": "child-blocked",
            "parent_id": "p1",
            "email": "blocked@example.com",
            "status": manager.STATUS_BLOCKED,
            "remote_state": manager.REMOTE_STATE_MEMBER,
            "enabled": True,
        }
    ]

    monkeypatch.setattr(manager, "load_accounts", lambda: [dict(account) for account in state_accounts])

    def fake_update_account_by_id(account_id, **kwargs):
        state_accounts[0].update(kwargs)
        return dict(state_accounts[0])

    monkeypatch.setattr(manager, "update_account_by_id", fake_update_account_by_id)
    monkeypatch.setattr(manager, "_fetch_parent_team_state", lambda _parent: {"members": [], "invites": []})
    monkeypatch.setattr(manager, "update_parent", lambda parent_id, **kwargs: None)

    manager.reconcile_parent_team_state(parent, log_prefix="test")

    assert state_accounts[0]["status"] == manager.STATUS_REMOVED
    assert state_accounts[0]["remote_state"] == manager.REMOTE_STATE_ABSENT
    assert state_accounts[0]["removed_reason"] == "remote_absent_after_blocked"


def test_reconcile_parent_team_state_keeps_recent_invited_absent_settling(monkeypatch):
    parent = {"id": "p1", "label": "A", "email": "owner@example.com"}
    state_accounts = [
        {
            "id": "child-invited",
            "parent_id": "p1",
            "email": "new@example.com",
            "status": manager.STATUS_INVITED,
            "remote_state": manager.REMOTE_STATE_PENDING_INVITE,
            "invite_sent_at": 1_000.0,
            "error": manager.STALE_INVITE_ERROR,
            "error_stage": manager.ERROR_STAGE_RECONCILE,
            "completed_at": 123.0,
            "enabled": True,
        }
    ]
    parent_updates = []

    monkeypatch.setattr(manager.time, "time", lambda: 1_010.0)
    monkeypatch.setattr(manager, "load_accounts", lambda: [dict(account) for account in state_accounts])

    def fake_update_account_by_id(account_id, **kwargs):
        state_accounts[0].update(kwargs)
        return dict(state_accounts[0])

    monkeypatch.setattr(manager, "update_account_by_id", fake_update_account_by_id)
    monkeypatch.setattr(manager, "_fetch_parent_team_state", lambda _parent: {"members": [], "invites": []})
    monkeypatch.setattr(manager, "update_parent", lambda parent_id, **kwargs: parent_updates.append((parent_id, kwargs)))

    result = manager.reconcile_parent_team_state(parent, log_prefix="test")

    assert result["remote_occupied_count"] == 1
    assert result["settling_invite_count"] == 1
    assert state_accounts[0]["status"] == manager.STATUS_INVITED
    assert state_accounts[0]["remote_state"] == manager.REMOTE_STATE_ABSENT
    assert state_accounts[0]["error"] == ""
    assert state_accounts[0]["error_stage"] == ""
    assert state_accounts[0]["completed_at"] is None
    assert parent_updates[-1][1]["remote_member_count"] == 0


def test_run_fill_all_counts_remote_occupied_and_recovers_before_create(monkeypatch):
    parents = [
        {
            "id": "p1",
            "label": "A",
            "email": "owner@example.com",
            "workspace_name": "A",
            "session_token": "s",
            "account_id": "acct-1",
        }
    ]
    state_accounts = [
        {
            "id": "child-accepted",
            "parent_id": "p1",
            "email": "accepted@example.com",
            "status": manager.STATUS_ACCEPTED,
            "remote_state": manager.REMOTE_STATE_MEMBER,
            "enabled": True,
        },
        {
            "id": "child-auth",
            "parent_id": "p1",
            "email": "auth@example.com",
            "status": manager.STATUS_AUTH_SAVED,
            "remote_state": manager.REMOTE_STATE_MEMBER,
            "enabled": True,
        },
    ]
    resumed = []

    def fake_load_accounts():
        return [dict(account) for account in state_accounts]

    def fake_update_account_by_id(account_id, **kwargs):
        for account in state_accounts:
            if account["id"] == account_id:
                account.update(kwargs)
                return dict(account)
        return None

    monkeypatch.setattr(manager, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(manager, "check_and_setup", lambda interactive=False: True)
    monkeypatch.setattr(manager, "get_enabled_parents", lambda: parents)
    monkeypatch.setattr(manager, "FreemailClient", lambda: object())
    monkeypatch.setattr(manager, "load_accounts", fake_load_accounts)
    monkeypatch.setattr(manager, "update_account_by_id", fake_update_account_by_id)
    monkeypatch.setattr(
        manager,
        "reconcile_parent_team_state",
        lambda parent, log_prefix="fill-all": {
            "ok": True,
            "error": "",
            "members": [],
            "invites": [],
            "remote_pending_count": 0,
            "remote_member_count": 4,
            "remote_occupied_count": 4,
            "recoverable_count": sum(1 for account in state_accounts if account["status"] in {manager.STATUS_ACCEPTED, manager.STATUS_AUTH_SAVED}),
            "drift_count": 0,
        },
    )

    def fake_resume_account(parent, account_id, mail_client):
        resumed.append(account_id)
        return fake_update_account_by_id(account_id, status=manager.STATUS_READY, completed_at=123.0)

    monkeypatch.setattr(manager, "resume_account", fake_resume_account)
    monkeypatch.setattr(manager, "provision_single_account", lambda parent, mail_client: (_ for _ in ()).throw(AssertionError("should not create")))
    monkeypatch.setattr(manager, "update_parent", lambda parent_id, **kwargs: None)

    result = manager.run_fill_all(target_per_parent=4)

    assert result == {
        "created": 0,
        "recovered": 2,
        "failed": 0,
        "parents": 1,
        "planned": 0,
        "target_per_parent": 4,
        "blocked_parents": 0,
        "remote_occupied": 4,
    }
    assert resumed == ["child-auth", "child-accepted"]


def test_run_fill_all_blocks_when_reconcile_fails(monkeypatch):
    parents = [
        {
            "id": "p1",
            "label": "A",
            "email": "owner@example.com",
            "workspace_name": "A",
            "session_token": "s",
            "account_id": "acct-1",
        }
    ]

    monkeypatch.setattr(manager, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(manager, "check_and_setup", lambda interactive=False: True)
    monkeypatch.setattr(manager, "get_enabled_parents", lambda: parents)
    monkeypatch.setattr(manager, "FreemailClient", lambda: object())
    monkeypatch.setattr(
        manager,
        "reconcile_parent_team_state",
        lambda parent, log_prefix="fill-all": {
            "ok": False,
            "error": "members api failed",
            "members": [],
            "invites": [],
            "remote_pending_count": 0,
            "remote_member_count": 0,
            "remote_occupied_count": 0,
            "recoverable_count": 0,
            "drift_count": 0,
        },
    )
    monkeypatch.setattr(manager, "update_parent", lambda parent_id, **kwargs: None)
    monkeypatch.setattr(manager, "provision_single_account", lambda parent, mail_client: (_ for _ in ()).throw(AssertionError("should not create")))

    result = manager.run_fill_all(target_per_parent=4)

    assert result["created"] == 0
    assert result["recovered"] == 0
    assert result["failed"] == 1
    assert result["blocked_parents"] == 1


def test_run_repair_stuck_accounts_does_not_create_new_invites(monkeypatch):
    parents = [
        {
            "id": "p1",
            "label": "A",
            "email": "owner@example.com",
            "workspace_name": "A",
            "session_token": "s",
            "account_id": "acct-1",
        }
    ]
    state_accounts = [
        {
            "id": "child-accepted",
            "parent_id": "p1",
            "email": "accepted@example.com",
            "status": manager.STATUS_ACCEPTED,
            "remote_state": manager.REMOTE_STATE_MEMBER,
            "enabled": True,
        },
        {
            "id": "child-auth",
            "parent_id": "p1",
            "email": "auth@example.com",
            "status": manager.STATUS_AUTH_SAVED,
            "remote_state": manager.REMOTE_STATE_MEMBER,
            "enabled": True,
        },
    ]
    resumed = []

    def fake_load_accounts():
        return [dict(account) for account in state_accounts]

    def fake_update_account_by_id(account_id, **kwargs):
        for account in state_accounts:
            if account["id"] == account_id:
                account.update(kwargs)
                return dict(account)
        return None

    monkeypatch.setattr(manager, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(manager, "check_and_setup", lambda interactive=False: True)
    monkeypatch.setattr(manager, "get_enabled_parents", lambda: parents)
    monkeypatch.setattr(manager, "FreemailClient", lambda: object())
    monkeypatch.setattr(manager, "load_accounts", fake_load_accounts)
    monkeypatch.setattr(manager, "update_account_by_id", fake_update_account_by_id)
    monkeypatch.setattr(
        manager,
        "reconcile_parent_team_state",
        lambda parent, log_prefix="repair": {
            "ok": True,
            "error": "",
            "members": [],
            "invites": [],
            "remote_pending_count": 0,
            "remote_member_count": 2,
            "remote_occupied_count": 2,
            "recoverable_count": 2,
            "drift_count": 0,
        },
    )

    def fake_resume_account(parent, account_id, mail_client):
        resumed.append(account_id)
        return fake_update_account_by_id(account_id, status=manager.STATUS_READY, completed_at=123.0)

    monkeypatch.setattr(manager, "resume_account", fake_resume_account)
    monkeypatch.setattr(manager, "provision_single_account", lambda parent, mail_client: (_ for _ in ()).throw(AssertionError("should not create")))
    monkeypatch.setattr(manager, "update_parent", lambda parent_id, **kwargs: None)

    result = manager.run_repair_stuck_accounts()

    assert result == {
        "created": 0,
        "recovered": 2,
        "failed": 0,
        "parents": 1,
        "blocked_parents": 0,
        "remote_occupied": 2,
    }
    assert resumed == ["child-auth", "child-accepted"]


def test_run_check_child_health_reconciles_then_checks_ready_members(monkeypatch):
    parents = [{"id": "p1", "label": "A", "email": "owner@example.com", "session_token": "s", "account_id": "acct-1"}]
    state_accounts = [
        {
            "id": "child-ready",
            "parent_id": "p1",
            "email": "ready@example.com",
            "status": manager.STATUS_READY,
            "remote_state": manager.REMOTE_STATE_MEMBER,
            "enabled": True,
        },
        {
            "id": "child-accepted",
            "parent_id": "p1",
            "email": "accepted@example.com",
            "status": manager.STATUS_ACCEPTED,
            "remote_state": manager.REMOTE_STATE_MEMBER,
            "enabled": True,
        },
    ]
    checked = []

    monkeypatch.setattr(manager, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(manager, "check_and_setup", lambda interactive=False: True)
    monkeypatch.setattr(manager, "get_enabled_parents", lambda: parents)
    monkeypatch.setattr(
        manager,
        "reconcile_parent_team_state",
        lambda parent, log_prefix="health": {
            "ok": True,
            "error": "",
            "members": [],
            "invites": [],
            "remote_pending_count": 0,
            "remote_member_count": 1,
            "remote_occupied_count": 1,
            "recoverable_count": 0,
            "drift_count": 0,
        },
    )
    monkeypatch.setattr(manager, "load_accounts", lambda: [dict(account) for account in state_accounts])

    from autoteam import health

    def fake_check(account):
        checked.append(account["id"])
        return {"outcome": "healthy", "account_id": account["id"]}

    monkeypatch.setattr(health, "check_child_account_health", fake_check)

    result = manager.run_check_child_health()

    assert checked == ["child-ready"]
    assert result["checked"] == 1
    assert result["healthy"] == 1
    assert result["skipped"] == 0
    assert result["concurrency"] == 5


def test_run_check_child_health_checks_children_in_parallel(monkeypatch):
    parents = [{"id": "p1", "label": "A", "email": "owner@example.com", "session_token": "s", "account_id": "acct-1"}]
    state_accounts = [
        {
            "id": f"child-{index}",
            "parent_id": "p1",
            "email": f"child-{index}@example.com",
            "status": manager.STATUS_READY,
            "remote_state": manager.REMOTE_STATE_MEMBER,
            "enabled": True,
        }
        for index in range(3)
    ]
    active = {"count": 0, "max": 0}
    active_lock = threading.Lock()

    monkeypatch.setattr(manager, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(manager, "check_and_setup", lambda interactive=False: True)
    monkeypatch.setattr(manager, "get_enabled_parents", lambda: parents)
    monkeypatch.setattr(
        manager,
        "reconcile_parent_team_state",
        lambda parent, log_prefix="health": {
            "ok": True,
            "error": "",
            "members": [],
            "invites": [],
            "remote_pending_count": 0,
            "remote_member_count": 3,
            "remote_occupied_count": 3,
            "recoverable_count": 0,
            "drift_count": 0,
        },
    )
    monkeypatch.setattr(manager, "load_accounts", lambda: [dict(account) for account in state_accounts])

    from autoteam import health

    def fake_check(account):
        with active_lock:
            active["count"] += 1
            active["max"] = max(active["max"], active["count"])
        try:
            manager.time.sleep(0.03)
            return {"outcome": "healthy", "account_id": account["id"]}
        finally:
            with active_lock:
                active["count"] -= 1

    monkeypatch.setattr(health, "check_child_account_health", fake_check)

    result = manager.run_check_child_health(concurrency=3)

    assert result["checked"] == 3
    assert result["healthy"] == 3
    assert result["concurrency"] == 3
    assert active["max"] == 3


def test_remove_blocked_child_from_team_deletes_remote_and_marks_removed(monkeypatch):
    parent = {"id": "p1", "email": "owner@example.com", "session_token": "s", "account_id": "acct-1", "workspace_name": "Team A"}
    state_accounts = [
        {
            "id": "child-blocked",
            "parent_id": "p1",
            "email": "blocked@example.com",
            "status": manager.STATUS_BLOCKED,
            "remote_state": manager.REMOTE_STATE_MEMBER,
            "remote_member_id": "user-1",
            "enabled": True,
        }
    ]
    captured = {}
    parent_updates = []

    class DummyChatGPTTeamAPI:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        def start(self):
            captured["started"] = True

        def list_members(self):
            return 200, {"items": [{"email": "blocked@example.com", "user_id": "user-1", "role": "standard-user"}]}

        def remove_member(self, user_id):
            captured["removed_user_id"] = user_id
            return 204, ""

        def stop(self):
            captured["stopped"] = True

    monkeypatch.setattr(manager, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(manager, "load_parents", lambda: [parent])
    monkeypatch.setattr(manager, "ChatGPTTeamAPI", DummyChatGPTTeamAPI)
    monkeypatch.setattr(
        manager,
        "reconcile_parent_team_state",
        lambda _parent, log_prefix="remove-blocked": {
            "ok": True,
            "error": "",
            "members": [],
            "invites": [],
            "remote_pending_count": 0,
            "remote_member_count": 1,
            "remote_occupied_count": 1,
            "recoverable_count": 0,
            "drift_count": 0,
        },
    )
    monkeypatch.setattr(manager, "load_accounts", lambda: [dict(account) for account in state_accounts])
    monkeypatch.setattr(manager, "update_parent", lambda parent_id, **kwargs: parent_updates.append((parent_id, kwargs)))

    def fake_update_account_by_id(account_id, **kwargs):
        state_accounts[0].update(kwargs)
        return dict(state_accounts[0])

    monkeypatch.setattr(manager, "update_account_by_id", fake_update_account_by_id)

    result = manager.remove_blocked_child_from_team("p1", "child-blocked")

    assert result["removed"] is True
    assert captured["removed_user_id"] == "user-1"
    assert captured["stopped"] is True
    assert state_accounts[0]["status"] == manager.STATUS_REMOVED
    assert state_accounts[0]["remote_state"] == manager.REMOTE_STATE_ABSENT
    assert state_accounts[0]["removed_reason"] == "blocked_auth"
    assert parent_updates[-1][1]["remote_member_count"] == 0


def test_remove_blocked_child_from_team_refuses_owner_role(monkeypatch):
    parent = {"id": "p1", "email": "owner@example.com", "session_token": "s", "account_id": "acct-1"}
    state_accounts = [
        {
            "id": "child-blocked",
            "parent_id": "p1",
            "email": "blocked@example.com",
            "status": manager.STATUS_BLOCKED,
            "remote_state": manager.REMOTE_STATE_MEMBER,
            "remote_member_id": "user-1",
            "enabled": True,
        }
    ]

    class DummyChatGPTTeamAPI:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

        def list_members(self):
            return 200, {"items": [{"email": "blocked@example.com", "user_id": "user-1", "role": "owner"}]}

        def remove_member(self, user_id):
            raise AssertionError("should not remove owner")

        def stop(self):
            pass

    monkeypatch.setattr(manager, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(manager, "load_parents", lambda: [parent])
    monkeypatch.setattr(manager, "ChatGPTTeamAPI", DummyChatGPTTeamAPI)
    monkeypatch.setattr(manager, "load_accounts", lambda: [dict(account) for account in state_accounts])
    monkeypatch.setattr(manager, "update_account_by_id", lambda account_id, **kwargs: state_accounts[0].update(kwargs) or dict(state_accounts[0]))
    monkeypatch.setattr(manager, "update_parent", lambda parent_id, **kwargs: None)
    monkeypatch.setattr(
        manager,
        "reconcile_parent_team_state",
        lambda _parent, log_prefix="remove-blocked": {
            "ok": True,
            "error": "",
            "members": [],
            "invites": [],
            "remote_pending_count": 0,
            "remote_member_count": 1,
            "remote_occupied_count": 1,
            "recoverable_count": 0,
            "drift_count": 0,
        },
    )

    with pytest.raises(manager.TeamMemberRemoveError):
        manager.remove_blocked_child_from_team("p1", "child-blocked")

    assert state_accounts[0]["status"] == manager.STATUS_BLOCKED


def test_remove_all_blocked_children_from_team_only_attempts_blocked_members(monkeypatch):
    parent = {"id": "p1", "email": "owner@example.com", "session_token": "s", "account_id": "acct-1"}
    state_accounts = [
        {
            "id": "blocked-a",
            "parent_id": "p1",
            "email": "a@example.com",
            "status": manager.STATUS_BLOCKED,
            "remote_state": manager.REMOTE_STATE_MEMBER,
            "remote_member_id": "user-a",
        },
        {
            "id": "blocked-absent",
            "parent_id": "p1",
            "email": "absent@example.com",
            "status": manager.STATUS_BLOCKED,
            "remote_state": manager.REMOTE_STATE_ABSENT,
        },
        {
            "id": "ready-member",
            "parent_id": "p1",
            "email": "ready@example.com",
            "status": manager.STATUS_READY,
            "remote_state": manager.REMOTE_STATE_MEMBER,
        },
        {
            "id": "blocked-b",
            "parent_id": "p1",
            "email": "b@example.com",
            "status": manager.STATUS_BLOCKED,
            "remote_state": manager.REMOTE_STATE_MEMBER,
            "remote_member_id": "user-b",
        },
        {
            "id": "other-parent",
            "parent_id": "p2",
            "email": "other@example.com",
            "status": manager.STATUS_BLOCKED,
            "remote_state": manager.REMOTE_STATE_MEMBER,
        },
    ]
    calls = []

    def fake_remove_blocked_child(parent_id, child_id):
        calls.append((parent_id, child_id))
        if child_id == "blocked-b":
            raise manager.TeamMemberRemoveError("remote failed", status_code=502, remote_status=500)
        return {"already_absent": False, "remote_status": 204}

    monkeypatch.setattr(manager, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(manager, "load_parents", lambda: [parent])
    monkeypatch.setattr(manager, "load_accounts", lambda: [dict(account) for account in state_accounts])
    monkeypatch.setattr(manager, "remove_blocked_child_from_team", fake_remove_blocked_child)

    result = manager.remove_all_blocked_children_from_team("p1")

    assert calls == [("p1", "blocked-a"), ("p1", "blocked-b")]
    assert result["attempted"] == 2
    assert result["removed"] == 1
    assert result["failed"] == 1
    assert result["items"][0]["status"] == "removed"
    assert result["items"][1]["status"] == "failed"
    assert result["items"][1]["remote_status"] == 500


def test_run_delete_401_children_checks_then_removes_enabled_blocked_members(monkeypatch):
    parents = [
        {"id": "p1", "email": "one@example.com", "enabled": True},
        {"id": "p2", "email": "two@example.com", "enabled": True},
    ]
    accounts = [
        {"id": "blocked-1", "parent_id": "p1", "status": manager.STATUS_BLOCKED, "remote_state": manager.REMOTE_STATE_MEMBER},
        {"id": "ready-1", "parent_id": "p1", "status": manager.STATUS_READY, "remote_state": manager.REMOTE_STATE_MEMBER},
        {"id": "blocked-absent", "parent_id": "p1", "status": manager.STATUS_BLOCKED, "remote_state": manager.REMOTE_STATE_ABSENT},
        {"id": "blocked-2", "parent_id": "p2", "status": manager.STATUS_BLOCKED, "remote_state": manager.REMOTE_STATE_MEMBER},
        {"id": "disabled-blocked", "parent_id": "p3", "status": manager.STATUS_BLOCKED, "remote_state": manager.REMOTE_STATE_MEMBER},
    ]
    calls = []

    def fake_run_check_child_health(concurrency):
        assert concurrency == 1
        return {
            "parents": 2,
            "checked": 4,
            "healthy": 1,
            "quota_exhausted": 1,
            "blocked": 2,
            "check_failed": 0,
            "skipped": 0,
            "blocked_parents": 0,
            "concurrency": concurrency,
        }

    def fake_remove_all(parent_id):
        calls.append(parent_id)
        if parent_id == "p2":
            return {
                "parent_id": parent_id,
                "attempted": 1,
                "removed": 0,
                "failed": 1,
                "items": [{"child_id": "blocked-2", "email": "two@example.com", "status": "failed"}],
            }
        return {
            "parent_id": parent_id,
            "attempted": 1,
            "removed": 1,
            "failed": 0,
            "items": [{"child_id": "blocked-1", "email": "one@example.com", "status": "removed"}],
        }

    monkeypatch.setattr(manager, "run_check_child_health", fake_run_check_child_health)
    monkeypatch.setattr(manager, "get_enabled_parents", lambda: parents)
    monkeypatch.setattr(manager, "load_accounts", lambda: [dict(account) for account in accounts])
    monkeypatch.setattr(manager, "remove_all_blocked_children_from_team", fake_remove_all)

    result = manager.run_delete_401_children(concurrency=1)

    assert calls == ["p1", "p2"]
    assert result["checked"] == 4
    assert result["blocked"] == 2
    assert result["quota_exhausted"] == 1
    assert result["remove_parents"] == 2
    assert result["remove_attempted"] == 2
    assert result["removed"] == 1
    assert result["remove_failed"] == 1
    assert [item["child_id"] for item in result["items"]] == ["blocked-1", "blocked-2"]


def test_run_clear_pending_invites_cancels_pending_and_recovers_remote_members(monkeypatch):
    parents = [
        {"id": "p1", "label": "A", "workspace_name": "A", "email": "a@example.com", "session_token": "s", "account_id": "acc-1"},
    ]
    state_accounts = [
        {
            "id": "child-local",
            "parent_id": "p1",
            "email": "local@example.com",
            "invite_id": "invite-local",
            "status": manager.STATUS_INVITED,
            "remote_state": manager.REMOTE_STATE_PENDING_INVITE,
            "enabled": True,
        },
        {
            "id": "child-failed",
            "parent_id": "p1",
            "email": "member@example.com",
            "invite_id": "invite-member",
            "status": manager.STATUS_FAILED,
            "remote_state": manager.REMOTE_STATE_UNKNOWN,
            "enabled": True,
        },
    ]
    cancelled = []

    class DummyChatGPTTeamAPI:
        def __init__(self, *, session_token="", account_id="", workspace_name="", persist_callback=None):
            self.account_id = account_id

        def start(self):
            return None

        def list_invites(self):
            return (
                200,
                {
                    "account_invites": [
                        {"id": "invite-local", "email_address": "local@example.com", "role": "standard-user"},
                    ]
                },
            )

        def cancel_invite(self, invite_id):
            cancelled.append((self.account_id, invite_id))
            return 204, ""

        def stop(self):
            return None

    def fake_load_accounts():
        return [dict(account) for account in state_accounts]

    def fake_update_account_by_id(account_id, **kwargs):
        for account in state_accounts:
            if account["id"] == account_id:
                account.update(kwargs)
                return dict(account)
        return None

    def fake_reconcile(parent, log_prefix="clear-pending"):
        fake_update_account_by_id(
            "child-failed",
            status=manager.STATUS_ACCEPTED,
            remote_state=manager.REMOTE_STATE_MEMBER,
            remote_member_id="member-1",
            accepted_at=123.0,
        )
        return {
            "ok": True,
            "error": "",
            "members": [{"email": "member@example.com"}],
            "invites": [],
            "remote_pending_count": 0,
            "remote_member_count": 1,
            "remote_occupied_count": 1,
            "recoverable_count": 1,
            "drift_count": 0,
        }

    monkeypatch.setattr(manager, "migrate_legacy_data", lambda: None)
    monkeypatch.setattr(manager, "load_parents", lambda: parents)
    monkeypatch.setattr(manager, "load_accounts", fake_load_accounts)
    monkeypatch.setattr(manager, "update_account_by_id", fake_update_account_by_id)
    monkeypatch.setattr(manager, "ChatGPTTeamAPI", DummyChatGPTTeamAPI)
    monkeypatch.setattr(manager, "reconcile_parent_team_state", fake_reconcile)

    result = manager.run_clear_pending_invites()

    assert result == {
        "parents": 1,
        "processed_parents": 1,
        "found": 1,
        "cancelled": 1,
        "failed": 0,
        "skipped_unready": 0,
        "stale_local": 1,
        "stale_failed": 1,
        "recovered": 1,
        "remote_members": 1,
    }
    assert cancelled == [("acc-1", "invite-local")]
    assert state_accounts[0]["status"] == manager.STATUS_CANCELLED
    assert state_accounts[1]["status"] == manager.STATUS_ACCEPTED


def test_run_repair_parent_child_links_relinks_old_parent_records_and_auth_only_members(monkeypatch):
    parents = [
        {
            "id": "p-new",
            "email": "owner@example.com",
            "workspace_name": "Team A",
            "enabled": True,
            "session_token": "session-new",
            "account_id": "acct-new",
        },
        {
            "id": "p-other",
            "email": "other@example.com",
            "workspace_name": "Team B",
            "enabled": True,
            "session_token": "session-other",
            "account_id": "acct-other",
        },
    ]
    state_accounts = [
        {
            "id": "child-old-parent",
            "email": "child@example.com",
            "parent_id": "p-old",
            "parent_email": "owner@example.com",
            "workspace_name": "",
            "status": manager.STATUS_FAILED,
            "remote_state": manager.REMOTE_STATE_ABSENT,
            "remote_member_id": "",
            "enabled": True,
            "auth_file": "D:/autoteam/auths/codex-child@example.com-old.json",
            "cpa_uploaded_at": None,
            "error": "remote member no longer present",
            "error_stage": manager.ERROR_STAGE_RECONCILE,
        },
        {
            "id": "child-pending",
            "email": "pending@example.com",
            "parent_id": "",
            "parent_email": "",
            "workspace_name": "",
            "status": manager.STATUS_FAILED,
            "remote_state": manager.REMOTE_STATE_ABSENT,
            "remote_member_id": "",
            "enabled": True,
        },
        {
            "id": "child-conflict",
            "email": "conflict@example.com",
            "parent_id": "p-other",
            "parent_email": "other@example.com",
            "workspace_name": "Team B",
            "status": manager.STATUS_READY,
            "remote_state": manager.REMOTE_STATE_MEMBER,
            "remote_member_id": "user-conflict-old",
            "enabled": True,
        },
    ]
    added_accounts = []
    active = {"count": 0, "max": 0}
    active_lock = threading.Lock()

    def fake_load_accounts():
        return [dict(account) for account in state_accounts]

    def fake_update_account_by_id(account_id, **kwargs):
        for account in state_accounts:
            if account["id"] == account_id:
                account.update(kwargs)
                return dict(account)
        return None

    def fake_add_account(email, password, **kwargs):
        account = {"id": f"added-{len(added_accounts) + 1}", "email": email, "password": password, **kwargs}
        state_accounts.append(account)
        added_accounts.append(account)
        return dict(account)

    def fake_remote_state(parent):
        with active_lock:
            active["count"] += 1
            active["max"] = max(active["max"], active["count"])
        manager.time.sleep(0.03)
        with active_lock:
            active["count"] -= 1
        if parent["id"] == "p-new":
            return {
                "members": [
                    {"email": "child@example.com", "user_id": "user-child"},
                    {"email": "authonly@example.com", "user_id": "user-authonly"},
                    {"email": "conflict@example.com", "user_id": "user-conflict"},
                ],
                "invites": [{"id": "invite-pending", "email_address": "pending@example.com"}],
            }
        return {"members": [], "invites": []}

    def fake_reconcile(parent, log_prefix="relink"):
        if parent["id"] == "p-new":
            return {
                "ok": True,
                "error": "",
                "members": [],
                "invites": [],
                "remote_pending_count": 1,
                "remote_member_count": 3,
                "remote_occupied_count": 4,
                "recoverable_count": 0,
                "drift_count": 0,
            }
        return {
            "ok": True,
            "error": "",
            "members": [],
            "invites": [],
            "remote_pending_count": 0,
            "remote_member_count": 0,
            "remote_occupied_count": 0,
            "recoverable_count": 0,
            "drift_count": 0,
        }

    monkeypatch.setattr(manager, "check_and_setup", lambda interactive=False: True)
    monkeypatch.setattr(manager, "load_parents", lambda: [dict(parent) for parent in parents])
    monkeypatch.setattr(manager, "load_accounts", fake_load_accounts)
    monkeypatch.setattr(manager, "update_account_by_id", fake_update_account_by_id)
    monkeypatch.setattr(manager, "add_account", fake_add_account)
    monkeypatch.setattr(manager, "_fetch_parent_team_state", fake_remote_state)
    monkeypatch.setattr(manager, "reconcile_parent_team_state", fake_reconcile)
    monkeypatch.setattr(
        manager,
        "discover_auth_file",
        lambda email, auth_file="": auth_file or ("D:/autoteam/auths/codex-authonly@example.com-old.json" if email == "authonly@example.com" else ""),
    )

    result = manager.run_repair_parent_child_links(concurrency=2)

    assert result["processed_parents"] == 2
    assert result["concurrency"] == 2
    assert active["max"] == 2
    assert result["relinked"] == 2
    assert result["created_from_auth"] == 1
    assert result["conflicts"] == 1
    assert result["blocked_parents"] == 0
    old_child = next(account for account in state_accounts if account["id"] == "child-old-parent")
    assert old_child["parent_id"] == "p-new"
    assert old_child["parent_email"] == "owner@example.com"
    assert old_child["remote_state"] == manager.REMOTE_STATE_MEMBER
    assert old_child["remote_member_id"] == "user-child"
    assert old_child["status"] == manager.STATUS_AUTH_SAVED
    pending = next(account for account in state_accounts if account["id"] == "child-pending")
    assert pending["parent_id"] == "p-new"
    assert pending["status"] == manager.STATUS_INVITED
    assert pending["invite_id"] == "invite-pending"
    assert added_accounts[0]["email"] == "authonly@example.com"
    assert added_accounts[0]["status"] == manager.STATUS_AUTH_SAVED
    conflict = next(account for account in state_accounts if account["id"] == "child-conflict")
    assert conflict["parent_id"] == "p-other"
    assert conflict["remote_member_id"] == "user-conflict-old"


def test_repair_parent_child_links_keeps_removed_and_member_over_invite(monkeypatch):
    parent = {"id": "p1", "email": "owner@example.com", "workspace_name": "Team A"}
    state_accounts = [
        {
            "id": "removed-child",
            "email": "removed@example.com",
            "parent_id": "p-old",
            "parent_email": "owner@example.com",
            "status": manager.STATUS_REMOVED,
            "remote_state": manager.REMOTE_STATE_ABSENT,
            "remote_member_id": "",
            "removed_at": 100.0,
            "removed_reason": "blocked_auth",
            "health_status": "unknown",
            "health_error": "",
            "enabled": True,
        },
        {
            "id": "member-child",
            "email": "member@example.com",
            "parent_id": "",
            "parent_email": "",
            "status": manager.STATUS_FAILED,
            "remote_state": manager.REMOTE_STATE_ABSENT,
            "remote_member_id": "",
            "enabled": True,
        },
    ]

    def fake_load_accounts():
        return [dict(account) for account in state_accounts]

    def fake_update_account_by_id(account_id, **kwargs):
        for account in state_accounts:
            if account["id"] == account_id:
                account.update(kwargs)
                return dict(account)
        return None

    monkeypatch.setattr(manager, "load_accounts", fake_load_accounts)
    monkeypatch.setattr(manager, "update_account_by_id", fake_update_account_by_id)
    monkeypatch.setattr(manager, "discover_auth_file", lambda email, auth_file="": "")
    monkeypatch.setattr(
        manager,
        "reconcile_parent_team_state",
        lambda parent_arg, log_prefix="relink-final": {"ok": True, "error": ""},
    )

    results = {"processed_parents": 0, "relinked": 0, "created_from_auth": 0, "remote_only": 0, "conflicts": 0, "failed": 0, "items": []}
    manager._apply_repair_parent_child_links_snapshot(
        parent,
        {
            "members": [
                {"email": "removed@example.com", "user_id": "user-removed"},
                {"email": "member@example.com", "user_id": "user-member"},
            ],
            "invites": [{"id": "invite-stale", "email_address": "member@example.com"}],
        },
        {"p1"},
        results,
    )

    removed = next(account for account in state_accounts if account["id"] == "removed-child")
    assert removed["status"] == manager.STATUS_REMOVED
    assert removed["remote_state"] == manager.REMOTE_STATE_MEMBER
    assert removed["remote_member_id"] == "user-removed"
    assert removed["removed_at"] == 100.0
    assert removed["removed_reason"] == "blocked_auth"
    assert removed["health_status"] == "check_failed"
    assert "still present remotely" in removed["health_error"]

    member = next(account for account in state_accounts if account["id"] == "member-child")
    assert member["status"] == manager.STATUS_ACCEPTED
    assert member["remote_state"] == manager.REMOTE_STATE_MEMBER
    assert member["remote_member_id"] == "user-member"
    assert member.get("invite_id") in {None, ""}


def test_run_repair_parent_child_links_conflicts_duplicate_remote_members_before_mutating(monkeypatch):
    parents = [
        {"id": "p1", "email": "owner1@example.com", "enabled": True, "session_token": "s1", "account_id": "a1"},
        {"id": "p2", "email": "owner2@example.com", "enabled": True, "session_token": "s2", "account_id": "a2"},
    ]
    state_accounts = [
        {
            "id": "child-1",
            "email": "child@example.com",
            "parent_id": "",
            "parent_email": "",
            "status": manager.STATUS_FAILED,
            "remote_state": manager.REMOTE_STATE_ABSENT,
            "remote_member_id": "",
            "enabled": True,
        }
    ]

    def fake_load_accounts():
        return [dict(account) for account in state_accounts]

    def fake_update_account_by_id(account_id, **kwargs):
        for account in state_accounts:
            if account["id"] == account_id:
                account.update(kwargs)
                return dict(account)
        return None

    monkeypatch.setattr(manager, "check_and_setup", lambda interactive=False: True)
    monkeypatch.setattr(manager, "load_parents", lambda: [dict(parent) for parent in parents])
    monkeypatch.setattr(manager, "load_accounts", fake_load_accounts)
    monkeypatch.setattr(manager, "update_account_by_id", fake_update_account_by_id)
    monkeypatch.setattr(manager, "discover_auth_file", lambda email, auth_file="": "")
    monkeypatch.setattr(
        manager,
        "_fetch_parent_team_state",
        lambda parent: {"members": [{"email": "child@example.com", "user_id": f"user-{parent['id']}"}], "invites": []},
    )
    monkeypatch.setattr(
        manager,
        "reconcile_parent_team_state",
        lambda parent_arg, log_prefix="relink-final": {"ok": True, "error": ""},
    )

    result = manager.run_repair_parent_child_links(concurrency=2)

    assert result["conflicts"] == 2
    assert result["relinked"] == 0
    assert state_accounts[0]["parent_id"] == ""
    assert state_accounts[0]["remote_state"] == manager.REMOTE_STATE_ABSENT


def test_resume_account_does_not_fail_new_invite_when_remote_pending_not_visible_yet(monkeypatch):
    state_accounts = [
        {
            "id": "child-new",
            "parent_id": "p1",
            "email": "child@example.com",
            "status": manager.STATUS_INVITED,
            "remote_state": manager.REMOTE_STATE_ABSENT,
            "invite_sent_at": 10_000.0,
            "enabled": True,
        }
    ]
    parent = {"id": "p1", "label": "A", "session_token": "s", "account_id": "acct-1"}
    accepted_calls = []

    def fake_load_account_by_id(account_id):
        for account in state_accounts:
            if account["id"] == account_id:
                return dict(account)
        return None

    monkeypatch.setattr(manager, "_load_account_by_id", fake_load_account_by_id)
    monkeypatch.setattr(manager, "_repair_account_auth_reference", lambda account: account)
    monkeypatch.setattr(manager.time, "time", lambda: 10_010.0)
    monkeypatch.setattr(
        manager,
        "reconcile_parent_team_state",
        lambda parent_arg, log_prefix="resume": {
            "ok": True,
            "error": "",
            "members": [],
            "invites": [],
            "remote_pending_count": 0,
            "remote_member_count": 0,
            "remote_occupied_count": 0,
            "recoverable_count": 0,
            "drift_count": 0,
        },
    )

    def fake_accept(parent_arg, account_arg, mail_client):
        accepted_calls.append((parent_arg["id"], account_arg["id"]))
        state_accounts[0]["status"] = manager.STATUS_ACCEPTED
        state_accounts[0]["remote_state"] = manager.REMOTE_STATE_MEMBER
        return dict(state_accounts[0])

    monkeypatch.setattr(manager, "_accept_invite_and_register", fake_accept)
    monkeypatch.setattr(
        manager,
        "_generate_codex_auth",
        lambda parent_arg, account_arg, mail_client: {"id": account_arg["id"], "status": manager.STATUS_READY},
    )

    result = manager.resume_account(parent, "child-new", object())

    assert result == {"id": "child-new", "status": manager.STATUS_READY}
    assert accepted_calls == [("p1", "child-new")]
