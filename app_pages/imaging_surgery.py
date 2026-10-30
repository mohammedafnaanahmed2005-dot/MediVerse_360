import altair as alt
import streamlit as st

from mediverse import data
from mediverse.authz import Perm
from mediverse.ui import SYNTHETIC_NOTE, date_filter, guard, money

p = guard(Perm.IMAGING_READ)
st.title("Imaging & surgery intelligence", anchor=False)
st.caption(SYNTHETIC_NOTE + " Imaging is metadata and synthetic report text only; no images are stored.")
start, end = date_filter("img_dates")

img = data.imaging_summary(p, start, end)
bm = img["by_modality"]
with st.container(horizontal=True):
    st.metric("Studies", f"{int(bm['STUDIES'].sum()):,}", border=True)
    for mod, n in bm.groupby("MODALITY")["STUDIES"].sum().items():
        st.metric(mod, f"{n:,}", border=True)
    st.metric("Pending report", f"{int(bm.loc[bm['STATUS'] != 'REPORTED', 'STUDIES'].sum()):,}", border=True)

c1, c2 = st.columns(2)
with c1.container(border=True):
    st.subheader("Studies per month", anchor=False)
    st.altair_chart(alt.Chart(img["trend"]).mark_area().encode(
        x=alt.X("MONTH:T", title=None), y="STUDIES:Q", color="MODALITY:N"))
with c2.container(border=True):
    st.subheader("Body region by modality", anchor=False)
    st.altair_chart(alt.Chart(bm).mark_rect().encode(
        x="MODALITY:N", y=alt.Y("BODY_REGION:N", title=None), color=alt.Color("sum(STUDIES):Q", title="Studies")))

with st.expander("Radiology reports", icon=":material/description:"):
    rs = st.segmented_control("Report status", ["FINAL", "DRAFT"], key="rr_status")
    rep = img["reports"]
    st.dataframe(rep[rep["REPORT_STATUS"] == rs] if rs else rep, hide_index=True)

if p.can(Perm.SURGERY_READ):
    st.divider()
    st.subheader("Surgical records", anchor=False)
    s = data.surgery_summary(p, start, end)
    pr = s["procedures"]
    with st.container(horizontal=True):
        st.metric("Surgeries", f"{int(pr['SURGERIES'].sum()):,}", border=True)
        st.metric("Average duration", f"{pr['AVG_MIN'].mean():.0f} min" if not pr.empty else "-", border=True)
        st.metric("Average cost", money(pr["AVG_COST"].mean()) if not pr.empty else "-", border=True)
    c1, c2 = st.columns([2, 1])
    c1.dataframe(pr, hide_index=True, column_config={
        "PCT_ADVERSE": st.column_config.NumberColumn("% complication/readmit/death", format="%.1f%%")})
    with c2.container(border=True):
        st.altair_chart(alt.Chart(s["outcomes"]).mark_arc(innerRadius=50).encode(theta="N:Q", color="OUTCOME:N"))
        st.caption("Post-operative outcomes")
else:
    st.caption(":material/lock: Surgical records are not available to your role.")
