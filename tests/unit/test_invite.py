import random
import re
from datetime import date

from autoteam import invite


def _age_on(birth_date, today):
    return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))


def test_random_us_profile_uses_us_name_username_and_age_range():
    rng = random.Random(7)
    today = date(2026, 4, 25)
    seen_names = set()

    for _ in range(200):
        profile = invite._random_us_profile(rng=rng, today=today)

        assert profile["first_name"] in invite.US_FIRST_NAMES
        assert profile["last_name"] in invite.US_LAST_NAMES
        assert profile["full_name"] == f'{profile["first_name"]} {profile["last_name"]}'
        assert re.fullmatch(r"[a-z0-9]+", profile["username"])
        assert 18 <= profile["age"] <= 50
        assert _age_on(profile["birth_date"], today) == profile["age"]
        assert profile["birth_year"] == f'{profile["birth_date"].year:04d}'
        assert profile["birth_month"] == f'{profile["birth_date"].month:02d}'
        assert profile["birth_day"] == f'{profile["birth_date"].day:02d}'
        seen_names.add(profile["full_name"])

    assert len(seen_names) > 1


def test_random_birth_date_for_age_range_handles_leap_day_today():
    rng = random.Random(11)
    today = date(2024, 2, 29)

    for _ in range(100):
        birth_date, age = invite._random_birth_date_for_age_range(rng, today=today)

        assert 18 <= age <= 50
        assert _age_on(birth_date, today) == age


def test_build_exact_label_regex_does_not_match_google_variant():
    pattern = invite._build_exact_label_regex(["Continue", "缁х画"])

    assert pattern.fullmatch("Continue")
    assert pattern.fullmatch("缁х画")
    assert not pattern.fullmatch("Continue with Google")


def test_profile_step_selectors_do_not_match_generic_auth_buttons():
    assert 'button:has-text("Continue")' not in invite.PROFILE_STEP_SELECTORS
    assert 'button[type="submit"]' not in invite.PROFILE_STEP_SELECTORS


def test_profile_click_selectors_allow_submit_buttons():
    assert 'button:has-text("Continue")' in invite.PROFILE_BUTTON_SELECTORS
    assert 'button[type="submit"]' in invite.PROFILE_BUTTON_SELECTORS


def test_wait_for_otp_result_treats_profile_step_as_success(monkeypatch):
    monkeypatch.setattr(invite, "_detect_otp_error", lambda page: None)
    monkeypatch.setattr(invite, "_is_profile_step", lambda page: True)
    monkeypatch.setattr(invite, "_find_otp_input", lambda page, timeout=250: object())

    status, detail = invite._wait_for_otp_result(object(), timeout=0)

    assert status == "accepted"
    assert detail == "profile"


def test_fetch_verification_code_uses_shared_wait_helper():
    class FakeMailClient:
        def wait_for_verification_code(self, email, timeout=0):
            assert email == "child@example.com"
            assert timeout == 30
            return {"code": "123456", "email_id": 88}

    code = invite._fetch_verification_code(FakeMailClient(), "child@example.com", timeout=30)

    assert code == "123456"


def test_fetch_verification_code_skips_invites_and_reads_email_detail():
    class FakeMailClient:
        def search_emails_by_recipient(self, email, size=10):
            assert email == "child@example.com"
            return [
                {"id": 1, "subject": "team has invited you", "verification_code": "111111"},
                {"id": 2, "subject": "Your verification code"},
            ]

        def get_email(self, email_id):
            assert email_id == 2
            return {"id": 2, "subject": "Your verification code", "content": "Verification code: 654321"}

        def extract_verification_code(self, email_data):
            return "654321" if "654321" in str(email_data) else email_data.get("verification_code")

    code = invite._fetch_verification_code(FakeMailClient(), "child@example.com", timeout=1)

    assert code == "654321"
