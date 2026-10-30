"""Laboratory Docs: LAB_STAFF uploads lab report files and generates Word/CSV reports.

Every function requires ``Perm.LAB_DOCS`` (granted only to LAB_STAFF). Files go to an
SSE-encrypted internal stage; metadata goes to APP.LAB_DOCUMENTS. Queries are bound.
"""

from __future__ import annotations

import io
import re
import secrets
from datetime import datetime

import pandas as pd

from mediverse.authz import Perm, Principal
from mediverse.db import session

STAGE = "@MEDIVERSE360.APP.LAB_DOCS_STAGE"
T = "MEDIVERSE360.APP.LAB_DOCUMENTS"
C = "MEDIVERSE360.CURATED"
DOC_TYPES = ["Blood test (CBC)", "Metabolic panel", "Lipid panel", "Liver function", "Renal function", "Thyroid panel",
             "Urinalysis", "Microbiology / culture", "Pathology", "Other lab report"]
ALLOWED_EXT = {"pdf", "csv", "docx", "xlsx", "txt", "png", "jpg", "jpeg"}
MAX_BYTES = 10 * 1024 * 1024
PID_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")


def normalize_pid(raw: str | None) -> str:
    """Accept 'SYN000123', 'syn123' or '123' and return the canonical 'SYN000123'."""
    s = (raw or "").strip().upper()
    m = re.fullmatch(r"(?:SYN)?0*(\d{1,6})", s)
    return f"SYN{int(m.group(1)):06d}" if m else s


def patient_ids(p: Principal) -> list[str]:
    p.require(Perm.LAB_DOCS)
    return _df(f"SELECT PATIENT_ID FROM {C}.PATIENTS ORDER BY 1", [])["PATIENT_ID"].tolist()


def _audit(p: Principal, action: str, ref: str | None = None) -> None:
    from mediverse.services import repo
    repo().log_access(p, "LAB_DOCUMENTS", action, True, ref)


def _df(sql: str, params: list) -> pd.DataFrame:
    return session().sql(sql, params=params).to_pandas()


def patient_exists(p: Principal, patient_id: str) -> bool:
    p.require(Perm.LAB_DOCS)
    patient_id = normalize_pid(patient_id)
    if not PID_RE.match(patient_id or ""):
        return False
    return not _df(f"SELECT 1 FROM {C}.PATIENTS WHERE PATIENT_ID = ?", [patient_id]).empty


def upload(p: Principal, *, patient_id: str, doc_type: str, file_name: str, data: bytes,
           lab_order_id: str | None = None, notes: str = "") -> str:
    p.require(Perm.LAB_DOCS)
    patient_id = normalize_pid(patient_id)
    if doc_type not in DOC_TYPES:
        raise ValueError("Unknown document type.")
    if not patient_exists(p, patient_id):
        raise ValueError("Patient ID not found.")
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if ext not in ALLOWED_EXT:
        raise ValueError(f"File type .{ext} not allowed. Allowed: {', '.join(sorted(ALLOWED_EXT))}.")
    if not data or len(data) > MAX_BYTES:
        raise ValueError("File is empty or larger than 10 MB.")
    if lab_order_id and not PID_RE.match(lab_order_id):
        raise ValueError("Invalid lab order ID.")
    doc_id = "LD" + secrets.token_hex(6).upper()
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", file_name)[-120:]
    path = f"{patient_id}/{doc_id}_{safe}"
    session().file.put_stream(io.BytesIO(data), f"{STAGE}/{path}", auto_compress=False, overwrite=False)
    session().sql(f"INSERT INTO {T} (DOC_ID, PATIENT_ID, LAB_ORDER_ID, DOC_TYPE, FILE_NAME, STAGE_PATH, SIZE_BYTES, NOTES, "
                  f"UPLOADED_BY) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  params=[doc_id, patient_id, lab_order_id or None, doc_type, file_name[:200], path, len(data),
                          (notes or "")[:1000], p.username]).collect()
    _audit(p, "UPLOAD", doc_id)
    return doc_id


def list_documents(p: Principal, patient_id: str | None = None) -> pd.DataFrame:
    p.require(Perm.LAB_DOCS)
    patient_id = normalize_pid(patient_id) if patient_id else None
    where, prm = ("WHERE PATIENT_ID = ?", [patient_id]) if patient_id else ("", [])
    return _df(f"SELECT DOC_ID, PATIENT_ID, LAB_ORDER_ID, DOC_TYPE, FILE_NAME, SIZE_BYTES, NOTES, UPLOADED_BY, UPLOADED_AT, "
               f"STAGE_PATH FROM {T} {where} ORDER BY UPLOADED_AT DESC LIMIT 500", prm)


