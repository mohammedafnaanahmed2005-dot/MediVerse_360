import altair as alt
import streamlit as st

from mediverse.authz import Perm
from mediverse.db import session
from mediverse.ui import guard

p = guard(Perm.AUDIT_VIEW)
st.title("Security & audit", anchor=False)
S = "MEDIVERSE360.SECURITY"
days = st.segmented_control("Window", [1, 7, 30, 90], default=7, format_func=lambda d: f"Last {d} day{'s' if d > 1 else ''}") or 7


def q(sql, params=()):
    return session().sql(sql, params=list(params)).to_pandas()


ev = q(f"""SELECT TS, USERNAME, EVENT_TYPE, OUTCOME, ARRAY_TO_STRING(REASON_CODES, ', ') REASONS, FACE_SCORE, TYPING_SCORE,
           AUTH_METHOD, CHANNEL FROM {S}.AUTH_EVENTS WHERE TS >= DATEADD('day', -?, SYSDATE()) ORDER BY TS DESC LIMIT 5000""",
       (int(days),))
logins_ok = ev[(ev["EVENT_TYPE"] == "LOGIN") & (ev["OUTCOME"] == "OK")]
with st.container(horizontal=True):
    st.metric("Successful sign-ins", len(logins_ok), border=True)
    st.metric("Password failures", int(((ev["EVENT_TYPE"] == "PASSWORD") & (ev["OUTCOME"] == "FAIL")).sum()), border=True)
    st.metric("Biometric pass (no OTP)", int(((ev["EVENT_TYPE"] == "BIOMETRIC") & (ev["OUTCOME"] == "OK")).sum()), border=True)
    st.metric("Biometric fallback to OTP", int(((ev["EVENT_TYPE"] == "BIOMETRIC") & (ev["OUTCOME"] == "FALLBACK")).sum()), border=True)
    st.metric("OTP failures", int(((ev["EVENT_TYPE"] == "OTP") & (ev["OUTCOME"] == "FAIL")).sum()), border=True)
    st.metric("Security alerts", int((ev["EVENT_TYPE"] == "SECURITY_ALERT").sum()), border=True)

alerts = ev[ev["EVENT_TYPE"] == "SECURITY_ALERT"]
if len(alerts):
    st.error(f"{len(alerts)} security alert(s) in this window.", icon=":material/notification_important:")
    st.dataframe(alerts[["TS", "USERNAME", "REASONS"]], hide_index=True)

locked = q(f"SELECT USERNAME, ROLE_CODE, FAILED_COUNT, LOCKED_UNTIL FROM {S}.APP_USERS WHERE LOCKED_UNTIL > SYSDATE()")
if len(locked):
    st.warning(f"{len(locked)} account(s) currently locked.", icon=":material/lock_clock:")
    st.dataframe(locked, hide_index=True)

if len(ev):
    hourly = ev.assign(HOUR=ev["TS"].dt.floor("h")).groupby(["HOUR", "EVENT_TYPE", "OUTCOME"], as_index=False).size()
    st.altair_chart(alt.Chart(hourly).mark_bar().encode(
        x=alt.X("HOUR:T", title=None), y=alt.Y("size:Q", title="Events"), color="OUTCOME:N",
        tooltip=["EVENT_TYPE", "OUTCOME", "size"]))

t1, t2, t3, t4 = st.tabs(["Authentication events", "Data access log", "Administrator actions", "Active sessions"])
with t1:
    st.caption("Reason codes only. Passwords, OTPs, images and biometric templates are never logged.")
    st.dataframe(ev, hide_index=True)
with t2:
    st.dataframe(q(f"""SELECT TS, USERNAME, ROLE_CODE, RESOURCE, ACTION, RECORD_REF, ALLOWED, DETAIL FROM {S}.ACCESS_LOG
                      WHERE TS >= DATEADD('day', -?, SYSDATE()) ORDER BY TS DESC LIMIT 2000""", (int(days),)), hide_index=True)
with t3:
    st.dataframe(q(f"""SELECT a.TS, a.ADMIN_USERNAME, a.ACTION, u.USERNAME TARGET, a.DETAILS FROM {S}.ADMIN_AUDIT a
                      LEFT JOIN {S}.APP_USERS u ON u.USER_ID = a.TARGET_USER_ID
                      WHERE a.TS >= DATEADD('day', -?, SYSDATE()) ORDER BY a.TS DESC""", (int(days),)), hide_index=True)
with t4:
    st.dataframe(q(f"""SELECT u.USERNAME, s.ROLE_CODE, s.AUTH_METHOD, s.CREATED_AT, s.LAST_SEEN_AT, s.EXPIRES_AT
                      FROM {S}.SESSIONS s JOIN {S}.APP_USERS u USING (USER_ID)
                      WHERE NOT s.REVOKED AND s.EXPIRES_AT > SYSDATE() AND s.LAST_SEEN_AT > DATEADD('minute', -30, SYSDATE())
                      ORDER BY s.LAST_SEEN_AT DESC"""), hide_index=True)
