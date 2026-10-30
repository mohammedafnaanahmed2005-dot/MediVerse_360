"""Typing-rhythm risk signal (keystroke dynamics) for a fixed, consented phrase.

A sample is ``{"phrase": str, "events": [{"k": key, "t": "d"|"u", "ms": float}, ...]}``
captured client-side. Only timing features are kept; the keys themselves are
the known public phrase. This is a probabilistic risk signal, never a
standalone identity guarantee.
"""

from __future__ import annotations

import statistics

from .base import TypingVerifier, VerificationResult

PHRASE = "mediverse keeps patients safe"


def features(sample: dict) -> list[float] | None:
    """Dwell times (key held) followed by flight times (key-to-key), in ms."""
    if (sample.get("phrase") or "").strip().lower() != PHRASE:
        return None
    downs, dwell = [], []
    open_keys: dict[str, float] = {}
    for e in sample.get("events", []):
        k, typ, ms = e.get("k"), e.get("t"), float(e.get("ms", 0))
        if typ == "d" and k not in open_keys:
            open_keys[k] = ms
            downs.append(ms)
        elif typ == "u" and k in open_keys:
            dwell.append(ms - open_keys.pop(k))
    if len(downs) < len(PHRASE) - 2 or len(dwell) < len(PHRASE) - 2:
        return None
    n = len(PHRASE) - 2
    flight = [b - a for a, b in zip(downs, downs[1:])]
    return [min(max(x, 0.0), 2000.0) for x in dwell[:n] + flight[:n]]


class KeystrokeVerifier(TypingVerifier):
    MIN_SAMPLES = 5

    def enroll(self, samples: list[dict]) -> dict:
        vecs = [v for v in (features(s) for s in samples) if v]
        if len(vecs) < self.MIN_SAMPLES:
            raise ValueError(f"need {self.MIN_SAMPLES} valid typing samples, got {len(vecs)}")
        cols = list(zip(*vecs))
        return {"scheme": "keystroke-zscore-v1", "n": len(vecs),
                "mean": [round(statistics.fmean(c), 2) for c in cols],
                # floor the spread so a very consistent typist is not rejected on noise
                "std": [round(max(statistics.pstdev(c), 15.0), 2) for c in cols]}

    def verify(self, profile: dict, sample: dict, threshold: float) -> VerificationResult:
        if not profile or not profile.get("mean"):
            return VerificationResult("UNAVAILABLE", reasons=["TYPING_NOT_ENROLLED"])
        v = features(sample)
        if v is None:
            return VerificationResult("INCONCLUSIVE", reasons=["TYPING_SAMPLE_INVALID"])
        z = [abs(x - m) / s for x, m, s in zip(v, profile["mean"], profile["std"])]
        score = round(sum(1 for q in z if q <= 2.5) / len(z), 3)
        if score >= threshold:
            return VerificationResult("PASS", score, ["TYPING_MATCH"])
        if score >= threshold - 0.1:
            return VerificationResult("INCONCLUSIVE", score, ["TYPING_BORDERLINE"])
        return VerificationResult("FAIL", score, ["TYPING_LOW_SCORE"])
