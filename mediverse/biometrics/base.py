"""Biometric verification interfaces.

IMPORTANT: The implementations in this package are DEMONSTRATION MOCKS. They are
not production facial recognition, do not provide real presentation-attack
detection, and must not be used to protect real patient data. Swap in a vetted
provider by implementing ``FaceVerifier`` / ``TypingVerifier``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

DEMO_BANNER = ("Demonstration biometrics on synthetic data. Face matching uses classical algorithms "
               "(Haar detection + LBP histograms) and a motion-based liveness heuristic - not certified "
               "facial recognition or presentation-attack detection.")


@dataclass
class VerificationResult:
    status: str               # PASS | FAIL | INCONCLUSIVE | UNAVAILABLE
    score: float | None = None
    reasons: list[str] = field(default_factory=list)


class FaceVerifier(ABC):
    @abstractmethod
    def enroll(self, samples: list[bytes], user_key: bytes) -> dict:
        """Return a protected template (no raw image data)."""

    @abstractmethod
    def verify(self, template: dict, probe_frames: list[bytes], user_key: bytes, threshold: float) -> VerificationResult: ...


class TypingVerifier(ABC):
    @abstractmethod
    def enroll(self, samples: list[dict]) -> dict: ...

    @abstractmethod
    def verify(self, profile: dict, sample: dict, threshold: float) -> VerificationResult: ...
