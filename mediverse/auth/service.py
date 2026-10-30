"""Authentication state machine.

    password ──fail──> DENIED (counted toward lockout)
       │ok
       ├─ portal mismatch (Admin vs Staff user) ─> DENIED
       ├─ ADMIN via Admin portal ──────────> SESSION (password only)
       ├─ role in OTP_REQUIRED_ROLES ──────> OTP challenge
       ├─ LAB_STAFF or face/typing enrolled ─> BIOMETRIC challenge (typing within the admin-set seconds)
       │        both PASS ─────────────────> SESSION (no OTP)
       │        any FAIL/INCONCLUSIVE/UNAVAILABLE ─> OTP challenge
       └─ otherwise ───────────────────────> SESSION
    OTP ok ─> SESSION ; OTP attempts exhausted/expired ─> DENIED (+ recovery text)

Biometric success never skips the password, account-status or role checks: it
can only be reached after they pass, and sessions re-check account status.
Logged events carry reason codes only, never passwords, OTPs or biometric data.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol

from mediverse.auth import otp as otp_mod
from mediverse.auth.passwords import verify_password
from mediverse.biometrics.base import FaceVerifier, TypingVerifier, VerificationResult

RECOVERY_TEXT = ("Access denied. If you are locked out, contact the MediVerse administrator in person or "
                 "via the service desk. The administrator will verify your identity, unlock the account "
                 "and, if needed, reset your enrollment. Do not share codes with anyone.")

DEFAULT_CONFIG = {
    "MAX_FAILED_LOGINS": 5,
    "LOCKOUT_MINUTES": 15,
    "FAIL_WINDOW_MINUTES": 15,
    "OTP_TTL_MINUTES": 5,
    "OTP_MAX_ATTEMPTS": 3,
    "OTP_MAX_SENDS_PER_WINDOW": 3,
    "CHALLENGE_TTL_MINUTES": 5,
    "SESSION_IDLE_MINUTES": 30,
    "SESSION_ABSOLUTE_HOURS": 8,
    "FACE_THRESHOLD": 0.85,
    "TYPING_THRESHOLD": 0.70,
    "BIOMETRIC_MAX_ATTEMPTS": 3,
    "BIOMETRIC_ROLES": ["LAB_STAFF"],
    "OTP_REQUIRED_ROLES": ["ADMIN"],
}


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def typing_seconds(sample: dict) -> float | None:
    """Elapsed seconds from first to last key event of a typing sample."""
    ms = [float(e.get("ms", 0)) for e in (sample or {}).get("events", [])]
    return round((max(ms) - min(ms)) / 1000, 2) if len(ms) > 1 else None


def phrase_ok(sample: dict) -> bool:
    from mediverse.biometrics.typing_rhythm import PHRASE
    return ((sample or {}).get("phrase") or "").strip().lower() == PHRASE


class AuthRepo(Protocol):
    def get_user_by_username(self, username: str) -> dict | None: ...
    def get_user_by_email(self, email: str) -> dict | None: ...
    def get_user(self, user_id: str) -> dict | None: ...
    def set_login_failures(self, user_id: str, count: int, locked_until: datetime | None) -> None: ...
    def count_recent_events(self, username: str, event_type: str, outcome: str, since: datetime) -> int: ...
    def get_biometric_profile(self, user_id: str) -> dict | None: ...
    def create_challenge(self, ch: dict) -> None: ...
    def get_challenge(self, challenge_hash: str) -> dict | None: ...
    def update_challenge(self, challenge_hash: str, **fields) -> None: ...
    def create_session(self, s: dict) -> None: ...
    def get_session(self, session_hash: str) -> dict | None: ...
    def update_session(self, session_hash: str, **fields) -> None: ...
    def log_auth_event(self, e: dict) -> None: ...


@dataclass
class Step:
    kind: str                       # DENIED | SESSION | BIOMETRIC | OTP | RETRY_OTP
    token: str | None = None        # challenge token or session token
    message: str = ""
    reasons: list[str] = field(default_factory=list)
    delivery: otp_mod.Delivery | None = None


class AuthService:
    def __init__(self, repo: AuthRepo, pepper: str, otp_sender: otp_mod.OTPSender,
                 face: FaceVerifier, typing: TypingVerifier, config: dict | None = None, clock=now):
        self.repo, self.pepper, self.sender = repo, pepper, otp_sender
        self.face, self.typing = face, typing
        self.cfg = {**DEFAULT_CONFIG, **(config or {})}
        self.clock = clock

    # -- helpers ------------------------------------------------------------
    def _event(self, username, user_id, event_type, outcome, reasons, **extra):
        self.repo.log_auth_event({"event_id": secrets.token_hex(8), "ts": self.clock(), "username": username,
                                  "user_id": user_id, "event_type": event_type, "outcome": outcome,
                                  "reason_codes": list(reasons), **extra})

    def user_key(self, user_id: str, enroll_salt: str) -> bytes:
        return hashlib.sha256(f"{self.pepper}:{user_id}:{enroll_salt}".encode()).digest()

    def _new_challenge(self, user: dict, stage: str, reasons: list[str]) -> Step:
        token = secrets.token_urlsafe(32)
        ch = {"challenge_hash": token_hash(token), "user_id": user["user_id"], "stage": stage,
              "created_at": self.clock(), "expires_at": self.clock() + timedelta(minutes=self.cfg["CHALLENGE_TTL_MINUTES"]),
              "attempts": 0, "otp_hash": None, "otp_expires_at": None, "consumed": False}
        self.repo.create_challenge(ch)
        if stage == "OTP":
            return self._send_otp(user, token, ch, reasons)
        return Step("BIOMETRIC", token, "Password accepted. Complete face and typing verification within "
                    f"{self.cfg['CHALLENGE_TTL_MINUTES']} minutes.", reasons)

    def _send_otp(self, user: dict, token: str, ch: dict, reasons: list[str]) -> Step:
        since = self.clock() - timedelta(minutes=self.cfg["FAIL_WINDOW_MINUTES"])
        sent = self.repo.count_recent_events(user["username"], "OTP_SENT", "OK", since)
        if sent >= self.cfg["OTP_MAX_SENDS_PER_WINDOW"]:
            self.repo.update_challenge(ch["challenge_hash"], consumed=True)
            self._event(user["username"], user["user_id"], "OTP_SENT", "BLOCKED", reasons + ["OTP_RATE_LIMIT"])
            return Step("DENIED", message="Too many verification codes requested. " + RECOVERY_TEXT,
                        reasons=reasons + ["OTP_RATE_LIMIT"])
        if not user.get("email"):
            self.repo.update_challenge(ch["challenge_hash"], consumed=True)
            self._event(user["username"], user["user_id"], "OTP_SENT", "FAIL", reasons + ["NO_VERIFIED_CONTACT"])
            return Step("DENIED", message="No verified contact channel on file. " + RECOVERY_TEXT,
                        reasons=reasons + ["NO_VERIFIED_CONTACT"])
        code = otp_mod.generate_code()
        otp_exp = self.clock() + timedelta(minutes=self.cfg["OTP_TTL_MINUTES"])
        self.repo.update_challenge(ch["challenge_hash"], stage="OTP", attempts=0,
                                   otp_hash=otp_mod.hash_code(code, ch["challenge_hash"], self.pepper),
                                   otp_expires_at=otp_exp, expires_at=otp_exp + timedelta(minutes=1))
        try:
            delivery = self.sender.send(user["email"], code)
        except Exception:  # never fall back to showing the code
            self.repo.update_challenge(ch["challenge_hash"], consumed=True)
            self._event(user["username"], user["user_id"], "OTP_SENT", "FAIL", reasons + ["OTP_DELIVERY_FAILED"])
            return Step("DENIED", message="Verification failed and the one-time code could not be delivered to your "
                        "registered email. " + RECOVERY_TEXT, reasons=reasons + ["OTP_DELIVERY_FAILED"])
        self._event(user["username"], user["user_id"], "OTP_SENT", "OK", reasons, channel=delivery.channel)
        return Step("OTP", token, f"Enter the 6-digit code sent to {delivery.masked_destination}.", reasons, delivery)

    def _open_session(self, user: dict, reasons: list[str], method: str) -> Step:
        token = secrets.token_urlsafe(32)
        t = self.clock()
        self.repo.create_session({"session_hash": token_hash(token), "user_id": user["user_id"], "role_code": user["role_code"],
                                  "created_at": t, "last_seen_at": t,
                                  "expires_at": t + timedelta(hours=self.cfg["SESSION_ABSOLUTE_HOURS"]),
                                  "revoked": False, "auth_method": method})
        self.repo.set_login_failures(user["user_id"], 0, None)
        self._event(user["username"], user["user_id"], "LOGIN", "OK", reasons, auth_method=method)
        return Step("SESSION", token, "Signed in.", reasons)

    def _live_challenge(self, token: str, stage: str) -> tuple[dict | None, dict | None, str | None]:
        ch = self.repo.get_challenge(token_hash(token or ""))
        if not ch or ch["consumed"] or ch["stage"] != stage:
            return None, None, "CHALLENGE_INVALID"
        if self.clock() > ch["expires_at"]:
            self.repo.update_challenge(ch["challenge_hash"], consumed=True)
            return None, None, "CHALLENGE_EXPIRED"
        user = self.repo.get_user(ch["user_id"])
        if not user or user["status"] != "ACTIVE":
            self.repo.update_challenge(ch["challenge_hash"], consumed=True)
            return None, None, "ACCOUNT_INACTIVE"
        return ch, user, None

    # -- step 1: password -----------------------------------------------------
    def login_password(self, username: str, password: str, portal: str | None = None) -> Step:
        """Step 1. ``username`` may be the account username or its registered email address.
        ``portal`` is the sign-in type chosen on the page: "ADMIN" or "USER" (None = any)."""
        username = (username or "").strip().lower()
        since = self.clock() - timedelta(minutes=self.cfg["FAIL_WINDOW_MINUTES"])
        if self.repo.count_recent_events(username, "PASSWORD", "FAIL", since) >= self.cfg["MAX_FAILED_LOGINS"] * 2:
            self._event(username, None, "PASSWORD", "BLOCKED", ["USERNAME_RATE_LIMIT"])
            return Step("DENIED", message="Too many attempts. Try again later.", reasons=["USERNAME_RATE_LIMIT"])
        user = self.repo.get_user_by_email(username) if "@" in username else self.repo.get_user_by_username(username)
        if user:
            username = user["username"]
        ok = verify_password(password, user["password_hash"] if user else None, self.pepper)
        generic = "Invalid username or password."
        if not user:
            self._event(username, None, "PASSWORD", "FAIL", ["UNKNOWN_USER"])
            return Step("DENIED", message=generic, reasons=["UNKNOWN_USER"])
        if user.get("locked_until") and user["locked_until"] > self.clock():
            self._event(username, user["user_id"], "PASSWORD", "BLOCKED", ["ACCOUNT_LOCKED"])
            return Step("DENIED", message="Account temporarily locked. " + RECOVERY_TEXT, reasons=["ACCOUNT_LOCKED"])
        if not ok:
            fails = int(user.get("failed_count") or 0) + 1
            lock = None
            reasons = ["BAD_PASSWORD"]
            if fails >= self.cfg["MAX_FAILED_LOGINS"]:
                lock = self.clock() + timedelta(minutes=self.cfg["LOCKOUT_MINUTES"])
                reasons.append("LOCKOUT_TRIGGERED")
                self._event(username, user["user_id"], "SECURITY_ALERT", "ALERT", ["REPEATED_PASSWORD_FAILURES"])
            self.repo.set_login_failures(user["user_id"], fails, lock)
            self._event(username, user["user_id"], "PASSWORD", "FAIL", reasons)
            return Step("DENIED", message=generic, reasons=reasons)
        if user["status"] != "ACTIVE":
            self._event(username, user["user_id"], "PASSWORD", "BLOCKED", ["ACCOUNT_INACTIVE"])
            return Step("DENIED", message="Account is not active. " + RECOVERY_TEXT, reasons=["ACCOUNT_INACTIVE"])
        self._event(username, user["user_id"], "PASSWORD", "OK", ["PWD_OK"])
        is_admin = user["role_code"] == "ADMIN"
        if portal and (portal == "ADMIN") != is_admin:
            self._event(username, user["user_id"], "PASSWORD", "BLOCKED", ["WRONG_PORTAL"])
            other = "Admin" if is_admin else "Staff user"
            return Step("DENIED", message=f"This account cannot sign in here. Choose '{other}' and try again.",
                        reasons=["WRONG_PORTAL"])
        if is_admin and portal == "ADMIN":
            return self._open_session(user, ["PWD_OK", "ADMIN_PORTAL"], "PASSWORD")
        if user["role_code"] in self.cfg["OTP_REQUIRED_ROLES"]:
            return self._new_challenge(user, "OTP", ["PWD_OK", "OTP_POLICY_ROLE"])
        prof = self.repo.get_biometric_profile(user["user_id"]) or {}
        if user["role_code"] in self.cfg["BIOMETRIC_ROLES"] or prof.get("status") == "ACTIVE":
            return self._new_challenge(user, "BIOMETRIC", ["PWD_OK"])
        return self._open_session(user, ["PWD_OK"], "PASSWORD")

    # -- step 2: biometrics -------------------------------------------------------
    def verify_biometrics(self, token: str, face_frames: list[bytes] | None, typing_sample: dict | None) -> Step:
        ch, user, err = self._live_challenge(token, "BIOMETRIC")
        if err:
            self._event(None, None, "BIOMETRIC", "FAIL", [err])
            return Step("DENIED", message="Verification session expired. Please sign in again.", reasons=[err])
        profile = self.repo.get_biometric_profile(user["user_id"]) or {}
        active = profile.get("status") == "ACTIVE"
        key = self.user_key(user["user_id"], profile.get("enroll_salt", ""))
        if not active:
            face_r = VerificationResult("UNAVAILABLE", reasons=["NOT_ENROLLED"])
            typ_r = VerificationResult("UNAVAILABLE", reasons=["NOT_ENROLLED"])
        else:
            face_r = (self.face.verify(profile.get("face_template"), face_frames, key, self.cfg["FACE_THRESHOLD"])
                      if face_frames else VerificationResult("UNAVAILABLE", reasons=["FACE_NOT_PROVIDED"]))
            limit = user.get("typing_max_seconds")
            if not profile.get("typing_profile"):
                # Face-only enrollment: typing is judged on the admin-set time limit alone (no rhythm model).
                if not limit:
                    typ_r = VerificationResult("PASS", None, ["TYPING_NOT_REQUIRED"])
                elif not typing_sample:
                    typ_r = VerificationResult("UNAVAILABLE", reasons=["TYPING_NOT_PROVIDED"])
                else:
                    typ_r = VerificationResult("PASS", None, ["TYPING_WITHIN_LIMIT"])
            else:
                typ_r = (self.typing.verify(profile.get("typing_profile"), typing_sample, self.cfg["TYPING_THRESHOLD"])
                         if typing_sample else VerificationResult("UNAVAILABLE", reasons=["TYPING_NOT_PROVIDED"]))
            if typing_sample and limit:
                secs = typing_seconds(typing_sample)
                if secs is None or secs > float(limit) or not phrase_ok(typing_sample):
                    typ_r = VerificationResult("FAIL", typ_r.score, ["TYPING_TOO_SLOW" if phrase_ok(typing_sample)
                                                                     else "TYPING_PHRASE_WRONG"])
        reasons = [f"FACE_{face_r.status}", f"TYPING_{typ_r.status}"] + face_r.reasons + typ_r.reasons
        outcome = "OK" if face_r.status == typ_r.status == "PASS" else "FALLBACK"
        self._event(user["username"], user["user_id"], "BIOMETRIC", outcome, reasons,
                    face_score=face_r.score, typing_score=typ_r.score)
        if outcome == "OK":
            self.repo.update_challenge(ch["challenge_hash"], consumed=True)
            return self._open_session(user, reasons, "PASSWORD+BIOMETRIC")
        tries = int(ch.get("attempts") or 0) + 1
        if active and (face_frames or typing_sample) and tries < self.cfg["BIOMETRIC_MAX_ATTEMPTS"]:
            self.repo.update_challenge(ch["challenge_hash"], attempts=tries)
            left = self.cfg["BIOMETRIC_MAX_ATTEMPTS"] - tries
            hint = ("Typing took too long or the phrase was wrong." if "TYPING_FAIL" in reasons or
                    "TYPING_UNAVAILABLE" in reasons else
                    "Face did not match. Face the camera in good light, then turn your head slightly for photo 2.")
            return Step("BIOMETRIC", token, f"{hint} {left} attempt(s) left before a one-time code is required.", reasons)
        # Fallback: same challenge moves to OTP stage.
        return self._send_otp(user, token, ch, reasons + ["OTP_FALLBACK"])

    # -- step 3: OTP ------------------------------------------------------------------
    def verify_otp(self, token: str, code: str) -> Step:
        ch, user, err = self._live_challenge(token, "OTP")
        if err:
            self._event(None, None, "OTP", "FAIL", [err])
            return Step("DENIED", message="Verification session expired. Please sign in again.", reasons=[err])
        if not ch.get("otp_hash") or self.clock() > ch["otp_expires_at"]:
            self.repo.update_challenge(ch["challenge_hash"], consumed=True)
            self._event(user["username"], user["user_id"], "OTP", "FAIL", ["OTP_EXPIRED"])
            return Step("DENIED", message="Code expired. " + RECOVERY_TEXT, reasons=["OTP_EXPIRED"])
        attempts = int(ch["attempts"]) + 1
        if otp_mod.codes_match(code or "", ch["challenge_hash"], ch["otp_hash"], self.pepper):
            self.repo.update_challenge(ch["challenge_hash"], consumed=True, attempts=attempts)
            return self._open_session(user, ["OTP_OK"], "PASSWORD+OTP")
        self.repo.update_challenge(ch["challenge_hash"], attempts=attempts)
        if attempts >= self.cfg["OTP_MAX_ATTEMPTS"]:
            self.repo.update_challenge(ch["challenge_hash"], consumed=True)
            self._event(user["username"], user["user_id"], "OTP", "FAIL", ["OTP_ATTEMPTS_EXHAUSTED"])
            self._event(user["username"], user["user_id"], "SECURITY_ALERT", "ALERT", ["OTP_ATTEMPTS_EXHAUSTED"])
            return Step("DENIED", message=RECOVERY_TEXT, reasons=["OTP_ATTEMPTS_EXHAUSTED"])
        self._event(user["username"], user["user_id"], "OTP", "FAIL", ["OTP_MISMATCH"])
        left = self.cfg["OTP_MAX_ATTEMPTS"] - attempts
        return Step("RETRY_OTP", token, f"Incorrect code. {left} attempt(s) left.", ["OTP_MISMATCH"])

    # -- sessions ---------------------------------------------------------------------
    def validate_session(self, token: str | None) -> dict | None:
        """Return the user dict for a live session, else None. Re-checks account status and role."""
        if not token:
            return None
        s = self.repo.get_session(token_hash(token))
        t = self.clock()
        if not s or s["revoked"] or t > s["expires_at"] or \
                t - s["last_seen_at"] > timedelta(minutes=self.cfg["SESSION_IDLE_MINUTES"]):
            if s and not s["revoked"]:
                self.repo.update_session(s["session_hash"], revoked=True)
            return None
        user = self.repo.get_user(s["user_id"])
        if not user or user["status"] != "ACTIVE" or user["role_code"] != s["role_code"]:
            self.repo.update_session(s["session_hash"], revoked=True)
            return None
        self.repo.update_session(s["session_hash"], last_seen_at=t)
        return user

    def logout(self, token: str | None) -> None:
        if token:
            s = self.repo.get_session(token_hash(token))
            if s:
                self.repo.update_session(s["session_hash"], revoked=True)
                self._event(None, s["user_id"], "LOGOUT", "OK", ["USER_LOGOUT"])
