"""
Tests for the prediction service.

These load the real models and the real sector data, so they double as a guard
against the failure that motivated this version of the service: feature values
far outside the training range, which made the regressor emit large negative
incident counts.
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import HOTSPOT_THRESHOLD, app  # noqa: E402
from features import FEATURES, GRID_STEP, SectorIndex, build_feature_vector, snap  # noqa: E402

BUSY_SECTOR = {"lat": 41.885, "lon": -87.630}     # highest recorded volume in the dataset
QUIET_SECTOR = {"lat": 41.945, "lon": -87.635}    # 3 recorded incidents


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def index():
    return SectorIndex()


# ---------------------------------------------------------------- meta

def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "healthy"
    assert body["models_loaded"] is True
    assert body["sectors_indexed"] > 0


def test_stats_reports_the_real_dataset(client, index):
    body = client.get("/stats").json()
    assert body["dataset"]["total_incidents"] == index.total_incidents
    assert body["dataset"]["sectors"] == index.sector_count
    assert body["dataset"]["grid_resolution_degrees"] == GRID_STEP
    assert body["models"]["classifier"]["n_features"] == len(FEATURES)
    assert body["models"]["regressor"]["n_features"] == len(FEATURES)


def test_model_card_importances_sum_to_one(client):
    body = client.get("/model-card").json()
    for key in ("classifier_importances", "regressor_importances"):
        total = sum(item["importance"] for item in body[key])
        assert total == pytest.approx(1.0, abs=1e-3), f"{key} should sum to 1"
    assert body["features"] == FEATURES
    assert body["limitations"]


# ------------------------------------------------------------ features

def test_feature_vector_is_complete_and_ordered(index):
    features, provenance, _ = build_feature_vector(index, **{"lat": 41.885, "lon": -87.630})
    assert set(features) == set(FEATURES)
    assert set(provenance) == set(FEATURES)


def test_neighbour_features_come_from_the_record(index):
    features, provenance, _ = build_feature_vector(index, 41.885, -87.630)
    neighbours = index.neighbours(41.885, -87.630)
    assert features["neighbor_max_crimes"] == max(neighbours)
    assert features["neighbor_avg_crimes"] == pytest.approx(sum(neighbours) / len(neighbours))
    assert provenance["neighbor_avg_crimes"] == "observed"
    assert provenance["neighbor_max_crimes"] == "observed"


def test_peak_hour_is_derived_from_the_observed_total(index):
    features, provenance, context = build_feature_vector(index, 41.885, -87.630)
    expected = context["observed_incidents"] * features["peak_hour_ratio"]
    assert features["peak_hour_crimes"] == pytest.approx(expected, rel=1e-6)
    assert provenance["peak_hour_crimes"] == "derived"


def test_overrides_are_marked_as_such(index):
    features, provenance, _ = build_feature_vector(
        index, 41.885, -87.630, {"arrest_rate": 0.9, "peak_hour_crimes": 1000.0}
    )
    assert features["arrest_rate"] == 0.9
    assert features["peak_hour_crimes"] == 1000.0
    assert provenance["arrest_rate"] == "override"
    assert provenance["peak_hour_crimes"] == "override"


def test_coordinates_snap_to_the_grid():
    assert snap(41.8863) == 41.885
    assert snap(-87.6312) == -87.63


# ---------------------------------------------------------- prediction

def test_predict_returns_a_complete_assessment(client):
    body = client.post("/predict", json=BUSY_SECTOR).json()
    for key in ("sector_id", "is_hotspot", "hotspot_probability", "risk_level",
                "predicted_incidents", "observed_incidents", "confidence",
                "features_used", "feature_provenance"):
        assert key in body
    assert 0.0 <= body["hotspot_probability"] <= 1.0
    assert body["risk_level"] in ("LOW", "MEDIUM", "HIGH")
    assert body["is_hotspot"] == (body["hotspot_probability"] >= HOTSPOT_THRESHOLD)


def test_predicted_incidents_are_never_negative(client):
    """The regression head extrapolates below zero for very quiet sectors. A
    negative crime count is meaningless and must never reach a caller."""
    for lat, lon in [(41.945, -87.635), (41.65, -87.53), (42.02, -87.90), (41.70, -87.88)]:
        body = client.post("/predict", json={"lat": lat, "lon": lon}).json()
        assert body["predicted_incidents"] >= 0, f"negative count at {lat},{lon}"


def test_busy_sector_outranks_a_quiet_one(client):
    busy = client.post("/predict", json=BUSY_SECTOR).json()
    quiet = client.post("/predict", json=QUIET_SECTOR).json()
    assert busy["observed_incidents"] > quiet["observed_incidents"]
    assert busy["hotspot_probability"] > quiet["hotspot_probability"]
    assert busy["predicted_incidents"] >= quiet["predicted_incidents"]


def test_low_confidence_is_flagged_with_a_reason(client):
    body = client.post("/predict", json=QUIET_SECTOR).json()
    assert body["confidence"] == "low"
    assert body["notes"], "a low-confidence result must explain itself"


def test_busiest_sector_keeps_its_classification_but_flags_extrapolated_volume(client):
    """Above the busiest decision threshold the classifier saturates rather than
    failing, so the classification stands and only the count is marked."""
    body = client.post("/predict", json=BUSY_SECTOR).json()
    assert body["confidence"] == "normal"
    assert body["volume_extrapolated"] is True
    assert any("extrapolated" in note for note in body["notes"])


def test_mid_range_sector_is_neither_low_confidence_nor_extrapolated(client):
    body = client.post("/predict", json={"lat": 41.94, "lon": -87.66}).json()
    assert body["confidence"] == "normal"
    assert body["volume_extrapolated"] is False


def test_raising_peak_hour_volume_raises_risk(client):
    """The dominant feature must actually move the output; if it does not, the
    service is feeding the models constants again."""
    low = client.post("/predict", json={**BUSY_SECTOR, "peak_hour_crimes": 300}).json()
    high = client.post("/predict", json={**BUSY_SECTOR, "peak_hour_crimes": 1400}).json()
    assert high["hotspot_probability"] > low["hotspot_probability"]
    assert high["predicted_incidents"] > low["predicted_incidents"]


def test_predictions_vary_across_the_city(client):
    """Guards against every sector collapsing to the same answer."""
    points = [(41.885, -87.63), (41.775, -87.675), (41.94, -87.66),
              (41.70, -87.60), (42.00, -87.70), (41.83, -87.75)]
    probs = {
        client.post("/predict", json={"lat": la, "lon": lo}).json()["hotspot_probability"]
        for la, lo in points
    }
    assert len(probs) > 1, "predictions should differ between sectors"
    assert max(probs) - min(probs) > 0.1


def test_rejects_out_of_range_coordinates(client):
    assert client.post("/predict", json={"lat": 999, "lon": -87.6}).status_code == 422
    assert client.post("/predict", json={"lat": 41.8}).status_code == 422


def test_batch_matches_single_predictions(client):
    batch = client.post("/predict/batch", json={"sectors": [BUSY_SECTOR, QUIET_SECTOR]}).json()
    assert batch["count"] == 2
    single = client.post("/predict", json=BUSY_SECTOR).json()
    assert batch["results"][0]["hotspot_probability"] == single["hotspot_probability"]


# ------------------------------------------------------------ hotspots

def test_hotspots_are_ranked_and_capped(client):
    body = client.get("/hotspots?limit=10").json()
    assert body["count"] == 10
    probs = [h["hotspot_probability"] for h in body["hotspots"]]
    assert probs == sorted(probs, reverse=True)


def test_hotspot_output_tracks_the_record(client):
    """Modelled risk should correlate with what was actually recorded; if it
    does not, the feature pipeline has drifted from the models."""
    body = client.get("/risk-distribution").json()
    agreement = body["agreement_with_record"]
    assert agreement["hotspot_probability_vs_observed"] > 0.6
    assert agreement["predicted_volume_vs_observed"] > 0.6


def test_risk_bands_cover_every_sector(client, index):
    bands = client.get("/risk-distribution").json()["risk_bands"]
    assert bands["HIGH"] + bands["MEDIUM"] + bands["LOW"] == index.sector_count


# ------------------------------------------------------------- routing

def test_route_over_top_hotspots(client):
    body = client.post("/optimize-route", json={"from_hotspots": 8}).json()
    assert len(body["stops"]) == 8
    assert body["total_distance_km"] > 0
    assert [s["stop"] for s in body["stops"]] == list(range(1, 9))
    assert body["stops"][0]["cumulative_km"] == 0
    cumulative = [s["cumulative_km"] for s in body["stops"]]
    assert cumulative == sorted(cumulative), "distance must accumulate along the route"


def test_route_is_no_longer_than_the_input_order(client):
    """An optimised tour must not be worse than visiting the points as given."""
    from features import haversine_km

    points = [
        {"lat": 41.88, "lon": -87.63}, {"lat": 41.75, "lon": -87.65},
        {"lat": 41.94, "lon": -87.66}, {"lat": 41.78, "lon": -87.67},
        {"lat": 41.92, "lon": -87.76}, {"lat": 41.71, "lon": -87.58},
    ]
    naive = sum(
        haversine_km(points[i]["lat"], points[i]["lon"], points[i + 1]["lat"], points[i + 1]["lon"])
        for i in range(len(points) - 1)
    )
    naive += haversine_km(points[-1]["lat"], points[-1]["lon"], points[0]["lat"], points[0]["lon"])

    body = client.post("/optimize-route", json={"points": points, "round_trip": True}).json()
    assert body["total_distance_km"] <= naive + 1e-6


def test_route_needs_at_least_two_points(client):
    res = client.post("/optimize-route", json={"points": [{"lat": 41.8, "lon": -87.6}]})
    assert res.status_code == 400


def test_open_route_has_no_return_leg(client):
    body = client.post("/optimize-route", json={"from_hotspots": 5, "round_trip": False}).json()
    assert body["return_leg_km"] == 0
    assert body["round_trip"] is False
