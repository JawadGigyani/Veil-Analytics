<div align="center">

# Veil Analytics

**Useful answers. Protected people.**

A differentially private analytics platform. Run aggregate queries over
sensitive data without releasing raw records — and without pretending the
answers are exact.

</div>

---

![Workspace overview](docs/screenshots/05-overview.png)

## What it does

Aggregate statistics are not safe by default. Publish enough counts and averages
over the same dataset and the underlying rows can be reconstructed — the US
Census Bureau demonstrated this against its own 2010 published tables.

Veil Analytics replaces that hope with a bound. A data owner uploads a
sensitive dataset and declares how it may be released. Analysts ask aggregate
questions. Every answer comes back with calibrated random noise, an honest
statement of its uncertainty, and a permanent deduction from a fixed privacy
budget. When the budget is gone, the dataset stops answering.

**Core properties**

- **(ε, δ)-differential privacy** — Laplace, Gaussian, and exponential
  mechanisms, with Gaussian calibrated by the Balle–Wang (2018) analytic
  method rather than the classical bound that silently under-noises above ε = 1
- **Entity-level privacy** — noise calibrated to a *person*, not a *row*, via
  configurable contribution bounding
- **Enforced budget** — spend is atomic, happens inside a Postgres transaction
  before data is touched, and is never refunded
- **Honest uncertainty** — every release states its error, and flags results
  where the noise dominates the signal
- **Non-disclosive audit** — column names and epsilon are recorded; filter
  values never are
- **Server-only execution** — data is encrypted at rest and decrypted only
  inside an isolated Python worker

📖 **[Full user guide with screenshots →](docs/USER_GUIDE.md)**

---

## Screenshots

| Compose a release | Released answer |
| --- | --- |
| ![Composer](docs/screenshots/06-composer-grouped-count.png) | ![Result](docs/screenshots/07-release-grouped-count.png) |
| Cost, privacy unit, and expected error shown **before** any budget is spent | Noisy counts with per-group uncertainty and full release provenance |

---

## Architecture

```
Browser (Next.js 16 · React 19 · TypeScript)
    │  session cookie
    ▼
Next.js API routes ─────────────────► Supabase (Postgres)
    │  authorization, policy checks     identity · roles · permissions
    │  BUDGET RESERVATION (atomic)      policy · ledger · audit events
    │
    │  x-worker-token
    ▼
Python analytics worker (FastAPI · DuckDB · PyArrow)
    │  decrypt → restrict → contribution-bound → aggregate → add noise
    ▼
Encrypted Parquet (Fernet at rest)
```

The web tier never sees raw data. The worker is the only component that
decrypts, and it returns only noisy aggregates.

### Repository layout

| Path | Contents |
| --- | --- |
| `src/` | Next.js app — UI, API routes, auth |
| `services/analytics-worker/` | FastAPI worker — decryption, execution, noise |
| `packages/dp-core/` | DP mechanisms, sensitivity, composition accounting |
| `packages/query-ir/` | Query intermediate representation and SQL compiler |
| `packages/dp-audit/` | Attack simulations — reconstruction, membership inference |
| `supabase/` | SQL schema and migrations |
| `db/` | Alembic migration environment |
| `docs/` | Architecture, privacy model, threat model, user guide |

---

## Requirements

| Requirement | Version | Notes |
| --- | --- | --- |
| Node.js | 20+ | |
| Python | 3.11+ | 3.13 tested |
| Supabase project | — | free tier is sufficient |

---

## Setup

### 1. Clone and install

```bash
git clone <your-repo-url> && cd privacy-analytics-platform
npm install
```

```bash
python -m venv services/venv
```

```bash
services/venv/Scripts/python.exe -m pip install -r services/analytics-worker/requirements.txt
```

> On macOS/Linux use `services/venv/bin/python` throughout.

### 2. Apply the database schema

In the Supabase SQL editor, run **in this exact order**:

```
supabase/schema.sql
supabase/migration-001-real-records.sql
supabase/migration-002-platform-features.sql
supabase/migration-003-rate-limits.sql
supabase/migration-004-privacy-hardening.sql
supabase/migration-005-permissions-audit.sql
supabase/migration-006-contribution-bounding.sql
supabase/migration-007-delta-accounting.sql
supabase/migration-008-sensitive-columns-row-restrictions.sql
supabase/migration-009-bootstrap-idempotency.sql
```

> ⚠️ Order matters. Migration 007 drops and recreates `reserve_privacy_budget`
> with a new signature — applying 008 without 007 breaks every query.

An Alembic environment is also provided in `db/` if you prefer managed
migrations against a direct Postgres connection.

### 3. Configure environment

Generate a Fernet encryption key:

```bash
services/venv/Scripts/python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Create `.env.local` in the project root:

```ini
NEXT_PUBLIC_SUPABASE_URL=https://<project>.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon key>
SUPABASE_SERVICE_ROLE_KEY=<service role key>
ANALYTICS_WORKER_URL=http://localhost:8080
ANALYTICS_WORKER_TOKEN=<any long random string>
```

Create `services/analytics-worker/.env`:

```ini
ENCRYPTION_KEY=<the Fernet key generated above>
WORKER_TOKEN=<same value as ANALYTICS_WORKER_TOKEN>
STORAGE_ROOT=.veil-storage
MAX_UPLOAD_BYTES=25000000
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<service role key>
STORAGE_BUCKET=protected-datasets
```

> 🔐 Neither file is committed. The Fernet key decrypts every protected
> dataset — losing it makes uploaded data unrecoverable, and leaking it makes
> the encryption pointless.

### 4. Run

Both processes are required. The app cannot answer any query without the
worker.

**Terminal 1 — analytics worker**

```bash
cd services/analytics-worker && ../venv/Scripts/python.exe -m uvicorn app.main:app --port 8080
```

**Terminal 2 — web app**

```bash
npm run dev
```

Open <http://localhost:3000> and create an account. A demo workspace is
provisioned automatically on first sign-in.

---

## Try it with real data

The demo dataset is synthetic. To exercise entity-level privacy properly, use
**[Diabetes 130-US Hospitals (1999–2008)](https://archive.ics.uci.edu/dataset/296/diabetes+130+us+hospitals+for+years+1999+2008)**
— 101,766 hospital encounters belonging to only 71,518 distinct patients, which
is what makes contribution bounding meaningful.

The [user guide](docs/USER_GUIDE.md#6-loading-a-dataset) gives every field
value needed to load it.

---

## Testing

```bash
npm test
```

```bash
services/venv/Scripts/python.exe scripts/run_tests.py
```

**530 tests** — 205 dp-core, 151 analytics-worker, 78 query-ir, 53 dp-audit,
43 frontend. Property-based tests (Hypothesis) cover the mechanism invariants;
`packages/dp-audit` contains reconstruction and membership-inference attack
simulations used to validate the guarantees empirically.

```bash
npx tsc --noEmit && npm run lint
```



