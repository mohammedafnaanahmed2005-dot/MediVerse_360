import gzip
import io

import altair as alt
import pandas as pd
import streamlit as st

from mediverse import ai
from mediverse.authz import Perm
from mediverse.db import session
from mediverse.dq import validate
from mediverse.schema_spec import TABLES
from mediverse.services import repo
from mediverse.ui import AI_LABEL, guard

p = guard(Perm.DQ_REVIEW)
st.title("Data quality", anchor=False)


@st.cache_data(ttl=60, show_spinner=False)
def runs() -> pd.DataFrame:
    return session().sql("""SELECT RUN_ID, MAX(RUN_AT) RUN_AT, MAX(RUN_BY) RUN_BY, SUM(RAW_ROWS) RAW_ROWS,
                            SUM(ERROR_ROWS) ERROR_ROWS FROM MEDIVERSE360.STAGING.PIPELINE_RUNS
                            GROUP BY 1 ORDER BY 2 DESC LIMIT 20""").to_pandas()


r = runs()
if r.empty:
    st.info("No pipeline runs yet.")
    st.stop()
run_id = st.selectbox("Pipeline run", r["RUN_ID"], format_func=lambda x: f"{r.set_index('RUN_ID').loc[x, 'RUN_AT']:%Y-%m-%d %H:%M} · {x[:8]}")

tables = session().sql("""SELECT TABLE_NAME, RAW_ROWS, CURATED_ROWS, ERROR_ROWS,
                          ROUND(100 * (RAW_ROWS - ERROR_ROWS) / NULLIF(RAW_ROWS, 0), 2) PCT_VALID
                          FROM MEDIVERSE360.STAGING.PIPELINE_RUNS WHERE RUN_ID = ? ORDER BY ERROR_ROWS DESC""",
                       params=[run_id]).to_pandas()
issues = session().sql("""SELECT ISSUE_ID, TABLE_NAME, RECORD_ID, RULE_CODE, COLUMN_NAME, SEVERITY, DETAILS, STATUS,
                          AI_CATEGORY, REVIEWED_BY, RESOLUTION FROM MEDIVERSE360.STAGING.DQ_ISSUES
                          WHERE RUN_ID = ? ORDER BY SEVERITY, TABLE_NAME, RULE_CODE""", params=[run_id]).to_pandas()

with st.container(horizontal=True):
    st.metric("Rows processed", f"{int(tables['RAW_ROWS'].sum()):,}", border=True)
    st.metric("Rows rejected from CURATED", f"{int(tables['ERROR_ROWS'].sum()):,}", border=True)
    st.metric("Issues", f"{len(issues):,}", border=True)
    st.metric("Open", f"{int((issues['STATUS'] == 'OPEN').sum()):,}", border=True)
    st.metric("AI-categorised", f"{int(issues['AI_CATEGORY'].notna().sum()):,}", border=True)

c1, c2 = st.columns([3, 2])
with c1.container(border=True):
    st.subheader("Issues by rule and table", anchor=False)
    agg = issues.groupby(["TABLE_NAME", "RULE_CODE"], as_index=False).size()
    st.altair_chart(alt.Chart(agg).mark_bar().encode(x="size:Q", y=alt.Y("TABLE_NAME:N", sort="-x", title=None),
                                                     color="RULE_CODE:N"))
with c2.container(border=True):
    st.subheader("Load results per table", anchor=False)
    st.dataframe(tables, hide_index=True, height=320,
                 column_config={"PCT_VALID": st.column_config.ProgressColumn("% valid", min_value=0, max_value=100)})

st.subheader("Review queue", anchor=False)
st.caption("Rows with ERROR issues were held back from CURATED. Review each issue; fix the source and re-load to correct it.")
with st.container(horizontal=True, vertical_alignment="bottom"):
    f_table = st.selectbox("Table", ["All"] + sorted(issues["TABLE_NAME"].unique()))
    f_rule = st.selectbox("Rule", ["All"] + sorted(issues["RULE_CODE"].unique()))
    f_status = st.segmented_control("Status", ["OPEN", "ACCEPTED", "FIXED_AT_SOURCE", "FALSE_POSITIVE"], default="OPEN")
view = issues
if f_table != "All":
    view = view[view["TABLE_NAME"] == f_table]
if f_rule != "All":
    view = view[view["RULE_CODE"] == f_rule]
