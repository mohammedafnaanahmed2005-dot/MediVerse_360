"""Snowflake Cortex AI features (AI_* functions only).

Guardrails:
* Every call checks the caller's permission for the *source* data first.
* Outputs are stored in APP.AI_OUTPUTS with source references and start in
  PENDING_REVIEW; the UI labels them as AI-generated.
* Prompts forbid diagnosis, prescribing and treatment recommendations.
* Processing note: this account routes Cortex via cross-region inference (AWS_US).
"""

from __future__ import annotations

import json
import uuid

import pandas as pd

from mediverse.authz import AccessDenied, Perm, Principal
from mediverse.db import session

MODEL = "llama3.3-70b"
C = "MEDIVERSE360.CURATED"
GUARD = ("You are summarising SYNTHETIC demonstration hospital records. Summarise only what the text states. "
         "Do not diagnose, do not recommend or change medication, do not suggest treatment, and do not invent facts. "
         "If information is missing, say it is not stated.")

DQ_CATEGORIES = [
    {"label": "date inconsistency", "description": "dates out of order, impossible or in the future"},
    {"label": "missing value", "description": "a required field is empty"},
    {"label": "duplicate record", "description": "the same identifier appears more than once"},
    {"label": "broken relationship", "description": "references a parent record that does not exist"},
    {"label": "invalid format", "description": "value cannot be parsed, e.g. bad email, code or number"},
    {"label": "implausible value", "description": "value outside a realistic range"},
]


def _complete(prompt: str) -> str:
    r = session().sql(f"SELECT AI_COMPLETE('{MODEL}', ?)", params=[prompt]).collect()
    return (r[0][0] or "").strip()


def _store(p: Principal, task: str, source_table: str, ids: list[str], output) -> str:
    oid = str(uuid.uuid4())
    session().sql(
        """INSERT INTO MEDIVERSE360.APP.AI_OUTPUTS (OUTPUT_ID, REQUESTED_BY, TASK, MODEL, SOURCE_TABLE, SOURCE_IDS, OUTPUT)
           SELECT ?, ?, ?, ?, ?, PARSE_JSON(?), PARSE_JSON(?)""",
        params=[oid, p.user_id, task, MODEL, source_table, json.dumps(ids), json.dumps(output)]).collect()
    return oid


def _discharge_text(p: Principal, discharge_id: str) -> str:
    p.require(Perm.CLINICAL_READ)
    r = session().sql(f"""SELECT ds.SUMMARY_TEXT, BOOLOR_AGG(dx.IS_SENSITIVE)
                         FROM {C}.DISCHARGE_SUMMARIES ds LEFT JOIN {C}.DIAGNOSES dx USING (ENCOUNTER_ID)
                         WHERE ds.DISCHARGE_ID = ? GROUP BY 1""", params=[discharge_id]).collect()
    if not r:
        raise AccessDenied("Document not found.")
    if r[0][1] and not p.can(Perm.PSYCH_READ):
        raise AccessDenied("This document is restricted.")
    return r[0][0]


def summarize_discharge(p: Principal, discharge_id: str) -> dict:
    p.require(Perm.AI_USE)
    text = _discharge_text(p, discharge_id)
    summary = _complete(f"{GUARD}\n\nWrite a 3-bullet plain-language summary of this discharge note:\n\n{text}")
    oid = _store(p, "DISCHARGE_SUMMARY", "CURATED.DISCHARGE_SUMMARIES", [discharge_id], {"summary": summary})
    return {"output_id": oid, "summary": summary, "source": text}


def extract_discharge_fields(p: Principal, discharge_id: str) -> dict:
    p.require(Perm.AI_USE)
    text = _discharge_text(p, discharge_id)
    fmt = {"admission_reason": "What condition was the patient admitted with?",
           "length_of_stay_days": "How many days was the length of stay?",
           "disposition": "Where was the patient discharged to?",
           "follow_up": "When is the follow-up?",
           "hospital_course": "How is the hospital course described?"}
    r = session().sql("SELECT AI_EXTRACT(text => ?, responseFormat => PARSE_JSON(?)):response",
                      params=[text, json.dumps(fmt)]).collect()
    fields = json.loads(r[0][0]) if r and r[0][0] else {}
    oid = _store(p, "DISCHARGE_EXTRACT", "CURATED.DISCHARGE_SUMMARIES", [discharge_id], fields)
    return {"output_id": oid, "fields": fields, "source": text}


def summarize_pathology(p: Principal, pathology_id: str) -> dict:
    p.require(Perm.LAB_READ)
    p.require(Perm.AI_USE)
    r = session().sql(f"SELECT REPORT_TEXT FROM {C}.PATHOLOGY_REPORTS WHERE PATHOLOGY_ID = ?", params=[pathology_id]).collect()
    if not r:
        raise AccessDenied("Document not found.")
    summary = _complete(f"{GUARD}\n\nSummarise this laboratory pathology report in two sentences for a lab reviewer:\n\n{r[0][0]}")
    oid = _store(p, "PATHOLOGY_SUMMARY", "CURATED.PATHOLOGY_REPORTS", [pathology_id], {"summary": summary})
    return {"output_id": oid, "summary": summary, "source": r[0][0]}


