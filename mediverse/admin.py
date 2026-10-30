"""Administrator operations: accounts, roles, flags, biometric enrollment. All audited."""

from __future__ import annotations

import re
import secrets

from mediverse.auth.passwords import hash_password, password_policy_errors, verify_password
from mediverse.auth.service import AuthService
from mediverse.authz import Perm, Principal, Role

USERNAME_RE = re.compile(r"^[a-z][a-z0-9._-]{2,31}$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
VALID_FLAGS = {"PSYCH_ACCESS", "MORTALITY_ACCESS"}


class AdminError(Exception):
    pass


class AdminService:
    def __init__(self, repo, auth: AuthService):
        self.repo, self.auth = repo, auth

    def bootstrap_first_admin(self, token: str, stored_token: str | None, *, username: str, display_name: str,
                              email: str, password: str) -> str:
        """First-run only: create the initial administrator when no active ADMIN exists.
        Requires the one-time token from SECURITY.APP_KEYS (readable only by MV_APP_OWNER)."""
        import hmac
        if any(u["role_code"] == "ADMIN" and u["status"] == "ACTIVE" for u in self.repo.list_users()):
            raise AdminError("An administrator already exists.")
        if not stored_token or not token or not hmac.compare_digest(token.strip(), stored_token):
            raise AdminError("Invalid bootstrap token.")
        if not email or not EMAIL_RE.match(email):
            raise AdminError("A verified email is required for the administrator OTP channel.")
        username = username.strip().lower()
        if not USERNAME_RE.match(username):
            raise AdminError("Username must be 3-32 chars: lowercase letters, digits, . _ -")
        errs = password_policy_errors(password, username)
        if errs:
            raise AdminError("Password needs: " + ", ".join(errs))
        uid = "U" + secrets.token_hex(6).upper()
        self.repo.insert_user({"user_id": uid, "username": username, "display_name": display_name, "role_code": "ADMIN",
                               "email": email, "password_hash": hash_password(password, self.auth.pepper),
                               "flags": [], "created_by": "BOOTSTRAP"})
        self.repo.log_admin(Principal(uid, username, "ADMIN"), "BOOTSTRAP_ADMIN", uid, {"username": username})
        return uid

    def _target(self, admin: Principal, user_id: str, allow_self: bool = False) -> dict:
        u = self.repo.get_user(user_id)
        if not u:
            raise AdminError("User not found.")
        if not allow_self and u["user_id"] == admin.user_id:
            raise AdminError("Administrators cannot change their own account, role or enrollment.")
        return u

    def create_user(self, admin: Principal, *, username: str, display_name: str, role: str, email: str | None,
                    department_id: str | None, staff_id: str | None, temp_password: str, flags=(),
                    designation: str | None = None, typing_max_seconds: float | None = None) -> str:
        admin.require(Perm.USERS_MANAGE)
        username = username.strip().lower()
        if not USERNAME_RE.match(username):
            raise AdminError("Username must be 3-32 chars: lowercase letters, digits, . _ -")
        Role(role)
        if email and not EMAIL_RE.match(email):
            raise AdminError("Invalid email address.")
        if set(flags) - VALID_FLAGS:
            raise AdminError("Unknown access flag.")
        errs = password_policy_errors(temp_password, username)
        if errs:
            raise AdminError("Password needs: " + ", ".join(errs))
        if self.repo.get_user_by_username(username):
            raise AdminError("Username already exists.")
        if email and self.repo.get_user_by_email(email.strip().lower()):
            raise AdminError("Email already registered to another account.")
        if typing_max_seconds is not None and not (2 <= float(typing_max_seconds) <= 120):
            raise AdminError("Typing time limit must be between 2 and 120 seconds.")
        uid = "U" + secrets.token_hex(6).upper()
        self.repo.insert_user({"user_id": uid, "username": username, "display_name": display_name, "role_code": role,
                               "email": email or None, "department_id": department_id or None, "staff_id": staff_id or None,
                               "designation": (designation or "").strip()[:80] or None,
                               "typing_max_seconds": typing_max_seconds,
                               "password_hash": hash_password(temp_password, self.auth.pepper),
                               "flags": sorted(flags), "created_by": admin.user_id})
        self.repo.log_admin(admin, "CREATE_USER", uid, {"username": username, "role": role, "flags": sorted(flags)})
        return uid

    def set_status(self, admin: Principal, user_id: str, status: str) -> None:
        admin.require(Perm.USERS_MANAGE)
        if status not in ("ACTIVE", "INACTIVE"):
            raise AdminError("Invalid status.")
        self._target(admin, user_id)
        self.repo.update_user(user_id, status=status)
        if status == "INACTIVE":
            self.repo.revoke_user_sessions(user_id)
        self.repo.log_admin(admin, "SET_STATUS", user_id, {"status": status})

    def change_role(self, admin: Principal, user_id: str, role: str) -> None:
        admin.require(Perm.USERS_MANAGE)
        Role(role)
        old = self._target(admin, user_id)["role_code"]
        self.repo.update_user(user_id, role_code=role)
        self.repo.revoke_user_sessions(user_id)
        self.repo.log_admin(admin, "CHANGE_ROLE", user_id, {"from": old, "to": role})

    def set_flags(self, admin: Principal, user_id: str, flags) -> None:
        admin.require(Perm.USERS_MANAGE)
        if set(flags) - VALID_FLAGS:
            raise AdminError("Unknown access flag.")
        self._target(admin, user_id)
        self.repo.update_user(user_id, flags=sorted(flags))
        self.repo.revoke_user_sessions(user_id)
        self.repo.log_admin(admin, "SET_FLAGS", user_id, {"flags": sorted(flags)})

    def set_details(self, admin: Principal, user_id: str, *, designation: str | None,
                    typing_max_seconds: float | None, email: str | None = None) -> None:
        admin.require(Perm.USERS_MANAGE)
        self._target(admin, user_id)
        if typing_max_seconds is not None and not (2 <= float(typing_max_seconds) <= 120):
            raise AdminError("Typing time limit must be between 2 and 120 seconds.")
        fields = {"designation": (designation or "").strip()[:80] or None, "typing_max_seconds": typing_max_seconds}
        if email is not None:
            email = email.strip().lower() or None
            if email and not EMAIL_RE.match(email):
                raise AdminError("Invalid email address.")
            other = email and self.repo.get_user_by_email(email)
            if other and other["user_id"] != user_id:
                raise AdminError("Email already registered to another account.")
            fields["email"] = email
        self.repo.update_user(user_id, **fields)
        self.repo.log_admin(admin, "SET_DETAILS", user_id, {"designation": designation, "typing_max_seconds": typing_max_seconds,
                                                          "email_changed": email is not None})

    def unlock(self, admin: Principal, user_id: str) -> None:
        admin.require(Perm.USERS_MANAGE)
        self._target(admin, user_id)
        self.repo.set_login_failures(user_id, 0, None)
        self.repo.log_admin(admin, "UNLOCK", user_id, {})

    def reset_password(self, admin: Principal, user_id: str, temp_password: str) -> None:
        admin.require(Perm.USERS_MANAGE)
        u = self._target(admin, user_id)
        errs = password_policy_errors(temp_password, u["username"])
        if errs:
            raise AdminError("Password needs: " + ", ".join(errs))
        self.repo.update_user(user_id, password_hash=hash_password(temp_password, self.auth.pepper))
        self.repo.revoke_user_sessions(user_id)
        self.repo.log_admin(admin, "RESET_PASSWORD", user_id, {})

    def enroll_biometrics(self, admin: Principal, user_id: str, *, face_samples: list[bytes], consent_confirmed: bool,
                          typing_samples: list[dict] | None = None, admin_password: str | None = None,
                          identity_evidence: str | None = None) -> dict:
        """Face enrollment from 3+ photos (camera or upload). Typing samples, admin re-auth and an
        evidence note are optional extras; when given they are validated and stored."""
        admin.require(Perm.ENROLL_MANAGE)
        u = self._target(admin, user_id)
        if u["status"] != "ACTIVE":
            raise AdminError("Activate the account before enrolling.")
        if not consent_confirmed:
            raise AdminError("User consent must be recorded before enrollment.")
        if identity_evidence is not None and len(identity_evidence.strip()) < 8:
            raise AdminError("Describe the identity evidence checked (e.g. 'Staff ID card ST00123 checked in person').")
        if admin_password is not None:
            admin_row = self.repo.get_user(admin.user_id)
            if not verify_password(admin_password, admin_row and admin_row["password_hash"], self.auth.pepper):
                self.repo.log_admin(admin, "ENROLL_DENIED", user_id, {"reason": "ADMIN_REAUTH_FAILED"})
                raise AdminError("Administrator password re-verification failed.")
        salt = secrets.token_hex(16)
        key = self.auth.user_key(user_id, salt)
        try:
            face_t = self.auth.face.enroll(face_samples, key)
        except ValueError as e:
            raise AdminError(f"Face photos not usable: {e}. Use 3+ clear, well-lit photos with one face each.")
        typing_p = self.auth.typing.enroll(typing_samples) if typing_samples else None
        self.repo.upsert_biometric_profile({"user_id": user_id, "status": "ACTIVE", "enroll_salt": salt,
                                            "face_template": face_t, "typing_profile": typing_p,
                                            "enrolled_by": admin.user_id, "verified_by": admin.user_id,
                                            "identity_evidence": (identity_evidence or "").strip() or None})
        self.repo.revoke_user_sessions(user_id)
        summary = {"face_samples": len(face_samples), "typing_samples": typing_p["n"] if typing_p else 0,
                   "face_consistency": face_t.get("enroll_consistency")}
        self.repo.log_admin(admin, "ENROLL_BIOMETRICS", user_id, summary)  # never the templates
        return summary

    def reset_enrollment(self, admin: Principal, user_id: str, reason: str) -> None:
        admin.require(Perm.ENROLL_MANAGE)
        self._target(admin, user_id)
        self.repo.reset_biometric_profile(user_id)
        self.repo.revoke_user_sessions(user_id)
        self.repo.log_admin(admin, "RESET_ENROLLMENT", user_id, {"reason": reason[:200]})
