import streamlit as st

from mediverse.admin import AdminError
from mediverse.authz import AccessDenied, Perm, Role
from mediverse.biometrics.base import DEMO_BANNER
from mediverse.data import departments
from mediverse.services import DEMO_MODE, admin_service, clear_config_cache, repo
from mediverse.ui import guard

p = guard(Perm.USERS_MANAGE)
st.title("Users & enrollment", anchor=False)
svc = admin_service()
ss = st.session_state


def run(fn, *a, ok: str = "Done.", **kw):
    try:
        out = fn(p, *a, **kw)
        st.toast(ok, icon=":material/check_circle:")
        return out if out is not None else True
    except (AdminError, AccessDenied, ValueError) as e:
        st.error(str(e), icon=":material/error:")
        return None


users = repo().list_users()
others = [u for u in users if u["user_id"] != p.user_id]
label = lambda u: f"{u['username']} · {u['role_code']} · {u['status']}"

tab_users, tab_new, tab_enroll, tab_cfg = st.tabs(["Accounts", "Create account", "Face enrollment", "Security settings"])

with tab_users:
    st.dataframe([{k: u.get(k) for k in ("username", "display_name", "email", "designation", "role_code", "department_id",
                                         "typing_max_seconds", "status", "flags", "locked_until", "biometric_status",
                                         "enrolled_at")} for u in users], hide_index=True)
    if others:
        u = st.selectbox("Manage account", others, format_func=label, key="mg_user")
        c1, c2 = st.columns(2)
        with c1.container(border=True):
            new_role = st.selectbox("Role", [r.value for r in Role], index=[r.value for r in Role].index(u["role_code"]))
            if st.button("Change role", disabled=new_role == u["role_code"]):
                run(svc.change_role, u["user_id"], new_role, ok="Role changed; user sessions revoked.") and st.rerun()
            flags = st.pills("Restricted access flags", ["PSYCH_ACCESS", "MORTALITY_ACCESS"], selection_mode="multi",
                             default=u.get("flags") or [], key=f"flags_{u['user_id']}")
            st.caption("Flags only take effect for roles that are granted the matching restricted permission (Doctor).")
            if st.button("Save flags"):
                run(svc.set_flags, u["user_id"], flags, ok="Flags updated.") and st.rerun()
        with c2.container(border=True):
            target = "INACTIVE" if u["status"] == "ACTIVE" else "ACTIVE"
            if st.button(f"{'Deactivate' if target == 'INACTIVE' else 'Activate'} account",
                         icon=":material/person_off:" if target == "INACTIVE" else ":material/person_check:"):
                run(svc.set_status, u["user_id"], target, ok=f"Account {target.lower()}.") and st.rerun()
            if st.button("Unlock account", icon=":material/lock_open:"):
                run(svc.unlock, u["user_id"], ok="Unlocked.") and st.rerun()
            with st.form(f"pw_{u['user_id']}"):
                tmp = st.text_input("New temporary password", type="password")
                if st.form_submit_button("Reset password"):
                    run(svc.reset_password, u["user_id"], tmp, ok="Password reset; sessions revoked.")
            with st.form(f"det_{u['user_id']}"):
                mail = st.text_input("Email (for one-time codes)", value=u.get("email") or "")
                des = st.text_input("Designation", value=u.get("designation") or "")
                lim = st.number_input("Typing time limit (seconds, 0 = none)", 0.0, 120.0,
                                      float(u.get("typing_max_seconds") or 0), 0.5)
                if st.form_submit_button("Save details"):
                    run(svc.set_details, u["user_id"], designation=des, typing_max_seconds=lim or None,
                        email=mail, ok="Details saved.") and st.rerun()

with tab_new:
    depts = departments()
    with st.form("new_user", clear_on_submit=True):
        c1, c2 = st.columns(2)
        un = c1.text_input("Username", help="lowercase letters, digits, . _ -")
        dn = c2.text_input("Display name")
        role = c1.selectbox("Role", [r.value for r in Role])
        email = c2.text_input("Verified email (OTP channel)")
        dep = c1.selectbox("Department", [None] + depts["DEPARTMENT_ID"].tolist(),
                           format_func=lambda d: "None" if d is None else depts.set_index("DEPARTMENT_ID").loc[d, "DEPARTMENT_NAME"])
        staff = c2.text_input("Staff ID (optional)")
        pw = c1.text_input("Temporary password", type="password",
                           help="12+ chars with upper, lower, digit and symbol")
        fl = c2.pills("Restricted flags", ["PSYCH_ACCESS", "MORTALITY_ACCESS"], selection_mode="multi")
        desig = c1.text_input("Designation", placeholder="e.g. Senior Lab Technician")
        lim = c2.number_input("Typing time limit (seconds, 0 = none)", 0.0, 120.0, 15.0, 0.5,
                              help="Max time allowed to type the verification phrase at sign-in.")
        if st.form_submit_button("Create account", type="primary"):
            uid = run(svc.create_user, username=un, display_name=dn, role=role, email=email or None, department_id=dep,
                      staff_id=staff or None, temp_password=pw, flags=fl or [], designation=desig,
                      typing_max_seconds=lim or None, ok="Account created.")
            if uid:
                st.info("Next: open **Face enrollment** to add this user's face photos. Enrolled users must pass "
                        "the face check (and typing time limit) at sign-in, otherwise an emailed one-time code is required.")

