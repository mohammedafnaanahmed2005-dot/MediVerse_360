"""Cross-module reports: every chart the signed-in role may see, each with a CSV download."""

from datetime import date

import altair as alt
import streamlit as st

from mediverse import data
from mediverse.authz import Perm
from mediverse.ui import date_filter, principal

p = principal()
if p is None:
    st.stop()
REPORT_PERMS = [Perm.ANALYTICS_AGG, Perm.LAB_READ, Perm.IMAGING_READ, Perm.SURGERY_READ, Perm.PHARMACY_READ,
                Perm.FINANCE_READ, Perm.STAFF_SCHEDULE]
if not any(p.can(x) for x in REPORT_PERMS):
    st.error("No reports are available for your role.", icon=":material/block:")
    st.stop()

st.title("Reports", anchor=False)
st.caption("Synthetic data. Each report respects your role's permissions; download any table as CSV.")
start, end = date_filter("rep_dates", date(2023, 1, 1), date(2025, 12, 31))


def report(title: str, df, chart=None, key: str = ""):
    with st.container(border=True):
        st.subheader(title, anchor=False)
        if df is None or df.empty:
            st.info("No data for this selection.")
            return
        if chart is not None:
            st.altair_chart(chart)
        with st.expander("Table", icon=":material/table:"):
            st.dataframe(df, hide_index=True)
        st.download_button("Download CSV", df.to_csv(index=False), f"{key or title}.csv", "text/csv",
                           icon=":material/download:", key=f"dl_{key}")


tabs = [(n, perm) for n, perm in [("Hospital", Perm.ANALYTICS_AGG), ("Laboratory", Perm.LAB_READ),
                                  ("Imaging", Perm.IMAGING_READ), ("Surgery", Perm.SURGERY_READ),
                                  ("Pharmacy", Perm.PHARMACY_READ), ("Finance", Perm.FINANCE_READ),
                                  ("Staffing", Perm.STAFF_SCHEDULE)] if p.can(perm)]
choice = st.segmented_control("Module", [t[0] for t in tabs], default=tabs[0][0])

if choice == "Hospital":
    act = data.monthly_activity(p, start, end, None)
    report("Encounters by month and type", act, alt.Chart(act).mark_area().encode(
        x=alt.X("MONTH:T", title=None), y="ENCOUNTERS:Q", color="ENCOUNTER_TYPE:N"), "encounters_monthly")
    work = data.department_workload(p, start, end)
    report("Department workload", work, alt.Chart(work).mark_bar().encode(
        x="ENCOUNTERS:Q", y=alt.Y("DEPARTMENT_NAME:N", sort="-x", title=None), color="AVG_LOS_DAYS:Q"), "dept_workload")
    if p.can(Perm.MORTALITY_READ):
        m = data.mortality_stats(p)
        report("Mortality by year and location (restricted)", m, alt.Chart(m).mark_bar().encode(
            x="YEAR:O", y="DEATHS:Q", color="LOCATION:N"), "mortality")

elif choice == "Laboratory":
    s = data.lab_summary(p, start, end, None)
    report("Lab order volume by priority", s["volume"], alt.Chart(s["volume"]).mark_bar().encode(
        x=alt.X("MONTH:T", title=None), y="ORDERS:Q", color="PRIORITY:N"), "lab_volume")
    report("Abnormal result rate by test", s["abnormal"], alt.Chart(s["abnormal"]).mark_bar().encode(
        x="PCT_ABNORMAL:Q", y=alt.Y("TEST_NAME:N", sort="-x", title=None)), "lab_abnormal")
    report("Median turnaround by test (hours)", s["abnormal"], alt.Chart(s["abnormal"]).mark_point(size=80).encode(
        x="RESULTS:Q", y="MEDIAN_TAT_H:Q", tooltip=["TEST_NAME", "RESULTS", "MEDIAN_TAT_H"]), "lab_tat")
    report("Order status", s["status"], alt.Chart(s["status"]).mark_arc(innerRadius=50).encode(
        theta="ORDERS:Q", color="STATUS:N"), "lab_status")
    report("Specimen condition", s["specimens"], alt.Chart(s["specimens"]).mark_bar().encode(
        x="N:Q", y=alt.Y("SPECIMEN_TYPE:N", title=None), color="CONDITION:N"), "specimens")

elif choice == "Imaging":
    img = data.imaging_summary(p, start, end)
    report("Studies per month by modality", img["trend"], alt.Chart(img["trend"]).mark_line(point=True).encode(
        x=alt.X("MONTH:T", title=None), y="STUDIES:Q", color="MODALITY:N"), "imaging_trend")
    report("Studies by modality, region and status", img["by_modality"], alt.Chart(img["by_modality"]).mark_bar().encode(
        x="sum(STUDIES):Q", y=alt.Y("MODALITY:N", title=None), color="STATUS:N"), "imaging_mix")

