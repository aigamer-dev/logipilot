"""Seed the dummy logistics database in Firestore (idempotent; overwrites documents by id)."""

import asyncio
from datetime import UTC, datetime, timedelta

from logipilot.common.store import db

TODAY = datetime.now(UTC).replace(hour=10, minute=0, second=0, microsecond=0)


def d(days: float = 0) -> str:
    """ISO timestamp offset from today by the given number of days."""
    return (TODAY + timedelta(days=days)).isoformat()


def day(days: int = 0) -> str:
    """ISO date offset from today by the given number of days."""
    return d(days)[:10]


HUBS = [
    ("MAA", "Chennai", "India", 13.0827, 80.2707),
    ("BOM", "Mumbai", "India", 19.0760, 72.8777),
    ("BLR", "Bengaluru", "India", 12.9716, 77.5946),
    ("DEL", "Delhi", "India", 28.6139, 77.2090),
    ("HYD", "Hyderabad", "India", 17.3850, 78.4867),
    ("CCU", "Kolkata", "India", 22.5726, 88.3639),
    ("SIN", "Singapore", "Singapore", 1.3521, 103.8198),
    ("DXB", "Dubai", "United Arab Emirates", 25.2048, 55.2708),
]

LANES = [
    ("MAA", "BOM", "BlueDart", "air", 1, 42),
    ("MAA", "BOM", "Delhivery", "road", 3, 18),
    ("MAA", "DEL", "BlueDart", "air", 2, 55),
    ("MAA", "DEL", "Delhivery", "road", 5, 24),
    ("BLR", "SIN", "DHL", "air", 2, 120),
    ("BLR", "SIN", "Maersk", "sea", 9, 45),
    ("BLR", "BOM", "Delhivery", "road", 2, 20),
    ("BOM", "CCU", "BlueDart", "air", 2, 60),
    ("BOM", "CCU", "Delhivery", "road", 5, 28),
    ("DEL", "DXB", "FedEx", "air", 3, 140),
    ("DEL", "DXB", "Maersk", "sea", 12, 50),
    ("SIN", "MAA", "DHL", "air", 2, 110),
    ("SIN", "MAA", "Maersk", "sea", 8, 40),
    ("CCU", "HYD", "BlueDart", "air", 2, 58),
    ("CCU", "HYD", "Delhivery", "road", 4, 26),
    ("HYD", "BLR", "Delhivery", "road", 1, 14),
    ("DEL", "BLR", "FedEx", "air", 2, 75),
]


def cust(name: str, city: str, address: str | None = None) -> dict:
    """Build a customer record."""
    slug = name.lower().replace(" ", ".")
    return {
        "name": name,
        "email": f"{slug}@example.com",
        "phone": "+91 98" + str(abs(hash(name)) % 10**8).zfill(8),
        "address": address or f"12 MG Road, {city}",
    }


def ship(tid: str, **kw) -> dict:
    """Build a shipment document with defaults for optional fields."""
    base = {
        "tracking_id": tid,
        "service": "express",
        "hazmat": False,
        "temperature_controlled": False,
        "invoices": [],
        "events": [],
    }
    return {**base, **kw}


def ev(days: float, hub: str, status: str, note: str) -> dict:
    """Build a tracking event."""
    return {"at": d(days), "hub": hub, "status": status, "note": note}


