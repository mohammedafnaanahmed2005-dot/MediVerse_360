"""Snowflake implementation of the auth/admin repository (SECURITY schema).

All statements use bound parameters (``?``); no user input is interpolated into SQL.
"""

from __future__ import annotations

import json
from datetime import datetime

S = "MEDIVERSE360.SECURITY"

_USER_COLS = ("USER_ID, USERNAME, DISPLAY_NAME, ROLE_CODE, STAFF_ID, DEPARTMENT_ID, EMAIL, PASSWORD_HASH, "
              "STATUS, FLAGS, FAILED_COUNT, LOCKED_UNTIL, CREATED_AT, CREATED_BY, DESIGNATION, TYPING_MAX_SECONDS")


def _row(r) -> dict:
    d = {k.lower(): v for k, v in r.as_dict().items()}
    for k in ("flags", "reason_codes"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    for k in ("face_template", "typing_profile", "details"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    return d


class SnowflakeRepo:
    def __init__(self, session):
        self.s = session

    def _q(self, sql: str, params: list | None = None) -> list[dict]:
        return [_row(r) for r in self.s.sql(sql, params=params or []).collect()]

    def _x(self, sql: str, params: list | None = None) -> None:
        self.s.sql(sql, params=params or []).collect()

    # -- users -------------------------------------------------------------------
    def get_user_by_username(self, username: str) -> dict | None:
        r = self._q(f"SELECT {_USER_COLS} FROM {S}.APP_USERS WHERE USERNAME = ?", [username])
        return r[0] if r else None

    def get_user_by_email(self, email: str) -> dict | None:
        r = self._q(f"SELECT {_USER_COLS} FROM {S}.APP_USERS WHERE LOWER(EMAIL) = ? ORDER BY CREATED_AT LIMIT 2", [email])
        return r[0] if len(r) == 1 else None  # ambiguous emails never authenticate

    def get_user(self, user_id: str) -> dict | None:
        r = self._q(f"SELECT {_USER_COLS} FROM {S}.APP_USERS WHERE USER_ID = ?", [user_id])
        return r[0] if r else None

    def list_users(self) -> list[dict]:
        return self._q(f"""SELECT u.USER_ID, u.USERNAME, u.DISPLAY_NAME, u.ROLE_CODE, u.DEPARTMENT_ID, u.EMAIL,
                                  u.DESIGNATION, u.TYPING_MAX_SECONDS, u.STATUS, u.FLAGS, u.LOCKED_UNTIL, u.CREATED_AT, b.STATUS AS BIOMETRIC_STATUS, b.ENROLLED_AT
                           FROM {S}.APP_USERS u LEFT JOIN {S}.BIOMETRIC_PROFILES b USING (USER_ID) ORDER BY u.USERNAME""")

    def insert_user(self, u: dict) -> None:
        self._x(f"""INSERT INTO {S}.APP_USERS (USER_ID, USERNAME, DISPLAY_NAME, ROLE_CODE, STAFF_ID, DEPARTMENT_ID, EMAIL,
                                              PASSWORD_HASH, STATUS, FLAGS, CREATED_BY, DESIGNATION, TYPING_MAX_SECONDS)
                    SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, PARSE_JSON(?), ?, ?, ?""",
                [u["user_id"], u["username"], u.get("display_name"), u["role_code"], u.get("staff_id"),
                 u.get("department_id"), u.get("email"), u["password_hash"], u.get("status", "ACTIVE"),
                 json.dumps(u.get("flags", [])), u.get("created_by"), u.get("designation"),
                 u.get("typing_max_seconds")])

    def update_user(self, user_id: str, **f) -> None:
        allowed = {"display_name", "role_code", "department_id", "email", "status", "flags", "password_hash",
                   "failed_count", "locked_until", "designation", "typing_max_seconds"}
        sets, params = [], []
        for k, v in f.items():
            if k not in allowed:
                raise ValueError(k)
            if k == "flags":
                sets.append("FLAGS = PARSE_JSON(?)")
                params.append(json.dumps(v))
            else:
                sets.append(f"{k.upper()} = ?")
                params.append(v)
        sets.append("UPDATED_AT = SYSDATE()")
        self._x(f"UPDATE {S}.APP_USERS SET {', '.join(sets)} WHERE USER_ID = ?", params + [user_id])

    def set_login_failures(self, user_id: str, count: int, locked_until: datetime | None) -> None:
        self._x(f"UPDATE {S}.APP_USERS SET FAILED_COUNT = ?, LOCKED_UNTIL = ? WHERE USER_ID = ?",
                [count, locked_until, user_id])

    def count_recent_events(self, username: str, event_type: str, outcome: str, since: datetime) -> int:
        r = self.s.sql(f"SELECT COUNT(*) FROM {S}.AUTH_EVENTS WHERE USERNAME = ? AND EVENT_TYPE = ? AND OUTCOME = ? AND TS >= ?",
                       params=[username, event_type, outcome, since]).collect()
        return int(r[0][0])

    # -- biometrics ----------------------------------------------------------------
    def get_biometric_profile(self, user_id: str) -> dict | None:
        r = self._q(f"SELECT USER_ID, STATUS, ENROLL_SALT, FACE_TEMPLATE, TYPING_PROFILE, ENROLLED_AT "
                    f"FROM {S}.BIOMETRIC_PROFILES WHERE USER_ID = ?", [user_id])
        return r[0] if r else None

    def upsert_biometric_profile(self, p: dict) -> None:
        self._x(f"""MERGE INTO {S}.BIOMETRIC_PROFILES t
                    USING (SELECT ? USER_ID, ? STATUS, ? ENROLL_SALT, PARSE_JSON(?) FACE_TEMPLATE, PARSE_JSON(?) TYPING_PROFILE,
                                  ? ENROLLED_BY, ? VERIFIED_BY, ? IDENTITY_EVIDENCE) s
                    ON t.USER_ID = s.USER_ID
                    WHEN MATCHED THEN UPDATE SET STATUS = s.STATUS, ENROLL_SALT = s.ENROLL_SALT, FACE_TEMPLATE = s.FACE_TEMPLATE,
                         TYPING_PROFILE = s.TYPING_PROFILE, ENROLLED_BY = s.ENROLLED_BY, VERIFIED_BY = s.VERIFIED_BY,
                         IDENTITY_EVIDENCE = s.IDENTITY_EVIDENCE, CONSENT_AT = SYSDATE(), ENROLLED_AT = SYSDATE()
                    WHEN NOT MATCHED THEN INSERT (USER_ID, STATUS, ENROLL_SALT, FACE_TEMPLATE, TYPING_PROFILE, ENROLLED_BY,
                         VERIFIED_BY, IDENTITY_EVIDENCE, CONSENT_AT, ENROLLED_AT)
                    VALUES (s.USER_ID, s.STATUS, s.ENROLL_SALT, s.FACE_TEMPLATE, s.TYPING_PROFILE, s.ENROLLED_BY,
                            s.VERIFIED_BY, s.IDENTITY_EVIDENCE, SYSDATE(), SYSDATE())""",
                [p["user_id"], p["status"], p["enroll_salt"], json.dumps(p.get("face_template")),
                 json.dumps(p.get("typing_profile")), p.get("enrolled_by"), p.get("verified_by"), p.get("identity_evidence")])

    def reset_biometric_profile(self, user_id: str) -> None:
        self._x(f"UPDATE {S}.BIOMETRIC_PROFILES SET STATUS = 'RESET', FACE_TEMPLATE = NULL, TYPING_PROFILE = NULL, "
                f"ENROLL_SALT = NULL WHERE USER_ID = ?", [user_id])

    # -- challenges / sessions ------------------------------------------------------
    def create_challenge(self, ch: dict) -> None:
        self._x(f"INSERT INTO {S}.AUTH_CHALLENGES VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [ch["challenge_hash"], ch["user_id"], ch["stage"], ch["created_at"], ch["expires_at"], ch["attempts"],
                 ch["otp_hash"], ch["otp_expires_at"], ch["consumed"]])

    def get_challenge(self, challenge_hash: str) -> dict | None:
        r = self._q(f"SELECT * FROM {S}.AUTH_CHALLENGES WHERE CHALLENGE_HASH = ?", [challenge_hash])
        return r[0] if r else None

    def update_challenge(self, challenge_hash: str, **f) -> None:
        cols = {"stage", "attempts", "otp_hash", "otp_expires_at", "consumed", "expires_at"}
        if not set(f) <= cols:
            raise ValueError(set(f) - cols)
        self._x(f"UPDATE {S}.AUTH_CHALLENGES SET " + ", ".join(f"{k.upper()} = ?" for k in f) + " WHERE CHALLENGE_HASH = ?",
                list(f.values()) + [challenge_hash])

    def create_session(self, s: dict) -> None:
        self._x(f"INSERT INTO {S}.SESSIONS VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [s["session_hash"], s["user_id"], s["role_code"], s["created_at"], s["last_seen_at"], s["expires_at"],
                 s["revoked"], s["auth_method"]])

    def get_session(self, session_hash: str) -> dict | None:
        r = self._q(f"SELECT * FROM {S}.SESSIONS WHERE SESSION_HASH = ?", [session_hash])
        return r[0] if r else None

    def update_session(self, session_hash: str, **f) -> None:
        cols = {"last_seen_at", "revoked"}
        if not set(f) <= cols:
            raise ValueError(set(f) - cols)
        self._x(f"UPDATE {S}.SESSIONS SET " + ", ".join(f"{k.upper()} = ?" for k in f) + " WHERE SESSION_HASH = ?",
                list(f.values()) + [session_hash])

    def revoke_user_sessions(self, user_id: str) -> None:
        self._x(f"UPDATE {S}.SESSIONS SET REVOKED = TRUE WHERE USER_ID = ? AND NOT REVOKED", [user_id])

    # -- logs -------------------------------------------------------------------------
    def log_auth_event(self, e: dict) -> None:
        self._x(f"""INSERT INTO {S}.AUTH_EVENTS (EVENT_ID, TS, USERNAME, USER_ID, EVENT_TYPE, OUTCOME, REASON_CODES,
                                                FACE_SCORE, TYPING_SCORE, AUTH_METHOD, CHANNEL)
                    SELECT ?, ?, ?, ?, ?, ?, PARSE_JSON(?), ?, ?, ?, ?""",
                [e["event_id"], e["ts"], e.get("username"), e.get("user_id"), e["event_type"], e["outcome"],
                 json.dumps(e.get("reason_codes", [])), e.get("face_score"), e.get("typing_score"),
                 e.get("auth_method"), e.get("channel")])

    def log_admin(self, admin, action: str, target_user_id: str | None, details: dict) -> None:
        self._x(f"INSERT INTO {S}.ADMIN_AUDIT (ADMIN_USER_ID, ADMIN_USERNAME, ACTION, TARGET_USER_ID, DETAILS) "
                f"SELECT ?, ?, ?, ?, PARSE_JSON(?)",
                [admin.user_id, admin.username, action, target_user_id, json.dumps(details, default=str)])

    def log_access(self, principal, resource: str, action: str, allowed: bool, record_ref: str | None = None,
                   detail: str | None = None) -> None:
        self._x(f"INSERT INTO {S}.ACCESS_LOG (USER_ID, USERNAME, ROLE_CODE, RESOURCE, ACTION, RECORD_REF, ALLOWED, DETAIL) "
                f"VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [principal.user_id, principal.username, principal.role.value, resource, action, record_ref, allowed, detail])

    def get_config(self) -> dict:
        return {r["key"]: json.loads(r["value"]) if isinstance(r["value"], str) else r["value"]
                for r in self._q(f"SELECT KEY, VALUE FROM {S}.CONFIG")}

    def set_config(self, key: str, value, by: str) -> None:
        self._x(f"""MERGE INTO {S}.CONFIG t USING (SELECT ? K, PARSE_JSON(?) V) s ON t.KEY = s.K
                    WHEN MATCHED THEN UPDATE SET VALUE = s.V, UPDATED_AT = SYSDATE(), UPDATED_BY = ?
                    WHEN NOT MATCHED THEN INSERT (KEY, VALUE, UPDATED_AT, UPDATED_BY) VALUES (s.K, s.V, SYSDATE(), ?)""",
                [key, json.dumps(value), by, by])
