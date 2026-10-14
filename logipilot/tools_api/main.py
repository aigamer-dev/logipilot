"""Logistics tools REST API backed by Firestore, with live weather from Open-Meteo."""

import hashlib
import os
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from google.cloud import firestore
from pydantic import BaseModel, Field

from logipilot.common.store import db, get_doc, list_docs, now, put_doc

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"


async def require_key(x_api_key: str | None = Header(default=None)) -> None:
    """Enforce the optional shared secret when TOOLS_API_KEY is set."""
    key = os.getenv("TOOLS_API_KEY")
    if key and x_api_key != key:
        raise HTTPException(401, "Invalid or missing X-API-Key")


app = FastAPI(title="LogiPilot Tools API")
api = APIRouter(dependencies=[Depends(require_key)])


def delay_risk(current: dict, daily: dict) -> tuple[str, str]:
    """Classify weather delay risk as (low|medium|high, reason) from current and daily data."""
    precip = max(daily.get("precipitation_sum") or [0], default=0) or 0
    wind = max(daily.get("wind_speed_10m_max") or [0], default=0) or 0
    code = current.get("weather_code") or 0
    if precip >= 20 or wind >= 50 or code >= 95:
        level = "high"
    elif precip >= 5 or wind >= 30:
        level = "medium"
    else:
        return "low", "No significant weather expected."
    return level, f"Max precipitation {precip} mm, max wind {wind} km/h, weather code {code}."


async def _need(collection: str, doc_id: str, label: str) -> dict:
    doc = await get_doc(collection, doc_id)
    if doc is None:
        raise HTTPException(404, f"{label} {doc_id} not found")
    return doc


