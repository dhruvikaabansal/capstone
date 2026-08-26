"""
Feature construction for the Smart Policing Portal prediction service.

WHY THIS MODULE EXISTS
----------------------
Both trained models expect 18 features describing a *sector* (a 0.005-degree
lat/lon grid cell). A latitude and longitude on their own are not enough:
together `lat_bin` and `lon_bin` account for only ~1.3% of the classifier's
decision weight. The dominant signals are activity-history features such as
`peak_hour_crimes` (34%), `neighbor_avg_crimes` (14%) and `crimes_2yr_ago` (13%).

Feeding the models placeholder values for those features pushes the inputs far
outside the range they were trained on, and gradient-boosted trees extrapolate
badly there - the regressor returns large negative incident counts, which is
nonsense for a count target.

This module builds an in-distribution, largely observation-backed feature vector
for any point in the city. Every field is tagged with its provenance so the API
can report exactly what was measured and what was assumed.

PROVENANCE OF EACH FEATURE
--------------------------
observed   - computed directly from data/sector_incidents.csv, the per-sector
             incident totals for the 5,737,862 records the models were built on.
derived    - computed from observed values via a definitional identity.
default    - a city-wide central value. Callers may override any of these.
"""

from __future__ import annotations

import csv
import math
import os
from typing import Dict, Iterable, List, Optional, Tuple

# The grid resolution the sector features were aggregated at.
GRID_STEP = 0.005

