#!/usr/bin/env python3
"""Invite acceptance helpers."""

from __future__ import annotations

import logging
import os
import random
import re
import time
from datetime import date, timedelta

from playwright.sync_api import sync_playwright

from autoteam.freemail import FreemailClient

logger = logging.getLogger(__name__)

MAIL_TIMEOUT = int(os.environ.get("MAIL_TIMEOUT", "180"))
SCREENSHOT_DIR = "screenshots"
OTP_INPUT_SELECTORS = (
    'input[name="code"], input[autocomplete="one-time-code"], '
    'input[placeholder*="verification" i], input[placeholder*="code" i], input[placeholder*="验证码"]'
)
OTP_INVALID_HINTS = (
    "invalid code",
    "incorrect code",
    "wrong code",
    "expired code",
    "check the code and try again",
    "验证码无效",
    "验证码错误",
    "验证码已过期",
)
SIGNUP_BUTTON_SELECTORS = [
    'button:has-text("Sign up")',
    'a:has-text("Sign up")',
    'button:has-text("Create account")',
    'a:has-text("Create account")',
    'button:has-text("免费注册")',
    'a:has-text("免费注册")',
    'button:has-text("注册")',
    'a:has-text("注册")',
]
CONTINUE_BUTTON_SELECTORS = [
    'button:has-text("Continue")',
    'button:has-text("继续")',
    'button:has-text("Verify")',
    'button:has-text("Submit")',
    'button[type="submit"]',
]
EMAIL_SELECTORS = [
    'input[name="email"]',
    'input[type="email"]',
    'input[placeholder*="email" i]',
    'input[placeholder*="邮箱"]',
    'input[id="email"]',
    '#email-input',
    'input[autocomplete="email"]',
]
PASSWORD_SELECTORS = [
    'input[name="password"]',
    'input[type="password"]',
    'input[id="password"]',
]
NAME_SELECTORS = [
    'input[name="name"]',
    'input[name="full_name"]',
    'input[name="fullName"]',
    'input[name="displayName"]',
    'input[placeholder*="name" i]',
    'input[id="name"]',
    'input[id="full-name"]',
    'input[id="fullName"]',
    'input[autocomplete="name"]',
    'input[placeholder*="全名"]',
]
FIRST_NAME_SELECTORS = [
    'input[name="first_name"]',
    'input[name="firstName"]',
    'input[id="first-name"]',
    'input[id="firstName"]',
    'input[autocomplete="given-name"]',
    'input[placeholder*="first name" i]',
    'input[aria-label*="first name" i]',
]
LAST_NAME_SELECTORS = [
    'input[name="last_name"]',
    'input[name="lastName"]',
    'input[id="last-name"]',
    'input[id="lastName"]',
    'input[autocomplete="family-name"]',
    'input[placeholder*="last name" i]',
    'input[aria-label*="last name" i]',
]
USERNAME_SELECTORS = [
    'input[name="username"]',
    'input[id="username"]',
    'input[autocomplete="username"]',
    'input[placeholder*="username" i]',
    'input[aria-label*="username" i]',
]
AGE_SELECTORS = [
    'input[name="age"]',
    'input[id="age"]',
    'input[placeholder*="age" i]',
    'input[placeholder*="年龄"]',
    'input[type="number"]',
]
PROFILE_BUTTON_SELECTORS = [
    'button:has-text("Complete")',
    'button:has-text("Continue")',
    'button:has-text("继续")',
    'button:has-text("Agree")',
    'button:has-text("完成账户创建")',
    'button[type="submit"]',
    'input[type="submit"]',
]
PROFILE_STEP_SELECTORS = [
    'button:has-text("Complete")',
    'button:has-text("完成账户创建")',
]


US_FIRST_NAMES = [
    "James",
    "Michael",
    "Robert",
    "John",
    "David",
    "William",
    "Richard",
    "Joseph",
    "Thomas",
    "Christopher",
    "Mary",
    "Patricia",
    "Jennifer",
    "Linda",
    "Elizabeth",
    "Barbara",
    "Susan",
    "Jessica",
    "Sarah",
    "Karen",
    "Emily",
    "Ashley",
    "Amanda",
    "Daniel",
    "Matthew",
    "Anthony",
    "Mark",
    "Donald",
    "Steven",
    "Andrew",
]
US_LAST_NAMES = [
    "Smith",
    "Johnson",
    "Williams",
    "Brown",
    "Jones",
    "Garcia",
    "Miller",
    "Davis",
    "Rodriguez",
    "Martinez",
    "Hernandez",
    "Lopez",
    "Gonzalez",
    "Wilson",
    "Anderson",
    "Thomas",
    "Taylor",
    "Moore",
    "Jackson",
    "Martin",
    "Lee",
    "Perez",
    "Thompson",
    "White",
    "Harris",
    "Sanchez",
    "Clark",
    "Ramirez",
    "Lewis",
    "Robinson",
]


