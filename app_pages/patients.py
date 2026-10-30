import streamlit as st

from mediverse import ai, data
from mediverse.authz import AccessDenied, Perm
from mediverse.ui import AI_LABEL, SYNTHETIC_NOTE, guard, show_denied

p = guard(Perm.PATIENT_READ)
st.title("Patient360", anchor=False)
st.caption(SYNTHETIC_NOTE)
if not p.can(Perm.PATIENT_PII):
    st.caption(":material/visibility_off: Names and contact details are masked for your role.")
if p.department_scope:
    st.caption(f":material/filter_alt: Limited to patients seen in your department ({p.department_scope}).")

specs = data.departments()[["SPECIALTY_NAME"]].drop_duplicates()
with st.form("psearch", border=False):
    with st.container(horizontal=True, vertical_alignment="bottom"):
        term = st.text_input("Search", placeholder="Patient ID" + (" or name" if p.can(Perm.PATIENT_PII) else ""), max_chars=60)
        spec = st.selectbox("Primary specialty", ["Any"] + specs["SPECIALTY_NAME"].tolist())
        submitted = st.form_submit_button("Search", icon=":material/search:")
if submitted:
    sid = None
    if spec != "Any":
        sp = data._q("SELECT SPECIALTY_ID FROM MEDIVERSE360.CURATED.SPECIALTIES WHERE SPECIALTY_NAME = ?", (spec,))
        sid = sp.iloc[0, 0]
    st.session_state.p_results = data.search_patients(p, term, sid)

res = st.session_state.get("p_results")
if res is not None:
    if res.empty:
        st.info("No matching patients.")
    else:
        ev = st.dataframe(res, hide_index=True, on_select="rerun", selection_mode="single-row", key="p_table")
        rows = ev.selection.rows
        if rows:
            st.session_state.p_selected = res.iloc[rows[0]]["PATIENT_ID"]

pid = st.session_state.get("p_selected")
if pid:
    try:
        rec = data.patient_record(p, pid)
    except AccessDenied as e:
        show_denied(e)
        st.stop()
    prof = rec["profile"].iloc[0]
    st.subheader(f"{prof['NAME']} · {pid}", anchor=False)
    with st.container(horizontal=True):
        st.metric("Year/Date of birth", str(prof["DOB"]), border=True)
        st.metric("Sex", prof["SEX"], border=True)
        st.metric("Blood type", prof["BLOOD_TYPE"] or "-", border=True)
        st.metric("Primary specialty", prof["PRIMARY_SPECIALTY"], border=True)
    if not rec["allergies"].empty:
        st.warning("Allergies: " + ", ".join(f"{a} ({s.lower()})" for a, s in rec["allergies"].itertuples(index=False)),
                   icon=":material/warning:")
    if rec.get("death") is not None and not rec["death"].empty:
        st.error(f"Deceased record on file: {rec['death'].iloc[0]['DATE_OF_DEATH']} (restricted)", icon=":material/lock:")

    sections = [s for s in ["encounters", "labs", "imaging", "surgeries", "prescriptions", "discharges"] if s in rec]
    labels = {"encounters": "Encounters & diagnoses", "labs": "Laboratory", "imaging": "Imaging", "surgeries": "Surgery",
              "prescriptions": "Prescriptions", "discharges": "Discharge summaries"}
    view = st.segmented_control("Section", sections, format_func=labels.get, default=sections[0] if sections else None)
    if view and view != "discharges":
        st.dataframe(rec[view], hide_index=True)
        if view == "encounters" and not p.can(Perm.PSYCH_READ):
            st.caption("Sensitive diagnoses are shown as [restricted] for your account.")
    elif view == "discharges":
        ds = rec["discharges"]
        if ds.empty:
            st.info("No discharge summaries available to you.")
        for row in ds.itertuples():
            with st.container(border=True):
                st.markdown(f"**{row.DISCHARGE_ID}** · discharged {row.DISCHARGE_DATE} · {row.DISCHARGE_DISPOSITION}")
                st.text(row.SUMMARY_TEXT)
                if p.can(Perm.AI_USE):
                    c1, c2 = st.columns(2)
                    if c1.button("Summarise (AI)", key=f"s_{row.DISCHARGE_ID}", icon=":material/auto_awesome:"):
                        st.session_state[f"ai_{row.DISCHARGE_ID}"] = ("summary", ai.summarize_discharge(p, row.DISCHARGE_ID))
                    if c2.button("Extract fields (AI)", key=f"e_{row.DISCHARGE_ID}", icon=":material/data_object:"):
                        st.session_state[f"ai_{row.DISCHARGE_ID}"] = ("fields", ai.extract_discharge_fields(p, row.DISCHARGE_ID))
                    got = st.session_state.get(f"ai_{row.DISCHARGE_ID}")
                    if got:
                        st.info(AI_LABEL, icon=":material/smart_toy:")
                        st.json(got[1]["fields"]) if got[0] == "fields" else st.markdown(got[1]["summary"])
                        st.caption(f"Source: CURATED.DISCHARGE_SUMMARIES/{row.DISCHARGE_ID} · output {got[1]['output_id']} (pending review)")

if p.can(Perm.AI_USE) and (p.can(Perm.CLINICAL_READ) or p.can(Perm.IMAGING_READ) or p.can(Perm.LAB_READ)):
    with st.expander("Search approved clinical documents", icon=":material/manage_search:"):
        q = st.text_input("Keywords", placeholder="e.g. effusion follow-up", key="doc_q", max_chars=120)
        if q:
            try:
                docs = ai.search_documents(p, q)
            except AccessDenied as e:
                show_denied(e)
                docs = None
            if docs is not None:
                st.dataframe(docs, hide_index=True)
                if not docs.empty and st.button("Answer from these documents (AI)", icon=":material/auto_awesome:"):
                    out = ai.answer_from_documents(p, q, docs)
                    st.info(AI_LABEL, icon=":material/smart_toy:")
                    st.markdown(out["answer"])
                    st.caption(f"Output {out['output_id']} · cites document IDs above")