if f_status:
    view = view[view["STATUS"] == f_status]
ev = st.dataframe(view.head(500), hide_index=True, on_select="rerun", selection_mode="multi-row", key="dq_tbl")
picked = view.head(500).iloc[ev.selection.rows]["ISSUE_ID"].tolist() if ev.selection.rows else []
if picked:
    with st.form("dq_review", border=True):
        st.markdown(f"**{len(picked)}** issue(s) selected")
        new = st.segmented_control("Resolution", ["ACCEPTED", "FIXED_AT_SOURCE", "FALSE_POSITIVE"], default="FIXED_AT_SOURCE")
        note = st.text_input("Note", max_chars=300)
        if st.form_submit_button("Record review", type="primary"):
            session().sql(f"""UPDATE MEDIVERSE360.STAGING.DQ_ISSUES SET STATUS = ?, RESOLUTION = ?, REVIEWED_BY = ?,
                              REVIEWED_AT = SYSDATE() WHERE ISSUE_ID IN ({','.join('?' * len(picked))})""",
                          params=[new, note, p.username] + picked).collect()
            repo().log_access(p, "DQ_ISSUES", "REVIEW", True, run_id, f"{len(picked)} -> {new}")
            st.rerun()

with st.expander("Suggest categories with Cortex AI", icon=":material/auto_awesome:"):
    st.caption(AI_LABEL + " Categories are suggestions; statuses do not change until a person reviews them.")
    n = st.number_input("Issues to categorise", 10, 200, 50, step=10)
    if st.button("Estimate tokens"):
        st.write(f"About **{ai.estimate_dq_tokens(run_id, n):,}** input tokens for AI_CLASSIFY on up to {n} issues.")
    if st.button("Categorise", type="primary"):
        with st.spinner("Classifying..."):
            done = ai.classify_dq_issues(p, run_id, n)
        st.success(f"Suggested categories for {done} issues.")
        st.rerun()

if p.can(Perm.DATA_INGEST):
    st.divider()
    st.subheader("Upload a CSV", anchor=False)
    st.caption("Validated here first, then landed in RAW and re-run through STAGING checks. Only valid rows with new IDs "
               "reach CURATED; existing CURATED rows are never overwritten.")
    table = st.selectbox("Target table", list(TABLES), key="up_table")
    up = st.file_uploader("CSV file", type=["csv"], key="up_file")
    if up and up.size > 50 * 1024 * 1024:
        st.error("Files over 50 MB must be loaded through the stage + RAW.LOAD_RAW_TABLE procedure.")
        up = None
    if up:
        df = pd.read_csv(up, dtype=str, keep_default_na=False)
        found, summ = validate(table, df)
        with st.container(horizontal=True):
            st.metric("Rows", summ["rows"], border=True)
            st.metric("Rows with errors", summ["error_rows"], border=True)
            st.metric("Valid rows", summ["valid_rows"], border=True)
        if summ["extra_columns"]:
            st.warning("Ignored extra columns: " + ", ".join(summ["extra_columns"]))
        if len(found):
            st.dataframe(found, hide_index=True)
            st.download_button("Download error report", found.to_csv(index=False), f"{table}_errors.csv", "text/csv",
                               icon=":material/download:")
        confirm = st.checkbox(f"I confirm loading this file replaces the RAW landing table for **{table}** "
                              "(CURATED keeps existing records).")
        if st.button("Load into pipeline", type="primary", disabled=not confirm or summ["valid_rows"] == 0):
            buf = io.BytesIO(gzip.compress(df.to_csv(index=False).encode()))
            session().file.put_stream(buf, f"@MEDIVERSE360.RAW.CSV_STAGE/{table}.csv.gz", auto_compress=False, overwrite=True)
            with st.spinner("Loading RAW and running pipeline..."):
                msg = session().sql("CALL MEDIVERSE360.RAW.LOAD_RAW_TABLE(?, TRUE)", params=[table]).collect()[0][0]
                new_run = session().sql("CALL MEDIVERSE360.STAGING.RUN_PIPELINE(FALSE)").collect()[0][0]
            repo().log_access(p, "INGEST", "UPLOAD", True, table, f"{msg}; run={new_run}")
            runs.clear()
            st.success(f"{msg}. Pipeline run {new_run[:8]} complete.")