def _replace_year_safe(value, year):
    try:
        return value.replace(year=year)
    except ValueError:
        return value.replace(year=year, day=28)


def _age_on(birth_date, today):
    birthday_passed = (today.month, today.day) >= (birth_date.month, birth_date.day)
    return today.year - birth_date.year - (0 if birthday_passed else 1)


def _random_birth_date_for_age_range(rng, today=None, min_age=18, max_age=50):
    today = today or date.today()
    latest = _replace_year_safe(today, today.year - min_age)
    earliest = _replace_year_safe(today, today.year - max_age - 1) + timedelta(days=1)
    birth_date = date.fromordinal(rng.randint(earliest.toordinal(), latest.toordinal()))
    return birth_date, _age_on(birth_date, today)


def _random_us_profile(rng=None, today=None):
    rng = rng or random.SystemRandom()
    first_name = rng.choice(US_FIRST_NAMES)
    last_name = rng.choice(US_LAST_NAMES)
    birth_date, age = _random_birth_date_for_age_range(rng, today=today)
    username_base = re.sub(r"[^a-z0-9]", "", f"{first_name}{last_name}".lower()) or "user"
    username = f"{username_base}{rng.randint(1000, 999999)}"
    return {
        "first_name": first_name,
        "last_name": last_name,
        "full_name": f"{first_name} {last_name}",
        "username": username,
        "age": age,
        "birth_date": birth_date,
        "birth_year": f"{birth_date.year:04d}",
        "birth_month": f"{birth_date.month:02d}",
        "birth_day": f"{birth_date.day:02d}",
    }


def _fill_text(locator, value, page=None):
    value = str(value)
    try:
        locator.fill(value)
        return True
    except Exception:
        pass

    if page is None:
        return False
    try:
        locator.click(force=True)
        time.sleep(0.1)
        page.keyboard.press("Control+A")
        page.keyboard.type(value, delay=80)
        return True
    except Exception:
        return False


def screenshot(page, name):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = f"{SCREENSHOT_DIR}/{name}"
    page.screenshot(path=path, full_page=True)
    logger.debug("[invite] screenshot: %s", path)


def find_and_click(page, selectors, label="element", timeout=3000):
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.is_visible(timeout=timeout):
                logger.info("[invite] click %s via %s", label, selector)
                locator.click()
                return True
        except Exception:
            continue
    return False


def find_visible(page, selectors, label="element", timeout=3000):
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.is_visible(timeout=timeout):
                logger.debug("[invite] found %s via %s", label, selector)
                return locator
        except Exception:
            continue
    return None


def _build_exact_label_regex(labels):
    return re.compile(rf"^(?:{'|'.join(re.escape(label) for label in labels)})$", re.I)


def _click_primary_auth_button(page, field, labels):
    """
    Only submit the current auth step's primary button and avoid social-login variants
    such as "Continue with Google".
    """
    label_re = _build_exact_label_regex(labels)

    try:
        form = field.locator("xpath=ancestor::form[1]").first
        button = form.get_by_role("button", name=label_re).first
        if button.is_visible(timeout=2000):
            button.click()
            return True
    except Exception:
        pass

    try:
        form = field.locator("xpath=ancestor::form[1]").first
        button = form.locator('button[type="submit"], input[type="submit"]').first
        if button.is_visible(timeout=2000):
            button.click()
            return True
    except Exception:
        pass

    try:
        button = page.get_by_role("button", name=label_re).last
        if button.is_visible(timeout=2000):
            button.click()
            return True
    except Exception:
        pass

    try:
        field.press("Enter")
        return True
    except Exception:
        return False


def _is_google_page(candidate):
    url = (getattr(candidate, "url", "") or "").lower()
    if "accounts.google.com" in url:
        return True

    try:
        body = candidate.locator("body").inner_text(timeout=1000).lower()
    except Exception:
        return False
    return "sign in with google" in body[:300]