with tab_enroll:
    from mediverse.biometrics import lbp_face
    if lbp_face.available():
        st.caption(":material/face: Face engine: OpenCV LBP matcher active.")
    else:
        st.warning(f"Face engine: OpenCV is not available ({lbp_face.LAST_ERROR}); using the simplified demo "
                   "matcher, which is less reliable.", icon=":material/warning:")
    if DEMO_MODE:
        st.warning(DEMO_BANNER, icon=":material/science:")
    lab = others
    if not lab:
        st.info("No other accounts to enroll.")
    else:
        u = st.selectbox("Staff member", lab, format_func=lambda x: f"{label(x)} · biometrics: {x.get('biometric_status') or 'none'}")
        if u.get("typing_max_seconds"):
            st.caption(f"Typing time limit at sign-in: {float(u['typing_max_seconds']):g} s (change it on the Accounts tab).")
        st.markdown("Add **three face photos** of the user: take them with the camera, or upload photos.")
        key = f"enr_{u['user_id']}"
        src = st.radio("Photo source", ["Camera", "Upload"], horizontal=True, key=f"{key}_src")
        if src == "Camera":
            cols = st.columns(3)
            shots = [cols[i].camera_input(f"Photo {i + 1}", key=f"{key}_f{i}") for i in range(3)]
            frames = [s.getvalue() for s in shots if s is not None]
        else:
            ups = st.file_uploader("Face photos (JPG/PNG, one face per photo, 3 or more)", type=["jpg", "jpeg", "png"],
                                   accept_multiple_files=True, key=f"{key}_up")
            frames = [f.getvalue() for f in ups or []]
            if frames:
                st.image(frames, width=140)
        st.caption(f"{len(frames)} photo(s) ready.")
        with st.form(f"{key}_confirm", border=True):
            consent = st.checkbox("The user consents to face enrollment.")
            if st.form_submit_button("Enroll", type="primary"):
                res = run(svc.enroll_biometrics, u["user_id"], face_samples=frames, consent_confirmed=consent,
                          ok="Enrolled.")
                if res:
                    st.success(f"Enrollment saved (face consistency {res.get('face_consistency')}). "
                               "Photos were reduced to a protected template and discarded.")
        with st.form(f"{key}_reset"):
            reason = st.text_input("Reason for reset")
            if st.form_submit_button("Reset enrollment", icon=":material/restart_alt:"):
                run(svc.reset_enrollment, u["user_id"], reason or "unspecified", ok="Enrollment reset; OTP will be required.")

with tab_cfg:
    p.require(Perm.CONFIG_MANAGE)
    cfg = svc.auth.cfg
    with st.form("cfg"):
        c1, c2 = st.columns(2)
        face_t = c1.slider("Face match threshold", 0.70, 0.99, float(cfg["FACE_THRESHOLD"]), 0.01)
        typ_t = c2.slider("Typing rhythm threshold", 0.40, 0.95, float(cfg["TYPING_THRESHOLD"]), 0.05)
        max_fail = c1.number_input("Failed sign-ins before lockout", 3, 10, int(cfg["MAX_FAILED_LOGINS"]))
        lock_min = c2.number_input("Lockout minutes", 5, 120, int(cfg["LOCKOUT_MINUTES"]))
        idle = c1.number_input("Session idle timeout (min)", 5, 120, int(cfg["SESSION_IDLE_MINUTES"]))
        otp_roles = c2.multiselect("Roles that always require OTP", [r.value for r in Role], default=cfg["OTP_REQUIRED_ROLES"])
        if st.form_submit_button("Save settings", type="primary"):
            if "ADMIN" not in otp_roles:
                st.error("ADMIN must remain in the OTP-required roles.")
            else:
                for k, v in {"FACE_THRESHOLD": face_t, "TYPING_THRESHOLD": typ_t, "MAX_FAILED_LOGINS": int(max_fail),
                             "LOCKOUT_MINUTES": int(lock_min), "SESSION_IDLE_MINUTES": int(idle),
                             "OTP_REQUIRED_ROLES": otp_roles}.items():
                    repo().set_config(k, v, p.user_id)
                repo().log_admin(p, "UPDATE_CONFIG", None, {"face": face_t, "typing": typ_t, "max_fail": int(max_fail),
                                                            "lock_min": int(lock_min), "idle": int(idle), "otp_roles": otp_roles})
                clear_config_cache()
                st.success("Settings saved.")
