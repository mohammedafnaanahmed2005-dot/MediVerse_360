import altair as alt
import streamlit as st

from mediverse import data
from mediverse.authz import Perm
from mediverse.ui import guard, money

p = guard(Perm.PHARMACY_READ)
st.title("PharmaStock 360", anchor=False)

days = st.segmented_control("Expiry alert window", [30, 60, 90, 180], default=90, format_func=lambda d: f"{d} days")
inv = data.pharmacy_inventory(p, days or 90)
stock, batches = inv["stock"], inv["batches"]
expired = batches[batches["DAYS_TO_EXPIRY"] < 0]
soon = batches[batches["DAYS_TO_EXPIRY"] >= 0]

with st.container(horizontal=True):
    st.metric("Medicines", len(stock), border=True)
    st.metric("Below reorder level", int(stock["LOW_STOCK"].sum()), border=True)
    st.metric("Expired batches with stock", len(expired), border=True)
    st.metric(f"Expiring within {days or 90} days", len(soon), border=True)

if len(expired):
    st.error(f"{len(expired)} expired batches still hold stock. Quarantine and dispose per pharmacy policy.", icon=":material/dangerous:")
low = stock[stock["LOW_STOCK"]]
if len(low):
    st.warning(f"{len(low)} medicines are below their reorder level.", icon=":material/inventory:")

t1, t2, t3 = st.tabs(["Stock levels", "Expiry alerts", "Suppliers & dispensing"])
with t1:
    st.dataframe(stock, hide_index=True, column_config={
        "LOW_STOCK": st.column_config.CheckboxColumn("Low stock"),
        "IS_CONTROLLED": st.column_config.CheckboxColumn("Controlled")})
with t2:
    st.dataframe(batches, hide_index=True, column_config={
        "DAYS_TO_EXPIRY": st.column_config.NumberColumn("Days to expiry", help="Negative = already expired")})
with t3:
    sup = inv["suppliers"]
    c1, c2 = st.columns(2)
    c1.dataframe(sup, hide_index=True, column_config={"SPEND": st.column_config.NumberColumn("Spend", format="$%.0f")})
    with c2.container(border=True):
        st.altair_chart(alt.Chart(inv["dispensing"]).mark_line(point=True).encode(
            x=alt.X("MONTH:T", title=None), y="DISPENSES:Q"))
        st.caption(f"Total supplier spend {money(sup['SPEND'].sum())}")
