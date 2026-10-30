import pandas as pd
import streamlit as st

from mediverse import lab_docs as ld
from mediverse.authz import Perm
from mediverse.ui import guard

p = guard(Perm.LAB_DOCS)
st.title("Laboratory Docs", anchor=False)
st.caption("Laboratory staff only. Upload blood tests and other lab reports, and generate Word / CSV reports per patient.")

tab_up, tab_list, tab_rep = st.tabs(["Upload report", "Documents", "Generate report"])


@st.cache_data(ttl=3600, show_spinner=False)
def _ids(_role: str) -> list[str]:
    return ld.patient_ids(p)


IDS = _ids(p.role.value)

with tab_up:
    with st.form("lab_upload", clear_on_submit=True, border=True):
        c1, c2 = st.columns(2)
        pid = c1.selectbox("Patient ID", IDS, index=None, placeholder="Type to search, e.g. SYN000123")
        order = c2.text_input("Lab order ID (optional)").strip()
        dtype = c1.selectbox("Document type", ld.DOC_TYPES)
        notes = c2.text_input("Notes (optional)", max_chars=1000)
        files = st.file_uploader("Report files", type=sorted(ld.ALLOWED_EXT), accept_multiple_files=True,
                                 help="PDF, CSV, DOCX, XLSX, TXT or images, up to 10 MB each.")
        if st.form_submit_button("Upload", type="primary", icon=":material/upload:"):
            if not pid:
                st.warning("Choose a patient.")
            elif not files:
                st.warning("Choose at least one file.")
            for f in (files or []) if pid else []:
                try:
                    doc = ld.upload(p, patient_id=pid, doc_type=dtype, file_name=f.name, data=f.getvalue(),
                                    lab_order_id=order or None, notes=notes)
                    st.success(f"{f.name} uploaded as {doc}.", icon=":material/check_circle:")
                except ValueError as e:
                    st.error(f"{f.name}: {e}", icon=":material/error:")

with tab_list:
    flt = st.text_input("Filter by patient ID", key="ld_filter", placeholder="e.g. SYN000123 or 123").strip()
    docs = ld.list_documents(p, flt or None)
    if docs.empty:
        st.info("No documents uploaded yet.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Documents", len(docs))
        c2.metric("Patients", docs["PATIENT_ID"].nunique())
        c3.metric("Total size", f"{docs['SIZE_BYTES'].sum() / 1024:,.0f} KB")
        st.bar_chart(docs.groupby("DOC_TYPE").size().rename("Documents"), horizontal=True)
        st.dataframe(docs.drop(columns=["STAGE_PATH"]), hide_index=True)
        st.download_button("Download list (CSV)", docs.drop(columns=["STAGE_PATH"]).to_csv(index=False).encode(),
                           "lab_documents.csv", "text/csv", icon=":material/download:")
        sel = st.selectbox("Open document", docs["DOC_ID"].tolist(),
                           format_func=lambda d: f"{d} · {docs.set_index('DOC_ID').loc[d, 'FILE_NAME']}")
        if st.button("Prepare download", icon=":material/description:"):
            try:
                name, data = ld.download(p, sel)
                st.download_button(f"Download {name}", data, name, icon=":material/download:")
            except ValueError as e:
                st.error(str(e))

with tab_rep:
    rpid = st.selectbox("Patient for report", IDS, index=None, key="ld_rep_pid",
                        placeholder="Type to search, e.g. SYN000123")
    if rpid:
        try:
            hdr, res = ld.patient_lab_results(p, rpid)
        except ValueError as e:
            st.error(str(e))
        else:
            st.subheader(f"{hdr['NAME']} · {hdr['SEX']} · blood type {hdr['BLOOD_TYPE']}", anchor=False)
            if res.empty:
                st.info("No lab results on file for this patient.")
            else:
                abn = res[res["FLAG"].fillna("N").ne("N")]
                c1, c2, c3 = st.columns(3)
                c1.metric("Orders", res["LAB_ORDER_ID"].nunique())
                c2.metric("Results", len(res))
                c3.metric("Abnormal", len(abn))
                st.bar_chart(res.assign(FLAG=res["FLAG"].fillna("N")).groupby(["TEST_NAME", "FLAG"]).size()
                             .unstack(fill_value=0), horizontal=True)
                st.dataframe(res, hide_index=True)
            pdocs = ld.list_documents(p, rpid)
            c1, c2 = st.columns(2)
            c1.download_button("Word report (.docx)", ld.report_docx(hdr, res, p.display_name, pdocs),
                               f"lab_report_{rpid}.docx",
                               "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                               type="primary", icon=":material/article:", width="stretch")
            c2.download_button("CSV report", ld.report_csv(res), f"lab_report_{rpid}.csv", "text/csv",
                               icon=":material/table:", width="stretch")
