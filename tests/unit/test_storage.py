from autoteam import accounts, parents


def test_add_and_update_child_account(tmp_path, monkeypatch):
    accounts_file = tmp_path / "accounts.json"
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", accounts_file)

    child = accounts.add_account(
        "child@example.com",
        "secret",
        parent_id="parent-1",
        parent_email="parent@example.com",
        workspace_name="Workspace A",
        mail_provider="freemail",
        mailbox_address="child@example.com",
    )

    assert child["status"] == accounts.STATUS_INVITED
    assert child["parent_id"] == "parent-1"

    updated = accounts.update_account_by_id(child["id"], status=accounts.STATUS_READY, auth_file=str(auth_file))

    assert updated["status"] == accounts.STATUS_READY
    assert accounts.load_accounts()[0]["auth_file"] == str(auth_file.resolve())


def test_cancelled_child_status_is_preserved(tmp_path, monkeypatch):
    accounts_file = tmp_path / "accounts.json"
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", accounts_file)

    child = accounts.add_account(
        "child@example.com",
        "secret",
        parent_id="parent-1",
        parent_email="parent@example.com",
        workspace_name="Workspace A",
        mail_provider="freemail",
        mailbox_address="child@example.com",
    )

    updated = accounts.update_account_by_id(child["id"], status=accounts.STATUS_CANCELLED, error="")

    assert updated["status"] == accounts.STATUS_CANCELLED
    assert accounts.load_accounts()[0]["status"] == accounts.STATUS_CANCELLED


def test_blocked_and_removed_statuses_are_preserved_with_health_fields(tmp_path, monkeypatch):
    accounts_file = tmp_path / "accounts.json"
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", accounts_file)

    child = accounts.add_account(
        "child@example.com",
        "secret",
        parent_id="parent-1",
        parent_email="parent@example.com",
        workspace_name="Workspace A",
        mail_provider="freemail",
        mailbox_address="child@example.com",
    )

    blocked = accounts.update_account_by_id(
        child["id"],
        status=accounts.STATUS_BLOCKED,
        remote_state=accounts.REMOTE_STATE_MEMBER,
        health_status=accounts.HEALTH_STATUS_AUTH_ERROR,
        health_checked_at=123.0,
        health_error="unauthorized",
    )
    removed = accounts.update_account_by_id(
        blocked["id"],
        status=accounts.STATUS_REMOVED,
        remote_state=accounts.REMOTE_STATE_ABSENT,
        removed_at=456.0,
        removed_reason="blocked_auth",
        completed_at=456.0,
    )

    stored = accounts.load_accounts()[0]

    assert accounts.occupies_slot(accounts.STATUS_BLOCKED) is True
    assert accounts.occupies_slot(accounts.STATUS_REMOVED) is False
    assert removed["status"] == accounts.STATUS_REMOVED
    assert stored["health_status"] == accounts.HEALTH_STATUS_AUTH_ERROR
    assert stored["health_checked_at"] == 123.0
    assert stored["health_error"] == "unauthorized"
    assert stored["removed_at"] == 456.0
    assert stored["removed_reason"] == "blocked_auth"


def test_new_recoverable_child_statuses_are_preserved(tmp_path, monkeypatch):
    accounts_file = tmp_path / "accounts.json"
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", accounts_file)

    child = accounts.add_account(
        "child@example.com",
        "secret",
        parent_id="parent-1",
        parent_email="parent@example.com",
        workspace_name="Workspace A",
        mail_provider="freemail",
        mailbox_address="child@example.com",
    )

    updated = accounts.update_account_by_id(
        child["id"],
        status=accounts.STATUS_ACCEPTED,
        remote_state=accounts.REMOTE_STATE_MEMBER,
        accepted_at=123.0,
        error_stage="registration",
    )
    updated = accounts.update_account_by_id(
        updated["id"],
        status=accounts.STATUS_AUTH_SAVED,
        auth_file=str(auth_file),
        auth_saved_at=456.0,
        error_stage="cpa",
    )

    stored = accounts.load_accounts()[0]

    assert updated["status"] == accounts.STATUS_AUTH_SAVED
    assert stored["status"] == accounts.STATUS_AUTH_SAVED
    assert stored["remote_state"] == accounts.REMOTE_STATE_MEMBER
    assert stored["accepted_at"] == 123.0
    assert stored["auth_saved_at"] == 456.0


def test_add_and_update_parent(tmp_path, monkeypatch):
    parents_file = tmp_path / "main_accounts.json"
    monkeypatch.setattr(parents, "PARENTS_FILE", parents_file)

    parent = parents.add_parent(label="Team A", email="owner@example.com", default_batch_size=3)
    assert parent["enabled"] is True
    assert parent["default_batch_size"] == 3

    updated = parents.update_parent(
        parent["id"],
        enabled=False,
        workspace_name="Workspace A",
        main_auth_file=str(tmp_path / "codex-main-parent-1.json"),
        main_auth_plan_type="team",
        main_auth_refreshed_at=123.0,
        main_cpa_uploaded_at=456.0,
        main_codex_error="boom",
        main_codex_error_stage="cpa",
    )
    assert updated["enabled"] is False
    stored = parents.load_parents()[0]
    assert stored["workspace_name"] == "Workspace A"
    assert stored["main_auth_file"].endswith("codex-main-parent-1.json")
    assert stored["main_auth_plan_type"] == "team"
    assert stored["main_auth_refreshed_at"] == 123.0
    assert stored["main_cpa_uploaded_at"] == 456.0
    assert stored["main_codex_error"] == "boom"
    assert stored["main_codex_error_stage"] == "cpa"
