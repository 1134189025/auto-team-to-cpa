import json

from autoteam import accounts, health


def _write_auth(path, *, access_token="token-old", refresh_token="refresh-1", expired="2099-01-01T00:00:00Z"):
    path.write_text(
        json.dumps(
            {
                "type": "codex",
                "access_token": access_token,
                "refresh_token": refresh_token,
                "account_id": "acct-child",
                "expired": expired,
            }
        ),
        encoding="utf-8",
    )
    return str(path)


def _add_ready_member(tmp_path, monkeypatch, auth_file):
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", tmp_path / "accounts.json")
    child = accounts.add_account(
        "child@example.com",
        "secret",
        parent_id="parent-1",
        parent_email="owner@example.com",
        workspace_name="Team A",
        mail_provider="freemail",
        mailbox_address="child@example.com",
        status=accounts.STATUS_READY,
        remote_state=accounts.REMOTE_STATE_MEMBER,
        auth_file=auth_file,
    )
    return child


def test_check_child_account_health_marks_healthy(tmp_path, monkeypatch):
    auth_file = _write_auth(tmp_path / "auth.json")
    child = _add_ready_member(tmp_path, monkeypatch, auth_file)
    monkeypatch.setattr(health, "check_codex_quota", lambda access_token, account_id=None: ("ok", {"primary_pct": 1}))

    result = health.check_child_account_health(child)
    stored = accounts.load_accounts()[0]

    assert result["outcome"] == "healthy"
    assert stored["status"] == accounts.STATUS_READY
    assert stored["health_status"] == accounts.HEALTH_STATUS_HEALTHY
    assert stored["health_error"] == ""


def test_check_child_account_health_marks_quota_exhausted_without_blocking(tmp_path, monkeypatch):
    auth_file = _write_auth(tmp_path / "auth.json")
    child = _add_ready_member(tmp_path, monkeypatch, auth_file)
    monkeypatch.setattr(
        health,
        "check_codex_quota",
        lambda access_token, account_id=None: ("exhausted", {"error": "usage_limit_reached"}),
    )

    result = health.check_child_account_health(child)
    stored = accounts.load_accounts()[0]

    assert result["outcome"] == "quota_exhausted"
    assert stored["status"] == accounts.STATUS_READY
    assert stored["health_status"] == accounts.HEALTH_STATUS_QUOTA_EXHAUSTED
    assert stored["health_error"] == "usage_limit_reached"


def test_check_child_account_health_blocks_auth_error(tmp_path, monkeypatch):
    auth_file = _write_auth(tmp_path / "auth.json", refresh_token="")
    child = _add_ready_member(tmp_path, monkeypatch, auth_file)
    monkeypatch.setattr(health, "check_codex_quota", lambda access_token, account_id=None: ("auth_error", {"status_code": 401}))

    result = health.check_child_account_health(child)
    stored = accounts.load_accounts()[0]

    assert result["outcome"] == "blocked"
    assert stored["status"] == accounts.STATUS_BLOCKED
    assert stored["remote_state"] == accounts.REMOTE_STATE_MEMBER
    assert stored["health_status"] == accounts.HEALTH_STATUS_AUTH_ERROR


def test_check_child_account_health_refreshes_once_after_auth_error(tmp_path, monkeypatch):
    auth_path = tmp_path / "auth.json"
    auth_file = _write_auth(auth_path)
    child = _add_ready_member(tmp_path, monkeypatch, auth_file)
    seen_tokens = []

    monkeypatch.setattr(
        health,
        "refresh_access_token",
        lambda refresh_token: {"access_token": "token-new", "refresh_token": refresh_token, "expires_in": 3600},
    )

    def fake_check(access_token, account_id=None):
        seen_tokens.append(access_token)
        if access_token == "token-old":
            return "auth_error", {"status_code": 401}
        return "ok", {}

    monkeypatch.setattr(health, "check_codex_quota", fake_check)

    result = health.check_child_account_health(child)
    stored = accounts.load_accounts()[0]
    updated_auth = json.loads(auth_path.read_text(encoding="utf-8"))

    assert result["outcome"] == "healthy"
    assert seen_tokens == ["token-old", "token-new"]
    assert stored["status"] == accounts.STATUS_READY
    assert stored["health_status"] == accounts.HEALTH_STATUS_HEALTHY
    assert updated_auth["access_token"] == "token-new"


def test_check_child_account_health_refreshes_expired_token(tmp_path, monkeypatch):
    auth_path = tmp_path / "auth.json"
    auth_file = _write_auth(auth_path, expired="2000-01-01T00:00:00Z")
    child = _add_ready_member(tmp_path, monkeypatch, auth_file)
    seen = {}

    monkeypatch.setattr(
        health,
        "refresh_access_token",
        lambda refresh_token: {"access_token": "token-new", "refresh_token": refresh_token, "expires_in": 3600},
    )

    def fake_check(access_token, account_id=None):
        seen["access_token"] = access_token
        return "ok", {}

    monkeypatch.setattr(health, "check_codex_quota", fake_check)

    result = health.check_child_account_health(child)
    updated_auth = json.loads(auth_path.read_text(encoding="utf-8"))

    assert result["outcome"] == "healthy"
    assert seen["access_token"] == "token-new"
    assert updated_auth["access_token"] == "token-new"


def test_check_child_account_health_refresh_exception_is_check_failed(tmp_path, monkeypatch):
    auth_file = _write_auth(tmp_path / "auth.json", expired="2000-01-01T00:00:00Z")
    child = _add_ready_member(tmp_path, monkeypatch, auth_file)

    def fail_refresh(refresh_token):
        raise RuntimeError("HTTP 500")

    monkeypatch.setattr(health, "refresh_access_token", fail_refresh)

    result = health.check_child_account_health(child)
    stored = accounts.load_accounts()[0]

    assert result["outcome"] == "check_failed"
    assert stored["status"] == accounts.STATUS_READY
    assert stored["health_status"] == accounts.HEALTH_STATUS_CHECK_FAILED
    assert "HTTP 500" in stored["health_error"]


def test_check_child_account_health_check_failed_does_not_block(tmp_path, monkeypatch):
    auth_file = _write_auth(tmp_path / "auth.json")
    child = _add_ready_member(tmp_path, monkeypatch, auth_file)
    monkeypatch.setattr(health, "check_codex_quota", lambda access_token, account_id=None: ("check_failed", {"error": "timeout"}))

    result = health.check_child_account_health(child)
    stored = accounts.load_accounts()[0]

    assert result["outcome"] == "check_failed"
    assert stored["status"] == accounts.STATUS_READY
    assert stored["health_status"] == accounts.HEALTH_STATUS_CHECK_FAILED
    assert stored["health_error"] == "timeout"
