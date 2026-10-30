"""Algorithmic face verification: Haar-cascade detection + uniform LBP histograms + chi-square.

Pipeline (classical computer vision, Ahonen et al. 2006 "Face description with local binary patterns"):
  1. Detect exactly one frontal face (OpenCV Haar cascade, Viola-Jones 2001).
  2. Crop, grayscale, resize to 128x128, histogram-equalise (lighting normalisation).
  3. Uniform LBP(8,1) codes -> 59-bin histogram per cell on an 8x8 grid -> 3,776-dim feature.
  4. Distance = chi-square between feature vectors.
  5. Per-user adaptive threshold = 1.5 x the largest distance between that user's own enrollment
     samples (bounded), so consistent enrollees get a tighter threshold.
Template protection: bins are permuted with a per-user key (pepper + enrollment salt). Chi-square is
invariant to a consistent permutation, so matching still works, but templates are not comparable
across users and are revoked by re-enrollment. No image is stored.

Liveness (presentation-attack heuristic, NOT certified PAD): two frames must each contain one face,
the face box must move or change scale between them (head turn), and the frames must be sharp
(Laplacian variance) - this rejects a single static photo re-submitted twice and very blurry
screen captures, but a determined attacker with video can defeat it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from .base import FaceVerifier, VerificationResult

GRID, SIDE = 8, 128
MIN_FACE_PX = 80
BLUR_MIN = 25.0          # Laplacian variance below this = too blurry / flat
MOVE_MIN = 0.04          # face box must shift >= 4% of its size between frames
THR_FLOOR, THR_CEIL = 0.35, 1.2   # bounds on the per-user chi-square threshold


def _cv2():
    import cv2
    return cv2


def _uniform_lut() -> np.ndarray:
    """Map 256 LBP codes to 59 uniform-pattern bins (58 uniform + 1 for all non-uniform)."""
    lut = np.full(256, 58, dtype=np.int32)
    k = 0
    for code in range(256):
        bits = [(code >> i) & 1 for i in range(8)]
        transitions = sum(bits[i] != bits[(i + 1) % 8] for i in range(8))
        if transitions <= 2:
            lut[code] = k
            k += 1
    return lut


LUT = _uniform_lut()


def lbp_image(gray: np.ndarray) -> np.ndarray:
    g = gray.astype(np.int16)
    c = g[1:-1, 1:-1]
    offsets = [(-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1)]
    code = np.zeros_like(c, dtype=np.uint8)
    for bit, (dy, dx) in enumerate(offsets):
        nb = g[1 + dy:g.shape[0] - 1 + dy, 1 + dx:g.shape[1] - 1 + dx]
        code |= ((nb >= c).astype(np.uint8) << bit)
    return LUT[code]


def lbp_features(face: np.ndarray) -> np.ndarray:
    """Concatenated, per-cell L1-normalised 59-bin histograms over a GRID x GRID layout."""
    codes = lbp_image(face)
    h, w = codes.shape
    feats = []
    for i in range(GRID):
        for j in range(GRID):
            cell = codes[i * h // GRID:(i + 1) * h // GRID, j * w // GRID:(j + 1) * w // GRID]
            hist = np.bincount(cell.ravel(), minlength=59).astype(np.float64)
            feats.append(hist / max(hist.sum(), 1.0))
    return np.concatenate(feats)


def chi_square(a: np.ndarray, b: np.ndarray) -> float:
    den = a + b
    mask = den > 0
    return float(np.sum((a[mask] - b[mask]) ** 2 / den[mask]) / GRID ** 2)


def _perm(user_key: bytes, n: int) -> np.ndarray:
    seed = int.from_bytes(hashlib.sha256(b"lbp-perm:" + user_key).digest()[:8], "big")
    return np.random.default_rng(seed).permutation(n)


def decode_image(image_bytes: bytes) -> np.ndarray:
    cv2 = _cv2()
    arr = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    if arr is None:
        raise ValueError("unreadable image")
    return arr


def detect_face(gray: np.ndarray) -> tuple[np.ndarray | None, tuple | None, str | None]:
    """Return (normalised 128x128 face, box, error_reason)."""
    cv2 = _cv2()
    cascade = _cascade()
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(MIN_FACE_PX, MIN_FACE_PX))
    if len(faces) == 0:
        return None, None, "NO_FACE_DETECTED"
    if len(faces) > 1:
        return None, None, "MULTIPLE_FACES"
    x, y, w, h = faces[0]
    crop = gray[y:y + h, x:x + w]
    face = cv2.equalizeHist(cv2.resize(crop, (SIDE, SIDE), interpolation=cv2.INTER_AREA))
    return face, (int(x), int(y), int(w), int(h)), None


_CASCADE = None


def _cascade():
    global _CASCADE
    if _CASCADE is None:
        cv2 = _cv2()
        # Bundled copy: OpenCV 5.x wheels no longer ship cv2.data haarcascades.
        local = Path(__file__).with_name("haarcascade_frontalface_default.xml")
        path = str(local) if local.is_file() else cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        _CASCADE = cv2.CascadeClassifier(path)
    return _CASCADE


def sharpness(gray: np.ndarray) -> float:
    cv2 = _cv2()
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def box_motion(a: tuple, b: tuple) -> float:
    (x1, y1, w1, h1), (x2, y2, w2, h2) = a, b
    size = max(w1, h1, 1)
    return max(abs(x1 - x2), abs(y1 - y2), abs(w1 - w2), abs(h1 - h2)) / size


class LBPFaceVerifier(FaceVerifier):
    def __init__(self, detector=detect_face, decoder=decode_image, sharp=sharpness):
        self.detect, self.decode, self.sharp = detector, decoder, sharp

    def _feature(self, image: bytes) -> tuple[np.ndarray | None, tuple | None, str | None, np.ndarray | None]:
        try:
            gray = self.decode(image)
        except Exception:
            return None, None, "FACE_IMAGE_UNREADABLE", None
        face, box, err = self.detect(gray)
        if err:
            return None, None, err, gray
        return lbp_features(face), box, None, gray

    def enroll(self, samples: list[bytes], user_key: bytes) -> dict:
        if len(samples) < 3:
            raise ValueError("at least 3 face samples are required")
        feats = []
        for i, s in enumerate(samples, 1):
            f, _, err, _ = self._feature(s)
            if err:
                raise ValueError(f"face sample {i}: {err.replace('_', ' ').lower()}")
            feats.append(f)
        pair = [chi_square(feats[i], feats[j]) for i in range(len(feats)) for j in range(i + 1, len(feats))]
        thr = float(np.clip(1.5 * max(pair), THR_FLOOR, THR_CEIL))
        perm = _perm(user_key, feats[0].size)
        return {"scheme": "lbp-u2-8x8-chi2-v1", "threshold": round(thr, 4),
                "templates": [np.round(f[perm], 5).tolist() for f in feats],
                "enroll_consistency": round(max(pair), 4)}

    def verify(self, template: dict, probe_frames: list[bytes], user_key: bytes, threshold: float) -> VerificationResult:
        if not template or template.get("scheme") != "lbp-u2-8x8-chi2-v1":
            return VerificationResult("UNAVAILABLE", reasons=["FACE_NOT_ENROLLED"])
        if not probe_frames or len(probe_frames) < 2:
            return VerificationResult("INCONCLUSIVE", reasons=["LIVENESS_FRAMES_MISSING"])
        probes = []
        for frame in probe_frames[:2]:
            f, box, err, gray = self._feature(frame)
            if err:
                return VerificationResult("INCONCLUSIVE", reasons=[err])
            if self.sharp(gray) < BLUR_MIN:
                return VerificationResult("INCONCLUSIVE", reasons=["IMAGE_TOO_BLURRY"])
            probes.append((f, box))
        if probe_frames[0] == probe_frames[1]:
            return VerificationResult("FAIL", reasons=["LIVENESS_STATIC_FRAMES"])
        if box_motion(probes[0][1], probes[1][1]) < MOVE_MIN:
            return VerificationResult("FAIL", reasons=["LIVENESS_NO_HEAD_MOVEMENT"])
        perm = _perm(user_key, probes[0][0].size)
        enrolled = [np.asarray(t) for t in template["templates"]]
        dist = min(chi_square(p[perm], e) for p, _ in probes for e in enrolled)
        thr = float(template["threshold"])
        score = round(float(np.clip(1 - dist / (2 * thr), 0, 1)), 3)  # 0.5 at the threshold
        if dist <= thr:
            return VerificationResult("PASS", score, ["FACE_MATCH"])
        if dist <= 1.25 * thr:
            return VerificationResult("INCONCLUSIVE", score, ["FACE_BORDERLINE"])
        return VerificationResult("FAIL", score, ["FACE_LOW_SCORE"])


LAST_ERROR: str | None = None


def available() -> bool:
    global LAST_ERROR
    try:
        _cv2()
        if _cascade().empty():
            LAST_ERROR = "Haar cascade file could not be loaded"
            return False
        LAST_ERROR = None
        return True
    except Exception as e:  # record why, so the admin page can show it
        LAST_ERROR = f"{type(e).__name__}: {e}"
        return False