def download(p: Principal, doc_id: str) -> tuple[str, bytes]:
    p.require(Perm.LAB_DOCS)
    r = _df(f"SELECT FILE_NAME, STAGE_PATH FROM {T} WHERE DOC_ID = ?", [doc_id])
    if r.empty:
        raise ValueError("Document not found.")
    _audit(p, "DOWNLOAD", doc_id)
    with session().file.get_stream(f"{STAGE}/{r.iloc[0]['STAGE_PATH']}") as f:
        return r.iloc[0]["FILE_NAME"], f.read()


def patient_lab_results(p: Principal, patient_id: str) -> tuple[dict, pd.DataFrame]:
    """Patient header + all lab results with reference ranges, for report generation."""
    p.require(Perm.LAB_DOCS)
    patient_id = normalize_pid(patient_id)
    hdr = _df(f"SELECT PATIENT_ID, FIRST_NAME || ' ' || LAST_NAME AS NAME, DATE_OF_BIRTH, SEX, BLOOD_TYPE "
              f"FROM {C}.PATIENTS WHERE PATIENT_ID = ?", [patient_id])
    if hdr.empty:
        raise ValueError("Patient ID not found.")
    res = _df(f"""SELECT o.LAB_ORDER_ID, o.ORDERED_TS, o.PRIORITY, c.TEST_CODE, c.TEST_NAME, r.VALUE, c.UNIT,
                         c.REF_LOW, c.REF_HIGH, r.FLAG, r.RESULTED_TS, c.SPECIMEN_TYPE
                  FROM {C}.LAB_ORDERS o JOIN {C}.LAB_RESULTS r USING (LAB_ORDER_ID)
                  JOIN {C}.LAB_TEST_CATALOG c USING (TEST_CODE)
                  WHERE o.PATIENT_ID = ? ORDER BY o.ORDERED_TS DESC, c.TEST_NAME""", [patient_id])
    _audit(p, "REPORT", patient_id)
    return hdr.iloc[0].to_dict(), res


def report_csv(results: pd.DataFrame) -> bytes:
    return results.to_csv(index=False).encode()


def report_docx(header: dict, results: pd.DataFrame, author: str, docs: pd.DataFrame | None = None) -> bytes:
    from docx import Document
    from docx.shared import Pt, RGBColor

    d = Document()
    d.add_heading("MediVerse 360 - Laboratory Report", 0)
    d.add_paragraph("Synthetic demonstration data. Not clinical advice.").runs[0].italic = True
    t = d.add_table(rows=0, cols=2)
    t.style = "Light Grid Accent 1"
    for k, v in [("Patient ID", header["PATIENT_ID"]), ("Name", header["NAME"]), ("Date of birth", header["DATE_OF_BIRTH"]),
                 ("Sex", header["SEX"]), ("Blood type", header["BLOOD_TYPE"]),
                 ("Generated", datetime.now().strftime("%Y-%m-%d %H:%M")), ("Prepared by", author)]:
        row = t.add_row().cells
        row[0].text, row[1].text = k, str(v)

    abn = results[results["FLAG"].fillna("N").ne("N")] if not results.empty else results
    d.add_heading("Summary", 1)
    d.add_paragraph(f"{results['LAB_ORDER_ID'].nunique() if not results.empty else 0} orders, {len(results)} results, "
                    f"{len(abn)} outside the reference range.")

    d.add_heading("Results", 1)
    cols = ["ORDERED_TS", "TEST_NAME", "VALUE", "UNIT", "REF_LOW", "REF_HIGH", "FLAG"]
    tbl = d.add_table(rows=1, cols=len(cols))
    tbl.style = "Light Grid Accent 1"
    for c, name in zip(tbl.rows[0].cells, ["Ordered", "Test", "Value", "Unit", "Ref low", "Ref high", "Flag"]):
        c.text = name
    for _, r in results.head(400).iterrows():
        cells = tbl.add_row().cells
        for c, col in zip(cells, cols):
            v = r[col]
            c.text = "" if pd.isna(v) else (f"{v:%Y-%m-%d}" if col == "ORDERED_TS" else str(v))
        if str(r["FLAG"] or "N") != "N":
            for run in cells[2].paragraphs[0].runs + cells[6].paragraphs[0].runs:
                run.font.bold = True
                run.font.color.rgb = RGBColor(0xB0, 0x00, 0x20)
    if len(results) > 400:
        d.add_paragraph(f"Showing 400 of {len(results)} results; see the CSV export for the full list.")

    if docs is not None and not docs.empty:
        d.add_heading("Uploaded lab documents", 1)
        for _, r in docs.iterrows():
            d.add_paragraph(f"{r['DOC_TYPE']}: {r['FILE_NAME']} ({r['UPLOADED_AT']:%Y-%m-%d}, {r['UPLOADED_BY']})",
                            style="List Bullet")
    for s in d.styles:
        if getattr(s, "font", None) is not None and s.name == "Normal":
            s.font.size = Pt(10)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()