SHIPMENTS = [
    ship(
        "LP1001",
        status="in_transit",
        origin="MAA",
        destination="BOM",
        current_hub="MAA",
        carrier="BlueDart",
        eta=day(2),
        promised_date=day(0),
        contents="Laptops (2 units)",
        weight_kg=5.2,
        declared_value_usd=2400,
        customer=cust("Ananya Iyer", "Mumbai"),
        events=[
            ev(-2, "MAA", "picked_up", "Picked up from shipper"),
            ev(-1, "MAA", "delayed", "Departure delayed by heavy rain at Chennai hub; 2 days late"),
        ],
    ),
    ship(
        "LP1002",
        status="delivered",
        origin="BOM",
        destination="BLR",
        current_hub="BLR",
        carrier="Delhivery",
        eta=day(-3),
        promised_date=day(-3),
        contents="Smartphone SKU-PHONE-01",
        weight_kg=0.8,
        declared_value_usd=1800,
        customer=cust("Rohan Mehta", "Bengaluru"),
        events=[
            ev(-6, "BOM", "picked_up", "Picked up"),
            ev(-3, "BLR", "delivered", "Delivered; consignee reports crushed carton on receipt"),
        ],
    ),
    ship(
        "LP1003",
        status="lost_in_transit",
        origin="DEL",
        destination="BLR",
        current_hub="HYD",
        carrier="FedEx",
        eta=day(-4),
        promised_date=day(-5),
        contents="Apparel (12 pieces)",
        weight_kg=6.0,
        declared_value_usd=650,
        customer=cust("Kavya Nair", "Bengaluru"),
        events=[
            ev(-12, "DEL", "picked_up", "Picked up"),
            ev(-9, "HYD", "scanned", "Last scan at Hyderabad hub; no movement since"),
        ],
    ),
    ship(
        "LP1004",
        status="customs_hold",
        origin="SIN",
        destination="MAA",
        current_hub="MAA",
        carrier="DHL",
        eta=day(3),
        promised_date=day(1),
        contents="Industrial sensors",
        weight_kg=14.5,
        declared_value_usd=5200,
        customer=cust("Vikram Rao", "Chennai"),
        customs_docs={
            "commercial_invoice": False,
            "hs_code": False,
            "packing_list": True,
            "airway_bill": True,
        },
        events=[
            ev(-3, "SIN", "departed", "Departed Singapore"),
            ev(
                -1, "MAA", "customs_hold", "Held at customs: commercial invoice and HS code missing"
            ),
        ],
    ),
    ship(
        "LP1005",
        status="in_transit",
        origin="DEL",
        destination="DXB",
        current_hub="DEL",
        carrier="FedEx",
        eta=day(3),
        promised_date=day(3),
        contents="Lithium-ion batteries",
        weight_kg=22.0,
        declared_value_usd=3100,
        hazmat=True,
        customer=cust("Sara Khan", "Dubai"),
        customs_docs={
            "commercial_invoice": True,
            "hs_code": True,
            "msds": True,
            "dg_declaration": False,
        },
        events=[ev(-1, "DEL", "picked_up", "Picked up; UN3480 dangerous goods handling")],
    ),
    ship(
        "LP1006",
        status="in_transit",
        origin="BOM",
        destination="CCU",
        current_hub="BOM",
        carrier="BlueDart",
        service="cold_chain",
        eta=day(1),
        promised_date=day(1),
        contents="Vaccines (cold chain, 2-8C)",
        weight_kg=18.0,
        declared_value_usd=12000,
        temperature_controlled=True,
        customer=cust("Dr. Meera Das", "Kolkata"),
        events=[
            ev(-1, "BOM", "picked_up", "Picked up at 4C"),
            ev(
                -0.2, "BOM", "temperature_excursion", "Logger reads 11C for 3 hours, above 8C limit"
            ),
        ],
    ),
    ship(
        "LP1007",
        status="out_for_delivery",
        origin="BLR",
        destination="DEL",
        current_hub="DEL",
        carrier="Delhivery",
        eta=day(0),
        promised_date=day(0),
        contents="Home appliance",
        weight_kg=9.0,
        declared_value_usd=300,
        customer=cust("Arjun Singh", "Delhi", "Sector 21, Dwarka, New Delhi"),
        events=[
            ev(-1, "DEL", "arrived", "Arrived at Delhi hub"),
            ev(
                -0.1,
                "DEL",
                "delivery_failed",
                "Attempted delivery failed: address has no flat number",
            ),
        ],
    ),
    ship(
        "LP1008",
        status="delivered",
        origin="HYD",
        destination="BLR",
        current_hub="BLR",
        carrier="Delhivery",
        eta=day(-5),
        promised_date=day(-5),
        contents="Office furniture",
        weight_kg=30.0,
        declared_value_usd=420,
        customer=cust("Neha Gupta", "Bengaluru"),
        invoices=[
            {"invoice_id": "INV-7781", "amount_usd": 420, "status": "paid"},
            {"invoice_id": "INV-7782", "amount_usd": 420, "status": "paid"},
        ],
        events=[ev(-5, "BLR", "delivered", "Delivered")],
    ),
    ship(
        "LP1009",
        status="delivered",
        origin="MAA",
        destination="BLR",
        current_hub="BLR",
        carrier="BlueDart",
        eta=day(-2),
        promised_date=day(-2),
        contents="Running shoes SKU-SHOE-09",
        weight_kg=1.1,
        declared_value_usd=95,
        customer=cust("Dev Patel", "Bengaluru"),
        events=[ev(-2, "BLR", "delivered", "Delivered; customer says size is wrong, wants return")],
    ),
    ship(
        "LP1010",
        status="delivered",
        origin="MAA",
        destination="DEL",
        current_hub="DEL",
        carrier="BlueDart",
        eta=day(-4),
        promised_date=day(-4),
        contents="Books",
        weight_kg=3.5,
        declared_value_usd=60,
        customer=cust("Isha Verma", "Delhi"),
        events=[
            ev(-6, "MAA", "picked_up", "Picked up"),
            ev(-4, "DEL", "delivered", "Delivered on time"),
        ],
    ),
    ship(
        "LP1011",
        status="in_transit",
        origin="BLR",
        destination="SIN",
        current_hub="BLR",
        carrier="DHL",
        eta=day(2),
        promised_date=day(2),
        contents="Software dev boards",
        weight_kg=4.0,
        declared_value_usd=1500,
        customer=cust("Li Wei", "Singapore"),
        customs_docs={
            "commercial_invoice": True,
            "hs_code": True,
            "packing_list": True,
            "airway_bill": True,
        },
        events=[ev(-1, "BLR", "departed", "Departed Bengaluru; all customs documents present")],
    ),
    ship(
        "LP1012",
        status="in_transit",
        origin="CCU",
        destination="HYD",
        current_hub="CCU",
        carrier="Delhivery",
        eta=day(4),
        promised_date=day(1),
        contents="Textile rolls",
        weight_kg=120.0,
        declared_value_usd=2200,
        customer=cust("Farhan Ali", "Hyderabad"),
        events=[ev(-1, "CCU", "delayed", "Carrier workers' strike halting line-haul from Kolkata")],
    ),
]

