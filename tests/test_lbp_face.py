"""Tests for the LBP face verifier and email sign-in."""

import io
import random

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from mediverse.biometrics.lbp_face import LBPFaceVerifier, chi_square, lbp_features, lbp_image  # noqa: E402


def textured(seed: int, noise: float = 0.0, size: int = 160) -> np.ndarray:
    """Deterministic structured pattern per subject (stands in for a detected face crop);
    ``noise`` simulates capture-to-capture variation."""
    rng = random.Random(seed)
    img = np.full((size, size), 60, np.uint8)
    for _ in range(14):
        c = (rng.randint(10, size - 10), rng.randint(10, size - 10))
        axes = (rng.randint(6, 30), rng.randint(6, 30))
        cv2.ellipse(img, c, axes, rng.randint(0, 180), 0, 360, rng.randint(110, 255), -1)
    img = cv2.GaussianBlur(img, (0, 0), 1.5)
    if noise:
        n = np.random.default_rng(int(noise * 1e4) + seed).normal(0, noise * 255, img.shape)
        img = np.clip(img + n, 0, 255).astype(np.uint8)
    return img


def png(arr: np.ndarray) -> bytes:
    return cv2.imencode(".png", arr)[1].tobytes()


def make_verifier(boxes: dict):
    """Inject a deterministic detector: decode PNG, use whole image as the face, box from lookup."""
    def detect(gray):
        key = int(gray[0, 0]) + int(gray[-1, -1])
        face = cv2.equalizeHist(cv2.resize(gray, (128, 128)))
        return face, boxes.get(key, (10, 10, 100, 100)), None
    return LBPFaceVerifier(detector=detect, sharp=lambda g: 100.0)


def test_lbp_uniform_codes_in_range():
    codes = lbp_image(textured(1))
    assert codes.min() >= 0 and codes.max() <= 58
    f = lbp_features(cv2.resize(textured(1), (128, 128)))
    assert f.shape == (64 * 59,) and abs(f.sum() - 64) < 1e-6


def test_chi_square_identity_and_symmetry():
    a, b = lbp_features(cv2.resize(textured(1), (128, 128))), lbp_features(cv2.resize(textured(2), (128, 128)))
    assert chi_square(a, a) == 0
    assert abs(chi_square(a, b) - chi_square(b, a)) < 1e-12 and chi_square(a, b) > 0


def boxes_for(*imgs_boxes):
    return {int(i[0, 0]) + int(i[-1, -1]): b for i, b in imgs_boxes}


def test_same_subject_passes_other_subject_fails():
    enroll = [textured(7, 0.004 * (k + 1)) for k in range(3)]
    p1, p2 = textured(7, 0.005), textured(7, 0.006)
    o1, o2 = textured(99, 0.01), textured(99, 0.02)
    v = make_verifier(boxes_for((p1, (10, 10, 100, 100)), (p2, (20, 14, 104, 104)),
                                (o1, (10, 10, 100, 100)), (o2, (20, 14, 104, 104))))
    key = b"k" * 32
    tpl = v.enroll([png(x) for x in enroll], key)
    assert tpl["scheme"] == "lbp-u2-8x8-chi2-v1" and 0.35 <= tpl["threshold"] <= 1.2
    assert v.verify(tpl, [png(p1), png(p2)], key, 0.85).status == "PASS"
    assert v.verify(tpl, [png(o1), png(o2)], key, 0.85).status == "FAIL"


def test_liveness_requires_head_movement_and_distinct_frames():
    enroll = [textured(7, 0.004 * (k + 1)) for k in range(3)]
    p1, p2 = textured(7, 0.005), textured(7, 0.006)
    v = make_verifier(boxes_for((p1, (10, 10, 100, 100)), (p2, (11, 10, 100, 100))))  # 1% move
    key = b"k" * 32
    tpl = v.enroll([png(x) for x in enroll], key)
    assert v.verify(tpl, [png(p1), png(p2)], key, 0.85).reasons == ["LIVENESS_NO_HEAD_MOVEMENT"]
    assert v.verify(tpl, [png(p1), png(p1)], key, 0.85).reasons == ["LIVENESS_STATIC_FRAMES"]


def test_template_is_keyed():
    enroll = [textured(7, 0.004 * (k + 1)) for k in range(3)]
    p1, p2 = textured(7, 0.005), textured(7, 0.006)
    v = make_verifier(boxes_for((p1, (10, 10, 100, 100)), (p2, (20, 14, 104, 104))))
    tpl = v.enroll([png(x) for x in enroll], b"old-key")
    assert v.verify(tpl, [png(p1), png(p2)], b"new-key", 0.85).status != "PASS"


def test_real_detector_rejects_image_without_face():
    v = LBPFaceVerifier()
    with pytest.raises(ValueError, match="no face"):
        v.enroll([png(textured(1))] * 3, b"k")


def test_blurry_probe_inconclusive():
    enroll = [textured(7, 0.004 * (k + 1)) for k in range(3)]
    v = make_verifier({})
    tpl = v.enroll([png(x) for x in enroll], b"k")
    blurry = LBPFaceVerifier(detector=v.detect, sharp=lambda g: 1.0)
    assert blurry.verify(tpl, [png(textured(7, 0.005)), png(textured(7, 0.007))], b"k", 0.85).reasons == ["IMAGE_TOO_BLURRY"]


def test_email_login():
    from mediverse.auth.memory_repo import MemoryRepo
    from mediverse.auth.otp import DemoOTPSender
    from mediverse.auth.passwords import hash_password
    from mediverse.auth.service import AuthService
    from mediverse.biometrics.typing_rhythm import KeystrokeVerifier
    repo = MemoryRepo()
    repo.add_user(user_id="U1", username="dr.x", role_code="DOCTOR", email="dr.x@h.example",
                  password_hash=hash_password("Pass-Word-123!", "p", iterations=1000))
    svc = AuthService(repo, "p", DemoOTPSender(), LBPFaceVerifier(), KeystrokeVerifier())
    assert svc.login_password("DR.X@h.example", "Pass-Word-123!").kind == "SESSION"
    assert svc.login_password("nobody@h.example", "Pass-Word-123!").kind == "DENIED"
    assert repo.events[-1]["reason_codes"] == ["UNKNOWN_USER"]


