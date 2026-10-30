import altair as alt
import streamlit as st

from mediverse import ai, data
from mediverse.authz import Perm
from mediverse.ui import AI_LABEL, date_filter, department_filter, guard, money

p = guard(Perm.ANALYTICS_AGG)
st.title("Executive dashboard", anchor=False)

with st.container(horizontal=True, vertical_alignment="bottom"):
    start, end = date_filter("exec_dates")
    dept = department_filter("exec_dept")

k = data.executive_kpis(p)
with st.container(horizontal=True):
    st.metric("Patients", f"{k['patients']:,}", border=True)
    st.metric("Encounters", f"{k['encounters']:,}", border=True)
    st.metric("Open encounters", f"{k['open_encounters']:,}", border=True)
    st.metric("Lab results", f"{k['lab_results']:,}", border=True)
    st.metric("Imaging studies", f"{k['imaging']:,}", border=True)
    st.metric("Surgeries", f"{k['surgeries']:,}", border=True)
with st.container(horizontal=True):
    st.metric("Low-stock medicines", k["low_stock"], border=True)
    st.metric("Expired batches with stock", k["expired_batches"], border=True)
    st.metric("Salary expenditure (2025)", money(k["salary_spend"]), border=True)
    st.metric("Equipment asset cost", money(k["equipment_cost"]), border=True)
    st.metric("Open data-quality issues", f"{k['dq_open']:,}", border=True)

act = data.monthly_activity(p, start, end, dept)
work = data.department_workload(p, start, end)
c1, c2 = st.columns(2)
with c1.container(border=True):
    st.subheader("Encounters by month", anchor=False)
    st.altair_chart(alt.Chart(act).mark_line(point=True).encode(
        x=alt.X("MONTH:T", title=None), y=alt.Y("ENCOUNTERS:Q", title="Encounters"),
        color=alt.Color("ENCOUNTER_TYPE:N", title="Type")))
with c2.container(border=True):
    st.subheader("Department workload", anchor=False)
    st.altair_chart(alt.Chart(work).mark_bar().encode(
        x=alt.X("ENCOUNTERS:Q"), y=alt.Y("DEPARTMENT_NAME:N", sort="-x", title=None),
        tooltip=["DEPARTMENT_NAME", "ENCOUNTERS", "PATIENTS", "INPATIENT", "AVG_LOS_DAYS"]))
    st.caption("Groups with fewer than 10 patients are suppressed.")

if p.can(Perm.MORTALITY_READ):
    with st.expander("Mortality statistics (restricted)", icon=":material/lock:"):
        st.dataframe(data.mortality_stats(p), hide_index=True)

if p.can(Perm.AI_USE):
    with st.container(border=True):
        st.subheader("Operational trend summary", anchor=False)
        if st.button("Generate with Cortex AI", icon=":material/auto_awesome:"):
            with st.spinner("Summarising aggregated metrics..."):
                st.session_state.exec_ai = ai.trend_narrative(p, "department workload and encounter mix", work, Perm.ANALYTICS_AGG)
        if st.session_state.get("exec_ai"):
            st.info(AI_LABEL, icon=":material/smart_toy:")
            st.markdown(st.session_state.exec_ai["narrative"])
            st.caption(f"Source: department workload table above · output {st.session_state.exec_ai['output_id']}")
