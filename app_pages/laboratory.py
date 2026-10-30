import altair as alt
import streamlit as st

from mediverse import ai, data
from mediverse.authz import Perm
from mediverse.ui import AI_LABEL, SYNTHETIC_NOTE, date_filter, department_filter, guard

p = guard(Perm.LAB_READ)
st.title("LabLens AI", anchor=False)
st.caption(SYNTHETIC_NOTE + " Reference ranges are illustrative.")

with st.container(horizontal=True, vertical_alignment="bottom"):
    start, end = date_filter("lab_dates")
    dept = department_filter("lab_dept")

s = data.lab_summary(p, start, end, dept)
total = int(s["status"]["ORDERS"].sum()) if not s["status"].empty else 0
with st.container(horizontal=True):
    st.metric("Lab orders", f"{total:,}", border=True)
    for row in s["status"].itertuples():
        st.metric(row.STATUS.title().replace("_", " "), f"{row.ORDERS:,}", border=True)

c1, c2 = st.columns(2)
with c1.container(border=True):
    st.subheader("Test volume", anchor=False)
    st.altair_chart(alt.Chart(s["volume"]).mark_bar().encode(
        x=alt.X("MONTH:T", title=None), y="ORDERS:Q", color=alt.Color("PRIORITY:N")))
with c2.container(border=True):
    st.subheader("Specimen condition", anchor=False)
    st.altair_chart(alt.Chart(s["specimens"]).mark_bar().encode(
        x="N:Q", y=alt.Y("SPECIMEN_TYPE:N", title=None), color="CONDITION:N"))

st.subheader("Abnormal-result rate and turnaround by test", anchor=False)
st.dataframe(s["abnormal"], hide_index=True, column_config={
    "PCT_ABNORMAL": st.column_config.ProgressColumn("% outside reference", min_value=0, max_value=100, format="%.1f%%"),
    "MEDIAN_TAT_H": st.column_config.NumberColumn("Median turnaround (h)")})

st.subheader("Worklist", anchor=False)
with st.container(horizontal=True):
    status = st.segmented_control("Status", ["COMPLETED", "IN_PROGRESS", "CANCELLED"], key="wl_status")
    prio = st.segmented_control("Priority", ["ROUTINE", "URGENT", "STAT"], key="wl_prio")
st.dataframe(data.lab_worklist(p, status, prio), hide_index=True)

with st.expander("Pathology reports", icon=":material/description:"):
    pr = data.pathology_reports(p)
    pick = st.selectbox("Report", pr["PATHOLOGY_ID"].tolist(), key="path_pick")
    if pick:
        row = pr[pr["PATHOLOGY_ID"] == pick].iloc[0]
        st.text(row["REPORT_TEXT"])
        if p.can(Perm.AI_USE) and st.button("Summarise report (AI)", icon=":material/auto_awesome:"):
            out = ai.summarize_pathology(p, pick)
            st.info(AI_LABEL, icon=":material/smart_toy:")
            st.markdown(out["summary"])
            st.caption(f"Source: CURATED.PATHOLOGY_REPORTS/{pick} · output {out['output_id']} (pending review)")
