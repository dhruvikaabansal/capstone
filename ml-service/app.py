"""
Smart Policing Portal - prediction and route-optimisation service.

Serves two trained models over the city sector grid:

  hotspot_classifier.pkl    RandomForestClassifier  - probability a sector is a hotspot
  crime_count_regressor.pkl XGBRegressor            - expected incident volume

Every response reports the provenance of the features behind it, so an operator
can tell measured input from assumed input.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

import joblib
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from features import (
    CITY_MEDIAN_PROFILE,
    FEATURES,
    GRID_STEP,
    PEAK_HOUR_TRAINED_RANGE,
    SectorIndex,
    build_feature_vector,
    haversine_km,
    sector_id,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger("ml-service")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOTSPOT_THRESHOLD = 0.5
HIGH_RISK_THRESHOLD = 0.75

# Metrics recorded when the models were trained. Reported as provenance, not as
# a live measurement - the held-out split they were computed on is not shipped
# with the service, so the service cannot recompute them.
TRAINING_METRICS = {
    "classifier": {"metric": "ROC-AUC", "value": 0.9902},
    "regressor": {"metric": "R2", "value": 0.9324, "mae": 5.76},
    "measured_at": "training time, on the held-out split",
}

state: Dict[str, object] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    state["clf"] = joblib.load(os.path.join(BASE_DIR, "hotspot_classifier.pkl"))
    state["reg"] = joblib.load(os.path.join(BASE_DIR, "crime_count_regressor.pkl"))
    state["index"] = SectorIndex()

    # Fail fast on a model/feature mismatch rather than serving silent nonsense.
    for name, model in (("classifier", state["clf"]), ("regressor", state["reg"])):
        expected = list(getattr(model, "feature_names_in_", FEATURES))
        if expected != FEATURES:
            raise RuntimeError(f"{name} feature order does not match FEATURES: {expected}")

    log.info("Loaded models over %d sectors (%d incidents)",
             len(state["index"]), state["index"].total_incidents)
    yield
    state.clear()


app = FastAPI(
    title="Smart Policing Portal - Prediction Service",
    description="Hotspot classification, incident-volume regression and patrol route optimisation.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ----------------------------------------------------------------------------
# Schemas
# ----------------------------------------------------------------------------

class PredictRequest(BaseModel):
    """A sector to assess. Any of the 18 model features may be supplied to
    override the observed or default value used for it."""

    lat: float = Field(..., ge=-90, le=90, description="Latitude of the sector")
    lon: float = Field(..., ge=-180, le=180, description="Longitude of the sector")

    arrest_rate: Optional[float] = Field(None, ge=0, le=1)
    domestic_rate: Optional[float] = Field(None, ge=0, le=1)
    avg_hour: Optional[float] = Field(None, ge=0, le=23)
    unique_types: Optional[float] = Field(None, ge=0)
    dominant_crime_type: Optional[float] = None
    avg_dayofweek: Optional[float] = Field(None, ge=0, le=6)
    avg_month: Optional[float] = Field(None, ge=1, le=12)
    district_median: Optional[float] = Field(None, ge=0)
    beat_median: Optional[float] = Field(None, ge=0)
    crimes_last_year: Optional[float] = Field(None, ge=0)
    crimes_2yr_ago: Optional[float] = Field(None, ge=0)
    year_over_year_change: Optional[float] = None
    peak_hour_crimes: Optional[float] = Field(None, ge=0)
    peak_hour_ratio: Optional[float] = Field(None, ge=0, le=1)
    neighbor_avg_crimes: Optional[float] = Field(None, ge=0)
    neighbor_max_crimes: Optional[float] = Field(None, ge=0)


class BatchPredictRequest(BaseModel):
    sectors: List[PredictRequest] = Field(..., min_length=1, max_length=500)


class Point(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


class RouteRequest(BaseModel):
    points: List[Point] = Field(default_factory=list, max_length=60)
    from_hotspots: int = Field(
        0, ge=0, le=30,
        description="Instead of supplying points, route over the N highest-risk sectors.")
    round_trip: bool = Field(True, description="Return to the starting sector.")


# ----------------------------------------------------------------------------
# Core scoring
# ----------------------------------------------------------------------------

def _risk_level(prob: float) -> str:
    if prob >= HIGH_RISK_THRESHOLD:
        return "HIGH"
    if prob >= HOTSPOT_THRESHOLD:
        return "MEDIUM"
    return "LOW"


def _score_frame(frame: pd.DataFrame):
    """Run both models over a feature frame. Counts are clipped at zero: the
    regressor targets a count, and gradient boosting can extrapolate below the
    range of values it saw in training."""
    probs = state["clf"].predict_proba(frame[FEATURES])[:, 1]
    raw = state["reg"].predict(frame[FEATURES])
    return probs, np.clip(raw, 0.0, None), raw


def score_sector(lat: float, lon: float, overrides: Optional[Dict[str, float]] = None) -> dict:
    index: SectorIndex = state["index"]
    features, provenance, context = build_feature_vector(index, lat, lon, overrides)

    probs, counts, raw = _score_frame(pd.DataFrame([features]))
    prob, count = float(probs[0]), float(counts[0])

    low, high = PEAK_HOUR_TRAINED_RANGE
    peak = features["peak_hour_crimes"]

    notes = []
    if not context["in_coverage"]:
        notes.append("Point lies outside the mapped sector grid; no incident history is available for it.")
    if context["below_trained_range"]:
        notes.append(
            f"Peak-hour volume ({peak:.0f}) sits below the quietest level the models learned to "
            f"discriminate ({low:.0f}). Treat this assessment as indicative only."
        )
    if context["above_trained_range"]:
        notes.append(
            f"Peak-hour volume ({peak:.0f}) is above the busiest decision threshold in the models "
            f"({high:.0f}). The hotspot classification is reliable - every sector this busy "
            f"classifies the same way - but the incident-count estimate is extrapolated."
        )
    if raw[0] < 0:
        notes.append("Raw regressor output was negative and has been clipped to zero.")

    # Only the quiet end and missing coverage undermine the classification; the
    # busy end saturates rather than failing.
    unreliable = context["below_trained_range"] or not context["in_coverage"]

    return {
        "sector_id": context["sector_id"],
        "lat": context["lat"],
        "lon": context["lon"],
        "is_hotspot": bool(prob >= HOTSPOT_THRESHOLD),
        "hotspot_probability": round(prob, 4),
        "risk_level": _risk_level(prob),
        "predicted_incidents": int(round(count)),
        "observed_incidents": context["observed_incidents"],
        "confidence": "low" if unreliable else "normal",
        "volume_extrapolated": bool(context["above_trained_range"]),
        "features_used": {k: round(float(v), 4) for k, v in features.items()},
        "feature_provenance": provenance,
        "notes": notes,
    }


def _score_grid():
    """Score every sector once and cache the result."""
    index: SectorIndex = state["index"]
    sectors = list(index.sectors())
    rows = [build_feature_vector(index, lat, lon)[0] for lat, lon, _ in sectors]
    probs, counts, _ = _score_frame(pd.DataFrame(rows))
    state["grid_sectors"] = sectors
    state["grid_scores"] = (probs, counts)
    return sectors, probs, counts


def _ensure_grid():
    if "grid_scores" not in state:
        _score_grid()
    return state["grid_sectors"], state["grid_scores"]


# ----------------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------------

@app.get("/", tags=["meta"])
def root():
    return {
        "service": "Smart Policing Portal - Prediction Service",
        "version": app.version,
        "docs": "/docs",
    }


@app.get("/health", tags=["meta"])
def health():
    if not all(k in state for k in ("clf", "reg", "index")):
        raise HTTPException(status_code=503, detail="Models not loaded")
    return {
        "status": "healthy",
        "models_loaded": True,
        "sectors_indexed": len(state["index"]),
    }


@app.get("/stats", tags=["meta"])
def stats():
    """Dataset and model facts, read off the shipped sector data and the trained
    estimators themselves."""
    index: SectorIndex = state["index"]
    clf, reg = state["clf"], state["reg"]
    _, (probs, counts) = _ensure_grid()

    totals = np.array([t for _, _, t in index.sectors()], dtype=float)

    return {
        "dataset": {
            "total_incidents": index.total_incidents,
            "sectors": index.sector_count,
            "grid_resolution_degrees": GRID_STEP,
            "bounds": index.bounds,
            "busiest_sector_incidents": int(totals.max()),
            "median_sector_incidents": int(np.median(totals)),
        },
        "operational": {
            "hotspots_flagged": int((probs >= HOTSPOT_THRESHOLD).sum()),
            "high_risk_sectors": int((probs >= HIGH_RISK_THRESHOLD).sum()),
            "share_of_city_flagged": round(float((probs >= HOTSPOT_THRESHOLD).mean()), 4),
            "predicted_incidents_total": int(counts.sum()),
        },
        "models": {
            "classifier": {
                "algorithm": type(clf).__name__,
                "n_estimators": int(getattr(clf, "n_estimators", 0)),
                "max_depth": getattr(clf, "max_depth", None),
                "class_weight": str(clf.get_params().get("class_weight")),
                "n_features": int(getattr(clf, "n_features_in_", len(FEATURES))),
            },
            "regressor": {
                "algorithm": type(reg).__name__,
                "n_estimators": int(reg.get_params().get("n_estimators") or 0),
                "max_depth": reg.get_params().get("max_depth"),
                "objective": reg.get_params().get("objective"),
                "n_features": int(getattr(reg, "n_features_in_", len(FEATURES))),
            },
            "training_metrics": TRAINING_METRICS,
        },
    }


@app.get("/model-card", tags=["meta"])
def model_card():
    """Feature importances and the assumptions this service makes."""
    clf, reg = state["clf"], state["reg"]

    def ranked(model):
        pairs = sorted(zip(FEATURES, model.feature_importances_), key=lambda p: -p[1])
        return [{"feature": f, "importance": round(float(v), 5)} for f, v in pairs]

    return {
        "features": FEATURES,
        "classifier_importances": ranked(clf),
        "regressor_importances": ranked(reg),
        "defaults": {
            "description": (
                "Features that cannot be recovered from the shipped sector totals fall back to "
                "these city-wide central values. Any of them can be overridden per request."
            ),
            "values": CITY_MEDIAN_PROFILE,
        },
        "derived": {
            "peak_hour_crimes": "observed sector incidents x peak_hour_ratio",
            "neighbor_avg_crimes": "mean observed incidents of the 8 adjacent sectors",
            "neighbor_max_crimes": "max observed incidents of the 8 adjacent sectors",
        },
        "trained_range": {"peak_hour_crimes": list(PEAK_HOUR_TRAINED_RANGE)},
        "limitations": [
            "Predictions describe historical incident concentration, not individuals or intent.",
            "Reported training metrics cannot be recomputed here; the evaluation split is not shipped.",
            "Sectors outside the mapped grid have no incident history and are scored at low confidence.",
        ],
    }


def _top_hotspots(limit: int = 25, min_probability: float = 0.0) -> List[dict]:
    """Sectors ranked by modelled hotspot probability. Plain helper so other
    endpoints can reuse it without going through FastAPI's parameter binding."""
    sectors, (probs, counts) = _ensure_grid()

    out = []
    for i in np.argsort(probs)[::-1]:
        if probs[i] < min_probability:
            break
        lat, lon, observed = sectors[i]
        out.append({
            "sector_id": sector_id(lat, lon),
            "lat": lat,
            "lon": lon,
            "hotspot_probability": round(float(probs[i]), 4),
            "risk_level": _risk_level(float(probs[i])),
            "predicted_incidents": int(round(float(counts[i]))),
            "observed_incidents": int(observed),
        })
        if len(out) >= limit:
            break
    return out


