"""Direct REST client for the LogiPilot tools API, usable as ADK function tools."""

import os

import httpx


async def _call(method: str, path: str, **kwargs) -> dict:
    """Call the tools API; return {"error": ...} on 4xx and raise on 5xx or connection errors."""
    headers = {"X-API-Key": os.environ["TOOLS_API_KEY"]} if os.getenv("TOOLS_API_KEY") else {}
    base = os.getenv("TOOLS_API_URL", "http://localhost:8080")
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=15) as client:
        resp = await client.request(method, path, **kwargs)
    if resp.status_code >= 500:
        resp.raise_for_status()
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        return {"error": f"{resp.status_code}: {detail}"}
    return resp.json()


async def lookup_shipment(tracking_id: str) -> dict:
    """Look up a shipment by tracking id (e.g. LP1001) and return its status, route, carrier,
    customer, customs documents, invoices and event history."""
    return await _call("GET", f"/shipments/{tracking_id}")


async def shipment_risk(tracking_id: str) -> dict:
    """Assess delay risk for a shipment by combining it with live weather at its current hub
    and its destination hub."""
    return await _call("GET", f"/shipments/{tracking_id}/risk")


async def hub_weather(hub_code: str) -> dict:
    """Get live weather and a low/medium/high delay risk for a hub (e.g. MAA, BOM, SIN)."""
    return await _call("GET", f"/weather/{hub_code}")


async def check_inventory(sku: str) -> dict:
    """Check stock per warehouse, reorder point and supplier lead days for a SKU."""
    return await _call("GET", f"/inventory/{sku}")


async def find_alternate_carriers(origin: str, destination: str, max_days: int) -> dict:
    """Find carrier lane options between two hub codes that arrive within max_days,
    sorted by cost in USD."""
    params = {"origin": origin, "destination": destination, "max_days": max_days}
    return await _call("GET", "/lanes", params=params)


async def file_damage_claim(tracking_id: str, description: str, claim_value_usd: float) -> dict:
    """File a damage or loss claim for a shipment with a description and claim value in USD."""
    body = {
        "tracking_id": tracking_id,
        "description": description,
        "claim_value_usd": claim_value_usd,
    }
    return await _call("POST", "/actions/damage-claim", json=body)


async def create_return(tracking_id: str, reason: str, disposition: str) -> dict:
    """Create a return for a delivered shipment. Disposition is one of restock, refurbish,
    liquidate or scrap."""
    body = {"tracking_id": tracking_id, "reason": reason, "disposition": disposition}
    return await _call("POST", "/actions/return", json=body)


async def reroute_shipment(tracking_id: str, new_destination: str, reason: str) -> dict:
    """Reroute a shipment to a new destination hub code or address, with a reason."""
    body = {"tracking_id": tracking_id, "new_destination": new_destination, "reason": reason}
    return await _call("POST", "/actions/reroute", json=body)


async def request_customs_documents(tracking_id: str, missing_documents: list[str]) -> dict:
    """Request missing customs documents (e.g. commercial_invoice, hs_code) for a shipment."""
    body = {"tracking_id": tracking_id, "missing_documents": missing_documents}
    return await _call("POST", "/actions/customs-docs", json=body)


async def open_billing_dispute(reference_id: str, disputed_amount_usd: float, reason: str) -> dict:
    """Open a billing dispute for an invoice or tracking id, with the disputed amount in USD."""
    body = {
        "reference_id": reference_id,
        "disputed_amount_usd": disputed_amount_usd,
        "reason": reason,
    }
    return await _call("POST", "/actions/billing-dispute", json=body)


async def create_replenishment_order(sku: str, quantity: int, warehouse: str) -> dict:
    """Create a replenishment order for a SKU, with a positive quantity and target warehouse."""
    body = {"sku": sku, "quantity": quantity, "warehouse": warehouse}
    return await _call("POST", "/actions/replenishment", json=body)


async def notify_customer(recipient: str, message: str) -> dict:
    """Send a notification message to a customer (email address or name)."""
    return await _call("POST", "/actions/notify", json={"recipient": recipient, "message": message})


READ_ONLY = [lookup_shipment, shipment_risk, hub_weather, check_inventory, find_alternate_carriers]

TOOLS = [
    *READ_ONLY,
    file_damage_claim,
    create_return,
    reroute_shipment,
    request_customs_documents,
    open_billing_dispute,
    create_replenishment_order,
    notify_customer,
]
