"""Role-based authorization. Every data access path calls ``require``/``can``.

Hiding a page is not the control: the data layer (``mediverse/data.py``) checks
permissions again before running any query.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    ADMIN = "ADMIN"
    DOCTOR = "DOCTOR"
    NURSE = "NURSE"
    LAB = "LAB_STAFF"
    RADIOLOGY = "RADIOLOGY_STAFF"
    PHARMACIST = "PHARMACIST"
    FINANCE = "FINANCE_STAFF"
    MANAGEMENT = "MANAGEMENT"


class Perm(str, Enum):
    USERS_MANAGE = "users.manage"
    ENROLL_MANAGE = "enrollment.manage"
    CONFIG_MANAGE = "config.manage"
    AUDIT_VIEW = "audit.view"
    DATA_INGEST = "data.ingest"
    DQ_REVIEW = "dq.review"
    PATIENT_READ = "patient.read"
    PATIENT_PII = "patient.pii"
    CLINICAL_READ = "clinical.read"
    NURSING_READ = "nursing.read"
    LAB_READ = "lab.read"
    IMAGING_READ = "imaging.read"
    SURGERY_READ = "surgery.read"
    PHARMACY_READ = "pharmacy.read"
    PRESCRIPTION_READ = "prescription.read"
    FINANCE_READ = "finance.read"
    PAYROLL_READ = "payroll.read"
    STAFF_SCHEDULE = "staff.schedule"
    ANALYTICS_AGG = "analytics.aggregate"
    AI_USE = "ai.use"
    LAB_DOCS = "lab.documents"
    # Restricted categories: role grant AND per-user flag both required.
    PSYCH_READ = "restricted.psychiatric"
    MORTALITY_READ = "restricted.mortality"


ROLE_PERMS: dict[Role, set[Perm]] = {
    Role.ADMIN: {Perm.USERS_MANAGE, Perm.ENROLL_MANAGE, Perm.CONFIG_MANAGE, Perm.AUDIT_VIEW,
                 Perm.DATA_INGEST, Perm.DQ_REVIEW, Perm.STAFF_SCHEDULE, Perm.ANALYTICS_AGG},
    Role.DOCTOR: {Perm.PATIENT_READ, Perm.PATIENT_PII, Perm.CLINICAL_READ, Perm.LAB_READ, Perm.IMAGING_READ,
                  Perm.SURGERY_READ, Perm.PRESCRIPTION_READ, Perm.AI_USE, Perm.PSYCH_READ, Perm.MORTALITY_READ},
    Role.NURSE: {Perm.PATIENT_READ, Perm.PATIENT_PII, Perm.NURSING_READ, Perm.PRESCRIPTION_READ, Perm.LAB_READ},
    Role.LAB: {Perm.PATIENT_READ, Perm.LAB_READ, Perm.AI_USE, Perm.LAB_DOCS},
    Role.RADIOLOGY: {Perm.PATIENT_READ, Perm.IMAGING_READ, Perm.AI_USE},
    Role.PHARMACIST: {Perm.PHARMACY_READ, Perm.PRESCRIPTION_READ, Perm.PATIENT_READ},
    Role.FINANCE: {Perm.FINANCE_READ, Perm.PAYROLL_READ, Perm.AI_USE},
    Role.MANAGEMENT: {Perm.ANALYTICS_AGG, Perm.STAFF_SCHEDULE, Perm.AI_USE, Perm.DQ_REVIEW},
}

# Per-user flags that must ALSO be present for restricted permissions.
RESTRICTED_FLAGS = {Perm.PSYCH_READ: "PSYCH_ACCESS", Perm.MORTALITY_READ: "MORTALITY_ACCESS"}

# Roles whose patient access is limited to their department (nurses: assigned care units).
DEPARTMENT_SCOPED = {Role.NURSE}

# Minimum group size for aggregated (management) outputs.
K_ANONYMITY_MIN = 10


class AccessDenied(Exception):
    pass


class Principal:
    def __init__(self, user_id: str, username: str, role: str, flags: list[str] | None = None,
                 department_id: str | None = None, display_name: str = ""):
        self.user_id = user_id
        self.username = username
        self.role = Role(role)
        self.flags = set(flags or [])
        self.department_id = department_id
        self.display_name = display_name or username

    def can(self, perm: Perm) -> bool:
        if perm not in ROLE_PERMS.get(self.role, set()):
            return False
        flag = RESTRICTED_FLAGS.get(perm)
        return flag is None or flag in self.flags

    def require(self, perm: Perm) -> None:
        if not self.can(perm):
            raise AccessDenied(f"{self.role.value} lacks {perm.value}")

    @property
    def department_scope(self) -> str | None:
        return self.department_id if self.role in DEPARTMENT_SCOPED else None


# Pages -> permission needed to open them (navigation is filtered with this, and
# each page calls ``require`` again on load).
PAGE_PERMS = {
    "executive": Perm.ANALYTICS_AGG,
    "patients": Perm.PATIENT_READ,
    "staff": Perm.STAFF_SCHEDULE,
    "laboratory": Perm.LAB_READ,
    "lab_docs": Perm.LAB_DOCS,
    "imaging_surgery": Perm.IMAGING_READ,
    "pharmacy": Perm.PHARMACY_READ,
    "finance": Perm.FINANCE_READ,
    "data_quality": Perm.DQ_REVIEW,
    "security": Perm.AUDIT_VIEW,
    "admin": Perm.USERS_MANAGE,
}