def trend_narrative(p: Principal, topic: str, df: pd.DataFrame, perm: Perm) -> dict:
    """Narrate an already-authorized aggregate table (no row-level PHI is sent)."""
    p.require(perm)
    p.require(Perm.AI_USE)
    table = df.head(60).to_csv(index=False)
    text = _complete("You are an operations analyst for a SYNTHETIC demo hospital. Using only the table below, write "
                     f"4 concise bullet points on notable trends for: {topic}. Quote numbers from the table; "
                     f"do not speculate about causes beyond the data.\n\n{table}")
    oid = _store(p, "TREND_NARRATIVE", topic, [], {"narrative": text})
    return {"output_id": oid, "narrative": text}


def classify_dq_issues(p: Principal, run_id: str, limit: int = 50) -> int:
    """Suggest a category for unreviewed issues. Status stays OPEN until a human reviews it."""
    p.require(Perm.DQ_REVIEW)
    r = session().sql(
        f"""UPDATE MEDIVERSE360.STAGING.DQ_ISSUES t SET AI_CATEGORY = s.CAT
           FROM (SELECT ISSUE_ID,
                        AI_CLASSIFY(RULE_CODE || ' on ' || TABLE_NAME || COALESCE('.' || COLUMN_NAME, '') || ': ' || DETAILS,
                                    PARSE_JSON(?), {{'task_description': 'Categorise a data-quality issue found while loading hospital data'}}
                        ):labels[0]::STRING AS CAT
                 FROM MEDIVERSE360.STAGING.DQ_ISSUES
                 WHERE RUN_ID = ? AND AI_CATEGORY IS NULL AND STATUS = 'OPEN' LIMIT {int(limit)}) s
           WHERE t.ISSUE_ID = s.ISSUE_ID""",
        params=[json.dumps(DQ_CATEGORIES), run_id]).collect()
    return int(r[0][0]) if r else 0


def estimate_dq_tokens(run_id: str, limit: int) -> int:
    r = session().sql(
        f"""SELECT SUM(AI_COUNT_TOKENS('ai_classify', RULE_CODE || ' on ' || TABLE_NAME || ': ' || DETAILS, PARSE_JSON(?)))
           FROM (SELECT * FROM MEDIVERSE360.STAGING.DQ_ISSUES WHERE RUN_ID = ? AND AI_CATEGORY IS NULL AND STATUS = 'OPEN' LIMIT {int(limit)})""",
        params=[json.dumps(DQ_CATEGORIES), run_id]).collect()
    return int(r[0][0] or 0)


def search_documents(p: Principal, query: str, limit: int = 15) -> pd.DataFrame:
    """Keyword search across approved synthetic document text the caller may read."""
    terms = [t for t in (query or "").lower().split() if len(t) > 2][:6]
    if not terms:
        return pd.DataFrame()
    parts, params = [], []
    cond = lambda col: " AND ".join([f"{col} ILIKE ?"] * len(terms))
    likes = [f"%{t}%" for t in terms]
    if p.can(Perm.CLINICAL_READ):
        psych = "" if p.can(Perm.PSYCH_READ) else (f" AND NOT EXISTS (SELECT 1 FROM {C}.DIAGNOSES dx WHERE dx.ENCOUNTER_ID = ds.ENCOUNTER_ID AND dx.IS_SENSITIVE)")
        parts.append(f"SELECT 'Discharge summary' DOC_TYPE, ds.DISCHARGE_ID DOC_ID, ds.SUMMARY_TEXT TEXT FROM {C}.DISCHARGE_SUMMARIES ds "
                     f"WHERE {cond('ds.SUMMARY_TEXT')}{psych}")
        params += likes
    if p.can(Perm.LAB_READ):
        parts.append(f"SELECT 'Pathology report', PATHOLOGY_ID, REPORT_TEXT FROM {C}.PATHOLOGY_REPORTS WHERE {cond('REPORT_TEXT')}")
        params += likes
    if p.can(Perm.IMAGING_READ):
        parts.append(f"SELECT 'Radiology report', REPORT_ID, FINDINGS || ' Impression: ' || COALESCE(IMPRESSION, '') "
                     f"FROM {C}.RADIOLOGY_REPORTS WHERE {cond('FINDINGS')}")
        params += likes
    if not parts:
        raise AccessDenied("Your role has no searchable clinical documents.")
    sql = " UNION ALL ".join(parts) + f" LIMIT {int(limit)}"
    return session().sql(sql, params=params).to_pandas()


def answer_from_documents(p: Principal, question: str, docs: pd.DataFrame) -> dict:
    p.require(Perm.AI_USE)
    ctx = "\n".join(f"[{r.DOC_ID}] {r.TEXT}" for r in docs.head(8).itertuples())
    ans = _complete(f"{GUARD}\nAnswer the question using ONLY the excerpts below and cite excerpt IDs in brackets. "
                    f"If the excerpts do not answer it, say so.\n\nQuestion: {question}\n\nExcerpts:\n{ctx}")
    oid = _store(p, "DOCUMENT_QA", "MULTIPLE", docs.head(8)["DOC_ID"].tolist(), {"question": question, "answer": ans})
    return {"output_id": oid, "answer": ans}


def review_output(p: Principal, output_id: str, status: str, note: str) -> None:
    if status not in ("APPROVED", "REJECTED"):
        raise ValueError(status)
    session().sql("""UPDATE MEDIVERSE360.APP.AI_OUTPUTS SET REVIEW_STATUS = ?, REVIEWED_BY = ?, REVIEWED_AT = SYSDATE(),
                     REVIEW_NOTE = ? WHERE OUTPUT_ID = ? AND REVIEW_STATUS = 'PENDING_REVIEW'""",
                  params=[status, p.user_id, note[:500], output_id]).collect()
