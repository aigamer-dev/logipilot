"""Integration tests for the tools API against seeded Firestore, plus the pure risk rule."""

import pytest
from fastapi.testclient import TestClient

from logipilot.tools_api.main import app, delay_risk


@pytest.fixture(scope="module")
def client() -> TestClient:
    """A TestClient sharing one event loop so the Firestore async client stays valid."""
    with TestClient(app) as c:
        yield c


def test_shipment_lookup(client: TestClient) -> None:
    resp = client.get("/shipments/lp1001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tracking_id"] == "LP1001" and body["origin"] == "MAA"


def test_shipment_not_found(client: TestClient) -> None:
    resp = client.get("/shipments/NOPE")
    assert resp.status_code == 404 and "detail" in resp.json()


def test_action_post_and_validation(client: TestClient) -> None:
    resp = client.post(
        "/actions/damage-claim",
        json={"tracking_id": "LP1002", "description": "Crushed carton", "claim_value_usd": 1800},
    )
    assert resp.status_code == 200
    assert resp.json()["kind"] == "damage_claim" and resp.json()["status"] == "open"
    assert client.get("/shipments/LP1002").json()["events"][-1]["status"] == "damage_claim"
    bad = client.post(
        "/actions/replenishment", json={"sku": "SKU-PHONE-01", "quantity": 0, "warehouse": "BLR"}
    )
    assert bad.status_code == 422
    missing = client.post("/actions/notify", json={"recipient": "x"})
    assert missing.status_code == 422
    ghost = client.post(
        "/actions/return", json={"tracking_id": "NOPE", "reason": "x", "disposition": "scrap"}
    )
    assert ghost.status_code == 404


def test_lanes_filter(client: TestClient) -> None:
    options = client.get(
        "/lanes", params={"origin": "MAA", "destination": "BOM", "max_days": 2}
    ).json()["options"]
    assert options and all(o["transit_days"] <= 2 for o in options)
    costs = [o["cost_usd"] for o in options]
    assert costs == sorted(costs)


def test_delay_risk_rule() -> None:
    calm = {"precipitation_sum": [0, 1], "wind_speed_10m_max": [10, 12]}
    assert delay_risk({"weather_code": 1}, calm)[0] == "low"
    assert delay_risk({"weather_code": 1}, {**calm, "precipitation_sum": [5, 0]})[0] == "medium"
    assert delay_risk({"weather_code": 1}, {**calm, "wind_speed_10m_max": [30]})[0] == "medium"
    assert delay_risk({"weather_code": 1}, {**calm, "precipitation_sum": [20]})[0] == "high"
    assert delay_risk({"weather_code": 1}, {**calm, "wind_speed_10m_max": [50]})[0] == "high"
    assert delay_risk({"weather_code": 95}, calm)[0] == "high"