elif choice == "Surgery":
    s = data.surgery_summary(p, start, end)
    report("Surgeries and average duration by procedure", s["procedures"], alt.Chart(s["procedures"]).mark_bar().encode(
        x="SURGERIES:Q", y=alt.Y("PROCEDURE_NAME:N", sort="-x", title=None), color="AVG_MIN:Q"), "surgery_procedures")
    report("Post-operative outcomes", s["outcomes"], alt.Chart(s["outcomes"]).mark_arc(innerRadius=50).encode(
        theta="N:Q", color="OUTCOME:N"), "surgery_outcomes")
    report("Anaesthesia type", s["anesthesia"], alt.Chart(s["anesthesia"]).mark_bar().encode(
        x="N:Q", y=alt.Y("ANESTHESIA_TYPE:N", title=None)), "anesthesia")

elif choice == "Pharmacy":
    inv = data.pharmacy_inventory(p, 90)
    st_ = inv["stock"].assign(STATUS=lambda d: d["LOW_STOCK"].map({True: "Below reorder", False: "OK"}))
    report("Usable stock vs reorder level", st_, alt.Chart(st_).mark_bar().encode(
        x="USABLE_QTY:Q", y=alt.Y("DRUG_NAME:N", sort="x", title=None), color="STATUS:N"), "stock")
    report("Batches expiring within 90 days", inv["batches"], alt.Chart(inv["batches"]).mark_bar().encode(
        x="DAYS_TO_EXPIRY:Q", y=alt.Y("DRUG_NAME:N", title=None), color="SUPPLIER:N"), "expiry")
    report("Supplier spend", inv["suppliers"], alt.Chart(inv["suppliers"]).mark_bar().encode(
        x="SPEND:Q", y=alt.Y("NAME:N", sort="-x", title=None), color="RATING:Q"), "suppliers")
    report("Dispensing per month", inv["dispensing"], alt.Chart(inv["dispensing"]).mark_line(point=True).encode(
        x=alt.X("MONTH:T", title=None), y="UNITS:Q"), "dispensing")

elif choice == "Finance":
    year = st.segmented_control("Fiscal year", [2024, 2025], default=2025, key="rep_year") or 2025
    f = data.finance_summary(p, year)
    report("Budget utilisation", f["budget"], alt.Chart(f["budget"]).mark_bar().encode(
        x="PCT_USED:Q", y=alt.Y("DEPARTMENT_NAME:N", sort="-x", title=None)), "budget")
    report("Expenditure by category", f["expense_cat"], alt.Chart(f["expense_cat"]).mark_area().encode(
        x=alt.X("MONTH:T", title=None), y="AMOUNT:Q", color="CATEGORY:N"), "expenses")
    report("Equipment purchase vs maintenance cost", f["equipment"], alt.Chart(f["equipment"]).mark_point(size=90).encode(
        x="PURCHASE_COST:Q", y="MAINTENANCE_COST:Q", color="STATUS:N", tooltip=["NAME", "UNITS"]), "equipment")
    report("Billing by payer and status", f["billing"], alt.Chart(f["billing"]).mark_bar().encode(
        x="AMOUNT:Q", y=alt.Y("PAYER:N", title=None), color="BILL_STATUS:N"), "billing")
    if "payroll" in f:
        pr = f["payroll"].groupby(["PAY_PERIOD", "ROLE"], as_index=False)[["GROSS", "OVERTIME"]].sum()
        report("Payroll by role (aggregated)", pr, alt.Chart(pr).mark_bar().encode(
            x="PAY_PERIOD:O", y="GROSS:Q", color="ROLE:N"), "payroll")

elif choice == "Staffing":
    sh = data.shifts(p, start, end, None, None)
    if not sh.empty:
        agg = sh.groupby(["DEPARTMENT_NAME", "SHIFT_TYPE"], as_index=False).size()
        report("Shifts by department and type", agg, alt.Chart(agg).mark_bar().encode(
            x="size:Q", y=alt.Y("DEPARTMENT_NAME:N", title=None), color="SHIFT_TYPE:N"), "shifts")
        stat = sh.groupby("STATUS", as_index=False).size()
        report("Shift status", stat, alt.Chart(stat).mark_arc(innerRadius=50).encode(theta="size:Q", color="STATUS:N"),
               "shift_status")
    stf = data.staffing(p)
    report("Active staff by department and role", stf, alt.Chart(stf).mark_bar().encode(
        x="ACTIVE:Q", y=alt.Y("DEPARTMENT_NAME:N", title=None), color="ROLE:N"), "staffing")
