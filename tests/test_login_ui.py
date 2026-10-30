"""Headless UI tests for the sign-in flow (streamlit.testing AppTest), using the in-memory repo."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

import mediverse.services as services  # noqa: E402
from mediverse.auth.memory_repo import MemoryRepo  # noqa: E402
from mediverse.auth.otp import Delivery, OTPSender  # noqa: E402
from mediverse.auth.passwords import hash_password  # noqa: E402
from mediverse.auth.service import AuthService  # noqa: E402
from mediverse.biometrics.mock_face import MockFaceVerifier  # noqa: E402
from mediverse.biometrics.typing_rhythm import KeystrokeVerifier  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PW = "Correct-Horse-9!"


class CaptureSender(OTPSender):
    """Test double for the email channel: records codes, never returns them to the UI."""
    def __init__(self):
        self.sent = []

    def send(self, destination, code):
        self.sent.append(code)
        return Delivery("EMAIL", "a***@x.example")


SENDER = CaptureSender()


@pytest.fixture
def repo(monkeypatch):
    r = MemoryRepo()
    h = hash_password(PW, "ui-pepper", iterations=1000)
    r.add_user(user_id="U1", username="admin", role_code="ADMIN", password_hash=h, email="a@x.example")
    r.add_user(user_id="U2", username="lab", role_code="LAB_STAFF", password_hash=h, email="l@x.example")
    svc = AuthService(r, "ui-pepper", SENDER, MockFaceVerifier(), KeystrokeVerifier())
    monkeypatch.setattr(services, "auth_service", lambda: svc)
    monkeypatch.setattr(services, "needs_bootstrap", lambda: False)
    # AppTest (Streamlit 1.52) mocks the runtime registry, so CCv2 components cannot mount in tests.
    import mediverse.components as comps
    monkeypatch.setattr(comps, "_component", lambda: None)
    return r


def app():
    at = AppTest.from_file(str(ROOT / "app_pages" / "login.py"), default_timeout=30)
    at.session_state["auth_step"] = "PASSWORD"
    at.session_state["challenge"] = None
    at.session_state["session_token"] = None
    return at


def sign_in(at, user, pw, portal=None):
    portal = portal or ("Admin" if user == "admin" else "Staff user")
    at.radio[0].set_value(portal).run()
    at.text_input[0].input(user)
    at.text_input[1].input(pw)
    at.button[0].click().run()


def test_bad_password_shows_generic_error(repo):
    at = app().run()
    sign_in(at, "admin", "nope")
    assert any("Invalid username or password" in e.value for e in at.error)
    assert at.session_state["auth_step"] == "PASSWORD"


def test_page_asks_admin_or_user_first(repo):
    at = app().run()
    assert len(at.text_input) == 0
    assert any("Admin" in i.value and "Staff user" in i.value for i in at.info)


def test_admin_signs_in_with_password_only(repo):
    at = app().run()
    sign_in(at, "admin", PW)
    assert at.session_state["session_token"]
    assert not SENDER.sent or all(e["event_type"] != "OTP_SENT" for e in repo.events)


def test_wrong_portal_is_denied(repo):
    at = app().run()
    sign_in(at, "lab", PW, portal="Admin")
    assert not at.session_state["session_token"]
    assert any("Staff user" in e.value for e in at.error)
    at = app().run()
    sign_in(at, "admin", PW, portal="Staff user")
    assert not at.session_state["session_token"]


def test_staff_otp_is_emailed_never_shown_and_completes(repo):
    at = app().run()
    sign_in(at, "lab", PW)
    next(b for b in at.button if "one-time code" in b.label).click().run()
    assert at.session_state["auth_step"] == "OTP"
    code = SENDER.sent[-1]
    shown = [x.value for x in list(at.warning) + list(at.info) + list(at.caption) + list(at.markdown)]
    assert not any(code in s for s in shown)
    at.text_input[0].input(code)
    at.button[0].click().run()
    assert at.session_state["session_token"]
    assert any(e["event_type"] == "LOGIN" and e["outcome"] == "OK" for e in repo.events)


def test_lab_user_sees_biometric_step_with_deadline(repo):
    at = app().run()
    sign_in(at, "lab", PW)
    assert at.session_state["auth_step"] == "BIOMETRIC"
    assert any("Complete this step by" in c.value for c in at.caption)


def test_lab_user_can_choose_otp_fallback(repo):
    at = app().run()
    sign_in(at, "lab", PW)
    fallback = next(b for b in at.button if "one-time code" in b.label)
    fallback.click().run()
    assert at.session_state["auth_step"] == "OTP"
    assert any("OTP_FALLBACK" in e["reason_codes"] for e in repo.events)
