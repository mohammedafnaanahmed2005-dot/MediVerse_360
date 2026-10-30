"""Capture every SELECT the data layer issues (across roles) into one SQL script.

Running the script in Snowflake proves the queries compile and execute against
the real schema. Only read-only statements are written.

    python scripts/capture_sql.py  ->  build/captured_selects.sql
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CAPTURED: list[tuple[str, list]] = []
# Superset of columns that page/data code post-processes, so pandas steps succeed on fake results.
FAKE = pd.DataFrame({"USABLE_QTY": [0], "REORDER_LEVEL": [1], "SENSITIVE": [False], "PATIENT_ID": ["SYN000001"],
                     "DOC_ID": ["X"], "TEXT": ["t"]})


class FakeResult:
    def __init__(self, sql, params):
        CAPTURED.append((sql, params))

    def to_pandas(self):
        return FAKE.copy()

    def collect(self):
        return [(1,)]


class FakeSession:
    def sql(self, sql, params=None):
        return FakeResult(sql, list(params or []))


class NullRepo:
    def log_access(self, *a, **k):
        pass


def lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def inline(sql: str, params: list) -> str:
    out, i = [], 0
    for ch in sql:
        if ch == "?":
            out.append(lit(params[i]))
            i += 1
        else:
            out.append(ch)
    assert i == len(params), (sql, params)
    return "".join(out)


def main() -> None:
    import mediverse.db as db
    import mediverse.services as services
    db.session = lambda: FakeSession()
    services.repo = lambda: NullRepo()
    from mediverse import ai, data
    data.session = db.session
    ai.session = db.session
    data._q = lambda sql, params=(): FakeSession().sql(sql, params).to_pandas()
    from mediverse.authz import Principal

    s, e = date(2025, 1, 1), date(2025, 12, 31)
    doc = Principal("u", "d", "DOCTOR", ["PSYCH_ACCESS", "MORTALITY_ACCESS"])
    doc_plain = Principal("u", "d2", "DOCTOR")
    nurse = Principal("u", "n", "NURSE", department_id="D01")
    lab = Principal("u", "l", "LAB_STAFF")
    rad = Principal("u", "r", "RADIOLOGY_STAFF")
    mgmt = Principal("u", "m", "MANAGEMENT")
    fin = Principal("u", "f", "FINANCE_STAFF")
    ph = Principal("u", "p", "PHARMACIST")
    adm = Principal("u", "a", "ADMIN")

    data.departments(); data.date_bounds()
    data.executive_kpis(mgmt); data.monthly_activity(mgmt, s, e, None); data.monthly_activity(mgmt, s, e, "D01")
    data.department_workload(mgmt, s, e); data.mortality_stats(doc)
    for who in (doc, doc_plain, nurse, lab):
        data.search_patients(who, "SYN0001", None); data.search_patients(who, "Alex", "SP01")
        data.patient_record(who, "SYN000001")
    data.lab_summary(lab, s, e, None); data.lab_summary(lab, s, e, "D02")
    data.lab_worklist(lab, None, None); data.lab_worklist(lab, "COMPLETED", "STAT"); data.pathology_reports(lab)
    data.imaging_summary(rad, s, e); data.surgery_summary(doc, s, e)
    data.pharmacy_inventory(ph, 90)
    data.finance_summary(fin, 2025)
    data.shifts(mgmt, s, e, None, None); data.shifts(adm, s, e, "D01", "NIGHT"); data.staffing(mgmt)
    for who in (doc, doc_plain, lab, rad):
        ai.search_documents(who, "follow up imaging")
    ai.estimate_dq_tokens("RUN", 5)

    selects = []
    for sql, params in CAPTURED:
        if sql.lstrip().upper().startswith(("SELECT", "WITH")):
            selects.append(inline(sql, params).strip().rstrip(";"))
    out = ROOT / "build" / "captured_selects.sql"
    out.parent.mkdir(exist_ok=True)
    out.write_text("USE WAREHOUSE MEDIVERSE_WH;\n" + "\n".join(f"SELECT COUNT(*) FROM ({q});" for q in selects) + "\n",
                   encoding="utf-8")
    print(f"captured {len(CAPTURED)} statements, wrote {len(selects)} selects to {out}")


if __name__ == "__main__":
    main()
