"""Administrator workflow tests: only admins enroll, no self-service, everything audited."""

import pytest

from mediverse.admin import AdminError, AdminService
from mediverse.auth.memory_repo import MemoryRepo
from mediverse.auth.otp import DemoOTPSender
from mediverse.auth.passwords import hash_password
from mediverse.auth.service import AuthService
from mediverse.authz import AccessDenied, Principal
from mediverse.biometrics.mock_face import MockFaceVerifier
from mediverse.biometrics.typing_rhythm import KeystrokeVerifier
from tests.test_security_units import face_png, typing_sample

PEPPER = "pep"
ADMIN_PW = "Admin-Password-9!"


@pytest.fixture
def svc():
    repo = MemoryRepo()
    repo.add_user(user_id="A1", username="admin", role_code="ADMIN",
                  password_hash=hash_password(ADMIN_PW, PEPPER, iterations=1000), email="a@x.example")
    auth = AuthService(repo, PEPPER, DemoOTPSender(), MockFaceVerifier(), KeystrokeVerifier())
    return repo, auth, AdminService(repo, auth), Principal("A1", "admin", "ADMIN")


def new_lab(admin_svc, admin):
    return admin_svc.create_user(admin, username="lab.one", display_name="Lab One", role="LAB_STAFF",
                                 email="lab1@x.example", department_id="D01", staff_id=None,
                                 temp_password="Temp-Password-77!")


def enroll(admin_svc, admin, uid, pw=ADMIN_PW, consent=True, evidence="Staff ID card checked in person"):
    return admin_svc.enroll_biometrics(admin, uid, face_samples=[face_png(5), face_png(5, 1), face_png(5, 2)],
                                       typing_samples=[typing_sample(90, 160, 10, s) for s in range(5)],
                                       admin_password=pw, identity_evidence=evidence, consent_confirmed=consent)


def test_create_and_enroll_lab_user_then_biometric_login(svc):
    repo, auth, a, admin = svc
    uid = new_lab(a, admin)
    summary = enroll(a, admin, uid)
    assert summary["face_samples"] == 3
    assert repo.bio[uid]["status"] == "ACTIVE"
    assert [x["action"] for x in repo.admin_audit] == ["CREATE_USER", "ENROLL_BIOMETRICS"]
    assert "templates" not in repr(repo.admin_audit)
    step = auth.login_password("lab.one", "Temp-Password-77!")
    assert step.kind == "BIOMETRIC"
    done = auth.verify_biometrics(step.token, [face_png(5, 1, 0.01), face_png(5, 3, 0.01)], typing_sample(90, 160, 10, 99))
    assert done.kind == "SESSION"


def test_non_admin_cannot_create_or_enroll(svc):
    repo, auth, a, admin = svc
    uid = new_lab(a, admin)
    lab = Principal(uid, "lab.one", "LAB_STAFF")
    with pytest.raises(AccessDenied):
        enroll(a, lab, uid)
    with pytest.raises(AccessDenied):
        a.create_user(lab, username="x.y", display_name="x", role="ADMIN", email=None, department_id=None,
                      staff_id=None, temp_password="Temp-Password-77!")


def test_admin_cannot_modify_self(svc):
    _, _, a, admin = svc
    with pytest.raises(AdminError):
        a.change_role(admin, "A1", "DOCTOR")
    with pytest.raises(AdminError):
        a.set_status(admin, "A1", "INACTIVE")


def test_enrollment_requires_reauth_consent_evidence(svc):
    repo, _, a, admin = svc
    uid = new_lab(a, admin)
    with pytest.raises(AdminError, match="re-verification"):
        enroll(a, admin, uid, pw="wrong")
    assert repo.admin_audit[-1]["action"] == "ENROLL_DENIED"
    with pytest.raises(AdminError, match="consent"):
        enroll(a, admin, uid, consent=False)
    with pytest.raises(AdminError, match="identity evidence"):
        enroll(a, admin, uid, evidence="ok")


def test_face_only_enrollment_then_login_with_time_limit(svc):
    _, auth, a, admin = svc
    uid = new_lab(a, admin)
    a.enroll_biometrics(admin, uid, face_samples=[face_png(5), face_png(5, 1), face_png(5, 2)], consent_confirmed=True)
    assert auth.repo.bio[uid]["typing_profile"] is None
    a.set_details(admin, uid, designation=None, typing_max_seconds=30)
    probes = [face_png(5, 1, 0.01), face_png(5, 3, 0.01)]
    ok = auth.verify_biometrics(auth.login_password("lab.one", "Temp-Password-77!").token, probes,
                                typing_sample(90, 160, 10, 99))
    assert ok.kind == "SESSION"
    a.set_details(admin, uid, designation=None, typing_max_seconds=2)
    slow = auth.verify_biometrics(auth.login_password("lab.one", "Temp-Password-77!").token, probes,
                                  typing_sample(90, 160, 10, 98))
    assert slow.kind == "BIOMETRIC" and "TYPING_TOO_SLOW" in slow.reasons


