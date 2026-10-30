import altair as alt
import streamlit as st

from mediverse import ai, data
from mediverse.authz import Perm
from mediverse.ui import AI_LABEL, guard, money

p = guard(Perm.FINANCE_READ)
st.title("Finance & budgets", anchor=False)

year = st.segmented_control("Fiscal year", [2024, 2025], default=2025)
f = data.finance_summary(p, year or 2025)
b = f["budget"]
spent = b["EXPENSES"].sum() + b["MAINTENANCE"].sum()
with st.container(horizontal=True):
    st.metric("Total budget", money(b["BUDGET_AMOUNT"].sum()), border=True)
    st.metric("Spent (expenses + maintenance)", money(spent), border=True)
    st.metric("Utilisation", f"{100 * spent / b['BUDGET_AMOUNT'].sum():.1f}%", border=True)
    st.metric("Departments over 90%", int((b["PCT_USED"] > 90).sum()), border=True)
    if "payroll" in f:
        st.metric("Payroll (gross + OT)", money(f["payroll"]["GROSS"].sum() + f["payroll"]["OVERTIME"].sum()), border=True)

c1, c2 = st.columns(2)
with c1.container(border=True):
    st.subheader("Budget utilisation by department", anchor=False)
    st.altair_chart(alt.Chart(b).mark_bar().encode(
        x=alt.X("PCT_USED:Q", title="% of budget used"), y=alt.Y("DEPARTMENT_NAME:N", sort="-x", title=None),
        color=alt.condition("datum.PCT_USED > 90", alt.value("#d9534f"), alt.value("#29B5E8"))))
with c2.container(border=True):
    st.subheader("Expenditure by category", anchor=False)
    st.altair_chart(alt.Chart(f["expense_cat"]).mark_bar().encode(
        x=alt.X("MONTH:T", title=None), y="AMOUNT:Q", color="CATEGORY:N"))

t1, t2, t3 = st.tabs(["Equipment", "Billing", "Payroll"])
with t1:
    st.dataframe(f["equipment"], hide_index=True)
with t2:
    st.dataframe(f["billing"], hide_index=True)
with t3:
    if "payroll" in f:
        pr = f["payroll"].groupby(["PAY_PERIOD", "ROLE"], as_index=False)[["GROSS", "OVERTIME"]].sum()
        st.altair_chart(alt.Chart(pr).mark_bar().encode(x="PAY_PERIOD:O", y="GROSS:Q", color="ROLE:N"))
        st.caption("Aggregated by role and department; individual salaries are not shown.")
    else:
        st.caption(":material/lock: Payroll is restricted.")

if p.can(Perm.AI_USE):
    if st.button("Summarise expenditure trends (AI)", icon=":material/auto_awesome:"):
        with st.spinner("Summarising..."):
            st.session_state.fin_ai = ai.trend_narrative(p, f"department budget utilisation {year}", b, Perm.FINANCE_READ)
    if st.session_state.get("fin_ai"):
        st.info(AI_LABEL, icon=":material/smart_toy:")
        st.markdown(st.session_state.fin_ai["narrative"])
