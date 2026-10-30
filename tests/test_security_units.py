import io
import random

import pytest
from PIL import Image, ImageDraw

from mediverse.auth.otp import codes_match, generate_code, hash_code
from mediverse.auth.passwords import hash_password, password_policy_errors, verify_password
from mediverse.authz import AccessDenied, Perm, Principal, Role, ROLE_PERMS
from mediverse.biometrics.mock_face import MockFaceVerifier
from mediverse.biometrics.typing_rhythm import PHRASE, KeystrokeVerifier


# --- passwords / otp --------------------------------------------------------
def test_password_roundtrip_and_pepper():
    h = hash_password("Abcdef-12345!", "p1", iterations=1000)
    assert verify_password("Abcdef-12345!", h, "p1")
    assert not verify_password("Abcdef-12345!", h, "p2")
    assert not verify_password("wrong", h, "p1")
    assert h != hash_password("Abcdef-12345!", "p1", iterations=1000)  # salted
    assert "Abcdef" not in h


def test_password_malformed_hash():
    assert not verify_password("x", "garbage", "p")
    assert not verify_password("x", None, "p")


def test_password_policy():
    assert password_policy_errors("short")
    assert password_policy_errors("labuser-Strong-9!", "labuser")
    assert password_policy_errors("Very-Strong-Pass-9") == []


def test_otp_hash():
    c = generate_code()
    assert len(c) == 6 and c.isdigit()
    h = hash_code(c, "ch1", "p")
    assert codes_match(c, "ch1", h, "p") and not codes_match(c, "ch2", h, "p")


# --- authz ------------------------------------------------------------------
def test_restricted_requires_flag():
    d = Principal("u", "d", "DOCTOR")
    assert not d.can(Perm.PSYCH_READ) and not d.can(Perm.MORTALITY_READ)
    d2 = Principal("u", "d", "DOCTOR", flags=["PSYCH_ACCESS"])
    assert d2.can(Perm.PSYCH_READ) and not d2.can(Perm.MORTALITY_READ)


def test_flag_without_role_grant_is_useless():
    assert not Principal("u", "f", "FINANCE_STAFF", flags=["PSYCH_ACCESS"]).can(Perm.PSYCH_READ)


@pytest.mark.parametrize("role,perm,allowed", [
    ("LAB_STAFF", Perm.LAB_READ, True), ("LAB_STAFF", Perm.PAYROLL_READ, False),
    ("LAB_STAFF", Perm.ENROLL_MANAGE, False), ("LAB_STAFF", Perm.IMAGING_READ, False),
    ("NURSE", Perm.CLINICAL_READ, False), ("FINANCE_STAFF", Perm.PATIENT_READ, False),
    ("MANAGEMENT", Perm.PATIENT_PII, False), ("ADMIN", Perm.PATIENT_READ, False),
    ("PHARMACIST", Perm.PHARMACY_READ, True), ("RADIOLOGY_STAFF", Perm.LAB_READ, False),
])
def test_matrix(role, perm, allowed):
    assert Principal("u", "x", role).can(perm) is allowed


def test_only_admin_manages_enrollment():
    assert [r for r, p in ROLE_PERMS.items() if Perm.ENROLL_MANAGE in p] == [Role.ADMIN]


def test_require_raises():
    with pytest.raises(AccessDenied):
        Principal("u", "x", "NURSE").require(Perm.PAYROLL_READ)


def test_nurse_department_scope():
    assert Principal("u", "n", "NURSE", department_id="D03").department_scope == "D03"
    assert Principal("u", "d", "DOCTOR", department_id="D03").department_scope is None


# --- mock face --------------------------------------------------------------
def face_png(seed: int, shift: int = 0, noise: float = 0.0) -> bytes:
    rnd = random.Random(seed)
    img = Image.new("L", (128, 128), 40)
    d = ImageDraw.Draw(img)
    for _ in range(12):
        x, y = rnd.randint(0, 110), rnd.randint(0, 110)
        d.ellipse([x + shift, y, x + shift + rnd.randint(8, 30), y + rnd.randint(8, 30)], fill=rnd.randint(120, 255))
    if noise:
        n = random.Random(seed * 7 + shift)
        px = img.load()
        for _ in range(int(128 * 128 * noise)):
            px[n.randint(0, 127), n.randint(0, 127)] = n.randint(0, 255)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_face_same_subject_passes_other_fails():
    v = MockFaceVerifier()
    key = b"k" * 32
    tpl = v.enroll([face_png(1, 0), face_png(1, 1), face_png(1, 2)], key)
    assert "templates" in tpl and all(len(t) == 64 for t in tpl["templates"])
    assert v.verify(tpl, [face_png(1, 1, 0.01), face_png(1, 3, 0.01)], key, 0.85).status == "PASS"
    assert v.verify(tpl, [face_png(99, 0), face_png(99, 2)], key, 0.85).status != "PASS"


def test_face_liveness_rejects_identical_frames():
    v = MockFaceVerifier()
    key = b"k" * 32
    tpl = v.enroll([face_png(1), face_png(1, 1), face_png(1, 2)], key)
    f = face_png(1)
    assert v.verify(tpl, [f, f], key, 0.85).reasons == ["LIVENESS_STATIC_FRAMES"]


def test_face_template_is_cancelable():
    v = MockFaceVerifier()
    tpl = v.enroll([face_png(1), face_png(1, 1), face_png(1, 2)], b"old-key")
    # after enrollment reset the key changes, so the old template no longer matches
    assert v.verify(tpl, [face_png(1, 1, 0.01), face_png(1, 3, 0.01)], b"new-key", 0.85).status != "PASS"


def test_face_needs_three_enrollment_samples():
    with pytest.raises(ValueError):
        MockFaceVerifier().enroll([face_png(1)], b"k")


# --- typing -----------------------------------------------------------------
def typing_sample(dwell: float, flight: float, jitter: float, seed: int, phrase: str = PHRASE) -> dict:
    r = random.Random(seed)
    t, ev = 0.0, []
    for ch in phrase:
        ev.append({"k": ch + str(t), "t": "d", "ms": t})
        ev.append({"k": ch + str(t), "t": "u", "ms": t + dwell + r.uniform(-jitter, jitter)})
        t += flight + r.uniform(-jitter, jitter)
    return {"phrase": phrase, "events": ev}


def test_typing_enroll_and_verify():
    v = KeystrokeVerifier()
    prof = v.enroll([typing_sample(90, 160, 10, s) for s in range(5)])
    assert v.verify(prof, typing_sample(90, 160, 10, 42), 0.7).status == "PASS"
    assert v.verify(prof, typing_sample(200, 400, 10, 43), 0.7).status == "FAIL"


def test_typing_wrong_phrase_inconclusive():
    v = KeystrokeVerifier()
    prof = v.enroll([typing_sample(90, 160, 10, s) for s in range(5)])
    assert v.verify(prof, typing_sample(90, 160, 10, 1, "something else"), 0.7).status == "INCONCLUSIVE"


def test_typing_requires_five_samples():
    with pytest.raises(ValueError):
        KeystrokeVerifier().enroll([typing_sample(90, 160, 10, 1)])


def test_typing_not_enrolled():
    assert KeystrokeVerifier().verify({}, typing_sample(90, 160, 10, 1), 0.7).status == "UNAVAILABLE"
