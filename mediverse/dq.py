"""Pre-load validation for uploaded CSVs, driven by ``schema_spec``.

This is the fast, client-side gate shown before an upload is confirmed. The
authoritative checks (including FKs against CURATED) run again in
STAGING.RUN_PIPELINE, so nothing invalid can reach CURATED even if this step
is bypassed.
"""

from __future__ import annotations

import pandas as pd

from mediverse.schema_spec import EMAIL_RE, TABLES

MAX_UPLOAD_ROWS = 200_000


def _parse(series: pd.Series, typ: str) -> pd.Series:
    s = series.astype("string").str.strip().replace("", pd.NA)
    if typ == "I":
        v = pd.to_numeric(s, errors="coerce")
        return v.where(v.isna() | (v % 1 == 0))
    if typ == "F":
        return pd.to_numeric(s, errors="coerce")
    if typ == "D":
        return pd.to_datetime(s, format="%Y-%m-%d", errors="coerce")
    if typ == "T":
        return pd.to_datetime(s, errors="coerce", format="mixed")
    if typ == "B":
        return s.map({"1": True, "0": False, "true": True, "false": False, "TRUE": True, "FALSE": False})
    return s


def validate(table: str, df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Return (issues, summary). Issues columns: row, record_id, rule, column, severity, detail."""
    if table not in TABLES:
        raise ValueError(f"Unknown table {table!r}")
    spec = TABLES[table]
    df = df.rename(columns=lambda c: str(c).strip().lower())
    issues: list[dict] = []
    expected = [c for c, _, _ in spec["cols"]]
    missing = [c for c in expected if c not in df.columns]
    extra = [c for c in df.columns if c not in expected]
    if missing:
        return pd.DataFrame([{"row": None, "record_id": None, "rule": "MISSING_COLUMN", "column": c, "severity": "ERROR",
                              "detail": "Required column absent from file"} for c in missing]), \
            {"rows": len(df), "error_rows": len(df), "valid_rows": 0, "extra_columns": extra}
    if len(df) > MAX_UPLOAD_ROWS:
        raise ValueError(f"File has {len(df)} rows; the upload limit is {MAX_UPLOAD_ROWS}.")
    pk = spec["pk"]
    ids = df[pk].astype("string")

    def add(mask: pd.Series, rule: str, col: str | None, sev: str, detail: str):
        for i in mask[mask.fillna(False)].index:
            issues.append({"row": int(i) + 2, "record_id": ids.iloc[i], "rule": rule, "column": col,
                           "severity": sev, "detail": detail})

    parsed = {}
    for col, typ, req in spec["cols"]:
        raw = df[col].astype("string").str.strip().replace("", pd.NA)
        val = _parse(df[col], typ)
        parsed[col] = val
        add(raw.notna() & val.isna(), "INVALID_FORMAT", col, "ERROR", f"Not a valid {'date' if typ == 'D' else 'value'}")
        if req:
            add(raw.isna(), "REQUIRED_MISSING", col, "ERROR", "Required field is empty")
    add(ids.notna() & ids.duplicated(keep="first"), "DUPLICATE_ID", pk, "ERROR", "Primary key appears more than once")
    for col in [c for c, _, _ in spec["cols"] if "email" in c]:
        e = df[col].astype("string").str.strip()
        add(e.notna() & (e != "") & ~e.str.match(EMAIL_RE, na=False), "INVALID_EMAIL", col, "WARN", "Malformed email")
    # Common date-order rules expressed on parsed values.
    for a, b in [("admit_date", "discharge_date"), ("start_ts", "end_ts"), ("collected_ts", "received_ts"),
                 ("received_date", "expiry_date")]:
        if a in parsed and b in parsed:
            add(parsed[b] < parsed[a], "DATE_ORDER", b, "ERROR", f"{b} is before {a}")
    if "date_of_birth" in parsed:
        add(parsed["date_of_birth"] > pd.Timestamp.today(), "DATE_ORDER", "date_of_birth", "ERROR", "Date of birth in the future")

    out = pd.DataFrame(issues, columns=["row", "record_id", "rule", "column", "severity", "detail"])
    err_rows = out.loc[out["severity"] == "ERROR", "row"].nunique()
    return out, {"rows": len(df), "error_rows": int(err_rows), "valid_rows": len(df) - int(err_rows), "extra_columns": extra}
