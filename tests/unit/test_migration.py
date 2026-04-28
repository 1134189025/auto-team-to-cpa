import json

from autoteam import accounts, migration, parents


def test_migrate_legacy_state_and_accounts(tmp_path, monkeypatch):
    legacy_state = tmp_path / "state.json"
    legacy_accounts = tmp_path / "accounts.json"
    parents_file = tmp_path / "main_accounts.json"

    legacy_state.write_text(
        json.dumps(
            {
                "email": "owner@example.com",
                "session_token": "session-token",
                "account_id": "11111111-1111-1111-1111-111111111111",
                "workspace_name": "Team A",
            }
        ),
        encoding="utf-8",
    )
    legacy_accounts.write_text(
        json.dumps(
            [
                {"email": "child-ready@example.com", "password": "secret", "auth_file": str(tmp_path / "auth.json")},
                {"email": "child-failed@example.com", "password": "secret", "auth_file": ""},
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(migration, "LEGACY_STATE_FILE", legacy_state)
    monkeypatch.setattr(migration, "LEGACY_ACCOUNTS_FILE", legacy_accounts)
    monkeypatch.setattr(migration, "PARENTS_FILE", parents_file)
    monkeypatch.setattr(parents, "PARENTS_FILE", parents_file)
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", legacy_accounts)
    monkeypatch.setattr(migration, "_MIGRATED", False)

    migration.migrate_legacy_data()

    migrated_parents = parents.load_parents()
    migrated_accounts = accounts.load_accounts()

    assert migrated_parents[0]["email"] == "owner@example.com"
    assert migrated_accounts[0]["mail_provider"] == "legacy-cloudmail"
    assert {item["status"] for item in migrated_accounts} == {"ready", "failed"}


def test_migrate_legacy_data_skips_new_cancelled_accounts(tmp_path, monkeypatch):
    legacy_accounts = tmp_path / "accounts.json"
    parents_file = tmp_path / "main_accounts.json"

    legacy_accounts.write_text(
        json.dumps(
            [
                {
                    "id": "child-1",
                    "email": "child@example.com",
                    "parent_id": "parent-1",
                    "parent_email": "owner@example.com",
                    "workspace_name": "Team A",
                    "mail_provider": "freemail",
                    "mailbox_address": "child@example.com",
                    "invite_id": "invite-1",
                    "status": "cancelled",
                }
            ]
        ),
        encoding="utf-8",
    )
    parents_file.write_text(
        json.dumps(
            [
                {
                    "id": "parent-1",
                    "label": "Team A",
                    "email": "owner@example.com",
                    "session_token": "session-token",
                    "account_id": "11111111-1111-1111-1111-111111111111",
                    "workspace_name": "Team A",
                    "enabled": True,
                    "default_batch_size": 1,
                }
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(migration, "LEGACY_ACCOUNTS_FILE", legacy_accounts)
    monkeypatch.setattr(migration, "PARENTS_FILE", parents_file)
    monkeypatch.setattr(parents, "PARENTS_FILE", parents_file)
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", legacy_accounts)
    monkeypatch.setattr(migration, "_MIGRATED", False)

    before = legacy_accounts.read_text(encoding="utf-8")
    migration.migrate_legacy_data()
    after = legacy_accounts.read_text(encoding="utf-8")

    assert before == after


def test_migrate_legacy_data_skips_new_recoverable_statuses(tmp_path, monkeypatch):
    legacy_accounts = tmp_path / "accounts.json"
    parents_file = tmp_path / "main_accounts.json"

    legacy_accounts.write_text(
        json.dumps(
            [
                {
                    "id": "child-1",
                    "email": "child@example.com",
                    "parent_id": "parent-1",
                    "parent_email": "owner@example.com",
                    "workspace_name": "Team A",
                    "mail_provider": "freemail",
                    "mailbox_address": "child@example.com",
                    "invite_id": "invite-1",
                    "status": "accepted",
                    "remote_state": "member",
                },
                {
                    "id": "child-2",
                    "email": "child2@example.com",
                    "parent_id": "parent-1",
                    "parent_email": "owner@example.com",
                    "workspace_name": "Team A",
                    "mail_provider": "freemail",
                    "mailbox_address": "child2@example.com",
                    "invite_id": "invite-2",
                    "status": "auth_saved",
                    "remote_state": "member",
                },
            ]
        ),
        encoding="utf-8",
    )
    parents_file.write_text(
        json.dumps(
            [
                {
                    "id": "parent-1",
                    "label": "Team A",
                    "email": "owner@example.com",
                    "session_token": "session-token",
                    "account_id": "11111111-1111-1111-1111-111111111111",
                    "workspace_name": "Team A",
                    "enabled": True,
                    "default_batch_size": 1,
                }
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(migration, "LEGACY_ACCOUNTS_FILE", legacy_accounts)
    monkeypatch.setattr(migration, "PARENTS_FILE", parents_file)
    monkeypatch.setattr(parents, "PARENTS_FILE", parents_file)
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", legacy_accounts)
    monkeypatch.setattr(migration, "_MIGRATED", False)

    before = legacy_accounts.read_text(encoding="utf-8")
    migration.migrate_legacy_data()
    after = legacy_accounts.read_text(encoding="utf-8")

    assert before == after


def test_migrate_legacy_data_skips_blocked_and_removed_statuses(tmp_path, monkeypatch):
    legacy_accounts = tmp_path / "accounts.json"
    parents_file = tmp_path / "main_accounts.json"

    legacy_accounts.write_text(
        json.dumps(
            [
                {"id": "child-1", "email": "blocked@example.com", "parent_id": "parent-1", "status": "blocked"},
                {"id": "child-2", "email": "removed@example.com", "parent_id": "parent-1", "status": "removed"},
            ]
        ),
        encoding="utf-8",
    )
    parents_file.write_text(
        json.dumps(
            [
                {
                    "id": "parent-1",
                    "label": "Team A",
                    "email": "owner@example.com",
                    "session_token": "session-token",
                    "account_id": "11111111-1111-1111-1111-111111111111",
                    "workspace_name": "Team A",
                    "enabled": True,
                    "default_batch_size": 1,
                }
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(migration, "LEGACY_ACCOUNTS_FILE", legacy_accounts)
    monkeypatch.setattr(migration, "PARENTS_FILE", parents_file)
    monkeypatch.setattr(parents, "PARENTS_FILE", parents_file)
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", legacy_accounts)
    monkeypatch.setattr(migration, "_MIGRATED", False)

    before = legacy_accounts.read_text(encoding="utf-8")
    migration.migrate_legacy_data()
    after = legacy_accounts.read_text(encoding="utf-8")

    assert before == after
