from autoteam import chatgpt_api


def test_pick_preferred_workspace_option_prefers_saved_workspace():
    api = chatgpt_api.ChatGPTTeamAPI(workspace_name="Team Alpha")

    option = api._pick_preferred_workspace_option(
        [
            {"id": "1", "label": "Personal workspace"},
            {"id": "2", "label": "Team Alpha", "kind": "preferred"},
            {"id": "3", "label": "Team Beta", "kind": "preferred"},
        ]
    )

    assert option["id"] == "2"


def test_pick_preferred_workspace_option_skips_personal_and_free():
    api = chatgpt_api.ChatGPTTeamAPI()

    option = api._pick_preferred_workspace_option(
        [
            {"id": "1", "label": "Personal workspace"},
            {"id": "2", "label": "Free"},
            {"id": "3", "label": "Team Beta", "kind": "preferred"},
        ]
    )

    assert option["id"] == "3"


def test_switch_password_to_otp_clicks_visible_entry(monkeypatch):
    class DummyEntry:
        def __init__(self):
            self.clicked = False

        def click(self, force=False):
            del force
            self.clicked = True

    api = chatgpt_api.ChatGPTTeamAPI()
    entry = DummyEntry()

    monkeypatch.setattr(api, "_visible_locator_in_frames", lambda selectors, timeout_ms=0: entry)
    monkeypatch.setattr(api, "_log_login_state", lambda label: None)
    monkeypatch.setattr(chatgpt_api.time, "sleep", lambda seconds: None)

    assert api._switch_password_to_otp() is True
    assert entry.clicked is True


def test_remove_member_uses_account_users_delete(monkeypatch):
    api = chatgpt_api.ChatGPTTeamAPI()
    api.account_id = "acct-1"
    captured = {}

    def fake_fetch(method, path, body=None):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        return {"status": 204, "body": ""}

    monkeypatch.setattr(api, "_api_fetch", fake_fetch)

    status, payload = api.remove_member("user-1")

    assert status == 204
    assert payload == {}
    assert captured == {"method": "DELETE", "path": "/backend-api/accounts/acct-1/users/user-1", "body": None}
