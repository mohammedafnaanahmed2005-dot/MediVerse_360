import streamlit as st

from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
from mediverse.authz import PAGE_PERMS, Perm, Principal

REPORT_PERMS = [Perm.ANALYTICS_AGG, Perm.LAB_READ, Perm.IMAGING_READ, Perm.SURGERY_READ, Perm.PHARMACY_READ,
                Perm.FINANCE_READ, Perm.STAFF_SCHEDULE]
from mediverse.services import DEMO_MODE, auth_service
from mediverse.ui import SYNTHETIC_NOTE

st.set_page_config(page_title="MediVerse 360", page_icon=":material/local_hospital:", layout="wide")

for k, v in {"session_token": None, "principal": None, "auth_step": "PASSWORD", "challenge": None}.items():
    st.session_state.setdefault(k, v)

# Re-validate the server-side session on every rerun (expiry, deactivation, role change).
user = auth_service().validate_session(st.session_state.session_token) if st.session_state.session_token else None
if user:
    st.session_state.principal = Principal(user["user_id"], user["username"], user["role_code"], user.get("flags") or [],
                                           user.get("department_id"), user.get("display_name") or user["username"])
else:
    if st.session_state.session_token:
        st.session_state.update(session_token=None, auth_step="PASSWORD", challenge=None)
        st.toast("Your session ended. Please sign in again.", icon=":material/timer_off:")
    st.session_state.principal = None

p: Principal | None = st.session_state.principal

PAGES = {
    "executive": ("app_pages/executive.py", "Executive dashboard", ":material/monitoring:"),
    "reports": ("app_pages/reports.py", "Reports", ":material/summarize:"),
    "patients": ("app_pages/patients.py", "Patient360", ":material/personal_injury:"),
    "staff": ("app_pages/staff.py", "Staff & schedules", ":material/badge:"),
    "laboratory": ("app_pages/laboratory.py", "LabLens AI", ":material/biotech:"),
    "lab_docs": ("app_pages/lab_docs.py", "Laboratory Docs", ":material/lab_profile:"),
    "imaging_surgery": ("app_pages/imaging_surgery.py", "Imaging & surgery", ":material/radiology:"),
    "pharmacy": ("app_pages/pharmacy.py", "PharmaStock 360", ":material/medication:"),
    "finance": ("app_pages/finance.py", "Finance & budgets", ":material/payments:"),
    "data_quality": ("app_pages/data_quality.py", "Data quality", ":material/fact_check:"),
    "security": ("app_pages/security.py", "Security & audit", ":material/shield_person:"),
    "admin": ("app_pages/admin.py", "Users & enrollment", ":material/manage_accounts:"),
}
SECTIONS = {"Overview": ["executive", "reports"], "Clinical": ["patients", "laboratory", "lab_docs", "imaging_surgery"],
            "Operations": ["pharmacy", "staff", "finance"], "HealthShield": ["data_quality", "security", "admin"]}

if p is None:
    nav = st.navigation([st.Page("app_pages/login.py", title="Sign in", icon=":material/login:")], position="hidden")
else:
    groups = {}
    for section, keys in SECTIONS.items():
        pages = [st.Page(PAGES[k][0], title=PAGES[k][1], icon=PAGES[k][2]) for k in keys
                 if (APP_DIR / PAGES[k][0]).is_file()  # tolerate a partially synced deployment
                 and ((k == "reports" and any(p.can(x) for x in REPORT_PERMS)) or (k != "reports" and p.can(PAGE_PERMS[k])))]
        if pages:
            groups[section] = pages
    if not groups:
        groups = {"": [st.Page("app_pages/no_access.py", title="No access", icon=":material/block:")]}
    nav = st.navigation(groups, position="sidebar")
    with st.sidebar:
        with st.container(border=True):
            st.markdown(f"**{p.display_name}**")
            st.caption(f"{p.role.value.replace('_', ' ').title()} · {p.username}")
            if st.button("Sign out", icon=":material/logout:", width="stretch"):
                auth_service().logout(st.session_state.session_token)
                st.session_state.update(session_token=None, principal=None, auth_step="PASSWORD", challenge=None)
                st.rerun()
        st.caption(SYNTHETIC_NOTE)
        if DEMO_MODE:
            st.caption(":orange[Demo mode: simulated face matching (OpenCV unavailable).]")

nav.run()