def _dismiss_google_popups(page):
    try:
        pages = list(page.context.pages)
    except Exception:
        return False

    dismissed = False
    for popup in pages:
        if popup == page or not _is_google_page(popup):
            continue
        logger.warning("[invite] closing unexpected Google login popup: %s", popup.url)
        try:
            popup.close()
        except Exception:
            pass
        dismissed = True
    return dismissed


def _submit_auth_step(page, field, labels, step_name, retries=2):
    for _attempt in range(retries):
        if not _click_primary_auth_button(page, field, labels):
            return False
        time.sleep(1)

        if _dismiss_google_popups(page):
            logger.warning("[invite] %s opened Google login popup, retrying submit", step_name)
            continue

        if _is_google_page(page):
            logger.warning("[invite] %s redirected to Google login, going back and retrying", step_name)
            try:
                page.go_back(wait_until="domcontentloaded", timeout=10000)
            except Exception:
                pass
            time.sleep(1)
            continue

        return True

    return False


def _submit_profile_step(page, field):
    try:
        field.press("Tab")
    except Exception:
        pass
    time.sleep(0.3)

    if _click_primary_auth_button(page, field, ["Complete", "完成账户创建", "Continue", "继续", "Agree"]):
        logger.info("[invite] submitted profile step via primary button helper")
        return True

    if find_and_click(page, PROFILE_BUTTON_SELECTORS, "complete profile", timeout=5000):
        return True

    logger.warning("[invite] profile submit button not found or not clickable")
    screenshot(page, "reg_12_profile_submit_not_found.png")
    return False