async def _weather(hub_code: str) -> dict:
    hub = await _need("hubs", hub_code.upper(), "Hub")
    params = {
        "latitude": hub["lat"],
        "longitude": hub["lon"],
        "current": "temperature_2m,precipitation,wind_speed_10m,weather_code",
        "daily": "precipitation_sum,wind_speed_10m_max",
        "forecast_days": 3,
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(OPEN_METEO, params=params)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(503, f"Weather service unavailable: {exc!r}") from exc
    level, reason = delay_risk(data["current"], data["daily"])
    return {
        "hub": hub,
        "current": data["current"],
        "daily": data["daily"],
        "delay_risk": level,
        "reason": reason,
    }


@app.get("/health")
async def health() -> dict:
    """Report liveness and whether Firestore is reachable."""
    try:
        await db.collection("hubs").limit(1).get()
        ok = True
    except Exception:
        ok = False
    return {"ok": True, "firestore": ok}


@api.get("/shipments/{tracking_id}/risk")
async def shipment_risk(tracking_id: str) -> dict:
    """Combine a shipment with weather at its current hub and destination hub."""
    shipment = await _need("shipments", tracking_id.upper(), "Shipment")
    current = await _weather(shipment["current_hub"])
    dest = await _weather(shipment["destination"])
    order = ["low", "medium", "high"]
    overall = max(current["delay_risk"], dest["delay_risk"], key=order.index)
    return {
        "shipment": shipment,
        "current_hub_weather": current,
        "destination_weather": dest,
        "delay_risk": overall,
    }


@api.get("/shipments/{tracking_id}")
async def get_shipment(tracking_id: str) -> dict:
    """Return one shipment by tracking id."""
    return await _need("shipments", tracking_id.upper(), "Shipment")


@api.get("/shipments")
async def list_shipments(status: str | None = None) -> list[dict]:
    """List shipments, optionally filtered by status."""
    return await list_docs("shipments", **({"status": status} if status else {}))


@api.get("/inventory/{sku}")
async def get_inventory(sku: str) -> dict:
    """Return inventory for a SKU."""
    return await _need("inventory", sku.upper(), "SKU")


@api.get("/lanes")
async def lanes(origin: str, destination: str, max_days: int = 365) -> dict:
    """Return carrier lane options within max_days, cheapest first."""
    found = await list_docs(
        "carrier_lanes", limit=200, origin=origin.upper(), destination=destination.upper()
    )
    options = [lane for lane in found if lane["transit_days"] <= max_days]
    return {"options": sorted(options, key=lambda lane: lane["cost_usd"])}


@api.get("/hubs/{code}")
async def get_hub(code: str) -> dict:
    """Return a hub by IATA-style code."""
    return await _need("hubs", code.upper(), "Hub")


@api.get("/weather/{hub_code}")
async def weather(hub_code: str) -> dict:
    """Return live weather and delay risk for a hub."""
    return await _weather(hub_code)


class DamageClaim(BaseModel):
    tracking_id: str
    description: str
    claim_value_usd: float = Field(ge=0)


class ReturnReq(BaseModel):
    tracking_id: str
    reason: str
    disposition: Literal["restock", "refurbish", "liquidate", "scrap"]


class Reroute(BaseModel):
    tracking_id: str
    new_destination: str
    reason: str


class CustomsDocs(BaseModel):
    tracking_id: str
    missing_documents: list[str]


class BillingDispute(BaseModel):
    reference_id: str
    disputed_amount_usd: float = Field(gt=0)
    reason: str


class Replenishment(BaseModel):
    sku: str
    quantity: int = Field(gt=0)
    warehouse: str


class Notify(BaseModel):
    recipient: str
    message: str


# Fields that identify "the same action" for idempotency; free-text fields are excluded.
IDEMPOTENCY_KEYS = {
    "damage_claim": ["tracking_id"],
    "return": ["tracking_id"],
    "reroute": ["tracking_id", "new_destination"],
    "customs_docs": ["tracking_id"],
    "billing_dispute": ["reference_id"],
    "replenishment": ["sku", "warehouse"],
    # One notification per recipient per day, however the message is worded (no spam on retries).
    "notify": ["recipient", "_day"],
}


async def _record(kind: str, body: BaseModel, event_note: str | None = None) -> dict:
    """Validate references, store the action, and optionally append a shipment event."""
    fields = body.model_dump()
    tid = fields.get("tracking_id")
    if tid:
        fields["tracking_id"] = tid = tid.upper()
        await _need("shipments", tid, "Shipment")
    if "sku" in fields:
        fields["sku"] = fields["sku"].upper()
        await _need("inventory", fields["sku"], "SKU")
    # Idempotency: the same action on the same object returns the existing record, never a copy.
    key_fields = IDEMPOTENCY_KEYS.get(kind, sorted(fields))
    keyed = {**fields, "_day": now()[:10]}
    key = "|".join([kind, *(str(keyed.get(f, "")).upper() for f in key_fields)])
    action_id = hashlib.sha1(key.encode()).hexdigest()[:20]
    existing = await get_doc("actions", action_id)
    if existing:
        return {"action_id": action_id, "duplicate": True, **existing}
    await put_doc(
        "actions", action_id, {"kind": kind, "status": "open", "created_at": now(), **fields}
    )
    if event_note:
        event = {"at": now(), "hub": None, "status": kind, "note": event_note}
        await db.collection("shipments").document(tid).update(
            {"events": firestore.ArrayUnion([event])}
        )
    return {"action_id": action_id, "kind": kind, "status": "open", **fields}


@api.post("/actions/damage-claim")
async def damage_claim(body: DamageClaim) -> dict:
    """File a damage claim."""
    return await _record("damage_claim", body, f"Damage claim filed: {body.description}")


@api.post("/actions/return")
async def create_return(body: ReturnReq) -> dict:
    """Create a return."""
    return await _record("return", body)


@api.post("/actions/reroute")
async def reroute(body: Reroute) -> dict:
    """Reroute a shipment."""
    note = f"Reroute to {body.new_destination} requested: {body.reason}"
    return await _record("reroute", body, note)


@api.post("/actions/customs-docs")
async def customs_docs(body: CustomsDocs) -> dict:
    """Request missing customs documents."""
    return await _record("customs_docs", body)


@api.post("/actions/billing-dispute")
async def billing_dispute(body: BillingDispute) -> dict:
    """Open a billing dispute; reference_id is an invoice or tracking id."""
    return await _record("billing_dispute", body)


@api.post("/actions/replenishment")
async def replenishment(body: Replenishment) -> dict:
    """Create a replenishment order."""
    return await _record("replenishment", body)


@api.post("/actions/notify")
async def notify(body: Notify) -> dict:
    """Queue a customer notification."""
    return await _record("notify", body)


@api.get("/actions")
async def list_actions(tracking_id: str | None = None) -> list[dict]:
    """List actions, optionally for one shipment."""
    filt = {"tracking_id": tracking_id.upper()} if tracking_id else {}
    return await list_docs("actions", **filt)


app.include_router(api)
