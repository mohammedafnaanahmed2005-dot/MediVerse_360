import streamlit as st

from mediverse.admin import AdminError
from mediverse.biometrics.base import DEMO_BANNER
from mediverse.biometrics.typing_rhythm import PHRASE
from mediverse.components import typing_capture
from mediverse.services import DEMO_MODE, auth_service, bootstrap_admin, needs_bootstrap
from mediverse.ui import SYNTHETIC_NOTE

ss = st.session_state


def apply(step) -> None:
    # A duplicate submit (Enter + click, or a rerun racing the first) re-sends an already-consumed
    # challenge. If the first submit already opened a session, keep it instead of showing "expired".
    if step.kind == "DENIED" and "CHALLENGE_INVALID" in step.reasons and ss.get("session_token"):
        return
    if step.kind != "BIOMETRIC":
        ss.pop("bio_deadline", None)
    if step.kind == "SESSION":
        ss.update(session_token=step.token, auth_step="PASSWORD", challenge=None, otp_demo=None)
    elif step.kind in ("BIOMETRIC", "OTP", "RETRY_OTP"):
        if step.kind == "BIOMETRIC":
            ss.bio_round = ss.get("bio_round", 0) + 1  # fresh camera/typing widgets for each try
            ss.pop("login_typing_sample", None)
        ss.update(auth_step="OTP" if step.kind != "BIOMETRIC" else "BIOMETRIC", challenge=step.token)
    else:
        ss.update(auth_step="PASSWORD", challenge=None, otp_demo=None)
    ss.auth_msg = (step.kind, step.message)


_, mid, _ = st.columns([1, 1.4, 1])
with mid:
    st.title("MediVerse 360", anchor=False)
    st.caption("AI-powered hospital intelligence platform · " + SYNTHETIC_NOTE)

    kind, msg = ss.get("auth_msg", (None, None))
    if msg and kind == "DENIED":
        st.error(msg, icon=":material/error:")
    elif msg and kind in ("OTP", "RETRY_OTP", "BIOMETRIC"):
        st.info(msg, icon=":material/info:")

    step = ss.auth_step
    if step == "PASSWORD" and needs_bootstrap():
        with st.form("bootstrap", border=True):
            st.subheader("First-run setup", anchor=False)
            st.caption("No administrator exists yet. As MV_APP_OWNER, run "
                       "`SELECT KEY_VALUE FROM MEDIVERSE360.SECURITY.APP_KEYS WHERE KEY_NAME = 'BOOTSTRAP_TOKEN'` "
                       "in Snowsight and paste the token. It is deleted after use.")
            tok = st.text_input("Bootstrap token", type="password")
            b_user = st.text_input("Administrator username")
            b_name = st.text_input("Display name")
            b_mail = st.text_input("Verified email")
            b_pw = st.text_input("Password", type="password", help="12+ chars with upper, lower, digit and symbol")
            if st.form_submit_button("Create administrator", type="primary"):
                try:
                    bootstrap_admin(tok, username=b_user, display_name=b_name, email=b_mail, password=b_pw)
                    st.success("Administrator created. Sign in below.")
                    st.rerun()
                except AdminError as e:
                    st.error(str(e))
    elif step == "PASSWORD":
        portal = st.radio("Sign in as", ["Admin", "Staff user"], index=None, key="portal", horizontal=True)
        if not portal:
            st.info("Choose **Admin** or **Staff user** to continue.", icon=":material/badge:")
        else:
            st.caption("Admin: password only." if portal == "Admin" else
                       "Staff user: password, then face and typing check. If they don't match, a one-time code "
                       "is emailed to you.")
            with st.form("login", border=True):
                u = st.text_input("Username or email", max_chars=120, autocomplete="username")
                pw = st.text_input("Password", type="password", max_chars=128, autocomplete="current-password")
                if st.form_submit_button("Sign in", type="primary", icon=":material/login:", width="stretch"):
                    with st.spinner("Verifying..."):
                        apply(auth_service().login_password(u, pw, "ADMIN" if portal == "Admin" else "USER"))
                    st.rerun()

    elif step == "BIOMETRIC":
        from datetime import datetime, timedelta
        ss.setdefault("bio_deadline", datetime.now() + timedelta(minutes=auth_service().cfg["CHALLENGE_TTL_MINUTES"]))
        left = int((ss.bio_deadline - datetime.now()).total_seconds())
        if left <= 0:
            st.error("Verification time limit reached. Please sign in again.", icon=":material/timer_off:")
        else:
            st.caption(f":material/timer: Complete this step by {ss.bio_deadline:%H:%M:%S} ({left // 60}m {left % 60}s left).")
        if DEMO_MODE:
            st.warning(DEMO_BANNER, icon=":material/science:")
        with st.container(border=True):
            st.subheader("1. Face verification", anchor=False)
            st.caption("Take two photos: look straight at the camera, then turn your head slightly. "
                       "Images are reduced to a protected template in memory and never stored.")
            c1, c2 = st.columns(2)
            r = ss.get("bio_round", 0)
            f1 = c1.camera_input("Photo 1 - look straight", key=f"probe1_{r}")
            f2 = c2.camera_input("Photo 2 - turn slightly", key=f"probe2_{r}")
        with st.container(border=True):
            st.subheader("2. Typing rhythm", anchor=False)
            st.caption("A consented typing exercise. Only key timing is analysed, and it must be completed "
                       "within the time limit your administrator set.")
            sample = typing_capture(PHRASE, key=f"login_typing_{ss.get('bio_round', 0)}")
            if sample:
                ss.login_typing_sample = sample
            if ss.get("login_typing_sample"):
                st.success("Typing sample captured.", icon=":material/keyboard:")
        c1, c2 = st.columns(2)
        if c1.button("Verify", type="primary", icon=":material/verified_user:", width="stretch"):
            frames = [f.getvalue() for f in (f1, f2) if f is not None]
            with st.spinner("Checking..."):
                apply(auth_service().verify_biometrics(ss.challenge, frames or None, ss.pop("login_typing_sample", None)))
            st.rerun()
        if c2.button("Use one-time code instead", icon=":material/sms:", width="stretch"):
            apply(auth_service().verify_biometrics(ss.challenge, None, None))
            st.rerun()

    elif step == "OTP":
        st.caption("Face or typing verification did not match, so this sign-in needs a one-time code. "
                   "Check your registered email.")
        with st.form("otp", border=True):
            code = st.text_input("6-digit code", max_chars=6)
            if st.form_submit_button("Verify code", type="primary", width="stretch") and not ss.get("session_token"):
                apply(auth_service().verify_otp(ss.challenge, code))
                st.rerun()
        if st.button("Cancel", icon=":material/close:"):
            ss.update(auth_step="PASSWORD", challenge=None, otp_demo=None, auth_msg=(None, None))
            st.rerun()
