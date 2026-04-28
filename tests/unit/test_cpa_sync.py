from pathlib import Path

from autoteam import cpa_sync


def test_cpa_sync_reads_latest_config(monkeypatch):
    monkeypatch.setattr(cpa_sync.config, "CPA_URL", "http://new-cpa.local")
    monkeypatch.setattr(cpa_sync.config, "CPA_KEY", "new-cpa-key")

    assert cpa_sync._headers() == {"Authorization": "Bearer new-cpa-key"}

    calls = {}

    class DummyResponse:
        status_code = 200

        def json(self):
            return {"files": []}

    def fake_get(url, headers=None, timeout=0):
        calls["url"] = url
        calls["headers"] = headers
        calls["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr(cpa_sync.requests, "get", fake_get)

    result = cpa_sync.list_cpa_files()

    assert result == []
    assert calls["url"] == "http://new-cpa.local/v0/management/auth-files"
    assert calls["headers"] == {"Authorization": "Bearer new-cpa-key"}


def test_resync_ready_accounts_repairs_auth_reference_and_uploads(monkeypatch, tmp_path):
    auth_file = tmp_path / "codex-child@example.com-1.json"
    auth_file.write_text("{}", encoding="utf-8")
    state_accounts = [
        {
            "id": "child-1",
            "email": "child@example.com",
            "auth_file": "",
            "status": "accepted",
            "cpa_uploaded_at": None,
            "auth_saved_at": None,
        }
    ]
    updates = []

    monkeypatch.setattr(
        cpa_sync,
        "upload_to_cpa",
        lambda filepath: Path(filepath) == auth_file,
    )

    def fake_load_accounts():
        return [dict(account) for account in state_accounts]

    def fake_update_account_by_id(account_id, **kwargs):
        for account in state_accounts:
            if account["id"] == account_id:
                account.update(kwargs)
                updates.append((account_id, kwargs))
                return dict(account)
        return None

    monkeypatch.setattr(
        "autoteam.accounts.load_accounts",
        fake_load_accounts,
    )
    monkeypatch.setattr(
        "autoteam.accounts.update_account_by_id",
        fake_update_account_by_id,
    )
    monkeypatch.setattr(
        "autoteam.accounts.discover_auth_file",
        lambda email, auth_path="": str(auth_file) if email == "child@example.com" else "",
    )

    result = cpa_sync.resync_ready_accounts()

    assert result["uploaded"] == 1
    assert result["failed"] == 0
    assert result["skipped"] == 0
    assert result["repaired"] == 1
    assert state_accounts[0]["status"] == "ready"
    assert state_accounts[0]["auth_file"] == str(auth_file)
    assert state_accounts[0]["cpa_uploaded_at"] is not None
    assert any(update[1].get("status") == "auth_saved" for update in updates)
    assert any(update[1].get("status") == "ready" for update in updates)


def test_resync_ready_accounts_keeps_auth_saved_on_upload_failure(monkeypatch, tmp_path):
    auth_file = tmp_path / "codex-child@example.com-1.json"
    auth_file.write_text("{}", encoding="utf-8")
    state_accounts = [
        {
            "id": "child-1",
            "email": "child@example.com",
            "auth_file": str(auth_file),
            "status": "auth_saved",
            "cpa_uploaded_at": None,
            "auth_saved_at": 123.0,
        }
    ]

    monkeypatch.setattr(cpa_sync, "upload_to_cpa", lambda filepath: False)
    monkeypatch.setattr("autoteam.accounts.load_accounts", lambda: [dict(account) for account in state_accounts])
    monkeypatch.setattr("autoteam.accounts.discover_auth_file", lambda email, auth_path="": str(auth_file))

    def fake_update_account_by_id(account_id, **kwargs):
        for account in state_accounts:
            if account["id"] == account_id:
                account.update(kwargs)
                return dict(account)
        return None

    monkeypatch.setattr("autoteam.accounts.update_account_by_id", fake_update_account_by_id)

    result = cpa_sync.resync_ready_accounts()

    assert result == {"uploaded": 0, "failed": 1, "skipped": 0, "repaired": 0}
    assert state_accounts[0]["status"] == "auth_saved"
    assert state_accounts[0]["error"] == "CPA upload failed"


def test_resync_ready_accounts_does_not_repair_blocked_or_removed(monkeypatch, tmp_path):
    auth_file = tmp_path / "codex-child@example.com-1.json"
    auth_file.write_text("{}", encoding="utf-8")
    state_accounts = [
        {"id": "child-blocked", "email": "blocked@example.com", "auth_file": "", "status": "blocked"},
        {"id": "child-removed", "email": "removed@example.com", "auth_file": "", "status": "removed"},
    ]
    uploads = []

    monkeypatch.setattr(cpa_sync, "upload_to_cpa", lambda filepath: uploads.append(filepath) or True)
    monkeypatch.setattr("autoteam.accounts.load_accounts", lambda: [dict(account) for account in state_accounts])
    monkeypatch.setattr("autoteam.accounts.discover_auth_file", lambda email, auth_path="": str(auth_file))

    def fake_update_account_by_id(account_id, **kwargs):
        for account in state_accounts:
            if account["id"] == account_id:
                account.update(kwargs)
                return dict(account)
        return None

    monkeypatch.setattr("autoteam.accounts.update_account_by_id", fake_update_account_by_id)

    result = cpa_sync.resync_ready_accounts()

    assert result["uploaded"] == 0
    assert result["skipped"] == 2
    assert uploads == []
    assert {account["status"] for account in state_accounts} == {"blocked", "removed"}


def test_sync_parent_main_auth_to_cpa_replaces_same_named_file(monkeypatch, tmp_path):
    auth_file = tmp_path / "codex-main-parent-1.json"
    auth_file.write_text("{}", encoding="utf-8")
    calls = {"deleted": [], "uploaded": []}

    monkeypatch.setattr(
        cpa_sync,
        "list_cpa_files",
        lambda: [{"name": "codex-main-parent-1.json"}, {"name": "other.json"}],
    )
    monkeypatch.setattr(
        cpa_sync,
        "delete_from_cpa",
        lambda name: calls["deleted"].append(name) or True,
    )
    monkeypatch.setattr(
        cpa_sync,
        "upload_to_cpa",
        lambda filepath: calls["uploaded"].append(Path(filepath).name) or True,
    )

    result = cpa_sync.sync_parent_main_auth_to_cpa("parent-1", str(auth_file))

    assert result["parent_id"] == "parent-1"
    assert result["filename"] == "codex-main-parent-1.json"
    assert result["deleted_existing"] is True
    assert calls["deleted"] == ["codex-main-parent-1.json"]
    assert calls["uploaded"] == ["codex-main-parent-1.json"]
