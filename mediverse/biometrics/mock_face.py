"""DEMO face matcher: keyed, cancelable template over a 16x16 average-hash.

* Images are reduced to 256 bits immediately; raw bytes are discarded and never stored.
* The bit vector is XOR-masked and permuted with a per-user key derived from the
  server pepper + a per-enrollment salt, so stored templates cannot be compared
  across users or re-used after an enrollment reset.
* "Liveness" is simulated: two probe frames must differ slightly (subject moved)
  but not be totally different. This does NOT stop real presentation attacks.
"""

from __future__ import annotations

import hashlib
import io
import random

from .base import FaceVerifier, VerificationResult

BITS = 256
SIDE = 16


def average_hash(image_bytes: bytes) -> list[int]:
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img).convert("L")
    w, h = img.size
    s = min(w, h)  # centre crop to the face region of a webcam frame
    img = img.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2)).resize((SIDE, SIDE))
    px = list(img.tobytes())  # mode "L": one byte per pixel
    mean = sum(px) / len(px)
    return [1 if p > mean else 0 for p in px]


def _transform(bits: list[int], user_key: bytes) -> list[int]:
    rng = random.Random(hashlib.sha256(user_key).digest())
    perm = list(range(BITS))
    rng.shuffle(perm)
    mask = [rng.getrandbits(1) for _ in range(BITS)]
    return [bits[perm[i]] ^ mask[i] for i in range(BITS)]


def _pack(bits: list[int]) -> str:
    return int("".join(map(str, bits)), 2).to_bytes(BITS // 8, "big").hex()


def _unpack(h: str) -> list[int]:
    return [int(b) for b in bin(int(h, 16))[2:].zfill(BITS)]


def similarity(a: list[int], b: list[int]) -> float:
    return 1 - sum(x != y for x, y in zip(a, b)) / BITS


class MockFaceVerifier(FaceVerifier):
    LIVENESS_MIN_DIFF = 0.01   # frames must not be byte-identical images
    LIVENESS_MAX_DIFF = 0.35   # ...but must still look like the same scene
    FLOOR = 0.75               # lowest per-user match threshold

    def enroll(self, samples: list[bytes], user_key: bytes) -> dict:
        if len(samples) < 3:
            raise ValueError("at least 3 face samples are required")
        hashes = [average_hash(s) for s in samples]
        consistency = min(similarity(hashes[i], hashes[j]) for i in range(len(hashes)) for j in range(i + 1, len(hashes)))
        return {"scheme": "demo-ahash-cancelable-v1",
                "templates": [_pack(_transform(h, user_key)) for h in hashes],
                "enroll_consistency": round(consistency, 3)}

    def verify(self, template: dict, probe_frames: list[bytes], user_key: bytes, threshold: float) -> VerificationResult:
        if not template or not template.get("templates"):
            return VerificationResult("UNAVAILABLE", reasons=["FACE_NOT_ENROLLED"])
        if len(probe_frames) < 2:
            return VerificationResult("INCONCLUSIVE", reasons=["LIVENESS_FRAMES_MISSING"])
        try:
            probes = [average_hash(f) for f in probe_frames[:2]]
        except Exception:
            return VerificationResult("INCONCLUSIVE", reasons=["FACE_IMAGE_UNREADABLE"])
        diff = 1 - similarity(probes[0], probes[1])
        if diff < self.LIVENESS_MIN_DIFF:
            return VerificationResult("FAIL", reasons=["LIVENESS_STATIC_FRAMES"])
        if diff > self.LIVENESS_MAX_DIFF:
            return VerificationResult("INCONCLUSIVE", reasons=["LIVENESS_SCENE_CHANGED"])
        enrolled = [_unpack(t) for t in template["templates"]]
        score = max(similarity(_transform(p, user_key), e) for p in probes for e in enrolled)
        # Per-user threshold: a user whose own enrollment photos agree only at 0.70 cannot be
        # expected to reach 0.85 at sign-in. Never below FLOOR (unrelated faces score ~0.5-0.65).
        threshold = min(threshold, max(self.FLOOR, float(template.get("enroll_consistency") or threshold)))
        if score >= threshold:
            return VerificationResult("PASS", round(score, 3), ["FACE_MATCH"])
        if score >= threshold - 0.05:
            return VerificationResult("INCONCLUSIVE", round(score, 3), ["FACE_BORDERLINE"])
        return VerificationResult("FAIL", round(score, 3), ["FACE_LOW_SCORE"])
