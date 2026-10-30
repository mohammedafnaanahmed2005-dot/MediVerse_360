"""One-time passcodes. Only a keyed hash of the code is ever stored."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass


def generate_code(digits: int = 6) -> str:
    return f"{secrets.randbelow(10 ** digits):0{digits}d}"


def hash_code(code: str, challenge_id: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), f"{challenge_id}:{code}".encode(), hashlib.sha256).hexdigest()


def codes_match(code: str, challenge_id: str, stored_hash: str, pepper: str) -> bool:
    return hmac.compare_digest(hash_code(code.strip(), challenge_id, pepper), stored_hash)


@dataclass
class Delivery:
    channel: str
    masked_destination: str
    demo_code: str | None = None  # only populated by the demo sender


class OTPSender(ABC):
    @abstractmethod
    def send(self, destination: str, code: str) -> Delivery: ...


def mask_email(email: str) -> str:
    if not email or "@" not in email:
        return "(no verified contact)"
    local, domain = email.split("@", 1)
    return f"{local[:1]}***@{domain}"


class DemoOTPSender(OTPSender):
    """DEMO ONLY: returns the code so the UI can show it in a clearly labelled panel.
    Replace with a real channel (e.g. SYSTEM$SEND_EMAIL via a notification integration)."""

    def send(self, destination: str, code: str) -> Delivery:
        return Delivery("DEMO_SCREEN", mask_email(destination), demo_code=code)


class SnowflakeEmailOTPSender(OTPSender):
    """Sends via SYSTEM$SEND_EMAIL. Requires an EMAIL notification integration and a
    recipient that is a verified email of a Snowflake user in the account."""

    def __init__(self, session, integration: str):
        self.session = session
        self.integration = integration

    def send(self, destination: str, code: str) -> Delivery:
        self.session.sql(
            "CALL SYSTEM$SEND_EMAIL(?, ?, ?, ?)",
            params=[self.integration, destination, "MediVerse 360 sign-in code",
                    f"Your MediVerse 360 sign-in code is {code}. It expires in 5 minutes. "
                    "If you did not try to sign in, contact your administrator."],
        ).collect()
        return Delivery("EMAIL", mask_email(destination))