@app.get("/hotspots", tags=["prediction"])
def hotspots(limit: int = Query(25, ge=1, le=500),
             min_probability: float = Query(0.0, ge=0.0, le=1.0)):
    """Sectors ranked by modelled hotspot probability."""
    out = _top_hotspots(limit, min_probability)
    return {"count": len(out), "hotspots": out}


@app.get("/grid", tags=["prediction"])
def grid(min_incidents: int = Query(0, ge=0)):
    """Observed incident totals and modelled risk for every sector."""
    sectors, (probs, counts) = _ensure_grid()
    cells = [
        {"lat": lat, "lon": lon, "observed": int(observed),
         "probability": round(float(probs[i]), 4),
         "predicted": int(round(float(counts[i])))}
        for i, (lat, lon, observed) in enumerate(sectors)
        if observed >= min_incidents
    ]
    return {"count": len(cells), "grid_step": GRID_STEP, "cells": cells}


@app.get("/risk-distribution", tags=["prediction"])
def risk_distribution():
    """How modelled risk is spread across the city, and how closely the modelled
    volume tracks what was actually recorded."""
    sectors, (probs, counts) = _ensure_grid()
    observed = np.array([s[2] for s in sectors], dtype=float)

    edges = np.arange(0.0, 1.01, 0.1)
    hist, _ = np.histogram(probs, bins=edges)

    return {
        "histogram": [
            {"range": f"{edges[i]:.1f}-{edges[i + 1]:.1f}", "sectors": int(hist[i])}
            for i in range(len(hist))
        ],
        "risk_bands": {
            "HIGH": int((probs >= HIGH_RISK_THRESHOLD).sum()),
            "MEDIUM": int(((probs >= HOTSPOT_THRESHOLD) & (probs < HIGH_RISK_THRESHOLD)).sum()),
            "LOW": int((probs < HOTSPOT_THRESHOLD).sum()),
        },
        "agreement_with_record": {
            "description": "Pearson correlation between modelled output and recorded incident totals across all sectors.",
            "hotspot_probability_vs_observed": round(float(np.corrcoef(observed, probs)[0, 1]), 4),
            "predicted_volume_vs_observed": round(float(np.corrcoef(observed, counts)[0, 1]), 4),
        },
    }


