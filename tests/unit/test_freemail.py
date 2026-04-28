import pytest

from autoteam import freemail


def test_create_temp_email_rotates_domains(tmp_path, monkeypatch):
    state_file = tmp_path / "freemail_state.json"
    monkeypatch.setattr(freemail, "PROVIDER_STATE_FILE", state_file)
    monkeypatch.setattr(freemail.config, "FREEMAIL_BASE_URL", "https://freemail.example.com")
    monkeypatch.setattr(freemail.config, "FREEMAIL_ROOT_TOKEN", "root-token")

    client = freemail.FreemailClient()
    monkeypatch.setattr(client, "get_domains", lambda: ["a.example.com", "b.example.com"])

    calls = []

    def fake_post(path, data=None):
        calls.append((path, data))
        domain = "a.example.com" if data["domainIndex"] == 0 else "b.example.com"
        return {"email": f"{data['local']}@{domain}", "expires": 1}

    monkeypatch.setattr(client, "_post", fake_post)

    first = client.create_temp_email(prefix="one")
    second = client.create_temp_email(prefix="two")

    assert first["email"] == "one@a.example.com"
    assert second["email"] == "two@b.example.com"
    assert calls[0][1]["domainIndex"] == 0
    assert calls[1][1]["domainIndex"] == 1


def test_extract_verification_code_and_invite_link():
    client = freemail.FreemailClient()

    assert client.extract_verification_code({"verification_code": "123456"}) == "123456"
    assert (
        client.extract_invite_link({"html_content": '<a href="https://chatgpt.com/auth/login?foo=bar">Join</a>'})
        == "https://chatgpt.com/auth/login?foo=bar"
    )


def test_freemail_client_uses_latest_config(monkeypatch):
    monkeypatch.setattr(freemail.config, "FREEMAIL_BASE_URL", "https://new-freemail.example.com")
    monkeypatch.setattr(freemail.config, "FREEMAIL_ROOT_TOKEN", "new-root-token")

    client = freemail.FreemailClient()

    assert client.base_url == "https://new-freemail.example.com"
    assert client.token == "new-root-token"


def test_get_latest_email_id_filters_sender(monkeypatch):
    client = freemail.FreemailClient()
    monkeypatch.setattr(
        client,
        "search_emails_by_recipient",
        lambda email, size=20: [
            {"id": 2, "sender": "billing@example.com"},
            {"id": 5, "sender": "team@openai.com"},
            {"id": 4, "sender": "noreply@openai.com"},
        ],
    )

    latest_id = client.get_latest_email_id("owner@example.com", sender_keyword="openai")

    assert latest_id == 5


def test_wait_for_verification_code_respects_since_id_and_ignores_invites(monkeypatch):
    client = freemail.FreemailClient()
    monkeypatch.setattr(freemail.config, "EMAIL_POLL_INTERVAL", 0)
    monkeypatch.setattr(
        client,
        "search_emails_by_recipient",
        lambda email, size=10: [
            {"id": 7, "sender": "team@openai.com", "subject": "You are invited to a workspace", "preview": "invite"},
            {"id": 8, "sender": "team@openai.com", "subject": "Your verification code"},
            {"id": 6, "sender": "team@openai.com", "subject": "Old verification code"},
        ],
    )
    monkeypatch.setattr(
        client,
        "get_email",
        lambda email_id: {"id": email_id, "subject": "Your verification code", "content": "Verification code: 654321"},
    )

    result = client.wait_for_verification_code("owner@example.com", timeout=1, since_id=7)

    assert result["code"] == "654321"
    assert result["email_id"] == 8


def test_wait_for_verification_code_times_out(monkeypatch):
    client = freemail.FreemailClient()
    monkeypatch.setattr(freemail.config, "EMAIL_POLL_INTERVAL", 0)
    monkeypatch.setattr(client, "search_emails_by_recipient", lambda email, size=10: [])

    with pytest.raises(TimeoutError):
        client.wait_for_verification_code("owner@example.com", timeout=0.01, since_id=0)
