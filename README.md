# MediVerse 360 — AI-powered hospital intelligence platform (synthetic demo)

MediVerse 360 is a Streamlit-in-Snowflake application covering five modules:
**Patient360**, **LabLens AI**, **Imaging & Surgery Intelligence**, **PharmaStock 360** and
**HealthPulse / HealthShield** (staff, finance, data quality, security monitoring).

> **All data is synthetic.** Patient IDs start with `SYN`, names come from small fictional lists, and
> no real hospital system, image or person is involved. Nothing in the app is clinical advice.
> No legal or regulatory compliance (HIPAA, GDPR, etc.) is claimed.

## Architecture

```
            ┌──────────────── Streamlit in Snowflake (container runtime, owner = MV_APP_OWNER) ───────────────┐
 Browser ──►│ streamlit_app.py ─ session re-validation ─ role-filtered st.navigation                          │
            │ app_pages/*      ─ every page calls ui.guard(Perm)                                              │
            │ mediverse/data.py ─ every query: Principal.require(...) + PII/psych/department scoping          │
            │ mediverse/auth/*  ─ PBKDF2 passwords, server-side sessions, OTP, lockout, rate limits           │
            │ mediverse/biometrics/* ─ DEMO face + typing verifiers behind FaceVerifier/TypingVerifier        │
            │ mediverse/ai.py   ─ AI_COMPLETE / AI_EXTRACT / AI_CLASSIFY with stored source refs + review     │
            └───────────────────────────────────────────────┬─────────────────────────────────────────────────┘
                                                            ▼
 MEDIVERSE360   RAW (VARCHAR landing) ─► STAGING (typed + DQ_ISSUES) ─► CURATED (PK/FK model, policies)
                SECURITY (managed access: users, biometric templates, sessions, audit)   APP (stages, AI outputs)
```

**Two enforcement layers**
1. **App layer** (`mediverse/authz.py`). Streamlit in Snowflake container apps always run with
   *owner's rights*, so per-user authorization is enforced server-side in Python. Every data-access
   function checks a permission before running SQL, not just the navigation.
2. **Snowflake layer** (`sql/05_policies.sql`). `MV_*` roles get least-privilege grants, masking policies
   (PII, salaries, biometric templates, password hashes) and row-access policies (psychiatric/HIV
   diagnoses, mortality). These apply to direct SQL/BI access outside the app.

## Project layout

| Path | Purpose |
|---|---|
| `data_gen/generate.py` | Seeded synthetic data generator (4,000 patients, 20 specialties, 35 tables, ~2% injected defects) |
| `mediverse/schema_spec.py` | Single source of truth: columns, types, PKs, FKs, DQ rules, classification |
| `scripts/build_pipeline_sql.py` | Generates `sql/02_pipeline.sql` and `sql/03_validate_curated.sql` from the spec |
| `sql/00_setup.sql` … `05_policies.sql` | Roles, warehouse, schemas, RAW loader, pipeline, security schema, policies |
| `sql/09_deploy_app.sql`, `snowflake.yml` | Deploy via plain SQL or `snow streamlit deploy` |
| `streamlit_app.py`, `app_pages/` | Entry point and pages |
| `mediverse/` | Auth, RBAC, data access, AI, DQ validator, components |
| `tests/` | 73 automated tests (unit, auth state machine, admin, DQ, generator, headless UI) |

## Role permissions

| Capability | Admin | Doctor | Nurse | Lab | Radiology | Pharmacist | Finance | Management |
|---|---|---|---|---|---|---|---|---|
| Users, enrollment, config, audit | ✔ | | | | | | | |
| CSV ingestion | ✔ | | | | | | | |
| DQ review | ✔ | | | | | | | ✔ |
| Patient search / record | | ✔ | dept-scoped | IDs only | IDs only | IDs only | | |
| Names & contact (PII) | | ✔ | ✔ | | | | | |
| Encounters & diagnoses | | ✔ | ✔ (nursing) | | | | | |
| Laboratory | | ✔ | ✔ | ✔ | | | | |
| Imaging | | ✔ | | | ✔ | | | |
| Surgery | | ✔ | | | | | | |
| Prescriptions | | ✔ | ✔ | | | ✔ | | |
| Pharmacy inventory | | | | | | ✔ | | |
| Finance, billing, budgets | | | | | | | ✔ | |
| Payroll (aggregated) | | | | | | | ✔ | |
| Staff schedules | ✔ | | | | | | | ✔ |
| Executive analytics (k≥10) | ✔ | | | | | | | ✔ |
| Psychiatric/HIV diagnoses | | flag `PSYCH_ACCESS` | | | | | | |
| Mortality records | | flag `MORTALITY_ACCESS` | | | | | | |
| Cortex AI features | | ✔ | | ✔ | ✔ | | ✔ | ✔ |