# Model input order. Must match `feature_names_in_` on both estimators.
FEATURES: List[str] = [
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

# City-wide central values for the features that cannot be recovered from the
# sector totals alone. These are the medians of the model's own decision
# thresholds, so a request that does not override them stays inside the region
# of feature space the models were fitted on.
CITY_MEDIAN_PROFILE: Dict[str, float] = {
    "arrest_rate": 0.262,
    "domestic_rate": 0.133,
    "avg_hour": 13.05,
    "unique_types": 23.5,
    "dominant_crime_type": 4.0,
    "avg_dayofweek": 2.96,
    "avg_month": 6.61,
    "district_median": 11.0,
    "beat_median": 1292.0,
    "crimes_last_year": 10.5,
    "crimes_2yr_ago": 18.5,
    "year_over_year_change": -7.5,
    "peak_hour_ratio": 0.322,
}

# Range of `peak_hour_crimes` covered by the models' decision thresholds. The
# two ends mean different things and are handled differently:
#
#   below the floor - the models never learned to discriminate this quiet, and
#       the regressor extrapolates linearly downward into negative counts. The
#       assessment is genuinely unreliable, so it is marked low confidence.
#
#   above the ceiling - no split sits above this point because every sector that
#       busy is already classified the same way; the classifier saturates rather
#       than failing. The classification stands, but the volume estimate is an
#       extrapolation and is noted as such.
PEAK_HOUR_TRAINED_RANGE: Tuple[float, float] = (33.0, 1762.0)

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sector_incidents.csv")


def snap(value: float) -> float:
    """Snap a coordinate to the centre of its grid cell."""
    return round(round(value / GRID_STEP) * GRID_STEP, 4)


def _key(lat: float, lon: float) -> Tuple[int, int]:
    return (int(round(lat / GRID_STEP)), int(round(lon / GRID_STEP)))


class SectorIndex:
    """Observed incident totals per sector, with neighbourhood lookups."""

    def __init__(self, path: str = DATA_FILE):
        self.path = path
        self._cells: Dict[Tuple[int, int], float] = {}
        with open(path, newline="") as handle:
            for row in csv.DictReader(handle):
                lat = float(row["lat_bin"])
                lon = float(row["lon_bin"])
                self._cells[_key(lat, lon)] = float(row["observed_incidents"])

        if not self._cells:
            raise ValueError(f"No sector rows loaded from {path}")

        lats = [k[0] * GRID_STEP for k in self._cells]
        lons = [k[1] * GRID_STEP for k in self._cells]
        self.bounds = {
            "lat_min": round(min(lats), 4), "lat_max": round(max(lats), 4),
            "lon_min": round(min(lons), 4), "lon_max": round(max(lons), 4),
        }
        self.total_incidents = int(sum(self._cells.values()))
        self.sector_count = len(self._cells)

    def __len__(self) -> int:
        return self.sector_count

    def sectors(self) -> Iterable[Tuple[float, float, float]]:
        for (la, lo), total in self._cells.items():
            yield (round(la * GRID_STEP, 4), round(lo * GRID_STEP, 4), total)

    def observed(self, lat: float, lon: float) -> float:
        return self._cells.get(_key(lat, lon), 0.0)

    def in_coverage(self, lat: float, lon: float) -> bool:
        b = self.bounds
        return (b["lat_min"] - GRID_STEP <= lat <= b["lat_max"] + GRID_STEP
                and b["lon_min"] - GRID_STEP <= lon <= b["lon_max"] + GRID_STEP)

    def neighbours(self, lat: float, lon: float) -> List[float]:
        """Totals for the eight sectors surrounding this one."""
        la, lo = _key(lat, lon)
        return [
            self._cells.get((la + i, lo + j), 0.0)
            for i in (-1, 0, 1)
            for j in (-1, 0, 1)
            if not (i == 0 and j == 0)
        ]


def build_feature_vector(
    index: SectorIndex,
    lat: float,
    lon: float,
    overrides: Optional[Dict[str, float]] = None,
) -> Tuple[Dict[str, float], Dict[str, str], Dict[str, object]]:
    """
    Assemble the 18-feature vector for one sector.

    Returns (features, provenance, context) where `provenance` maps each feature
    name to "observed", "derived", "default" or "override", and `context`
    carries the observed figures worth showing to an operator.
    """
    overrides = {k: v for k, v in (overrides or {}).items() if v is not None}

    lat_b, lon_b = snap(lat), snap(lon)
    observed_total = index.observed(lat_b, lon_b)
    neighbour_totals = index.neighbours(lat_b, lon_b)

    features: Dict[str, float] = dict(CITY_MEDIAN_PROFILE)
    provenance: Dict[str, str] = {k: "default" for k in CITY_MEDIAN_PROFILE}

    features["lat_bin"] = lat_b
    features["lon_bin"] = lon_b
    provenance["lat_bin"] = provenance["lon_bin"] = "observed"

    features["neighbor_avg_crimes"] = round(sum(neighbour_totals) / len(neighbour_totals), 3)
    features["neighbor_max_crimes"] = float(max(neighbour_totals))
    provenance["neighbor_avg_crimes"] = provenance["neighbor_max_crimes"] = "observed"

    # Apply caller overrides before deriving peak_hour_crimes, so an explicit
    # peak_hour_ratio is respected.
    for name, value in overrides.items():
        if name in FEATURES:
            features[name] = float(value)
            provenance[name] = "override"

    # peak_hour_crimes is definitionally the sector's total incident count times
    # the share of those incidents falling in its busiest hour of the day.
    if "peak_hour_crimes" in overrides:
        features["peak_hour_crimes"] = float(overrides["peak_hour_crimes"])
        provenance["peak_hour_crimes"] = "override"
    else:
        features["peak_hour_crimes"] = round(observed_total * features["peak_hour_ratio"], 3)
        provenance["peak_hour_crimes"] = "derived"

    low, high = PEAK_HOUR_TRAINED_RANGE
    peak = features["peak_hour_crimes"]

    context = {
        "sector_id": sector_id(lat_b, lon_b),
        "lat": lat_b,
        "lon": lon_b,
        "observed_incidents": int(observed_total),
        "neighbour_avg_incidents": round(features["neighbor_avg_crimes"], 1),
        "neighbour_max_incidents": int(features["neighbor_max_crimes"]),
        "in_coverage": index.in_coverage(lat_b, lon_b),
        "below_trained_range": bool(peak < low),
        "above_trained_range": bool(peak > high),
    }

    return features, provenance, context


def sector_id(lat: float, lon: float) -> str:
    """Stable, human-readable identifier for a sector."""
    return f"CHI-{int(round(lat * 1000)):05d}-{int(round(abs(lon) * 1000)):05d}"


def to_row(features: Dict[str, float]) -> List[float]:
    """Ordered feature values for a model call."""
    return [float(features[name]) for name in FEATURES]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    radius = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2)
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