def test_face_only_enrollment_needs_consent(svc):
    _, _, a, admin = svc
    uid = new_lab(a, admin)
    with pytest.raises(AdminError, match="consent"):
        a.enroll_biometrics(admin, uid, face_samples=[face_png(5)] * 3, consent_confirmed=False)


def test_any_role_can_be_enrolled_and_then_needs_face_and_typing(svc):
    _, auth, a, admin = svc
    uid = a.create_user(admin, username="dr.x", display_name="Dr X", role="DOCTOR", email="drx@x.example",
                        department_id=None, staff_id=None, temp_password="Temp-Password-77!", designation="Consultant")
    enroll(a, admin, uid)
    assert auth.repo.get_user(uid)["designation"] == "Consultant"
    assert auth.login_password("dr.x", "Temp-Password-77!").kind == "BIOMETRIC"


def test_typing_slower_than_admin_limit_fails_and_falls_back_to_otp(svc):
    _, auth, a, admin = svc
    uid = new_lab(a, admin)
    enroll(a, admin, uid)
    a.set_details(admin, uid, designation="Lab Tech", typing_max_seconds=2.0)  # sample takes ~7 s
    step = auth.login_password("lab.one", "Temp-Password-77!")
    out = auth.verify_biometrics(step.token, [face_png(5, 1, 0.01), face_png(5, 3, 0.01)], typing_sample(90, 160, 10, 99))
    assert out.kind == "BIOMETRIC" and "TYPING_TOO_SLOW" in out.reasons


def test_typing_limit_bounds(svc):
    _, _, a, admin = svc
    uid = new_lab(a, admin)
    with pytest.raises(AdminError, match="between 2 and 120"):
        a.set_details(admin, uid, designation=None, typing_max_seconds=500)


def test_otp_delivery_failure_denies_instead_of_showing_code(svc):
    repo, auth, _, _ = svc

    class Broken(DemoOTPSender):
        def send(self, destination, code):
            raise RuntimeError("email integration missing")

    auth.sender = Broken()
    step = auth.login_password("admin", ADMIN_PW)
    assert step.kind == "DENIED" and "OTP_DELIVERY_FAILED" in step.reasons and step.delivery is None


def test_reset_enrollment_forces_otp(svc):
    repo, auth, a, admin = svc
    uid = new_lab(a, admin)
    enroll(a, admin, uid)
    a.reset_enrollment(admin, uid, "new device")
    step = auth.login_password("lab.one", "Temp-Password-77!")
    out = auth.verify_biometrics(step.token, [face_png(5, 1, 0.01), face_png(5, 3, 0.01)], typing_sample(90, 160, 10, 99))
    assert out.kind == "OTP"


def test_deactivation_revokes_sessions(svc):
    repo, auth, a, admin = svc
    uid = a.create_user(admin, username="nurse.a", display_name="N", role="NURSE", email=None, department_id="D01",
                        staff_id=None, temp_password="Temp-Password-77!")
    tok = auth.login_password("nurse.a", "Temp-Password-77!").token
    assert auth.validate_session(tok)
    a.set_status(admin, uid, "INACTIVE")
    assert auth.validate_session(tok) is None


def test_bootstrap_only_once_and_needs_token():
    repo = MemoryRepo()
    auth = AuthService(repo, PEPPER, DemoOTPSender(), MockFaceVerifier(), KeystrokeVerifier())
    a = AdminService(repo, auth)
    kw = dict(username="root.admin", display_name="Root", email="root@x.example", password="Bootstrap-Pass-9!")
    with pytest.raises(AdminError, match="token"):
        a.bootstrap_first_admin("wrong", "tok123", **kw)
    with pytest.raises(AdminError, match="token"):
        a.bootstrap_first_admin("tok123", None, **kw)
    uid = a.bootstrap_first_admin("tok123", "tok123", **kw)
    assert repo.users[uid]["role_code"] == "ADMIN"
    with pytest.raises(AdminError, match="already exists"):
        a.bootstrap_first_admin("tok123", "tok123", **dict(kw, username="second.admin"))


def test_input_validation(svc):
    _, _, a, admin = svc
    with pytest.raises(AdminError):
        a.create_user(admin, username="Bad Name!", display_name="x", role="NURSE", email=None, department_id=None,
                      staff_id=None, temp_password="Temp-Password-77!")
    with pytest.raises(AdminError):
        a.create_user(admin, username="ok.name", display_name="x", role="NURSE", email=None, department_id=None,
                      staff_id=None, temp_password="weak")
    with pytest.raises(AdminError):
        a.create_user(admin, username="ok.name", display_name="x", role="NURSE", email=None, department_id=None,
                      staff_id=None, temp_password="Temp-Password-77!", flags=["SUPERUSER"])