Restricted categories need **both** the role grant and a per-user flag set by an administrator.

## Authentication

```
password ─fail─► DENIED (lockout after 5 in 15 min, SECURITY_ALERT)
   │ ok + account ACTIVE + not locked
   ├─ LAB_STAFF ──► face (2 frames + liveness) + typing rhythm
   │                 both PASS ─────────────► session (no OTP)
   │                 any FAIL/INCONCLUSIVE/UNAVAILABLE ─► OTP
   ├─ ADMIN ──────► OTP (configurable list)
   └─ others ─────► session
OTP: 6 digits, HMAC-hashed, 5 min TTL, 3 attempts, 3 sends / 15 min ─fail─► DENIED + recovery text
```

* Biometrics are only reachable **after** password, status and lock checks, and the session
  re-checks status and role on every rerun.
* Sessions: random 256-bit token, stored only as SHA-256, 30-minute idle and 8-hour absolute expiry.
  They are revoked on logout, deactivation, role/flag change, password reset or re-enrollment.
* `AUTH_EVENTS` holds reason codes and scores only. Tests assert that passwords, OTPs, raw images
  and tokens never appear in logs or stored rows.
* Enrollment is admin-only and cannot be self-service; admins cannot change their own account. It
  requires the admin password again, a consent checkbox and an identity-evidence note, and every
  action is written to `ADMIN_AUDIT`.

### Biometric demo — read this

`mediverse/biometrics/mock_face.py` and `typing_rhythm.py` are **clearly labelled demonstrations**:
* Face: a 16×16 average hash, XOR-masked and permuted with a per-user key (a cancelable template).
  "Liveness" only checks that two frames differ slightly. This is **not** facial recognition and
  gives **no** real presentation-attack protection. Different people may match; the same person
  may fail in different lighting.
* Typing: dwell/flight-time z-scores over a fixed consented phrase. It is a probabilistic risk signal
  only.
* To go to production, implement `FaceVerifier` / `TypingVerifier` with a vetted provider (ISO 30107-3
  PAD-certified liveness), host it on a secured service, and keep the OTP fallback.

## Cortex AI

Functions used: `AI_COMPLETE` (llama3.3-70b), `AI_EXTRACT`, `AI_CLASSIFY` and `AI_COUNT_TOKENS`.
They power discharge and pathology summaries, structured field extraction, DQ issue categorisation,
operational/expenditure trend narratives, and keyword search plus a cited answer over the documents
a role may read. Every output:
* is labelled *AI-generated, requires human review*,
* is stored in `APP.AI_OUTPUTS` with source table and IDs and starts as `PENDING_REVIEW`,
* comes from prompts that forbid diagnosis, prescribing and treatment advice.

**Data residency note:** this account's region (AWS AP-Southeast-7) uses Cortex **cross-region
inference** (`CORTEX_ENABLED_CROSS_REGION = AWS_US`), so the synthetic text is processed in AWS US
regions.

## Data pipeline and quality

1. `python data_gen/generate.py` writes CSVs to `data/synthetic/` (`--seed`, `--patients`, `--defect-rate`).
2. `PUT` the files to `@MEDIVERSE360.RAW.CSV_STAGE`, then `CALL RAW.LOAD_ALL_RAW(FALSE)`. Existing RAW
   tables are skipped unless you pass `TRUE`.
3. `CALL STAGING.RUN_PIPELINE(FALSE)` types every column with `TRY_*` and records issues
   (`REQUIRED_MISSING`, `INVALID_FORMAT`, `DUPLICATE_ID`, `FK_ORPHAN`, `DATE_ORDER`, `OUT_OF_RANGE`,
   `IMPLAUSIBLE_VALUE`, `INVALID_EMAIL`, ...). Only rows with no ERROR issue whose ID is not already
   in CURATED get promoted. `TRUE` empties CURATED first and is an explicit action.
4. `sql/03_validate_curated.sql` independently checks PK uniqueness and every FK. It returns zero rows.