def wait_for_cloudflare(page, max_wait=60):
    for index in range(max_wait // 5):
        html = page.content()[:2000].lower()
        if "verify you are human" not in html and "challenge" not in page.url:
            return True
        logger.info("[invite] waiting for Cloudflare... (%ds)", index * 5)
        time.sleep(5)
    return False


def _normalize_otp(code: str) -> str:
    digits = "".join(ch for ch in str(code or "") if ch.isdigit())
    return digits or str(code or "").strip()


def _visible_otp_cells(page, timeout=2000):
    deadline = time.time() + timeout / 1000
    selectors = (
        'input[maxlength="1"]',
        'input[data-input-otp]',
        'input[inputmode="numeric"][maxlength="1"]',
    )
    while time.time() < deadline:
        for selector in selectors:
            try:
                locators = page.locator(selector)
                count = min(locators.count(), 8)
                visible = []
                for index in range(count):
                    locator = locators.nth(index)
                    if locator.is_visible(timeout=100):
                        visible.append(locator)
                if len(visible) >= 4:
                    return visible
            except Exception:
                continue
        time.sleep(0.2)
    return []


def _find_otp_input(page, timeout=3000):
    cells = _visible_otp_cells(page, timeout=timeout)
    if cells:
        return cells[0]
    try:
        locator = page.locator(OTP_INPUT_SELECTORS).first
        if locator.is_visible(timeout=timeout):
            return locator
    except Exception:
        return None
    return None


def _fill_otp(page, code: str):
    code = _normalize_otp(code)
    cells = _visible_otp_cells(page, timeout=1500)
    if len(cells) >= min(len(code), 4):
        logger.info("[invite] detected %d OTP cells", len(cells))
        for index, char in enumerate(code):
            if index >= len(cells):
                break
            locator = cells[index]
            try:
                locator.click(timeout=1000)
            except Exception:
                pass
            try:
                locator.fill("")
            except Exception:
                pass
            locator.fill(char)
            time.sleep(0.1)
        return cells[0]

    code_input = _find_otp_input(page, timeout=3000)
    if not code_input:
        return None
    code_input.fill(code)
    return code_input


def _detect_otp_error(page):
    try:
        body = page.locator("body").inner_text(timeout=1500).lower().replace("\n", " ")
    except Exception:
        return None
    for hint in OTP_INVALID_HINTS:
        if hint in body:
            return hint
    return None


def _wait_for_otp_result(page, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        err = _detect_otp_error(page)
        if err:
            return "invalid", err
        if _is_profile_step(page):
            return "accepted", "profile"
        if not _find_otp_input(page, timeout=250):
            return "accepted", None
        time.sleep(0.5)
    err = _detect_otp_error(page)
    if err:
        return "invalid", err
    if _is_profile_step(page):
        return "accepted", "profile"
    return "pending", None


def _is_profile_step(page):
    if find_visible(page, NAME_SELECTORS, timeout=250):
        return True
    if find_visible(page, FIRST_NAME_SELECTORS, timeout=250):
        return True
    if find_visible(page, LAST_NAME_SELECTORS, timeout=250):
        return True
    if find_visible(page, USERNAME_SELECTORS, timeout=250):
        return True
    if find_visible(page, AGE_SELECTORS, timeout=250):
        return True
    return bool(find_visible(page, PROFILE_STEP_SELECTORS, timeout=250))


def _current_step(page):
    if _is_profile_step(page):
        return "profile"
    if _find_otp_input(page, timeout=500):
        return "otp"
    if find_visible(page, PASSWORD_SELECTORS, timeout=500):
        return "password"
    if find_visible(page, EMAIL_SELECTORS, timeout=500):
        return "email"
    return "other"


def _wait_for_step(page, expected_steps, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        step = _current_step(page)
        if step in expected_steps:
            return step
        time.sleep(0.5)
    return _current_step(page)


def _fetch_verification_code(mail_client, email, timeout):
    logger.info("[invite] waiting for verification code")
    if hasattr(mail_client, "wait_for_verification_code"):
        try:
            result = mail_client.wait_for_verification_code(email, timeout=timeout)
            if isinstance(result, dict):
                return result.get("code")
            return result
        except Exception:
            return None

    started = time.time()
    seen_ids: set[int] = set()
    while time.time() - started < timeout:
        for email_meta in mail_client.search_emails_by_recipient(email, size=10):
            email_id = int(email_meta.get("id") or 0)
            if email_id and email_id in seen_ids:
                continue
            subject = str(email_meta.get("subject") or "").lower()
            if "invited" in subject or "invitation" in subject:
                if email_id:
                    seen_ids.add(email_id)
                continue
            detail = email_meta
            if email_id:
                try:
                    detail = mail_client.get_email(email_id)
                except Exception:
                    detail = email_meta
            code = mail_client.extract_verification_code(detail)
            if code:
                return code
            if email_id:
                seen_ids.add(email_id)
        time.sleep(3)
    return None


def register_with_invite(page, invite_link, email, mail_client, password=None):
    logger.info("[invite] open invite link")
    page.goto(invite_link, wait_until="domcontentloaded", timeout=60000)
    time.sleep(5)
    wait_for_cloudflare(page)
    screenshot(page, "reg_01_invite_page.png")

    step = _current_step(page)
    if step == "other":
        clicked = find_and_click(page, SIGNUP_BUTTON_SELECTORS, "sign-up", timeout=5000)
        if not clicked:
            screenshot(page, "reg_02_signup_not_found.png")
            raise RuntimeError("invite landing page did not expose a sign-up action")
        step = _wait_for_step(page, {"email", "password", "otp"}, timeout=15)
        screenshot(page, "reg_03_after_signup_click.png")

    if step == "email":
        email_input = find_visible(page, EMAIL_SELECTORS, "email input", timeout=5000)
        if not email_input:
            screenshot(page, "reg_04_email_not_found.png")
            raise RuntimeError("email input not found after sign-up")
        email_input.fill(email)
        time.sleep(0.5)
        if not _submit_auth_step(page, email_input, ["Continue", "继续"], "email step"):
            screenshot(page, "reg_05_email_submit_failed.png")
            raise RuntimeError("failed to submit email step")
        step = _wait_for_step(page, {"password", "otp", "profile"}, timeout=15)
        screenshot(page, "reg_05_after_email_submit.png")

    if step == "password":
        password_input = find_visible(page, PASSWORD_SELECTORS, "password input", timeout=5000)
        if not password_input:
            screenshot(page, "reg_06_password_not_found.png")
            raise RuntimeError("password input not found")
        if not password:
            import uuid

            password = f"Tmp_{uuid.uuid4().hex[:12]}!"
        password_input.fill(password)
        time.sleep(0.5)
        if not _submit_auth_step(page, password_input, ["Continue", "继续", "Log in"], "password step"):
            screenshot(page, "reg_07_password_submit_failed.png")
            raise RuntimeError("failed to submit password step")
        step = _wait_for_step(page, {"otp", "profile"}, timeout=15)
        screenshot(page, "reg_07_after_password_submit.png")

    if step == "otp" or _find_otp_input(page, timeout=5000):
        verification_code = _fetch_verification_code(mail_client, email, MAIL_TIMEOUT)
        if not verification_code:
            screenshot(page, "reg_09_code_timeout.png")
            logger.warning("[invite] failed to fetch verification code")
            return False, password

        otp_field = _fill_otp(page, verification_code)
        if not otp_field:
            logger.warning("[invite] verification code input not found")
            screenshot(page, "reg_10_code_not_found.png")
            return False, password

        time.sleep(0.5)
        if not _submit_auth_step(page, otp_field, ["Continue", "继续", "Verify"], "verification step"):
            screenshot(page, "reg_11_verify_submit_failed.png")
            return False, password
        otp_status, otp_detail = _wait_for_otp_result(page, timeout=15)
        if otp_status != "accepted" and _find_otp_input(page, timeout=500):
            logger.warning("[invite] verification step did not pass: %s %s", otp_status, otp_detail or "")
            screenshot(page, "reg_11_code_stuck.png")
            return False, password
    elif step != "profile":
        screenshot(page, "reg_08_not_in_otp_step.png")
        raise RuntimeError(f"registration did not reach verification step, current step={step}")
    else:
        logger.info("[invite] registration skipped verification step and entered profile directly")

    profile = _random_us_profile()
    logger.info(
        "[invite] generated profile name=%s username=%s age=%s",
        profile["full_name"],
        profile["username"],
        profile["age"],
    )

    profile_field = None
    filled_profile = False
    first_name_input = find_visible(page, FIRST_NAME_SELECTORS, "first name input", timeout=2000)
    last_name_input = find_visible(page, LAST_NAME_SELECTORS, "last name input", timeout=2000)
    if first_name_input or last_name_input:
        if first_name_input and _fill_text(first_name_input, profile["first_name"]):
            profile_field = first_name_input
            filled_profile = True
            time.sleep(0.2)
        if last_name_input and _fill_text(last_name_input, profile["last_name"]):
            profile_field = last_name_input
            filled_profile = True
            time.sleep(0.2)
    else:
        name_input = find_visible(page, NAME_SELECTORS, "name input", timeout=5000)
        if name_input and _fill_text(name_input, profile["full_name"]):
            profile_field = name_input
            filled_profile = True
            time.sleep(0.5)

    username_input = find_visible(page, USERNAME_SELECTORS, "username input", timeout=3000)
    if username_input and _fill_text(username_input, profile["username"]):
        profile_field = username_input
        filled_profile = True
        time.sleep(0.3)

    filled_age = False
    try:
        spinbuttons = page.locator('[role="spinbutton"]').all()
    except Exception:
        spinbuttons = []
    if len(spinbuttons) >= 3:
        filled_birth_date = True
        for spinbutton, value in zip(
            spinbuttons[:3],
            [profile["birth_year"], profile["birth_month"], profile["birth_day"]],
        ):
            if not _fill_text(spinbutton, value, page=page):
                filled_birth_date = False
                break
            time.sleep(0.3)
        if filled_birth_date:
            filled_age = True
            profile_field = spinbuttons[0]
    else:
        age_input = find_visible(
            page,
            AGE_SELECTORS,
            "age input",
            timeout=3000,
        )
        if age_input and _fill_text(age_input, profile["age"]):
            filled_age = True
            profile_field = age_input

    if filled_profile or filled_age:
        _submit_profile_step(page, profile_field)
        time.sleep(8)

    find_and_click(
        page,
        [
            'button:has-text("Accept")',
            'button:has-text("Agree")',
            'button:has-text("Join")',
            'button:has-text("Join workspace")',
            'button:has-text("加入")',
            'button:has-text("Accept invite")',
        ],
        "join workspace",
        timeout=5000,
    )
    time.sleep(5)

    current_url = page.url
    try:
        page_text = page.inner_text("body")[:500].lower()
    except Exception:
        page_text = ""
    if "chatgpt.com" in current_url and "auth" not in current_url:
        return True, password
    if "workspace" in page_text or "welcome" in page_text:
        return True, password
    return False, password


def run():
    mail_client = FreemailClient()
    mailbox = mail_client.create_temp_email()
    email = mailbox["email"]
    logger.info("[invite] temp mailbox: %s", email)
    logger.info("[invite] this script only keeps register_with_invite for local debugging")
    return True


def main():
    result = run()
    raise SystemExit(0 if result else 1)


if __name__ == "__main__":
    with sync_playwright():
        main()
