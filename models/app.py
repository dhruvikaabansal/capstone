from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import joblib
import numpy as np
import pandas as pd
import math
from typing import List
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp

# ── single instance only ─────────────────────────────────────
app = FastAPI(title="Chicago Crime Prediction API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load models
clf = joblib.load("hotspot_classifier.pkl")
reg = joblib.load("crime_count_regressor.pkl")

FEATURES = [
    "lat_bin", "lon_bin",
    "arrest_rate", "domestic_rate",
    "avg_hour", "unique_types",
    "dominant_crime_type",
    "avg_dayofweek", "avg_month",
    "district_median", "beat_median",
    "crimes_last_year", "crimes_2yr_ago",
    "year_over_year_change",
    "peak_hour_crimes", "peak_hour_ratio",
    "neighbor_avg_crimes", "neighbor_max_crimes",
]

class PredictRequest(BaseModel):
    lat_bin: float
    lon_bin: float
    arrest_rate: float = 0.28
    domestic_rate: float = 0.18
    avg_hour: float = 12.0
    unique_types: float = 5.0
    dominant_crime_type: float = 0.0
    avg_dayofweek: float = 3.0
    avg_month: float = 6.0
    district_median: float = 10.0
    beat_median: float = 1000.0
    crimes_last_year: float = 100.0
    crimes_2yr_ago: float = 110.0
    year_over_year_change: float = -10.0
    peak_hour_crimes: float = 30.0
    peak_hour_ratio: float = 0.3
    neighbor_avg_crimes: float = 200.0
    neighbor_max_crimes: float = 500.0

@app.get("/")
def root():
    return {"status": "Chicago Crime Prediction API is running"}

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.post("/predict")
def predict(req: PredictRequest):
    data = pd.DataFrame([req.dict()])[FEATURES]
    hotspot_prob  = clf.predict_proba(data)[0][1]
    crime_count   = reg.predict(data)[0]
    is_hotspot    = bool(hotspot_prob >= 0.5)
    return {
        "lat"             : req.lat_bin,
        "lon"             : req.lon_bin,
        "is_hotspot"      : is_hotspot,
        "hotspot_prob"    : round(float(hotspot_prob), 4),
        "predicted_crimes": round(float(crime_count)),
        "risk_level"      : "HIGH"   if hotspot_prob >= 0.75
                            else "MEDIUM" if hotspot_prob >= 0.5
                            else "LOW"
    }

@app.get("/stats")
def stats():
    return {
        "model"  : "Random Forest + XGBoost",
        "roc_auc": 0.9902,
        "r2"     : 0.9324,
        "mae"    : 5.76,
    }

class Point(BaseModel):
    lat: float
    lon: float

class RouteRequest(BaseModel):
    points: List[Point]

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0 # Earth radius in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

@app.post("/optimize-route")
def optimize_route(req: RouteRequest):
    if len(req.points) < 2:
        return {"route": [p.dict() for p in req.points]}
    
    num_locations = len(req.points)
    distance_matrix = []
    for i in range(num_locations):
        row = []
        for j in range(num_locations):
            if i == j:
                row.append(0)
            else:
                dist = haversine(req.points[i].lat, req.points[i].lon, req.points[j].lat, req.points[j].lon)
                row.append(int(dist * 1000)) # OR-Tools uses integers
        distance_matrix.append(row)
        
    manager = pywrapcp.RoutingIndexManager(num_locations, 1, 0)
    routing = pywrapcp.RoutingModel(manager)
    
    def distance_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return distance_matrix[from_node][to_node]
        
    transit_callback_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)
    
    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    
    solution = routing.SolveWithParameters(search_parameters)
    
    if solution:
        index = routing.Start(0)
        optimized_route = []
        while not routing.IsEnd(index):
            node_index = manager.IndexToNode(index)
            optimized_route.append(req.points[node_index].dict())
            index = solution.Value(routing.NextVar(index))
        return {"route": optimized_route}
    else:
        return {"error": "Could not find an optimal route."}