Verified result for the shipped dataset (seed 360): every injected defect type was caught; orphaned
children of rejected parents were held back; the CURATED integrity check returned 0 rows.

## Windows setup

```powershell
# 1. Python deps for generator, tests and local preview
py -3.11 -m pip install -r requirements-dev.txt

# 2. Generate synthetic data
python data_gen\generate.py

# 3. Snowflake objects (run in Snowsight or `snow sql -f`, in order)
#    sql\00_setup.sql   (SECURITYADMIN / SYSADMIN / ACCOUNTADMIN sections)
#    sql\01_raw.sql     then PUT data\synthetic\*.csv to @MEDIVERSE360.RAW.CSV_STAGE and CALL RAW.LOAD_ALL_RAW(FALSE);
python scripts\build_pipeline_sql.py
#    sql\02_pipeline.sql then CALL MEDIVERSE360.STAGING.RUN_PIPELINE(FALSE);
#    sql\03_validate_curated.sql   (expect zero rows)
#    sql\04_security.sql, sql\05_policies.sql

# 4. Deploy the app (either)
snow streamlit deploy --replace          # with Snowflake CLI >= 3.14
#   or run sql\09_deploy_app.sql from a client that supports PUT

# 5. First run: open the app, read the bootstrap token as MV_APP_OWNER
#    SELECT KEY_VALUE FROM MEDIVERSE360.SECURITY.APP_KEYS WHERE KEY_NAME = 'BOOTSTRAP_TOKEN';
#    and create the first administrator. Then create staff accounts and enroll lab staff.
```

Local preview (optional): add a `mediverse_dev` connection to `~/.snowflake/connections.toml`
(role `MV_APP_OWNER`), copy `.env.example` to `.env`, then run
`$env:SNOWFLAKE_DEFAULT_CONNECTION_NAME="mediverse_dev"; streamlit run streamlit_app.py`.

## Testing

```powershell
python -m pytest -q tests
python scripts\capture_sql.py   # records every data-layer SELECT across roles -> build\captured_selects.sql
```
`captured_selects.sql` was executed against the live schema; all 75 statements succeeded.

## Security limitations (honest list)

* **Streamlit version is pinned** to `streamlit[snowflake]==1.52.2` in `pyproject.toml`, resolved from
  Snowflake's built-in PyPI mirror (`snowflake.snowpark.pypi_shared_repository`). The container
  runtime requires a declared Streamlit (>=1.48), but an unpinned latest release broke the platform's
  `st.connection` wrapper. Upgrade the pin deliberately and retest.
* **Trial account:** Snowflake does not allow external access integrations on trial accounts, so the
  app cannot receive a Snowflake `SECRET`. The pepper therefore lives in `SECURITY.APP_KEYS`
  (managed-access schema, masked for every role except `MV_APP_OWNER`). On a paid account, move it
  to `CREATE SECRET` + EAI + `ALTER STREAMLIT ... SET SECRETS`.
* **Owner's rights:** the app's Snowflake role can read everything; per-user enforcement is in app
  code. Anyone with `MV_APP_OWNER` bypasses app rules. Grant that role only to administrators.
* **Shared Snowflake identity:** Snowflake query history shows the app owner, not the app user.
  The app's `ACCESS_LOG` / `AUTH_EVENTS` hold user attribution.
* **OTP in demo mode** is shown on screen. Set `MEDIVERSE_DEMO_MODE=false` and configure an email
  notification integration (`SYSTEM$SEND_EMAIL`) for a real channel (verified Snowflake user emails only).
* **CSRF:** Streamlit uses a websocket protocol with its own origin checks inside Snowsight's
  authenticated iframe; there are no custom HTTP form endpoints to protect.
* **Rate limits** are per username and per account; there is no per-IP limit because the client IP
  is not exposed to the app.
* Biometrics are mocks (see above). Do not use this application with real patient data.

## Demo vs production configuration

| Setting | Demo (default) | Production |
|---|---|---|
| `MEDIVERSE_DEMO_MODE` | `true` (on-screen OTP, DEMO banners) | `false` |
| OTP channel | `DemoOTPSender` | `SnowflakeEmailOTPSender` / SMS provider |
| Biometric verifiers | `MockFaceVerifier`, `KeystrokeVerifier` | vetted provider implementations |
| Pepper | `SECURITY.APP_KEYS` | Snowflake `SECRET` via EAI |
| Data | synthetic CSVs | not supported by this project |
