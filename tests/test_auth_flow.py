"""Authentication state-machine tests covering success and every failure path."""

from datetime import datetime, timedelta

import pytest

from mediverse.auth import otp
from mediverse.auth.memory_repo import MemoryRepo
from mediverse.auth.passwords import hash_password
from mediverse.auth.service import AuthService
from mediverse.biometrics.base import FaceVerifier, TypingVerifier, VerificationResult

PEPPER = "test-pepper-not-secret"
PW = "Correct-Horse-9!"


class Clock:
    def __init__(self):
        self.t = datetime(2026, 1, 1, 9, 0)

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


class StubFace(FaceVerifier):
    def __init__(self, status="PASS"):
        self.status = status

    def enroll(self, samples, user_key):
        return {"templates": ["x"]}

    def verify(self, template, probe_frames, user_key, threshold):
        return VerificationResult(self.status, 0.9 if self.status == "PASS" else 0.4, [f"FACE_{self.status}"])


class StubTyping(TypingVerifier):
    def __init__(self, status="PASS"):
        self.status = status

    def enroll(self, samples):
        return {"mean": [1]}

    def verify(self, profile, sample, threshold):
        return VerificationResult(self.status, 0.8, [f"T_{self.status}"])


class CapturingSender(otp.OTPSender):
    def __init__(self):
        self.codes = []

    def send(self, destination, code):
        self.codes.append(code)
        return otp.Delivery("TEST", otp.mask_email(destination))


@pytest.fixture
def env():
    repo, clock, sender = MemoryRepo(), Clock(), CapturingSender()
    h = hash_password(PW, PEPPER, iterations=1000)
    repo.add_user(user_id="U1", username="labtech", role_code="LAB_STAFF", password_hash=h, email="lab@x.example")
    repo.add_user(user_id="U2", username="doctor", role_code="DOCTOR", password_hash=h, email="doc@x.example")
    repo.add_user(user_id="U3", username="admin", role_code="ADMIN", password_hash=h, email="adm@x.example")
    repo.add_user(user_id="U4", username="nocontact", role_code="LAB_STAFF", password_hash=h, email=None)
    for uid in ("U1", "U4"):
        repo.bio[uid] = {"status": "ACTIVE", "enroll_salt": "s", "face_template": {"templates": ["x"]},
                         "typing_profile": {"mean": [1]}}

    def make(face="PASS", typing="PASS", max_tries=1):
        return AuthService(repo, PEPPER, sender, StubFace(face), StubTyping(typing), clock=clock,
                           config={"BIOMETRIC_MAX_ATTEMPTS": max_tries})

    return repo, clock, sender, make


def events(repo, typ):
    return [e for e in repo.events if e["event_type"] == typ]


def test_face_failures_get_retries_before_otp(env):
    repo, _, sender, make = env
    svc = make(face="FAIL", max_tries=3)
    tok = svc.login_password("labtech", PW).token
    a = svc.verify_biometrics(tok, [b"f1", b"f2"], {"events": []})
    b = svc.verify_biometrics(tok, [b"f1", b"f2"], {"events": []})
    assert a.kind == b.kind == "BIOMETRIC" and "1 attempt(s) left" in b.message and not sender.codes
    c = svc.verify_biometrics(tok, [b"f1", b"f2"], {"events": []})
    assert c.kind == "OTP" and len(sender.codes) == 1
    ok = make(face="PASS", max_tries=3)
    tok = ok.login_password("labtech", PW).token
    assert make(face="FAIL", max_tries=3).verify_biometrics(tok, [b"f1", b"f2"], {"events": []}).kind == "BIOMETRIC"
    assert ok.verify_biometrics(tok, [b"f1", b"f2"], {"events": []}).kind == "SESSION"


def test_non_lab_role_password_only_session(env):
    repo, _, _, make = env
    s = make().login_password("doctor", PW)
    assert s.kind == "SESSION"
    assert make().validate_session(s.token)["user_id"] == "U2"


def test_bad_password_generic_message_and_unknown_user(env):
    _, _, _, make = env
    a = make().login_password("doctor", "wrong")
    b = make().login_password("ghost", "wrong")
    assert a.kind == b.kind == "DENIED"
    assert a.message == b.message  # no username enumeration


def test_lockout_after_max_failures_and_alert(env):
    repo, clock, _, make = env
    svc = make()
    for _ in range(5):
        svc.login_password("doctor", "wrong")
    assert svc.login_password("doctor", PW).reasons == ["ACCOUNT_LOCKED"]  # correct pw still blocked
    assert events(repo, "SECURITY_ALERT")
    clock.advance(minutes=16)
    assert svc.login_password("doctor", PW).kind == "SESSION"


def test_inactive_account_denied_even_with_correct_password(env):
    repo, _, _, make = env
    repo.users["U2"]["status"] = "INACTIVE"
    assert make().login_password("doctor", PW).reasons == ["ACCOUNT_INACTIVE"]


def test_lab_both_biometrics_pass_no_otp(env):
    repo, _, sender, make = env
    svc = make()
    s = svc.login_password("labtech", PW)
    assert s.kind == "BIOMETRIC"
    s2 = svc.verify_biometrics(s.token, [b"f1", b"f2"], {"phrase": "x"})
    assert s2.kind == "SESSION"
    assert sender.codes == []


@pytest.mark.parametrize("face,typing", [("FAIL", "PASS"), ("PASS", "FAIL"), ("INCONCLUSIVE", "PASS"),
                                         ("PASS", "UNAVAILABLE"), ("FAIL", "FAIL")])
