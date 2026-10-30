from datetime import date

import altair as alt
import streamlit as st

from mediverse import data
from mediverse.authz import Perm
from mediverse.ui import date_filter, department_filter, guard

p = guard(Perm.STAFF_SCHEDULE)
st.title("Staff & schedules", anchor=False)

with st.container(horizontal=True, vertical_alignment="bottom"):
    start, end = date_filter("shift_dates", date(2025, 11, 1), date(2025, 11, 30))
    dept = department_filter("shift_dept")
    stype = st.segmented_control("Shift", ["DAY", "EVENING", "NIGHT"], key="shift_type")

sh = data.shifts(p, start, end, dept, stype)
with st.container(horizontal=True):
    st.metric("Shifts", len(sh), border=True)
    st.metric("Staff scheduled", sh["STAFF_ID"].nunique() if len(sh) else 0, border=True)
    st.metric("Missed", int((sh["STATUS"] == "MISSED").sum()) if len(sh) else 0, border=True)
    st.metric("Swapped", int((sh["STATUS"] == "SWAPPED").sum()) if len(sh) else 0, border=True)

if len(sh):
    daily = sh.assign(DAY=sh["START_TS"].dt.date).groupby(["DAY", "SHIFT_TYPE"], as_index=False).size()
    st.altair_chart(alt.Chart(daily).mark_bar().encode(x=alt.X("DAY:T", title=None), y=alt.Y("size:Q", title="Shifts"),
                                                       color="SHIFT_TYPE:N"))
    st.dataframe(sh.drop(columns=["STAFF_ID"]), hide_index=True)
else:
    st.info("No shifts in this range.")

with st.expander("Staffing by department and role", icon=":material/groups:"):
    st.dataframe(data.staffing(p), hide_index=True)