INVENTORY = [
    ("SKU-PHONE-01", "Smartphone", {"BLR": 0, "MAA": 140, "BOM": 220, "DEL": 95}, 50, 14),
    ("SKU-LAPTOP-02", "Laptop", {"BLR": 35, "MAA": 20, "BOM": 12, "DEL": 40}, 25, 21),
    ("SKU-SHOE-09", "Running shoes", {"BLR": 300, "MAA": 180, "BOM": 250, "DEL": 90}, 100, 10),
    ("SKU-VAX-03", "Vaccine vials", {"BOM": 800, "CCU": 40, "DEL": 500, "BLR": 150}, 200, 7),
]

EVAL_CASES = [
    (
        "delay",
        "Where is LP1001? It was due today and I heard it is stuck at Chennai.",
        ["reroute", "notify"],
    ),
    ("damage", "LP1002 arrived with a crushed carton and the phone is broken.", ["damage_claim"]),
    ("lost", "LP1003 has not moved in 9 days. I think it is lost.", ["damage_claim", "notify"]),
    ("returns", "I want to return the shoes in LP1009, the size is wrong.", ["return"]),
    ("customs", "LP1004 is held at customs. What is missing?", ["customs_docs"]),
    ("inventory", "SKU-PHONE-01 is out of stock at BLR, please fix.", ["replenishment"]),
    ("address", "LP1007 delivery failed, the address has no flat number.", ["notify"]),
    ("billing", "I was charged twice for LP1008 (INV-7781 and INV-7782).", ["billing_dispute"]),
]


async def main() -> None:
    """Write every collection and print counts."""
    batches = {
        "hubs": {
            c: {"code": c, "city": city, "country": ctry, "lat": lat, "lon": lon}
            for c, city, ctry, lat, lon in HUBS
        },
        "shipments": {s["tracking_id"]: s for s in SHIPMENTS},
        "inventory": {
            sku: {
                "sku": sku,
                "name": n,
                "on_hand": oh,
                "reorder_point": rp,
                "supplier_lead_days": ld,
            }
            for sku, n, oh, rp, ld in INVENTORY
        },
        "carrier_lanes": {
            f"{o}-{de}-{c}-{m}".lower(): {
                "origin": o,
                "destination": de,
                "carrier": c,
                "mode": m,
                "transit_days": t,
                "cost_usd": cost,
            }
            for o, de, c, m, t, cost in LANES
        },
        "eval_cases": {
            f"case-{cat}": {"category": cat, "message": msg, "expected_actions": acts}
            for cat, msg, acts in EVAL_CASES
        },
    }
    for collection, docs in batches.items():
        batch = db.batch()
        for doc_id, data in docs.items():
            batch.set(db.collection(collection).document(doc_id), data)
        await batch.commit()
        print(f"{collection}: {len(docs)}")


if __name__ == "__main__":
    asyncio.run(main())
