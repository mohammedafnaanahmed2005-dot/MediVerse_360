"""Process-wide service objects (shared across viewers; hold no per-user state)."""

from __future__ import annotations

import os

import streamlit as st

from mediverse.admin import AdminService
from mediverse.auth import otp
from mediverse.auth.service import AuthService
from mediverse.biometrics import lbp_face
from mediverse.biometrics.lbp_face import LBPFaceVerifier
from mediverse.biometrics.mock_face import MockFaceVerifier
from mediverse.biometrics.typing_rhythm import KeystrokeVerifier
from mediverse.db import session
from mediverse.sf_repo import SnowflakeRepo

# DEMO_MODE only labels biometric checks as simulated when OpenCV is missing.
# OTP codes are never shown on screen in any mode.
DEMO_MODE = os.getenv("MEDIVERSE_DEMO_MODE", "false").lower() == "true"


def repo() -> SnowflakeRepo:
    return SnowflakeRepo(session())


@st.cache_resource(ttl=3600)
def _pepper() -> str:
    env = os.getenv("MEDIVERSE_PEPPER")
    if env:
        return env
    rows = session().sql("SELECT KEY_VALUE FROM MEDIVERSE360.SECURITY.APP_KEYS WHERE KEY_NAME = 'AUTH_PEPPER'").collect()
    if not rows or not rows[0][0] or rows[0][0] == "***":
        raise RuntimeError("Auth pepper unavailable: run sql/04_security.sql as MV_APP_OWNER.")
    return rows[0][0]


@st.cache_data(ttl=60, show_spinner=False)
def _config() -> dict:
    return repo().get_config()


def _sender() -> otp.OTPSender:
    """Email via SYSTEM$SEND_EMAIL (sql/12_email_otp.sql). If delivery fails, sign-in is denied."""
    return otp.SnowflakeEmailOTPSender(session(), os.getenv("MEDIVERSE_EMAIL_INTEGRATION", "MV_EMAIL_INT"))


class _SchemeDispatchFace:
    """Enroll with the best engine available; verify each template with the engine that created it,
    so users enrolled under the demo matcher keep working until they are re-enrolled."""

    def __init__(self):
        self.primary = LBPFaceVerifier() if lbp_face.available() else MockFaceVerifier()

    def enroll(self, samples, user_key):
        return self.primary.enroll(samples, user_key)

    def verify(self, template, probe_frames, user_key, threshold):
        if (template or {}).get("scheme") == "demo-ahash-cancelable-v1":
            return MockFaceVerifier().verify(template, probe_frames, user_key, threshold)
        return self.primary.verify(template, probe_frames, user_key, threshold)


def face_verifier():
    """Algorithmic LBP verifier when OpenCV is installed, otherwise the labelled hash mock."""
    return _SchemeDispatchFace()


def auth_service() -> AuthService:
    return AuthService(repo(), _pepper(), _sender(), face_verifier(), KeystrokeVerifier(), _config())


def admin_service() -> AdminService:
    return AdminService(repo(), auth_service())


def clear_config_cache() -> None:
    _config.clear()


def needs_bootstrap() -> bool:
    r = session().sql("SELECT COUNT(*) FROM MEDIVERSE360.SECURITY.APP_USERS WHERE ROLE_CODE = 'ADMIN' AND STATUS = 'ACTIVE'").collect()
    return int(r[0][0]) == 0


def bootstrap_admin(token: str, **kw) -> str:
    rows = session().sql("SELECT KEY_VALUE FROM MEDIVERSE360.SECURITY.APP_KEYS WHERE KEY_NAME = 'BOOTSTRAP_TOKEN'").collect()
    stored = rows[0][0] if rows else None
    uid = admin_service().bootstrap_first_admin(token, stored, **kw)
    session().sql("DELETE FROM MEDIVERSE360.SECURITY.APP_KEYS WHERE KEY_NAME = 'BOOTSTRAP_TOKEN'").collect()
    return uid
