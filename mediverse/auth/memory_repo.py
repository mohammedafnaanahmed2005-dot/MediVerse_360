"""In-memory AuthRepo used by unit tests and offline demos. Mirrors SECURITY schema semantics."""

from __future__ import annotations

from datetime import datetime


class MemoryRepo:
    def __init__(self):
        self.users: dict[str, dict] = {}
        self.bio: dict[str, dict] = {}
        self.challenges: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.events: list[dict] = []
        self.admin_audit: list[dict] = []

    def add_user(self, **u) -> dict:
        u.setdefault("status", "ACTIVE")
        u.setdefault("failed_count", 0)
        u.setdefault("locked_until", None)
        u.setdefault("flags", [])
        self.users[u["user_id"]] = u
        return u

    def get_user_by_username(self, username: str) -> dict | None:
        return next((dict(u) for u in self.users.values() if u["username"] == username), None)

    def get_user_by_email(self, email: str) -> dict | None:
        return next((dict(u) for u in self.users.values() if (u.get("email") or "").lower() == email), None)

    def get_user(self, user_id: str) -> dict | None:
        u = self.users.get(user_id)
        return dict(u) if u else None

    def set_login_failures(self, user_id: str, count: int, locked_until: datetime | None) -> None:
        self.users[user_id].update(failed_count=count, locked_until=locked_until)

    def count_recent_events(self, username: str, event_type: str, outcome: str, since: datetime) -> int:
        return sum(1 for e in self.events if e["username"] == username and e["event_type"] == event_type
                   and e["outcome"] == outcome and e["ts"] >= since)

    def get_biometric_profile(self, user_id: str) -> dict | None:
        return self.bio.get(user_id)

    def create_challenge(self, ch: dict) -> None:
        self.challenges[ch["challenge_hash"]] = dict(ch)

    def get_challenge(self, challenge_hash: str) -> dict | None:
        c = self.challenges.get(challenge_hash)
        return dict(c) if c else None

    def update_challenge(self, challenge_hash: str, **fields) -> None:
        self.challenges[challenge_hash].update(fields)

    def create_session(self, s: dict) -> None:
        self.sessions[s["session_hash"]] = dict(s)

    def get_session(self, session_hash: str) -> dict | None:
        s = self.sessions.get(session_hash)
        return dict(s) if s else None

    def update_session(self, session_hash: str, **fields) -> None:
        self.sessions[session_hash].update(fields)

    def log_auth_event(self, e: dict) -> None:
        self.events.append(dict(e))

    # admin operations ------------------------------------------------------------
    def insert_user(self, u: dict) -> None:
        self.add_user(**u)

    def update_user(self, user_id: str, **f) -> None:
        self.users[user_id].update(f)

    def list_users(self) -> list[dict]:
        return [dict(u, biometric_status=(self.bio.get(u["user_id"]) or {}).get("status")) for u in self.users.values()]

    def upsert_biometric_profile(self, p: dict) -> None:
        self.bio[p["user_id"]] = dict(p)

    def reset_biometric_profile(self, user_id: str) -> None:
        if user_id in self.bio:
            self.bio[user_id].update(status="RESET", face_template=None, typing_profile=None, enroll_salt=None)

    def revoke_user_sessions(self, user_id: str) -> None:
        for s in self.sessions.values():
            if s["user_id"] == user_id:
                s["revoked"] = True

    def log_admin(self, admin, action: str, target_user_id, details: dict) -> None:
        self.admin_audit.append({"admin": admin.user_id, "action": action, "target": target_user_id, "details": details})