@app.post("/predict", tags=["prediction"])
def predict(req: PredictRequest):
    return score_sector(req.lat, req.lon, req.model_dump(exclude={"lat", "lon"}, exclude_none=True))


@app.post("/predict/batch", tags=["prediction"])
def predict_batch(req: BatchPredictRequest):
    results = [
        score_sector(s.lat, s.lon, s.model_dump(exclude={"lat", "lon"}, exclude_none=True))
        for s in req.sectors
    ]
    return {"count": len(results), "results": results}


@app.post("/optimize-route", tags=["routing"])
def optimize_route(req: RouteRequest):
    """Shortest patrol sequence through a set of sectors, solved as a TSP with
    Google OR-Tools over great-circle distances."""
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    points = [{"lat": p.lat, "lon": p.lon} for p in req.points]

    if req.from_hotspots:
        points = [{"lat": h["lat"], "lon": h["lon"]}
                  for h in _top_hotspots(limit=req.from_hotspots)]

    if len(points) < 2:
        raise HTTPException(
            status_code=400,
            detail="Supply at least two points, or set from_hotspots to route over the top sectors.",
        )

    n = len(points)
    matrix = [
        [0 if i == j else int(haversine_km(points[i]["lat"], points[i]["lon"],
                                           points[j]["lat"], points[j]["lon"]) * 1000)
         for j in range(n)]
        for i in range(n)
    ]

    manager = pywrapcp.RoutingIndexManager(n, 1, 0)
    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(from_index, to_index):
        return matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]

    transit = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(3)

    solution = routing.SolveWithParameters(params)
    if not solution:
        raise HTTPException(status_code=422, detail="No feasible route found for the supplied points.")

    node = routing.Start(0)
    order: List[int] = []
    while not routing.IsEnd(node):
        order.append(manager.IndexToNode(node))
        node = solution.Value(routing.NextVar(node))

    index: SectorIndex = state["index"]
    legs_km = 0.0
    stops = []
    for position, i in enumerate(order):
        pt = points[i]
        if position > 0:
            prev = points[order[position - 1]]
            legs_km += haversine_km(prev["lat"], prev["lon"], pt["lat"], pt["lon"])
        stops.append({
            "stop": position + 1,
            "sector_id": sector_id(pt["lat"], pt["lon"]),
            "lat": pt["lat"],
            "lon": pt["lon"],
            "observed_incidents": int(index.observed(pt["lat"], pt["lon"])),
            "cumulative_km": round(legs_km, 2),
        })

    return_leg = 0.0
    if req.round_trip and len(order) > 1:
        first, last = points[order[0]], points[order[-1]]
        return_leg = haversine_km(last["lat"], last["lon"], first["lat"], first["lon"])

    return {
        "stops": stops,
        "route": [{"lat": s["lat"], "lon": s["lon"]} for s in stops],
        "total_distance_km": round(legs_km + return_leg, 2),
        "return_leg_km": round(return_leg, 2),
        "round_trip": req.round_trip,
        "solver": "OR-Tools TSP, guided local search",
    }
