# Smart Policing Portal

Sector-level crime risk assessment and patrol planning, built on **5,737,862 recorded
Chicago incidents** aggregated into **2,550 city sectors**.

A Node/Express portal handles authentication and the operations dashboard. A separate
Python service holds the two trained models and serves every prediction. Both run under
Docker Compose with a Jenkins pipeline for CI/CD.

![Node](https://img.shields.io/badge/Node.js-18%2B-green)
![Python](https://img.shields.io/badge/Python-3.11-blue)
![MongoDB](https://img.shields.io/badge/MongoDB-7-brightgreen)
![Docker](https://img.shields.io/badge/Docker-Compose-blue)
![Tests](https://img.shields.io/badge/tests-33%20JS%20%2B%2025%20Py-success)

---

## What it does

| Feature | What it actually does |
|---|---|
| **Risk Assessor** | Scores any point in the city: hotspot probability, expected incident volume, and a full breakdown of which input values were measured and which were assumed. |
| **Incident Map** | Density of recorded incidents across the sector grid. |
| **Patrol Optimiser** | Takes the sectors the classifier ranks highest and solves the visiting order as a TSP with Google OR-Tools, reporting real distances in km. |
| **Model Card** | Feature importances, hyperparameters and stated limitations, read directly off the trained estimators. |
| **Auth** | Email/password with bcrypt, optional Google OAuth, sessions stored in MongoDB. |

Everything the dashboard displays is computed at request time from the shipped sector
data or from the models themselves. There are no hardcoded figures in the UI.

---

## Architecture

```
                    ┌──────────────────────────────┐
   Browser ────────▶│  Portal (Node 20 / Express 5)│
                    │  auth · sessions · dashboard │
                    └───────┬──────────────┬───────┘
                            │              │
                 sessions & │              │ /api/* proxy
                     users  │              │ (session-guarded)
                            ▼              ▼
                    ┌──────────────┐  ┌─────────────────────────────┐
                    │  MongoDB 7   │  │ Prediction service (FastAPI)│
                    └──────────────┘  │  RandomForest + XGBoost     │
                                      │  OR-Tools TSP               │
                                      │  2,550-sector index         │
                                      └─────────────────────────────┘
```

The browser never calls the prediction service directly. Every model request goes through
`/api/*` on the portal, so it inherits the session guard, a timeout and consistent error
shapes.

---

## Quick start

### Docker Compose (recommended)

```bash
cp .env.example .env
# Set SESSION_SECRET in .env — compose refuses to start without it.
docker compose up -d --build
```

| Service | URL |
|---|---|
| Portal | http://localhost:3001 |
| Prediction service | http://localhost:8000 |
| Interactive API docs | http://localhost:8000/docs |
| MongoDB | `localhost:27018` |

Compose waits on each container's health check, so the portal will not start before
MongoDB and the models are ready.

```bash
docker compose logs -f      # follow logs
docker compose down         # stop
docker compose down -v      # stop and drop the database volume
```

### Running locally without Docker

Three terminals:

```bash
# 1 — MongoDB
mongod --dbpath /your/data/path

# 2 — prediction service
cd ml-service
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --reload --port 8000

# 3 — portal
npm install
cp .env.example .env         # then edit it
npm run dev
```

Open http://localhost:3000, create an account, and you land on the dashboard.

---

## The models

Two estimators, both trained on 18 features describing a sector (a 0.005° grid cell).

| | Hotspot classifier | Volume regressor |
|---|---|---|
| Algorithm | `RandomForestClassifier` | `XGBRegressor` |
| Size | 200 trees, max depth 5 | 500 rounds, max depth 3 |
| Class handling | `class_weight="balanced"` | `objective="reg:squarederror"` |
| Output | P(sector is a hotspot) | Expected incident count |
| Reported at training | ROC-AUC **0.9902** | R² **0.9324**, MAE **5.76** |

> The training scores are the figures recorded when the models were fitted. The service
> reports them as provenance and **cannot recompute them** — the held-out evaluation split
> is not shipped with the repository. They are labelled that way in the UI too.

What the service *can* verify, and does on every start-up, is that modelled output tracks
the incident record across all 2,550 sectors:

| Check | Value |
|---|---|
| Hotspot probability vs. recorded incidents (Pearson) | **0.83** |
| Predicted volume vs. recorded incidents (Pearson) | **0.92** |

Live at `GET /risk-distribution`.

### How a prediction is assembled

Latitude and longitude alone are **not** enough to drive these models — together they carry
about 1.3% of the classifier's decision weight. The real signal is a sector's activity
history:

| Feature | Classifier importance |
|---|---|
| `peak_hour_crimes` | 34.0% |
| `neighbor_avg_crimes` | 14.0% |
| `crimes_2yr_ago` | 13.5% |
| `unique_types` | 12.2% |
| `crimes_last_year` | 9.6% |
| `neighbor_max_crimes` | 8.0% |
| *(remaining 12 features)* | 8.7% |

So `ml-service/features.py` builds each feature vector from the incident record where it
can, and labels every value with its source:

| Source | Meaning | Features |
|---|---|---|
| `observed` | Measured from `data/sector_incidents.csv` | `lat_bin`, `lon_bin`, `neighbor_avg_crimes`, `neighbor_max_crimes` |
| `derived` | Computed from observed values | `peak_hour_crimes` = sector total × `peak_hour_ratio` |
| `default` | City-wide median, overridable per request | the remaining rate and history features |
| `override` | Supplied by the caller | any of the 18 |

Every `/predict` response returns the complete `features_used` map alongside
`feature_provenance`, and the dashboard renders both — so it is always visible what was
measured and what was assumed.

### Guard rails

- **Counts are clipped at zero.** Gradient boosting extrapolates linearly outside its
  training range, and a negative crime count is meaningless. When clipping kicks in, the
  response says so in `notes`.
- **Out-of-range inputs are flagged, and the two ends are treated differently.** The models'
  decision thresholds for `peak_hour_crimes` span roughly 33–1,762. *Below* that floor they
  never learned to discriminate and the regressor extrapolates into negative counts, so the
  response is marked `confidence: "low"`. *Above* the ceiling the classifier saturates
  rather than failing — every sector that busy classifies the same way — so the
  classification stands and only the count carries `volume_extrapolated: true`.
- **Feature order is checked at start-up.** If either model's `feature_names_in_` stops
  matching the service's `FEATURES` list, the service refuses to start instead of serving
  silently misaligned predictions.
- **Library versions are pinned to the ones the models were saved with**
  (`scikit-learn==1.6.1`, `xgboost==2.0.3`). Loading a pickle under a different minor
  release is not guaranteed to reproduce the same predictions.

### Limitations

- Output describes historical incident concentration by area. It says nothing about
  individuals, and nothing about intent.
- Recorded crime data reflects reporting and enforcement patterns as much as underlying
  activity. Sectors with heavier historical enforcement carry heavier recorded counts.
- The training metrics cannot be reproduced from this repository.
- Sectors outside the mapped grid have no incident history and are scored at low
  confidence.

---

## API reference

### Portal (`http://localhost:3001`)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/health` | — | Portal status plus MongoDB and model-service reachability |
| `GET` | `/login`, `/signup` | guest | Auth pages |
| `POST` | `/login`, `/signup` | guest | Credentials |
| `GET` | `/auth/google` | guest | OAuth start |
| `POST` | `/logout` | — | End session |
| `GET` | `/dashboard` | session | Operations dashboard |
| `GET` | `/density-map` | session | Incident density map |
| `*` | `/api/*` | session | Proxy to the prediction service |

### Prediction service (`http://localhost:8000`)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness and sector count |
| `GET` | `/stats` | Dataset totals, operational counts, model hyperparameters |
| `GET` | `/model-card` | Feature importances, defaults, limitations |
| `GET` | `/hotspots?limit=&min_probability=` | Sectors ranked by risk |
| `GET` | `/grid?min_incidents=` | Observed and modelled values for every sector |
| `GET` | `/risk-distribution` | Risk histogram and agreement with the record |
| `POST` | `/predict` | Score one sector |
| `POST` | `/predict/batch` | Score up to 500 sectors |
| `POST` | `/optimize-route` | TSP over supplied points or the top *N* hotspots |
| `GET` | `/docs` | Interactive OpenAPI docs |

**Example — assess a sector**

```bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"lat": 41.885, "lon": -87.63}'
```

```json
{
  "sector_id": "CHI-41885-87630",
  "is_hotspot": true,
  "hotspot_probability": 0.8776,
  "risk_level": "HIGH",
  "predicted_incidents": 5452,
  "observed_incidents": 24690,
  "confidence": "normal",
  "volume_extrapolated": true,
  "feature_provenance": { "neighbor_avg_crimes": "observed", "peak_hour_crimes": "derived", "...": "..." },
  "notes": ["Peak-hour volume (7950) is above the busiest decision threshold in the models (1762). The hotspot classification is reliable - every sector this busy classifies the same way - but the incident-count estimate is extrapolated."]
}
```

Any of the 18 features can be supplied to override what the service would otherwise assume:

```bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"lat": 41.885, "lon": -87.63, "peak_hour_crimes": 1200, "crimes_last_year": 18}'
```

**Example — plan a patrol**

```bash
curl -X POST http://localhost:8000/optimize-route \
  -H "Content-Type: application/json" \
  -d '{"from_hotspots": 8, "round_trip": true}'
```

Returns the visiting order, per-stop cumulative distance and the total route length in km.

---

## Tests

```bash
npm test                                  # 33 tests — models, middleware, HTTP routes
cd ml-service && python -m pytest tests   # 25 tests — features, predictions, routing
```

The Node HTTP tests run against the real Express app with
`SESSION_STORE=memory`, so they need no database.

The Python tests load the real models and the real sector data. Several exist specifically
to catch regressions in the feature pipeline:

- predicted counts are never negative, for any sector
- a busy sector always outranks a quiet one
- raising `peak_hour_crimes` raises both outputs — proving the models are not being fed
  constants
- predictions genuinely differ across the city
- an optimised route is never longer than visiting the same points in the given order

---

## Project structure

```
.
├── app.js                        Express entry point
├── config/passport.js            Local + Google strategies
├── middleware/auth.js            Route guards
├── models/User.js                Mongoose user schema (bcrypt hashing)
├── routes/
│   ├── auth.js                   Signup, login, OAuth, logout
│   └── api.js                    Guarded proxy to the prediction service
├── views/                        login, signup, dashboard, error (EJS)
├── public/css/portal.css         Shared styles
├── dashboard/
│   └── incident_density_map.html Pre-rendered density map
├── tests/                        Node test suites
├── ml-service/
│   ├── app.py                    FastAPI service
│   ├── features.py               Feature construction and provenance
│   ├── data/sector_incidents.csv 2,550 sectors, 5,737,862 incidents
│   ├── hotspot_classifier.pkl    RandomForestClassifier
│   ├── crime_count_regressor.pkl XGBRegressor
│   ├── tests/                    pytest suite
│   └── Dockerfile
├── Dockerfile
├── docker-compose.yml
├── Jenkinsfile
└── .env.example
```

---

## Configuration

Copy `.env.example` to `.env`.

| Variable | Purpose | Default |
|---|---|---|
| `PORT` | Portal port | `3000` |
| `NODE_ENV` | Environment | `development` |
| `SESSION_SECRET` | Session signing key — **required in production** | none |
| `MONGODB_URI` | Connection string | `mongodb://localhost:27017/smart-policing-portal` |
| `MODEL_API_URL` | Prediction service base URL | `http://localhost:8000` |
| `MODEL_TIMEOUT_MS` | Upstream timeout | `15000` |
| `SESSION_STORE` | `memory` to run without MongoDB — **tests only** | Mongo-backed |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | OAuth credentials; omit to hide the button | empty |
| `GOOGLE_CALLBACK_URL` | OAuth redirect path | `/auth/google/callback` |

The app refuses to start in production without `SESSION_SECRET`. Generate one with:

```bash
node -e "console.log(require('crypto').randomBytes(48).toString('hex'))"
```

### Google OAuth setup

1. [Google Cloud Console](https://console.cloud.google.com/apis/credentials) → **Create
   Credentials → OAuth 2.0 Client ID**.
2. Authorised redirect URI: `http://localhost:3000/auth/google/callback`
   (`http://localhost:3001/...` when running under Compose).
3. Put the client ID and secret in `.env`.

Leave them blank and the Google button simply does not render.

---

## CI/CD

`Jenkinsfile` defines a pipeline that runs on Windows or Unix agents — every shell step
dispatches through `isUnix()`.

1. **Checkout**
2. **Install** — npm and pip, in parallel
3. **Test** — Node and Python suites, in parallel
4. **Build images** — `docker compose build`
5. **Deploy** — `docker compose up -d`
6. **Smoke test** — health, `/stats` and a live prediction, with retries

On failure the pipeline dumps the last 100 lines of container logs.

Requires three Jenkins credentials: `spp-session-secret`, `google-client-id`,
`google-client-secret`.

---

## Security notes

- Passwords hashed with bcrypt at 12 rounds; the hash is stripped from JSON output.
- Sessions stored server-side in MongoDB, `httpOnly` + `sameSite=lax` cookies, `secure` in
  production, 24-hour TTL.
- Failed sign-in returns one message whether the email is unknown or the password is wrong,
  so the form cannot be used to enumerate accounts.
- Both containers run as non-root users.
- `x-powered-by` disabled; request bodies size-capped.
- Signing out destroys the session server-side and clears the cookie.

---

## Data

Sector incident totals live in `ml-service/data/sector_incidents.csv` — 2,550 rows of
`lat_bin, lon_bin, observed_incidents` on a 0.005° grid covering 41.645–42.025 N,
87.900–87.525 W, totalling 5,737,862 incidents. This is the aggregate the models were built
from, and the single source for every figure the dashboard shows.

---

## License

ISC
