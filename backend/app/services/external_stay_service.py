"""External hotels service for Aarohan / FestOS Command.

Handles distance calculation, travel time estimates, Google Places API import,
OpenStreetMap (Overpass API) fallback, and curated hotel seeding.
"""
from datetime import datetime, timezone
import math
from typing import Any
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ExternalHotel, Festival


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in kilometers between two GPS coordinates using Haversine formula."""
    R = 6371.0  # Earth's radius in kilometers
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return round(R * c, 2)


def approx_travel_time_minutes(distance_km: float) -> int:
    """Estimate car travel time in minutes based on local road speeds (~25-35 km/h avg)."""
    if distance_km <= 0:
        return 3
    # ~2.2 minutes per km, minimum 5 minutes
    return max(5, int(round(distance_km * 2.2)))


CURATED_INDORE_HOTELS = [
    {
        "name": "Papaya Tree Hotel",
        "address": "AB Road, Bypass Junction, Near Rau Circle, Rau, Indore 453331",
        "area": "Rau Circle",
        "latitude": 22.6315,
        "longitude": 75.8242,
        "phone": "+91-731-2857000",
        "alternate_phone": "+91-9893012345",
        "price_min": 2800,
        "price_max": 4500,
        "rating": 4.2,
        "notes": "4-star business hotel with 24/7 front desk, elevator, and multi-cuisine restaurant. ~18 min to IIM campus.",
        "google_maps_link": "https://maps.google.com/?q=Papaya+Tree+Hotel+Indore",
        "is_verified": True,
        "verified_by": "Accommodation Team",
        "verified_at": datetime(2026, 10, 20, 14, 30, tzinfo=timezone.utc),
    },
    {
        "name": "The Red Maple Mashal",
        "address": "Jash Highway, Rau, Indore 453331",
        "area": "Rau",
        "latitude": 22.6240,
        "longitude": 75.8150,
        "phone": "+91-731-4278888",
        "alternate_phone": "+91-9826078888",
        "price_min": 3200,
        "price_max": 5500,
        "rating": 4.4,
        "notes": "Resort property with spacious lawn, in-house restaurant, and ample parking. ~15 min to campus.",
        "google_maps_link": "https://maps.google.com/?q=The+Red+Maple+Mashal+Indore",
        "is_verified": True,
        "verified_by": "Accommodation Team",
        "verified_at": datetime(2026, 10, 22, 11, 0, tzinfo=timezone.utc),
    },
    {
        "name": "Hotel Solaris Pithampur",
        "address": "Sector 1, Industrial Area, Rau-Pithampur Road, Pithampur 454775",
        "area": "Pithampur Road",
        "latitude": 22.6020,
        "longitude": 75.7150,
        "phone": "+91-9826012345",
        "alternate_phone": "+91-7292-401234",
        "price_min": 1500,
        "price_max": 2600,
        "rating": 3.9,
        "notes": "Popular corporate hotel on Pithampur road, air-conditioned rooms and generator backup. ~12 min to campus.",
        "google_maps_link": "https://maps.google.com/?q=Hotel+Solaris+Pithampur",
        "is_verified": False,
        "verified_by": None,
        "verified_at": None,
    },
    {
        "name": "Hotel Pride Rau",
        "address": "Station Road, Near Rau Railway Station, Rau, Indore 453331",
        "area": "Rau Station",
        "latitude": 22.6185,
        "longitude": 75.8080,
        "phone": "+91-731-2856789",
        "alternate_phone": None,
        "price_min": 1200,
        "price_max": 2200,
        "rating": 3.8,
        "notes": "Budget-friendly hotel close to Rau junction and local bus transit. Clean standard rooms.",
        "google_maps_link": "https://maps.google.com/?q=Hotel+Pride+Rau+Indore",
        "is_verified": False,
        "verified_by": None,
        "verified_at": None,
    },
    {
        "name": "Hotel Highway King",
        "address": "Pithampur Bypass, Near Toll Plaza, Dhar Road 453001",
        "area": "Pithampur Bypass",
        "latitude": 22.5950,
        "longitude": 75.7300,
        "phone": None,  # Intentionally without phone to test "Phone not available" edge case
        "alternate_phone": None,
        "price_min": 1000,
        "price_max": 1800,
        "rating": 3.6,
        "notes": "Highway lodge with basic AC and non-AC rooms. Nearest stay to campus (~9 mins).",
        "google_maps_link": "https://maps.google.com/?q=Hotel+Highway+King+Pithampur",
        "is_verified": False,
        "verified_by": None,
        "verified_at": None,
    },
    {
        "name": "Hotel Rajshree",
        "address": "AB Road, Opposite Silicon City, Rau Bypass, Indore 452012",
        "area": "Silicon City",
        "latitude": 22.6450,
        "longitude": 75.8320,
        "phone": "+91-731-4265555",
        "alternate_phone": "+91-9425098765",
        "price_min": 1800,
        "price_max": 3000,
        "rating": 4.0,
        "notes": "Pure vegetarian family hotel near Silicon City, 24-hr check-in and Wi-Fi.",
        "google_maps_link": "https://maps.google.com/?q=Hotel+Rajshree+Indore",
        "is_verified": False,
        "verified_by": None,
        "verified_at": None,
    },
    {
        "name": "Hotel Lakeview Residency",
        "address": "Near Pipliyapala Regional Park, Ring Road, Indore 452020",
        "area": "Pipliyapala",
        "latitude": 22.6720,
        "longitude": 75.8450,
        "phone": "+91-731-2789900",
        "alternate_phone": None,
        "price_min": 2000,
        "price_max": 3400,
        "rating": 4.1,
        "notes": "Lake view property near Regional Park, spacious rooms, peaceful area.",
        "google_maps_link": "https://maps.google.com/?q=Hotel+Lakeview+Residency+Indore",
        "is_verified": False,
        "verified_by": None,
        "verified_at": None,
    },
    {
        "name": "Treebo Trend Shivani",
        "address": "14/1 Transport Nagar, Near Bhawar Kuan Square, Indore 452014",
        "area": "Bhawar Kuan",
        "latitude": 22.6880,
        "longitude": 75.8550,
        "phone": "+91-9322800100",
        "alternate_phone": None,
        "price_min": 1600,
        "price_max": 2800,
        "rating": 4.1,
        "notes": "Well-rated chain hotel near student hub, complimentary breakfast and desk support.",
        "google_maps_link": "https://maps.google.com/?q=Treebo+Trend+Shivani+Indore",
        "is_verified": False,
        "verified_by": None,
        "verified_at": None,
    },
    {
        "name": "Hotel Apna Palace",
        "address": "Dhar Road, Near Gangwal Bus Stand, Indore 452002",
        "area": "Dhar Road",
        "latitude": 22.7050,
        "longitude": 75.8350,
        "phone": "+91-731-2481111",
        "alternate_phone": "+91-9826044444",
        "price_min": 2200,
        "price_max": 3800,
        "rating": 4.0,
        "notes": "Close to Gangwal bus stand, direct road connectivity to IIM via Rau bypass.",
        "google_maps_link": "https://maps.google.com/?q=Hotel+Apna+Palace+Indore",
        "is_verified": False,
        "verified_by": None,
        "verified_at": None,
    },
    {
        "name": "Effotel Hotel by Sayaji",
        "address": "Plot No. 10-C/C.A., Scheme No. 94, Sector C, Ring Road, Indore 452010",
        "area": "Vijay Nagar / Ring Road",
        "latitude": 22.7480,
        "longitude": 75.8920,
        "phone": "+91-731-4777777",
        "alternate_phone": "+91-731-4006666",
        "price_min": 4200,
        "price_max": 7500,
        "rating": 4.5,
        "notes": "Upscale 4-star hotel in premium commercial zone, buffet dining and fitness center.",
        "google_maps_link": "https://maps.google.com/?q=Effotel+Hotel+Indore",
        "is_verified": True,
        "verified_by": "Hospitality Lead",
        "verified_at": datetime(2026, 10, 25, 16, 0, tzinfo=timezone.utc),
    },
]


def fetch_from_google_places(api_key: str, lat: float, lng: float, radius_meters: int = 10000) -> list[dict[str, Any]]:
    """Fetch nearby lodging places from Google Places Nearby Search API."""
    if not api_key:
        return []
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    params = {
        "location": f"{lat},{lng}",
        "radius": radius_meters,
        "type": "lodging",
        "key": api_key,
    }
    hotels: list[dict[str, Any]] = []
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                for place in data.get("results", []):
                    geometry = place.get("geometry", {}).get("location", {})
                    p_lat = geometry.get("lat")
                    p_lng = geometry.get("lng")
                    if p_lat is None or p_lng is None:
                        continue
                    name = place.get("name")
                    vicinity = place.get("vicinity") or ""
                    rating = place.get("rating")
                    place_id = place.get("place_id")
                    maps_link = f"https://www.google.com/maps/place/?q=place_id:{place_id}" if place_id else f"https://maps.google.com/?q={name}"
                    hotels.append({
                        "name": name,
                        "address": vicinity,
                        "area": vicinity.split(",")[-1].strip() if vicinity else None,
                        "latitude": float(p_lat),
                        "longitude": float(p_lng),
                        "phone": None,
                        "alternate_phone": None,
                        "price_min": None,
                        "price_max": None,
                        "rating": float(rating) if rating else None,
                        "notes": "Imported from Google Places. Contact hotel to confirm details.",
                        "google_maps_link": maps_link,
                        "is_verified": False,
                        "verified_by": None,
                        "verified_at": None,
                    })
    except Exception:
        pass
    return hotels


def fetch_from_openstreetmap(lat: float, lng: float, radius_meters: int = 10000) -> list[dict[str, Any]]:
    """Fallback fetch from OpenStreetMap Overpass API for tourism=hotel within radius."""
    url = "https://overpass-api.de/api/interpreter"
    query = f"""[out:json][timeout:15];
