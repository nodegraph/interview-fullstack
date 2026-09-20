# Visit Tracker

A clinician-centric patient health data system for tracking visits and clinical
notes, with **RxNorm** medication reference data.

## The Challenge

This repo is a working starter app. Your take-home is to make the medications in a
visit note **light up** — detected with an LLM, fuzzy-matched to **RxNorm** (even
when they're misspelled, abbreviated, or written by brand name), with the RxNorm
details and any spelling corrections surfaced in the UI you design.

**→ Read [EXERCISE.md](./EXERCISE.md)** for the full task, API contract, and
deliverables. Sample notes to test against are in
[`test-transcripts/`](./test-transcripts).

## Stack

- **Frontend**: React + Vite + Tailwind CSS (served as a static SPA)
- **Backend**: FastAPI + SQLAlchemy 2 + Alembic
- **Database**: Postgres (local Docker Compose, or Render Postgres in production)
- **Medications**: RxNorm reference catalog + [RxNav](https://rxnav.nlm.nih.gov) REST API
- **Deploy**: one [Render](https://render.com) account (free web service + free Postgres)

## Features

- **Clinician Login** — select a clinician profile to view assigned patients
- **Patient Dashboard** — patients assigned to the logged-in clinician
- **Patient Detail** — patient information and visit history
- **Visit Detail** — view and edit a visit's chief complaint and free-text notes
- **RxNorm Catalog** — search and page through the reference catalog at
  `/medications`, and bulk-import the full ~15k-concept RxNorm ingredient set
  from the live RxNav API
- **Medication detection** — LLM extraction with indexed RxNorm matching for
  exact names, brands, shorthand, and misspellings. Select a highlighted mention
  to inspect its RxNorm details and spelling correction.
- **DB Status Widget** — persistent status bar showing database connection

## API

| Method & path | Purpose |
| --- | --- |
| `GET /api/health` | DB connectivity check |
| `POST /api/seed` | Wipe + reseed demo data |
| `GET /api/clinicians` | List clinicians |
| `GET /api/patients?clinicianId=` | Patients (optionally by clinician) |
| `GET /api/patients/{id}` | Patient detail |
| `GET, POST /api/patients/{id}/visits` | List / create visits |
| `GET, PUT /api/visits/{id}` | Get / update a visit |
| `GET /api/medications` | RxNorm reference catalog (paginated, searchable) |
| `GET /api/medications/{rxcui}` | One RxNorm concept |
| `POST /api/medications/import` | Bulk-import the full RxNorm catalog (background job) |
| `GET /api/medications/import` | Progress of the current/last import |
| `POST /api/medications/scrape` | Resolve specific names against RxNav |
| `GET /api/rxnorm/lookup?name=` | Exact normalized RxNorm lookup (catalog → RxNav) |
| `POST /api/visits/{id}/analyze` | Detect and resolve medication mentions in a saved visit note |

## Local development

**Prerequisites:** Docker, Python 3.12+, Node.js 20+

```bash
docker compose up -d
cp backend/.env.example backend/.env
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). The Vite dev server proxies `/api` to FastAPI on port 8000.

If port 5432 is already in use, start Postgres on another host port:

```bash
POSTGRES_PORT=5434 docker compose up -d
```

Then set `DATABASE_URL=postgresql://postgres:postgres@localhost:5434/visit_tracker` in `backend/.env`.

Initialize demo data with **Initialize Database** on the login screen, or:

```bash
curl -X POST http://localhost:8000/api/seed
```

Seed **deletes** existing clinicians, patients, and visits, then inserts the demo
rows. Medications are **not** deleted — an imported RxNorm catalog survives a
reseed; the 20 demo concepts are added only if missing.

### Building the medication catalog

The seeded catalog is 20 concepts, enough to run the app. `POST
/api/medications/import` (the **Import full RxNorm** button on `/medications`)
pulls the full ingredient set from RxNav — about 15,000 concepts with brand names
and pharmacologic classes, taking a couple of minutes:

```bash
curl -X POST http://localhost:8000/api/medications/import \
  -H 'Content-Type: application/json' -d '{"scope":"all"}'
curl http://localhost:8000/api/medications/import   # progress
```

`scope` is `all` (~15k) or `prescribable` (~6k, RxNorm Current Prescribable
Content). The job runs on a background thread. Poll `GET /api/medications/import`
for progress.

### Optional: LLM configuration

The medication-detection exercise uses an LLM. Copy the extra settings from
`backend/.env.example` into `backend/.env` and add your key:

```bash
LLM_PROVIDER=openai
LLM_MODEL=gpt-4.1-mini
LLM_API_KEY=sk-...
```

The app runs without these settings, but medication analysis requires the key.
Only the `openai` provider is supported in this first pass. Set the same variables
in the Render service environment for deployment.

Open a saved visit to analyze its note automatically; saving edits reanalyzes it.
Select a highlight to see the normalized concept, RxCUI, match type, and any
spelling correction. Amber highlights are detected mentions without a confident
catalog match. Provider/configuration failures show an error and a retry button.

Matching uses the complete local catalog, including separate brand aliases, with
an exact map and a trigram shortlist for fuzzy comparisons. Import the full
catalog to resolve ingredients missing from the 20-concept seed, including MTX,
vitamin D, and folic acid. This first pass does not perform live RxNav fallback
during analysis, infer product dosages, or harden the importer. Notes are limited
to 20,000 characters and 200 extracted mentions per analysis. See
[SOLUTION.md](./SOLUTION.md) for the design and remaining work.

## Tests

```bash
docker compose up -d
cd backend
source .venv/bin/activate
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/visit_tracker_test pytest -v
```

Tests use a separate `visit_tracker_test` database on the same Compose Postgres. They do not touch the `visit_tracker` app database.

## Project structure

```
frontend/     Vite + React UI
backend/      FastAPI API, Alembic migrations, tests
Dockerfile    production image (builds the SPA, serves it from FastAPI)
render.yaml   Render Blueprint (web service + Postgres)
SETUP.md      interview checklist (fork, deploy, submit)
EXERCISE.md   the take-home task (medication detection)
test-transcripts/  sample messy notes + answer keys for testing
```

Key files for the exercise:

```
backend/app/rxnorm.py                    RxNorm catalog, RxNav client, per-name scraper
backend/app/rxnorm_bulk.py               bulk RxNorm catalog importer
backend/app/routers/medications.py       catalog + lookup + scrape endpoints
backend/app/routers/analysis.py          POST /analyze orchestration
backend/app/medication_extraction.py     structured LLM extraction and source spans
backend/app/medication_matching.py       cached alias/trigram index and resolution
frontend/src/pages/VisitPage.tsx         visit note rendering
frontend/src/components/MedicationNotes.tsx  highlights and RxNorm details
frontend/src/pages/MedicationsPage.tsx   RxNorm catalog browser
```

## Deploy publicly (Render only)

This puts the app on a public HTTPS URL using a single Render account. No credit card. The Blueprint in `render.yaml` creates:

- a **Free** web service (API + UI)
- a **Free** Postgres database
- `DATABASE_URL` wired automatically

Render’s free Postgres **expires 30 days after creation** (then a 14-day grace period). That is enough for this takehome. Each Render workspace can have **one** free Postgres at a time.

If you are doing this as an interview takehome, follow [SETUP.md](./SETUP.md) instead (fork first, then the same hosting steps).

### 1. Push the repo to GitHub

Fork or push this repository so Render can connect to it.

### 2. Apply the Blueprint

1. Sign up at [dashboard.render.com](https://dashboard.render.com) with GitHub
2. **New** → **Blueprint**
3. Select this repository
4. Render reads `render.yaml`. Confirm the web service and Postgres are both **Free**
5. **Apply** / **Deploy**

The first build takes a few minutes. The live URL looks like `https://visit-tracker-xxxx.onrender.com`.

On boot the container runs `alembic upgrade head`, then uvicorn. You do not run migrations by hand.

**Manual alternative** (if you skip Blueprint): **New** → **Postgres** (Free), then **New** → **Web Service** (Docker, Free). On the web service, add `DATABASE_URL` from the database’s **Internal Database URL**.

### 3. Confirm it works

1. Open the Render URL. The first hit on a sleeping free instance can take about a minute.
2. The bottom bar should show **Database connected**
3. Click **Initialize Database** (this wipes and reloads demo data)
4. Pick a clinician, open a patient, and open a visit; **RxNorm Catalog** on the
   dashboard browses the reference catalog and can import the full RxNorm
   ingredient set from RxNav

Later pushes to the connected branch auto-deploy.

### Deploy troubleshooting

- **Slow or blank first load:** free Render web services sleep after 15 minutes idle. Wait and refresh.
- **Red status bar:** wait for the first deploy to finish. If you created services by hand, `DATABASE_URL` must be the **internal** connection string from the Render Postgres instance, and the web service must be in the **same region** as the database.
- **Blueprint fails because you already have a free Postgres:** Render allows one free database per workspace. Delete or upgrade the existing one, or reuse it and set `DATABASE_URL` on the web service.
- **Refresh on `/dashboard` is a 404:** the frontend build did not copy into the image. Check that `npm run build` succeeded in the Render logs.
