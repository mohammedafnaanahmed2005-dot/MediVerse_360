"""Single source of truth for the CURATED data model and its validation rules.

Used by ``scripts/build_pipeline_sql.py`` (generates the Snowflake pipeline) and
by ``mediverse/dq.py`` (validates uploaded CSVs in the app before loading).

Column tuple: (name, type, required). Types: S string, I integer, F decimal,
D date, T timestamp, B 0/1 flag.
Checks: (rule_code, SQL predicate that is TRUE for a *bad* row, severity).
"""

from __future__ import annotations

SNOWFLAKE_TYPES = {"S": "VARCHAR", "I": "NUMBER(38,0)", "F": "NUMBER(14,2)",
                   "D": "DATE", "T": "TIMESTAMP_NTZ", "B": "BOOLEAN"}

EMAIL_RE = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}$"

# Ordered parents-first so foreign keys can be checked against CURATED.
TABLES: dict[str, dict] = {
    "specialties": {"cols": [("specialty_id", "S", 1), ("specialty_name", "S", 1)], "pk": "specialty_id"},
    "departments": {"cols": [("department_id", "S", 1), ("department_name", "S", 1), ("specialty_id", "S", 1),
                             ("floor", "I", 0), ("bed_capacity", "I", 0)],
                    "pk": "department_id", "fks": [("specialty_id", "specialties", "specialty_id")]},
    "staff": {"cols": [("staff_id", "S", 1), ("first_name", "S", 1), ("last_name", "S", 1), ("role", "S", 1),
                       ("department_id", "S", 1), ("hire_date", "D", 1), ("employment_status", "S", 1),
                       ("email", "S", 0), ("base_salary", "F", 1)],
              "pk": "staff_id", "fks": [("department_id", "departments", "department_id")],
              "checks": [("INVALID_EMAIL", f"email IS NOT NULL AND NOT REGEXP_LIKE(email, '{EMAIL_RE}')", "WARN"),
                         ("UNEXPECTED_VALUE", "employment_status NOT IN ('ACTIVE','ON_LEAVE','TERMINATED')", "ERROR"),
                         ("OUT_OF_RANGE", "base_salary <= 0", "ERROR")]},
    "doctors": {"cols": [("doctor_id", "S", 1), ("staff_id", "S", 1), ("specialty_id", "S", 1), ("department_id", "S", 1),
                         ("license_no", "S", 1), ("is_surgeon", "B", 1)],
                "pk": "doctor_id", "fks": [("staff_id", "staff", "staff_id"), ("specialty_id", "specialties", "specialty_id"),
                                           ("department_id", "departments", "department_id")]},
    "nurses": {"cols": [("nurse_id", "S", 1), ("staff_id", "S", 1), ("department_id", "S", 1), ("grade", "S", 0)],
               "pk": "nurse_id", "fks": [("staff_id", "staff", "staff_id"), ("department_id", "departments", "department_id")]},
    "shifts": {"cols": [("shift_id", "S", 1), ("staff_id", "S", 1), ("department_id", "S", 1), ("shift_type", "S", 1),
                        ("start_ts", "T", 1), ("end_ts", "T", 1), ("status", "S", 1)],
               "pk": "shift_id", "fks": [("staff_id", "staff", "staff_id"), ("department_id", "departments", "department_id")],
               "checks": [("DATE_ORDER", "end_ts <= start_ts", "ERROR")]},
    "payroll": {"cols": [("payroll_id", "S", 1), ("staff_id", "S", 1), ("pay_period", "S", 1), ("gross_pay", "F", 1),
                         ("overtime_pay", "F", 0), ("deductions", "F", 0)],
                "pk": "payroll_id", "fks": [("staff_id", "staff", "staff_id")],
                "checks": [("INVALID_FORMAT", "NOT REGEXP_LIKE(pay_period, '^[0-9]{4}-[0-9]{2}$')", "ERROR"),
                           ("OUT_OF_RANGE", "gross_pay < 0 OR deductions > gross_pay", "ERROR")]},
    "patients": {"cols": [("patient_id", "S", 1), ("first_name", "S", 1), ("last_name", "S", 1), ("date_of_birth", "D", 1),
                          ("sex", "S", 1), ("blood_type", "S", 0), ("city", "S", 0), ("phone", "S", 0), ("email", "S", 0),
                          ("primary_specialty_id", "S", 1), ("registered_on", "D", 1)],
                 "pk": "patient_id", "fks": [("primary_specialty_id", "specialties", "specialty_id")],
                 "checks": [("INVALID_EMAIL", f"email IS NOT NULL AND NOT REGEXP_LIKE(email, '{EMAIL_RE}')", "WARN"),
                            ("MISSING_CONTACT", "phone IS NULL AND email IS NULL", "WARN"),
                            ("DATE_ORDER", "date_of_birth > CURRENT_DATE()", "ERROR"),
                            ("UNEXPECTED_VALUE", "sex NOT IN ('F','M','X')", "ERROR")]},
    "allergies": {"cols": [("allergy_id", "S", 1), ("patient_id", "S", 1), ("allergen", "S", 1), ("severity", "S", 1)],
                  "pk": "allergy_id", "fks": [("patient_id", "patients", "patient_id")]},
    "encounters": {"cols": [("encounter_id", "S", 1), ("patient_id", "S", 1), ("doctor_id", "S", 1), ("department_id", "S", 1),
                            ("encounter_type", "S", 1), ("admit_date", "D", 1), ("discharge_date", "D", 0), ("status", "S", 1)],
                   "pk": "encounter_id", "fks": [("patient_id", "patients", "patient_id"), ("doctor_id", "doctors", "doctor_id"),
                                                 ("department_id", "departments", "department_id")],
                   "checks": [("DATE_ORDER", "discharge_date < admit_date", "ERROR"),
                              ("UNEXPECTED_VALUE", "encounter_type NOT IN ('OUTPATIENT','INPATIENT','EMERGENCY')", "ERROR")]},
    "diagnoses": {"cols": [("diagnosis_id", "S", 1), ("encounter_id", "S", 1), ("icd10_code", "S", 1), ("description", "S", 1),
                           ("is_primary", "B", 0), ("is_sensitive", "B", 1)],
                  "pk": "diagnosis_id", "fks": [("encounter_id", "encounters", "encounter_id")],
                  "checks": [("INVALID_FORMAT", "NOT REGEXP_LIKE(icd10_code, '^[A-Z][0-9]{2}([.][0-9A-Z]{1,4})?$')", "ERROR")]},
    "discharge_summaries": {"cols": [("discharge_id", "S", 1), ("encounter_id", "S", 1), ("summary_text", "S", 1),
                                     ("discharge_disposition", "S", 0), ("written_by_doctor_id", "S", 1)],
                            "pk": "discharge_id", "fks": [("encounter_id", "encounters", "encounter_id"),
                                                          ("written_by_doctor_id", "doctors", "doctor_id")]},
    "manufacturers": {"cols": [("manufacturer_id", "S", 1), ("name", "S", 1), ("country", "S", 0)], "pk": "manufacturer_id"},
    "suppliers": {"cols": [("supplier_id", "S", 1), ("name", "S", 1), ("contact_email", "S", 0), ("lead_time_days", "I", 0),
                           ("rating", "F", 0)], "pk": "supplier_id"},
    "drugs": {"cols": [("drug_id", "S", 1), ("drug_name", "S", 1), ("generic_name", "S", 1), ("form", "S", 1),
                       ("manufacturer_id", "S", 1), ("unit_price", "F", 1), ("reorder_level", "I", 1), ("is_controlled", "B", 1)],
              "pk": "drug_id", "fks": [("manufacturer_id", "manufacturers", "manufacturer_id")],
              "checks": [("OUT_OF_RANGE", "unit_price < 0", "ERROR")]},
    "prescriptions": {"cols": [("prescription_id", "S", 1), ("encounter_id", "S", 1), ("patient_id", "S", 1), ("doctor_id", "S", 1),
                               ("drug_id", "S", 1), ("dose", "S", 1), ("frequency", "S", 1), ("days", "I", 1), ("prescribed_on", "D", 1)],
                      "pk": "prescription_id", "fks": [("encounter_id", "encounters", "encounter_id"), ("patient_id", "patients", "patient_id"),
                                                       ("doctor_id", "doctors", "doctor_id"), ("drug_id", "drugs", "drug_id")]},
    "medication_orders": {"cols": [("order_id", "S", 1), ("prescription_id", "S", 1), ("nurse_id", "S", 1),
                                   ("administered_ts", "T", 1), ("status", "S", 1)],
                          "pk": "order_id", "fks": [("prescription_id", "prescriptions", "prescription_id"), ("nurse_id", "nurses", "nurse_id")]},
    "lab_test_catalog": {"cols": [("test_code", "S", 1), ("test_name", "S", 1), ("unit", "S", 1), ("ref_low", "F", 1),
                                  ("ref_high", "F", 1), ("specimen_type", "S", 1)], "pk": "test_code",
                         "checks": [("OUT_OF_RANGE", "ref_low > ref_high", "ERROR")]},
    "lab_orders": {"cols": [("lab_order_id", "S", 1), ("encounter_id", "S", 1), ("patient_id", "S", 1), ("ordering_doctor_id", "S", 1),
                            ("ordered_ts", "T", 1), ("priority", "S", 1), ("status", "S", 1)],
                   "pk": "lab_order_id", "fks": [("encounter_id", "encounters", "encounter_id"), ("patient_id", "patients", "patient_id"),
                                                 ("ordering_doctor_id", "doctors", "doctor_id")]},
    "specimens": {"cols": [("specimen_id", "S", 1), ("lab_order_id", "S", 1), ("specimen_type", "S", 1), ("collected_ts", "T", 1),
                           ("received_ts", "T", 0), ("condition", "S", 0)],
                  "pk": "specimen_id", "fks": [("lab_order_id", "lab_orders", "lab_order_id")],
                  "checks": [("DATE_ORDER", "received_ts < collected_ts", "ERROR")]},
    "lab_results": {"cols": [("result_id", "S", 1), ("lab_order_id", "S", 1), ("specimen_id", "S", 1), ("test_code", "S", 1),
                             ("value", "F", 1), ("flag", "S", 0), ("resulted_ts", "T", 1), ("verified_by_staff_id", "S", 0)],
                    "pk": "result_id", "fks": [("lab_order_id", "lab_orders", "lab_order_id"), ("specimen_id", "specimens", "specimen_id"),
                                               ("test_code", "lab_test_catalog", "test_code"), ("verified_by_staff_id", "staff", "staff_id")],
                    "checks": [("IMPLAUSIBLE_VALUE", "value < 0 OR value > 50000", "ERROR")]},
    "pathology_reports": {"cols": [("pathology_id", "S", 1), ("lab_order_id", "S", 1), ("specimen_site", "S", 0),
                                   ("report_text", "S", 1), ("status", "S", 1)],
                          "pk": "pathology_id", "fks": [("lab_order_id", "lab_orders", "lab_order_id")]},
    "imaging_studies": {"cols": [("study_id", "S", 1), ("encounter_id", "S", 1), ("patient_id", "S", 1), ("modality", "S", 1),
                                 ("body_region", "S", 1), ("study_date", "D", 1), ("ordering_doctor_id", "S", 1), ("status", "S", 1)],
                        "pk": "study_id", "fks": [("encounter_id", "encounters", "encounter_id"), ("patient_id", "patients", "patient_id"),
                                                  ("ordering_doctor_id", "doctors", "doctor_id")],
                        "checks": [("UNEXPECTED_VALUE", "modality NOT IN ('MRI','CT','XRAY','US')", "ERROR")]},
    "radiology_reports": {"cols": [("report_id", "S", 1), ("study_id", "S", 1), ("radiologist_doctor_id", "S", 1),
                                   ("findings", "S", 1), ("impression", "S", 0), ("report_status", "S", 1)],
                          "pk": "report_id", "fks": [("study_id", "imaging_studies", "study_id"),
                                                     ("radiologist_doctor_id", "doctors", "doctor_id")]},
    "surgeries": {"cols": [("surgery_id", "S", 1), ("encounter_id", "S", 1), ("patient_id", "S", 1), ("surgeon_doctor_id", "S", 1),
                           ("procedure_name", "S", 1), ("surgery_date", "D", 1), ("anesthesia_type", "S", 1),
                           ("duration_min", "I", 1), ("cost", "F", 1)],
                  "pk": "surgery_id", "fks": [("encounter_id", "encounters", "encounter_id"), ("patient_id", "patients", "patient_id"),
                                              ("surgeon_doctor_id", "doctors", "doctor_id")],
                  "checks": [("OUT_OF_RANGE", "duration_min <= 0 OR duration_min > 1440 OR cost < 0", "ERROR")]},
    "surgery_outcomes": {"cols": [("outcome_id", "S", 1), ("surgery_id", "S", 1), ("outcome", "S", 1),
                                  ("complication_notes", "S", 0), ("los_post_op_days", "I", 0)],
                         "pk": "outcome_id", "fks": [("surgery_id", "surgeries", "surgery_id")]},
    "death_records": {"cols": [("death_id", "S", 1), ("patient_id", "S", 1), ("date_of_death", "D", 1),
                               ("cause_icd10", "S", 1), ("location", "S", 0)],
                      "pk": "death_id", "fks": [("patient_id", "patients", "patient_id")]},
    "stock_batches": {"cols": [("batch_id", "S", 1), ("drug_id", "S", 1), ("supplier_id", "S", 1), ("lot_no", "S", 1),
                               ("received_date", "D", 1), ("expiry_date", "D", 1), ("qty_received", "I", 1), ("qty_on_hand", "I", 1)],
                      "pk": "batch_id", "fks": [("drug_id", "drugs", "drug_id"), ("supplier_id", "suppliers", "supplier_id")],
                      "checks": [("DATE_ORDER", "expiry_date <= received_date", "ERROR"),
                                 ("OUT_OF_RANGE", "qty_on_hand < 0 OR qty_on_hand > qty_received", "ERROR")]},
    "purchases": {"cols": [("purchase_id", "S", 1), ("batch_id", "S", 1), ("supplier_id", "S", 1), ("order_date", "D", 1),
                           ("qty", "I", 1), ("total_cost", "F", 1)],
                  "pk": "purchase_id", "fks": [("batch_id", "stock_batches", "batch_id"), ("supplier_id", "suppliers", "supplier_id")]},
    "dispensing": {"cols": [("dispense_id", "S", 1), ("prescription_id", "S", 1), ("batch_id", "S", 1),
                            ("pharmacist_staff_id", "S", 1), ("qty", "I", 1), ("dispensed_on", "D", 1)],
                   "pk": "dispense_id", "fks": [("prescription_id", "prescriptions", "prescription_id"),
                                                ("batch_id", "stock_batches", "batch_id"), ("pharmacist_staff_id", "staff", "staff_id")]},
    "equipment": {"cols": [("equipment_id", "S", 1), ("name", "S", 1), ("department_id", "S", 1), ("purchase_date", "D", 1),
                           ("purchase_cost", "F", 1), ("status", "S", 1)],
                  "pk": "equipment_id", "fks": [("department_id", "departments", "department_id")]},
    "maintenance": {"cols": [("maintenance_id", "S", 1), ("equipment_id", "S", 1), ("service_date", "D", 1),
                             ("maintenance_type", "S", 1), ("cost", "F", 1), ("vendor", "S", 0)],
                    "pk": "maintenance_id", "fks": [("equipment_id", "equipment", "equipment_id")]},
    "dept_budgets": {"cols": [("budget_id", "S", 1), ("department_id", "S", 1), ("fiscal_year", "I", 1), ("budget_amount", "F", 1)],
                     "pk": "budget_id", "fks": [("department_id", "departments", "department_id")]},
    "expenses": {"cols": [("expense_id", "S", 1), ("department_id", "S", 1), ("category", "S", 1), ("expense_date", "D", 1),
                          ("amount", "F", 1), ("description", "S", 0)],
                 "pk": "expense_id", "fks": [("department_id", "departments", "department_id")]},
    "billing": {"cols": [("bill_id", "S", 1), ("encounter_id", "S", 1), ("patient_id", "S", 1), ("amount", "F", 1),
                         ("payer", "S", 1), ("bill_status", "S", 1), ("billed_on", "D", 1)],
                "pk": "bill_id", "fks": [("encounter_id", "encounters", "encounter_id"), ("patient_id", "patients", "patient_id")],
                "checks": [("OUT_OF_RANGE", "amount < 0", "ERROR")]},
}

# Data classification used for masking and access decisions.
CLASSIFICATION = {
    "patients": {"first_name": "PII", "last_name": "PII", "date_of_birth": "PII", "phone": "PII", "email": "PII"},
    "staff": {"email": "PII", "base_salary": "FINANCIAL_SENSITIVE"},
    "payroll": {"gross_pay": "FINANCIAL_SENSITIVE", "overtime_pay": "FINANCIAL_SENSITIVE", "deductions": "FINANCIAL_SENSITIVE"},
    "diagnoses": {"description": "PHI", "icd10_code": "PHI"},
    "death_records": {"cause_icd10": "RESTRICTED_PHI"},
}


def topo_check() -> None:
    """Raise if any FK references a table that is not defined earlier."""
    seen: set[str] = set()
    for name, spec in TABLES.items():
        for _col, parent, _pcol in spec.get("fks", []):
            if parent not in seen:
                raise ValueError(f"{name} references {parent} before it is defined")
        seen.add(name)


topo_check()