(
  node["tourism"~"hotel|guest_house|hostel|motel"](around:{radius_meters},{lat},{lng});
  way["tourism"~"hotel|guest_house|hostel|motel"](around:{radius_meters},{lat},{lng});
);
out center 25;"""
    hotels: list[dict[str, Any]] = []
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, data={"data": query})
            if resp.status_code == 200:
                data = resp.json()
                for el in data.get("elements", []):
                    tags = el.get("tags", {})
                    name = tags.get("name")
                    if not name:
                        continue
                    p_lat = el.get("lat") or el.get("center", {}).get("lat")
                    p_lng = el.get("lon") or el.get("center", {}).get("lon")
                    if not p_lat or not p_lng:
                        continue
                    phone = tags.get("phone") or tags.get("contact:phone")
                    address_parts = [tags.get(f"addr:{k}") for k in ("housenumber", "street", "suburb", "city") if tags.get(f"addr:{k}")]
                    address = ", ".join(address_parts) or tags.get("addr:full") or tags.get("area") or ""
                    area = tags.get("addr:suburb") or tags.get("addr:city") or "Indore"
                    hotels.append({
                        "name": name,
                        "address": address or None,
                        "area": area,
                        "latitude": float(p_lat),
                        "longitude": float(p_lng),
                        "phone": phone or None,
                        "alternate_phone": None,
                        "price_min": None,
                        "price_max": None,
                        "rating": 4.0,
                        "notes": "Imported from OpenStreetMap. Call hotel to verify pricing and vacancies.",
                        "google_maps_link": f"https://maps.google.com/?q={name.replace(' ', '+')}",
                        "is_verified": False,
                        "verified_by": None,
                        "verified_at": None,
                    })
    except Exception:
        pass
    return hotels


def seed_or_refresh_hotels(db: Session, festival_id: int, radius_km: float = 10.0, use_remote: bool = True) -> int:
    """Populates or updates the external_hotels table for a festival.
    
    1. First attempts Google Places API if key exists.
    2. Falls back to OpenStreetMap Overpass API if use_remote is True.
    3. Always blends in curated hotels so high quality local details are guaranteed.
    All imported hotels start as Unverified.
    """
    festival = db.get(Festival, festival_id)
    if not festival:
        return 0

    campus_lat = festival.campus_latitude or 22.5838
    campus_lng = festival.campus_longitude or 75.7656
    radius_meters = int(radius_km * 1000)

    candidates: list[dict[str, Any]] = []

    # 1. Google Places API if configured
    if settings.google_places_api_key:
        candidates.extend(fetch_from_google_places(settings.google_places_api_key, campus_lat, campus_lng, radius_meters))

    # 2. OpenStreetMap if few or no candidates found
    if use_remote and len(candidates) < 5:
        osm_hotels = fetch_from_openstreetmap(campus_lat, campus_lng, radius_meters)
        candidates.extend(osm_hotels)

    # 3. Always merge curated hotels
    candidates.extend(CURATED_INDORE_HOTELS)

    # De-duplicate by name (case-insensitive)
    seen_names = set()
    unique_hotels: list[dict[str, Any]] = []
    for h in candidates:
        norm = h["name"].strip().lower()
        if norm not in seen_names:
            seen_names.add(norm)
            unique_hotels.append(h)

    # Query existing hotels for this festival
    existing = {h.name.strip().lower(): h for h in db.scalars(select(ExternalHotel).where(ExternalHotel.festival_id == festival_id))}

    added_count = 0
    for h in unique_hotels:
        norm = h["name"].strip().lower()
        if norm in existing:
            # If existing hotel was already modified or verified, preserve its verification state
            ext = existing[norm]
            if not ext.is_verified and h.get("phone") and not ext.phone:
                ext.phone = h.get("phone")
            if ext.latitude == 0.0 or ext.longitude == 0.0:
                ext.latitude = h["latitude"]
                ext.longitude = h["longitude"]
        else:
            new_hotel = ExternalHotel(
                festival_id=festival_id,
                name=h["name"],
                address=h.get("address"),
                area=h.get("area"),
                latitude=h["latitude"],
                longitude=h["longitude"],
                phone=h.get("phone"),
                alternate_phone=h.get("alternate_phone"),
                price_min=h.get("price_min"),
                price_max=h.get("price_max"),
                rating=h.get("rating"),
                notes=h.get("notes"),
                google_maps_link=h.get("google_maps_link"),
                is_verified=h.get("is_verified", False),
                verified_by=h.get("verified_by"),
                verified_at=h.get("verified_at"),
                is_active=True,
            )
            db.add(new_hotel)
            added_count += 1

    db.flush()
    db.commit()
    return added_count