def test_lab_any_biometric_problem_falls_back_to_otp(env, face, typing):
    _, _, sender, make = env
    svc = make(face, typing)
    s = svc.verify_biometrics(svc.login_password("labtech", PW).token, [b"a", b"b"], {"p": 1})
    assert s.kind == "OTP"
    s2 = svc.verify_otp(s.token, sender.codes[-1])
    assert s2.kind == "SESSION"


def test_missing_biometric_inputs_fall_back(env):
    _, _, _, make = env
    svc = make()
    s = svc.verify_biometrics(svc.login_password("labtech", PW).token, None, None)
    assert s.kind == "OTP" and "OTP_FALLBACK" in s.reasons


def test_not_enrolled_falls_back(env):
    repo, _, _, make = env
    repo.bio["U1"]["status"] = "RESET"
    svc = make()
    s = svc.verify_biometrics(svc.login_password("labtech", PW).token, [b"a", b"b"], {"p": 1})
    assert s.kind == "OTP"


def test_biometric_step_cannot_be_reached_without_password(env):
    _, _, _, make = env
    assert make().verify_biometrics("forged-token", [b"a", b"b"], {}).kind == "DENIED"


def test_biometric_cannot_be_retried_after_fallback(env):
    _, _, _, make = env
    svc = make("FAIL")
    tok = svc.login_password("labtech", PW).token
    svc.verify_biometrics(tok, [b"a", b"b"], {})
    assert svc.verify_biometrics(tok, [b"a", b"b"], {}).kind == "DENIED"


def test_otp_wrong_then_exhausted_denies_with_recovery(env):
    repo, _, sender, make = env
    svc = make("FAIL")
    s = svc.verify_biometrics(svc.login_password("labtech", PW).token, [b"a", b"b"], {})
    bad = "000000" if sender.codes[-1] != "000000" else "111111"
    assert svc.verify_otp(s.token, bad).kind == "RETRY_OTP"
    assert svc.verify_otp(s.token, bad).kind == "RETRY_OTP"
    d = svc.verify_otp(s.token, bad)
    assert d.kind == "DENIED" and "administrator" in d.message
    # correct code after exhaustion is rejected
    assert svc.verify_otp(s.token, sender.codes[-1]).kind == "DENIED"


def test_otp_expiry(env):
    _, clock, sender, make = env
    svc = make("FAIL")
    s = svc.verify_biometrics(svc.login_password("labtech", PW).token, [b"a", b"b"], {})
    clock.advance(minutes=6)
    assert svc.verify_otp(s.token, sender.codes[-1]).reasons == ["OTP_EXPIRED"]


def test_otp_single_use(env):
    _, _, sender, make = env
    svc = make("FAIL")
    s = svc.verify_biometrics(svc.login_password("labtech", PW).token, [b"a", b"b"], {})
    assert svc.verify_otp(s.token, sender.codes[-1]).kind == "SESSION"
    assert svc.verify_otp(s.token, sender.codes[-1]).kind == "DENIED"


def test_otp_no_contact_channel_denied(env):
    _, _, _, make = env
    svc = make("FAIL")
    s = svc.verify_biometrics(svc.login_password("nocontact", PW).token, [b"a", b"b"], {})
    assert s.kind == "DENIED" and "NO_VERIFIED_CONTACT" in s.reasons


def test_otp_send_rate_limit(env):
    _, _, _, make = env
    svc = make("FAIL")
    kinds = [svc.verify_biometrics(svc.login_password("labtech", PW).token, [b"a", b"b"], {}).kind for _ in range(4)]
    assert kinds == ["OTP", "OTP", "OTP", "DENIED"]


def test_admin_requires_otp(env):
    _, _, sender, make = env
    svc = make()
    s = svc.login_password("admin", PW)
    assert s.kind == "OTP"
    assert svc.verify_otp(s.token, sender.codes[-1]).kind == "SESSION"


def test_session_idle_expiry_and_deactivation(env):
    repo, clock, _, make = env
    svc = make()
    tok = svc.login_password("doctor", PW).token
    clock.advance(minutes=29)
    assert svc.validate_session(tok)
    clock.advance(minutes=31)
    assert svc.validate_session(tok) is None
    tok2 = svc.login_password("doctor", PW).token
    repo.users["U2"]["status"] = "INACTIVE"
    assert svc.validate_session(tok2) is None


def test_session_revoked_on_role_change(env):
    repo, _, _, make = env
    svc = make()
    tok = svc.login_password("doctor", PW).token
    repo.users["U2"]["role_code"] = "NURSE"
    assert svc.validate_session(tok) is None


def test_logout_revokes(env):
    _, _, _, make = env
    svc = make()
    tok = svc.login_password("doctor", PW).token
    svc.logout(tok)
    assert svc.validate_session(tok) is None


def test_logs_never_contain_secrets(env):
    repo, _, sender, make = env
    svc = make("FAIL")
    s = svc.verify_biometrics(svc.login_password("labtech", PW).token, [b"RAWFACE", b"b"], {"phrase": "secret"})
    svc.verify_otp(s.token, sender.codes[-1])
    blob = repr(repo.events) + repr(repo.challenges) + repr(repo.sessions)
    assert PW not in blob
    assert sender.codes[-1] not in blob
    assert "RAWFACE" not in blob
    assert s.token not in blob  # only hashes of tokens are stored
