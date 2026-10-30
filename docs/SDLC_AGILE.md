# SDLC and Agile process for MediVerse 360

## Model: Agile (Scrum) inside an iterative SDLC

The project follows the classic SDLC stages (requirements → design → implementation → testing →
deployment → maintenance), executed **iteratively** as Scrum sprints. Each sprint delivers a
working, tested increment rather than handing documents to the next stage, as a Waterfall model
would.

```
 Product backlog ─► Sprint planning ─► Sprint (design · build · test) ─► Review/demo ─► Retrospective
        ▲                                                                                   │
        └────────────────────────────── backlog refinement ◄────────────────────────────────┘
```

| Scrum element | How it is applied |
|---|---|
| Product owner | Hospital stakeholder (the requester); approves the plan and each phase |
| Product backlog | Sections 3–11 of the requirements, split into user stories (below) |
| Sprint | One phase (Phase 1–7), time-boxed, ending in a demonstrable increment |
| Definition of Done | Code + automated tests passing + verified against Snowflake + documented limitations |
| Review | Phase report: what was built, which tests passed, what remains, account changes needed |
| Retrospective | Issues found feed the next sprint (e.g. Streamlit API drift → `check_streamlit_api.py`) |

## SDLC stages mapped to sprints

| SDLC stage | Sprint / phase | Artefacts |
|---|---|---|
| Requirements & feasibility | Sprint 0 (Phase 1) | Environment inspection, runtime/dataset decisions, approved plan |
| System design | Sprint 0–1 | Architecture diagram, `schema_spec.py` data model, RBAC matrix, auth state machine |
| Implementation | Sprints 1–5 (Phases 2–6) | SQL pipeline, auth/RBAC, biometrics, module pages, Cortex AI, reports |
| Testing | Every sprint (continuous) | 80+ pytest tests, live SQL verification (`capture_sql.py`), Streamlit API check, browser checks |
| Deployment | Sprint 6 (Phase 7) | `sql/09_deploy_app.sql`, `sql/10_update_app.sql`, `snowflake.yml` |
| Maintenance | Ongoing | Version pinning, regression tests, audit/DQ monitoring pages |

## Sprint log

| Sprint | Goal | Increment delivered | Verification |
|---|---|---|---|
| 0 | Discover & plan | Architecture + plan approved | Account checks (roles, Cortex, compute pool) |
| 1 | Data foundation | Generator, RAW loader, STAGING/CURATED pipeline, DQ rules | Row counts match; CURATED integrity check returns 0 rows |
| 2 | Identity & access | PBKDF2 auth, sessions, lockout, admin service, RBAC | Auth state-machine and admin tests |
| 3 | Step-up auth | Face + typing verification, OTP fallback, time limits | All failure paths tested |
| 4 | Clinical & ops modules | Patient360, LabLens, Imaging/Surgery, Pharmacy, Finance, Staff | 75 queries executed against live schema |
| 5 | Intelligence & governance | Cortex AI, executive dashboard, reports, DQ review, security audit, masking/RAP | Policy checks as MV_DOCTOR / MV_LAB |
| 6 | Release | SiS deployment, local run, docs | App renders in Snowflake; API compatibility check clean |
| 7 | Hardening (this sprint) | Streamlit 1.52 fixes, LBP face recognition, email login, reports page | 81 tests pass; deployed |

## Example user stories (backlog)

* As a **lab technician**, I sign in with my email and password and verify with face + typing so
  that I can skip the OTP when both match. *Acceptance:* both PASS → session; any other result → OTP;
  the step expires after 5 minutes.
* As an **administrator**, I enroll lab staff in person, with consent and identity evidence, so
  that biometric profiles are trustworthy. *Acceptance:* staff cannot self-enroll; every action is
  written to ADMIN_AUDIT.
* As a **pharmacist**, I see low-stock and expiring batches so that I can reorder in time.
* As **finance staff**, I see budget utilisation by department without individual salaries.
* As **management**, I see k-anonymised workload trends and download reports as CSV.
* As a **data steward**, I review DQ issues, with AI-suggested categories, before records are corrected.

## Quality practices
* Test-driven checks on security-critical logic (auth paths, RBAC matrix, log redaction).
* Single source of truth (`schema_spec.py`) generates the SQL, which prevents drift.
* Static compatibility gate: `python scripts/check_streamlit_api.py` before every deploy.
* Peer-review-ready structure: small modules, parameterised SQL, no secrets in code.
