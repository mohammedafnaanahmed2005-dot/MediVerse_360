# Algorithms used in MediVerse 360

| Area | Algorithm | Where | Why |
|---|---|---|---|
| Password storage | **PBKDF2-HMAC-SHA256**, 600,000 iterations, 16-byte random salt, HMAC pepper | `mediverse/auth/passwords.py` | Slow key-derivation resists offline guessing; pepper lives outside the user table |
| Password check | **Constant-time comparison** (`hmac.compare_digest`) + dummy hash for unknown users | same | Prevents timing attacks and username enumeration |
| One-time codes | CSPRNG 6-digit code, **HMAC-SHA256(pepper, challenge‖code)** | `mediverse/auth/otp.py` | Only a keyed hash is stored; codes are single-use, 5-minute TTL, 3 attempts |
| Session tokens | 256-bit `secrets.token_urlsafe`, stored as **SHA-256** | `mediverse/auth/service.py` | A database leak does not reveal usable tokens |
| Brute-force control | **Sliding-window counters** (failures per user and per identifier in 15 min) + lockout | same | Rate limiting / account lockout |
| Face detection | **Viola–Jones Haar cascade** (OpenCV) | `mediverse/biometrics/lbp_face.py` | Locates exactly one frontal face; rejects none / multiple faces |
| Face normalisation | Crop → 128×128 resize → **histogram equalisation** | same | Reduces lighting differences |
| Face features | **Uniform Local Binary Patterns LBP(8,1)**, 59-bin histograms on an 8×8 grid (3,776-dim) — Ahonen et al. 2006 | same | Classical, explainable, CPU-only face descriptor |
| Face matching | **Chi-square distance**; per-user adaptive threshold = 1.5 × max intra-enrollment distance (bounded 0.35–1.2) | same | Users with consistent enrollment get stricter thresholds |
| Template protection | **Keyed permutation** of feature bins (key = SHA-256(pepper, user, salt)) | same | Cancelable templates: re-enrollment revokes old ones; not comparable across users |
| Liveness heuristic | Two frames: distinct bytes, **face-box motion ≥ 4 %**, **Laplacian-variance sharpness** | same | Rejects replaying one static photo and very blurry screen captures (not certified PAD) |
| Typing rhythm | **Keystroke dynamics**: dwell & flight times, per-feature mean/σ, fraction of features with **|z| ≤ 2.5** | `mediverse/biometrics/typing_rhythm.py` | Behavioural risk signal over a fixed consented phrase |
| Decision fusion | **AND-rule**: face PASS ∧ typing PASS → no OTP; anything else → OTP | `AuthService.verify_biometrics` | Fails safe: any doubt adds a factor, never removes one |
| Authorization | **RBAC** matrix + per-user attribute flags (ABAC-style) + department scoping | `mediverse/authz.py` | Least privilege for psychiatric, mortality and payroll data |
| Aggregate privacy | **k-anonymity suppression** (groups < 10 patients hidden) | `data.department_workload`, `V_DEPT_WORKLOAD` | Management sees trends, not individuals |
| Data quality | Rule engine: type coercion (`TRY_*`), required, **duplicate detection (window ROW_NUMBER)**, **referential integrity (anti-join)**, range/date-order checks | `scripts/build_pipeline_sql.py` | Invalid rows never reach CURATED |
| Load order | **Topological ordering** of tables by FK dependency | `schema_spec.topo_check` | Parents load before children so FK checks are meaningful |
| Synthetic data | Seeded **pseudo-random generation** (Mersenne Twister, seed 360) with controlled defect injection | `data_gen/generate.py` | Reproducible test data |
| AI | Snowflake Cortex **LLM summarisation** (`AI_COMPLETE`), **schema-guided extraction** (`AI_EXTRACT`), **zero-shot classification** (`AI_CLASSIFY`) | `mediverse/ai.py` | Human-reviewed assistance; outputs stored with source IDs |
| Search | Multi-term **keyword (ILIKE) retrieval** + LLM answer grounded in cited excerpts (retrieval-augmented) | `ai.search_documents` | Answers restricted to documents the role may read |

## Limitations
LBP + Haar is a well-known baseline, far weaker than modern deep face embeddings, and the liveness
check is a heuristic. Both are sufficient for a synthetic demonstration only.
