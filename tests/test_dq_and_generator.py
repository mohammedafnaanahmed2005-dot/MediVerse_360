"""Data-quality validator and synthetic generator integrity tests."""

import csv
from pathlib import Path

import pandas as pd
import pytest

from data_gen.generate import Gen
from mediverse.dq import validate
from mediverse.schema_spec import TABLES

ROOT = Path(__file__).resolve().parents[1]


def good_patients():
    return pd.DataFrame([
        {"patient_id": "SYN000001", "first_name": "A", "last_name": "B", "date_of_birth": "1980-01-02", "sex": "F",
         "blood_type": "O+", "city": "X", "phone": "1", "email": "a@b.example", "primary_specialty_id": "SP01",
         "registered_on": "2021-01-01"},
        {"patient_id": "SYN000002", "first_name": "C", "last_name": "D", "date_of_birth": "1990-05-06", "sex": "M",
         "blood_type": "A+", "city": "Y", "phone": "", "email": "", "primary_specialty_id": "SP02",
         "registered_on": "2021-01-01"},
    ])


def test_valid_file_has_no_errors():
    issues, s = validate("patients", good_patients())
    assert s["error_rows"] == 0 and s["valid_rows"] == 2


def test_each_defect_type_detected():
    df = good_patients()
    df = pd.concat([df, df.iloc[[0]]], ignore_index=True)        # duplicate id
    df.loc[1, "date_of_birth"] = "2099-13-45"                       # invalid date
    df.loc[1, "email"] = "not-an-email"                             # bad email (warn)
    df.loc[2, "first_name"] = ""                                    # required missing
    issues, s = validate("patients", df)
    rules = set(issues["rule"])
    assert {"DUPLICATE_ID", "INVALID_FORMAT", "INVALID_EMAIL", "REQUIRED_MISSING"} <= rules
    assert s["error_rows"] == 2
    assert issues.loc[issues["rule"] == "INVALID_EMAIL", "severity"].iloc[0] == "WARN"


def test_missing_column_blocks_everything():
    issues, s = validate("patients", good_patients().drop(columns=["sex"]))
    assert issues["rule"].tolist() == ["MISSING_COLUMN"] and s["valid_rows"] == 0


def test_date_order_rule():
    df = pd.DataFrame([{"encounter_id": "E1", "patient_id": "P", "doctor_id": "D", "department_id": "X",
                        "encounter_type": "INPATIENT", "admit_date": "2025-01-10", "discharge_date": "2025-01-05",
                        "status": "CLOSED"}])
    issues, _ = validate("encounters", df)
    assert "DATE_ORDER" in set(issues["rule"])


def test_unknown_table_rejected():
    with pytest.raises(ValueError):
        validate("users; drop table x", good_patients())


def test_generator_is_deterministic_and_complete(tmp_path):
    a = Gen(7, 200, 0.0).run()
    b = Gen(7, 200, 0.0).run()
    assert a["patients"] == b["patients"]
    assert set(TABLES) <= set(a)
    for name, spec in TABLES.items():
        assert list(a[name][0].keys()) == [c for c, _, _ in spec["cols"]], name


def test_generator_clean_mode_has_no_orphans():
    t = Gen(11, 300, 0.0).run()
    for name, spec in TABLES.items():
        for col, parent, pcol in spec.get("fks", []):
            keys = {r[pcol] for r in t[parent]}
            bad = [r for r in t[name] if r[col] not in (None, "") and r[col] not in keys]
            assert not bad, f"{name}.{col} -> {parent}"


def test_twenty_specialties_and_patient_ids_synthetic():
    t = Gen(360, 400, 0.0).run()
    assert len({p["primary_specialty_id"] for p in t["patients"]}) == 20
    assert all(p["patient_id"].startswith("SYN") for p in t["patients"])


def test_shipped_csvs_validate_with_expected_defects():
    path = ROOT / "data" / "synthetic" / "patients.csv"
    if not path.exists():
        pytest.skip("synthetic CSVs not generated")
    issues, s = validate("patients", pd.read_csv(path, dtype=str, keep_default_na=False))
    assert s["rows"] == 4020
    assert (issues["rule"] == "DUPLICATE_ID").sum() == 20
