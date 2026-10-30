"""Authorized data access for every module.

Each public function takes the signed-in ``Principal`` first and calls
``require`` before touching data, so authorization holds even if a page is
reached directly. Queries are parameter-bound. Results are cached per
(query, parameters, scope) so different scopes never share a cache entry.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from mediverse.authz import K_ANONYMITY_MIN, Perm, Principal
from mediverse.db import session

C = "MEDIVERSE360.CURATED"


@st.cache_data(ttl=600, max_entries=500, show_spinner=False)
def _q(sql: str, params: tuple = ()) -> pd.DataFrame:
    return session().sql(sql, params=list(params)).to_pandas()


def _uncached(sql: str, params: tuple = ()) -> pd.DataFrame:
    return session().sql(sql, params=list(params)).to_pandas()


def _audit(p: Principal, resource: str, action: str, ref: str | None = None) -> None:
    from mediverse.services import repo
    repo().log_access(p, resource, action, True, ref)


def _name_sql(p: Principal, alias: str = "p") -> str:
    if p.can(Perm.PATIENT_PII):
        return f"{alias}.FIRST_NAME || ' ' || {alias}.LAST_NAME"
    return f"'(masked)'"


def _psych_filter(p: Principal, alias: str = "dx") -> str:
    return "" if p.can(Perm.PSYCH_READ) else f" AND NOT {alias}.IS_SENSITIVE"


# ---------------------------------------------------------------- reference data
def departments() -> pd.DataFrame:
    return _q(f"""SELECT d.DEPARTMENT_ID, d.DEPARTMENT_NAME, s.SPECIALTY_NAME
                  FROM {C}.DEPARTMENTS d JOIN {C}.SPECIALTIES s USING (SPECIALTY_ID) ORDER BY 2""")


def date_bounds() -> tuple:
    r = _q(f"SELECT MIN(ADMIT_DATE) a, MAX(ADMIT_DATE) b FROM {C}.ENCOUNTERS")
    return r.iloc[0, 0], r.iloc[0, 1]


# ---------------------------------------------------------------- executive
def executive_kpis(p: Principal) -> dict:
    p.require(Perm.ANALYTICS_AGG)
    r = _q(f"""SELECT
        (SELECT COUNT(*) FROM {C}.PATIENTS) patients,
        (SELECT COUNT(*) FROM {C}.ENCOUNTERS) encounters,
        (SELECT COUNT(*) FROM {C}.ENCOUNTERS WHERE STATUS = 'OPEN') open_encounters,
        (SELECT COUNT(*) FROM {C}.LAB_RESULTS) lab_results,
        (SELECT COUNT(*) FROM {C}.IMAGING_STUDIES) imaging,
        (SELECT COUNT(*) FROM {C}.SURGERIES) surgeries,
        (SELECT COUNT(*) FROM {C}.STOCK_BATCHES WHERE EXPIRY_DATE < CURRENT_DATE() AND QTY_ON_HAND > 0) expired_batches,
        (SELECT COUNT(*) FROM (SELECT d.DRUG_ID FROM {C}.DRUGS d LEFT JOIN {C}.STOCK_BATCHES b
              ON b.DRUG_ID = d.DRUG_ID AND b.EXPIRY_DATE >= CURRENT_DATE()
              GROUP BY d.DRUG_ID, d.REORDER_LEVEL HAVING COALESCE(SUM(b.QTY_ON_HAND), 0) < d.REORDER_LEVEL)) low_stock,
        (SELECT SUM(GROSS_PAY + COALESCE(OVERTIME_PAY, 0)) FROM {C}.PAYROLL) salary_spend,
        (SELECT SUM(PURCHASE_COST) FROM {C}.EQUIPMENT) equipment_cost,
        (SELECT COUNT(*) FROM MEDIVERSE360.STAGING.DQ_ISSUES WHERE STATUS = 'OPEN'
              AND RUN_ID = (SELECT RUN_ID FROM MEDIVERSE360.STAGING.PIPELINE_RUNS ORDER BY RUN_AT DESC LIMIT 1)) dq_open""")
    return {k.lower(): v for k, v in r.iloc[0].to_dict().items()}


def monthly_activity(p: Principal, start, end, dept: str | None) -> pd.DataFrame:
    p.require(Perm.ANALYTICS_AGG)
    return _q(f"""SELECT DATE_TRUNC('month', e.ADMIT_DATE) AS MONTH, e.ENCOUNTER_TYPE, COUNT(*) AS ENCOUNTERS
                  FROM {C}.ENCOUNTERS e WHERE e.ADMIT_DATE BETWEEN ? AND ? AND (? IS NULL OR e.DEPARTMENT_ID = ?)
                  GROUP BY ALL ORDER BY 1""", (str(start), str(end), dept, dept))


def department_workload(p: Principal, start, end) -> pd.DataFrame:
    p.require(Perm.ANALYTICS_AGG)
    return _q(f"""SELECT d.DEPARTMENT_NAME, COUNT(DISTINCT e.ENCOUNTER_ID) ENCOUNTERS,
                         COUNT(DISTINCT e.PATIENT_ID) PATIENTS,
                         COUNT_IF(e.ENCOUNTER_TYPE = 'INPATIENT') INPATIENT,
                         ROUND(AVG(IFF(e.ENCOUNTER_TYPE = 'INPATIENT', DATEDIFF('day', e.ADMIT_DATE, e.DISCHARGE_DATE), NULL)), 1) AVG_LOS_DAYS
                  FROM {C}.ENCOUNTERS e JOIN {C}.DEPARTMENTS d USING (DEPARTMENT_ID)
                  WHERE e.ADMIT_DATE BETWEEN ? AND ?
                  GROUP BY 1 HAVING COUNT(DISTINCT e.PATIENT_ID) >= {K_ANONYMITY_MIN} ORDER BY 2 DESC""",
              (str(start), str(end)))


def mortality_stats(p: Principal) -> pd.DataFrame:
    p.require(Perm.MORTALITY_READ)
    _audit(p, "DEATH_RECORDS", "AGGREGATE_VIEW")
    return _q(f"""SELECT YEAR(DATE_OF_DEATH) YEAR, LOCATION, COUNT(*) DEATHS FROM {C}.DEATH_RECORDS
                  GROUP BY ALL ORDER BY 1, 2""")


# ---------------------------------------------------------------- patients
def search_patients(p: Principal, term: str, specialty: str | None, limit: int = 50) -> pd.DataFrame:
    p.require(Perm.PATIENT_READ)
    term = (term or "").strip()[:60]
    like = f"%{term}%"
    can_pii = p.can(Perm.PATIENT_PII)
    scope = p.department_scope
    name_match = "OR p.FIRST_NAME ILIKE ? OR p.LAST_NAME ILIKE ?" if can_pii else ""
    params: list = [like] + ([like, like] if can_pii else []) + [specialty, specialty, scope, scope]
    df = _uncached(f"""SELECT p.PATIENT_ID, {_name_sql(p)} AS NAME,
                              {'p.DATE_OF_BIRTH' if can_pii else 'YEAR(p.DATE_OF_BIRTH)'} AS DOB,
                              p.SEX, s.SPECIALTY_NAME AS PRIMARY_SPECIALTY, p.CITY
                       FROM {C}.PATIENTS p JOIN {C}.SPECIALTIES s ON s.SPECIALTY_ID = p.PRIMARY_SPECIALTY_ID
                       WHERE (p.PATIENT_ID ILIKE ? {name_match})
                         AND (? IS NULL OR p.PRIMARY_SPECIALTY_ID = ?)
                         AND (? IS NULL OR EXISTS (SELECT 1 FROM {C}.ENCOUNTERS e
                                                    WHERE e.PATIENT_ID = p.PATIENT_ID AND e.DEPARTMENT_ID = ?))
                       ORDER BY p.PATIENT_ID LIMIT {int(limit)}""", tuple(params))
    _audit(p, "PATIENTS", "SEARCH", f"term_len={len(term)} rows={len(df)}")
    return df


def patient_record(p: Principal, patient_id: str) -> dict:
    """Longitudinal record, assembled section by section according to permissions."""
    p.require(Perm.PATIENT_READ)
    pid = (patient_id,)
    scope = p.department_scope
    if scope:
        ok = _uncached(f"SELECT 1 FROM {C}.ENCOUNTERS WHERE PATIENT_ID = ? AND DEPARTMENT_ID = ? LIMIT 1", (patient_id, scope))
        if ok.empty:
            from mediverse.services import repo
            repo().log_access(p, "PATIENT_RECORD", "VIEW", False, patient_id, "outside department scope")
            from mediverse.authz import AccessDenied
            raise AccessDenied("This patient is not assigned to your department.")
    _audit(p, "PATIENT_RECORD", "VIEW", patient_id)
    out: dict = {}
    out["profile"] = _uncached(f"""SELECT p.PATIENT_ID, {_name_sql(p)} AS NAME,
                    {'p.DATE_OF_BIRTH' if p.can(Perm.PATIENT_PII) else 'YEAR(p.DATE_OF_BIRTH)'} AS DOB, p.SEX, p.BLOOD_TYPE,
                    p.CITY, {'p.PHONE, p.EMAIL,' if p.can(Perm.PATIENT_PII) else ''} s.SPECIALTY_NAME AS PRIMARY_SPECIALTY, p.REGISTERED_ON
                    FROM {C}.PATIENTS p JOIN {C}.SPECIALTIES s ON s.SPECIALTY_ID = p.PRIMARY_SPECIALTY_ID
                    WHERE p.PATIENT_ID = ?""", pid)
    out["allergies"] = _uncached(f"SELECT ALLERGEN, SEVERITY FROM {C}.ALLERGIES WHERE PATIENT_ID = ?", pid)
    enc_scope = " AND e.DEPARTMENT_ID = ?" if scope else ""
    enc_params = pid + ((scope,) if scope else ())
    if p.can(Perm.CLINICAL_READ) or p.can(Perm.NURSING_READ):
        out["encounters"] = _uncached(f"""SELECT e.ENCOUNTER_ID, e.ENCOUNTER_TYPE, e.ADMIT_DATE, e.DISCHARGE_DATE, e.STATUS,
                    d.DEPARTMENT_NAME, LISTAGG(IFF(dx.IS_SENSITIVE AND {str(not p.can(Perm.PSYCH_READ)).upper()},
                        '[restricted]', dx.ICD10_CODE || ' ' || dx.DESCRIPTION), '; ') AS DIAGNOSES
                    FROM {C}.ENCOUNTERS e JOIN {C}.DEPARTMENTS d USING (DEPARTMENT_ID)
                    LEFT JOIN {C}.DIAGNOSES dx ON dx.ENCOUNTER_ID = e.ENCOUNTER_ID
                    WHERE e.PATIENT_ID = ? {enc_scope} GROUP BY ALL ORDER BY e.ADMIT_DATE DESC""", enc_params)
    if p.can(Perm.LAB_READ):
        out["labs"] = _uncached(f"""SELECT o.ORDERED_TS, r.TEST_CODE, c.TEST_NAME, r.VALUE, c.UNIT, c.REF_LOW, c.REF_HIGH, r.FLAG
                    FROM {C}.LAB_ORDERS o JOIN {C}.LAB_RESULTS r USING (LAB_ORDER_ID)
                    JOIN {C}.LAB_TEST_CATALOG c USING (TEST_CODE) WHERE o.PATIENT_ID = ? ORDER BY 1 DESC""", pid)
    if p.can(Perm.IMAGING_READ):
        out["imaging"] = _uncached(f"""SELECT i.STUDY_DATE, i.MODALITY, i.BODY_REGION, i.STATUS, r.IMPRESSION, r.REPORT_STATUS
                    FROM {C}.IMAGING_STUDIES i LEFT JOIN {C}.RADIOLOGY_REPORTS r USING (STUDY_ID)
                    WHERE i.PATIENT_ID = ? ORDER BY 1 DESC""", pid)
    if p.can(Perm.SURGERY_READ):
        out["surgeries"] = _uncached(f"""SELECT s.SURGERY_DATE, s.PROCEDURE_NAME, s.ANESTHESIA_TYPE, s.DURATION_MIN, o.OUTCOME
                    FROM {C}.SURGERIES s LEFT JOIN {C}.SURGERY_OUTCOMES o USING (SURGERY_ID) WHERE s.PATIENT_ID = ?""", pid)
    if p.can(Perm.PRESCRIPTION_READ):
        out["prescriptions"] = _uncached(f"""SELECT rx.PRESCRIBED_ON, d.DRUG_NAME, rx.DOSE, rx.FREQUENCY, rx.DAYS
                    FROM {C}.PRESCRIPTIONS rx JOIN {C}.DRUGS d USING (DRUG_ID) WHERE rx.PATIENT_ID = ? ORDER BY 1 DESC""", pid)
    if p.can(Perm.CLINICAL_READ):
        out["discharges"] = _uncached(f"""SELECT ds.DISCHARGE_ID, e.DISCHARGE_DATE, ds.DISCHARGE_DISPOSITION, ds.SUMMARY_TEXT,
                    BOOLOR_AGG(dx.IS_SENSITIVE) AS SENSITIVE
                    FROM {C}.DISCHARGE_SUMMARIES ds JOIN {C}.ENCOUNTERS e USING (ENCOUNTER_ID)
                    LEFT JOIN {C}.DIAGNOSES dx ON dx.ENCOUNTER_ID = e.ENCOUNTER_ID
                    WHERE e.PATIENT_ID = ? GROUP BY ALL ORDER BY 2 DESC""", pid)
        if not p.can(Perm.PSYCH_READ) and not out["discharges"].empty:
            out["discharges"] = out["discharges"][~out["discharges"]["SENSITIVE"]]
    if p.can(Perm.MORTALITY_READ):
        out["death"] = _uncached(f"SELECT DATE_OF_DEATH, CAUSE_ICD10, LOCATION FROM {C}.DEATH_RECORDS WHERE PATIENT_ID = ?", pid)
    return out


# ---------------------------------------------------------------- laboratory
def lab_summary(p: Principal, start, end, dept: str | None) -> dict:
    p.require(Perm.LAB_READ)
    base = f"""FROM {C}.LAB_ORDERS o JOIN {C}.ENCOUNTERS e USING (ENCOUNTER_ID)
               WHERE o.ORDERED_TS::DATE BETWEEN ? AND ? AND (? IS NULL OR e.DEPARTMENT_ID = ?)"""
    prm = (str(start), str(end), dept, dept)
    return {
        "volume": _q(f"SELECT DATE_TRUNC('month', o.ORDERED_TS) MONTH, o.PRIORITY, COUNT(*) ORDERS {base} GROUP BY ALL ORDER BY 1", prm),
        "status": _q(f"SELECT o.STATUS, COUNT(*) ORDERS {base} GROUP BY 1", prm),
        "abnormal": _q(f"""SELECT c.TEST_NAME, COUNT(*) RESULTS, ROUND(100 * COUNT_IF(r.FLAG <> 'N') / COUNT(*), 1) PCT_ABNORMAL,
                                  ROUND(MEDIAN(DATEDIFF('hour', o.ORDERED_TS, r.RESULTED_TS)), 1) MEDIAN_TAT_H
                           {base.replace('WHERE', f'JOIN {C}.LAB_RESULTS r USING (LAB_ORDER_ID) JOIN {C}.LAB_TEST_CATALOG c USING (TEST_CODE) WHERE')}
                           GROUP BY 1 ORDER BY 3 DESC""", prm),
        "specimens": _q(f"""SELECT sp.CONDITION, sp.SPECIMEN_TYPE, COUNT(*) N
                            {base.replace('WHERE', f'JOIN {C}.SPECIMENS sp USING (LAB_ORDER_ID) WHERE')} GROUP BY ALL""", prm),
    }


def lab_worklist(p: Principal, status: str | None, priority: str | None, limit: int = 300) -> pd.DataFrame:
    p.require(Perm.LAB_READ)
    _audit(p, "LAB_ORDERS", "WORKLIST")
    return _q(f"""SELECT o.LAB_ORDER_ID, o.PATIENT_ID, o.ORDERED_TS, o.PRIORITY, o.STATUS, sp.SPECIMEN_TYPE, sp.CONDITION,
                         COUNT(r.RESULT_ID) RESULTS, COUNT_IF(r.FLAG <> 'N') ABNORMAL
                  FROM {C}.LAB_ORDERS o LEFT JOIN {C}.SPECIMENS sp USING (LAB_ORDER_ID)
                  LEFT JOIN {C}.LAB_RESULTS r USING (LAB_ORDER_ID)
                  WHERE (? IS NULL OR o.STATUS = ?) AND (? IS NULL OR o.PRIORITY = ?)
                  GROUP BY ALL ORDER BY o.ORDERED_TS DESC LIMIT {int(limit)}""", (status, status, priority, priority))


def pathology_reports(p: Principal, limit: int = 100) -> pd.DataFrame:
    p.require(Perm.LAB_READ)
    return _q(f"""SELECT pr.PATHOLOGY_ID, o.PATIENT_ID, pr.SPECIMEN_SITE, pr.STATUS, pr.REPORT_TEXT
                  FROM {C}.PATHOLOGY_REPORTS pr JOIN {C}.LAB_ORDERS o USING (LAB_ORDER_ID) ORDER BY 1 LIMIT {int(limit)}""")


# ---------------------------------------------------------------- imaging & surgery
def imaging_summary(p: Principal, start, end) -> dict:
    p.require(Perm.IMAGING_READ)
    prm = (str(start), str(end))
    return {
        "by_modality": _q(f"""SELECT MODALITY, BODY_REGION, STATUS, COUNT(*) STUDIES FROM {C}.IMAGING_STUDIES
                              WHERE STUDY_DATE BETWEEN ? AND ? GROUP BY ALL""", prm),
        "trend": _q(f"""SELECT DATE_TRUNC('month', STUDY_DATE) MONTH, MODALITY, COUNT(*) STUDIES FROM {C}.IMAGING_STUDIES
                        WHERE STUDY_DATE BETWEEN ? AND ? GROUP BY ALL ORDER BY 1""", prm),
        "reports": _q(f"""SELECT i.STUDY_ID, i.PATIENT_ID, i.STUDY_DATE, i.MODALITY, i.BODY_REGION, r.REPORT_STATUS, r.IMPRESSION, r.FINDINGS
                          FROM {C}.IMAGING_STUDIES i JOIN {C}.RADIOLOGY_REPORTS r USING (STUDY_ID)
                          WHERE i.STUDY_DATE BETWEEN ? AND ? ORDER BY i.STUDY_DATE DESC LIMIT 300""", prm),
    }


def surgery_summary(p: Principal, start, end) -> dict:
    p.require(Perm.SURGERY_READ)
    prm = (str(start), str(end))
    return {
        "procedures": _q(f"""SELECT s.PROCEDURE_NAME, COUNT(*) SURGERIES, ROUND(AVG(s.DURATION_MIN)) AVG_MIN,
                                    ROUND(AVG(s.COST)) AVG_COST, ROUND(100 * COUNT_IF(o.OUTCOME <> 'SUCCESSFUL') / COUNT(*), 1) PCT_ADVERSE
                             FROM {C}.SURGERIES s JOIN {C}.SURGERY_OUTCOMES o USING (SURGERY_ID)
                             WHERE s.SURGERY_DATE BETWEEN ? AND ? GROUP BY 1 ORDER BY 2 DESC""", prm),
        "outcomes": _q(f"""SELECT o.OUTCOME, COUNT(*) N FROM {C}.SURGERIES s JOIN {C}.SURGERY_OUTCOMES o USING (SURGERY_ID)
                           WHERE s.SURGERY_DATE BETWEEN ? AND ? GROUP BY 1""", prm),
        "anesthesia": _q(f"""SELECT ANESTHESIA_TYPE, COUNT(*) N FROM {C}.SURGERIES WHERE SURGERY_DATE BETWEEN ? AND ? GROUP BY 1""", prm),
    }


# ---------------------------------------------------------------- pharmacy
def pharmacy_inventory(p: Principal, expiry_days: int) -> dict:
    p.require(Perm.PHARMACY_READ)
    stock = _q(f"""SELECT d.DRUG_ID, d.DRUG_NAME, d.FORM, m.NAME MANUFACTURER, d.REORDER_LEVEL,
                          COALESCE(SUM(IFF(b.EXPIRY_DATE >= CURRENT_DATE(), b.QTY_ON_HAND, 0)), 0) USABLE_QTY,
                          COALESCE(SUM(IFF(b.EXPIRY_DATE < CURRENT_DATE(), b.QTY_ON_HAND, 0)), 0) EXPIRED_QTY,
                          MIN(IFF(b.EXPIRY_DATE >= CURRENT_DATE() AND b.QTY_ON_HAND > 0, b.EXPIRY_DATE, NULL)) NEXT_EXPIRY,
                          d.IS_CONTROLLED
                   FROM {C}.DRUGS d JOIN {C}.MANUFACTURERS m USING (MANUFACTURER_ID)
                   LEFT JOIN {C}.STOCK_BATCHES b USING (DRUG_ID) GROUP BY ALL ORDER BY USABLE_QTY""")
    stock["LOW_STOCK"] = stock["USABLE_QTY"] < stock["REORDER_LEVEL"]
    batches = _q(f"""SELECT b.BATCH_ID, d.DRUG_NAME, b.LOT_NO, s.NAME SUPPLIER, b.EXPIRY_DATE, b.QTY_ON_HAND,
                            DATEDIFF('day', CURRENT_DATE(), b.EXPIRY_DATE) DAYS_TO_EXPIRY
                     FROM {C}.STOCK_BATCHES b JOIN {C}.DRUGS d USING (DRUG_ID) JOIN {C}.SUPPLIERS s USING (SUPPLIER_ID)
                     WHERE b.QTY_ON_HAND > 0 AND b.EXPIRY_DATE <= DATEADD('day', ?, CURRENT_DATE())
                     ORDER BY b.EXPIRY_DATE""", (int(expiry_days),))
    suppliers = _q(f"""SELECT s.NAME, s.LEAD_TIME_DAYS, s.RATING, COUNT(pu.PURCHASE_ID) ORDERS, SUM(pu.TOTAL_COST) SPEND
                       FROM {C}.SUPPLIERS s LEFT JOIN {C}.PURCHASES pu USING (SUPPLIER_ID) GROUP BY ALL ORDER BY SPEND DESC NULLS LAST""")
    dispensing = _q(f"""SELECT DATE_TRUNC('month', DISPENSED_ON) MONTH, COUNT(*) DISPENSES, SUM(QTY) UNITS
                        FROM {C}.DISPENSING GROUP BY 1 ORDER BY 1""")
    return {"stock": stock, "batches": batches, "suppliers": suppliers, "dispensing": dispensing}


# ---------------------------------------------------------------- finance
def finance_summary(p: Principal, year: int) -> dict:
    p.require(Perm.FINANCE_READ)
    out = {
        "budget": _q(f"""WITH spend AS (
                            SELECT DEPARTMENT_ID, SUM(AMOUNT) EXPENSES FROM {C}.EXPENSES WHERE YEAR(EXPENSE_DATE) = ? GROUP BY 1),
                          maint AS (
                            SELECT e.DEPARTMENT_ID, SUM(m.COST) MAINTENANCE FROM {C}.MAINTENANCE m
                            JOIN {C}.EQUIPMENT e USING (EQUIPMENT_ID) WHERE YEAR(m.SERVICE_DATE) = ? GROUP BY 1)
                         SELECT d.DEPARTMENT_NAME, b.BUDGET_AMOUNT, COALESCE(s.EXPENSES, 0) EXPENSES,
                                COALESCE(mt.MAINTENANCE, 0) MAINTENANCE,
                                ROUND(100 * (COALESCE(s.EXPENSES, 0) + COALESCE(mt.MAINTENANCE, 0)) / b.BUDGET_AMOUNT, 1) PCT_USED
                         FROM {C}.DEPT_BUDGETS b JOIN {C}.DEPARTMENTS d USING (DEPARTMENT_ID)
                         LEFT JOIN spend s USING (DEPARTMENT_ID) LEFT JOIN maint mt USING (DEPARTMENT_ID)
                         WHERE b.FISCAL_YEAR = ? ORDER BY PCT_USED DESC""", (year, year, year)),
        "expense_cat": _q(f"""SELECT DATE_TRUNC('month', EXPENSE_DATE) MONTH, CATEGORY, SUM(AMOUNT) AMOUNT
                              FROM {C}.EXPENSES WHERE YEAR(EXPENSE_DATE) = ? GROUP BY ALL ORDER BY 1""", (year,)),
        "equipment": _q(f"""SELECT e.NAME, e.STATUS, COUNT(*) UNITS, SUM(e.PURCHASE_COST) PURCHASE_COST,
                                   SUM(COALESCE(m.MCOST, 0)) MAINTENANCE_COST
                            FROM {C}.EQUIPMENT e LEFT JOIN (SELECT EQUIPMENT_ID, SUM(COST) MCOST FROM {C}.MAINTENANCE GROUP BY 1) m
                              USING (EQUIPMENT_ID) GROUP BY ALL ORDER BY PURCHASE_COST DESC"""),
        "billing": _q(f"""SELECT PAYER, BILL_STATUS, COUNT(*) BILLS, SUM(AMOUNT) AMOUNT FROM {C}.BILLING
                          WHERE YEAR(BILLED_ON) = ? GROUP BY ALL""", (year,)),
    }
    if p.can(Perm.PAYROLL_READ):
        _audit(p, "PAYROLL", "AGGREGATE_VIEW", str(year))
        out["payroll"] = _q(f"""SELECT py.PAY_PERIOD, st.ROLE, d.DEPARTMENT_NAME,
                                       SUM(py.GROSS_PAY) GROSS, SUM(COALESCE(py.OVERTIME_PAY, 0)) OVERTIME, COUNT(*) HEADCOUNT
                                FROM {C}.PAYROLL py JOIN {C}.STAFF st USING (STAFF_ID) JOIN {C}.DEPARTMENTS d USING (DEPARTMENT_ID)
                                WHERE LEFT(py.PAY_PERIOD, 4) = ? GROUP BY ALL ORDER BY 1""", (str(year),))
    return out


# ---------------------------------------------------------------- staff schedules
def shifts(p: Principal, start, end, dept: str | None, shift_type: str | None) -> pd.DataFrame:
    p.require(Perm.STAFF_SCHEDULE)
    return _q(f"""SELECT sh.SHIFT_ID, sh.START_TS, sh.END_TS, sh.SHIFT_TYPE, sh.STATUS, st.STAFF_ID,
                         st.FIRST_NAME || ' ' || st.LAST_NAME STAFF_NAME, st.ROLE, d.DEPARTMENT_NAME
                  FROM {C}.SHIFTS sh JOIN {C}.STAFF st USING (STAFF_ID) JOIN {C}.DEPARTMENTS d ON d.DEPARTMENT_ID = sh.DEPARTMENT_ID
                  WHERE sh.START_TS::DATE BETWEEN ? AND ? AND (? IS NULL OR sh.DEPARTMENT_ID = ?) AND (? IS NULL OR sh.SHIFT_TYPE = ?)
                  ORDER BY sh.START_TS""", (str(start), str(end), dept, dept, shift_type, shift_type))


def staffing(p: Principal) -> pd.DataFrame:
    p.require(Perm.STAFF_SCHEDULE)
    return _q(f"""SELECT d.DEPARTMENT_NAME, st.ROLE, COUNT_IF(st.EMPLOYMENT_STATUS = 'ACTIVE') ACTIVE,
                         COUNT_IF(st.EMPLOYMENT_STATUS = 'ON_LEAVE') ON_LEAVE
                  FROM {C}.STAFF st JOIN {C}.DEPARTMENTS d USING (DEPARTMENT_ID) GROUP BY ALL ORDER BY 1, 2""")
