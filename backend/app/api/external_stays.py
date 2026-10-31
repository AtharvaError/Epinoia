"""External Stay Suggestions API.

Allows core team volunteers/admins to search hotels around campus, view on map,
and send hotel recommendations with map pins to participants via Telegram.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth.deps import current_user, festival_access, permissions_of
from app.database import get_db
from app.models import ExternalHotel, Festival, Participant, StaySuggestionLog, User
from app.services.audit import record
from app.services.external_stay_service import (
    approx_travel_time_minutes,
    haversine_distance,
    seed_or_refresh_hotels,
)
from app.services.telegram_service import (
    TelegramDeliveryError,
    deliver_stay_suggestions_via_telegram,
)

router = APIRouter(prefix="/api/festivals/{festival_id}", tags=["external_stays"])


# ------------------ Permissions ------------------

def require_stay_access(user: User = Depends(current_user)) -> User:
    """Ensure user belongs to core team with Accommodation or Stay Suggestions access."""
    perms = permissions_of(user)
    if (
        "can_send_stay_suggestions" not in perms
        and "accommodation.read" not in perms
        and "accommodation.write" not in perms
        and user.role.key not in ("super_admin", "festival_director", "accommodation_head")
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your role does not have permission to view or send stay suggestions.",
        )
    return user


# ------------------ Schemas ------------------

class ExternalHotelIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    address: str | None = Field(default=None, max_length=300)
    area: str | None = Field(default=None, max_length=120)
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    phone: str | None = Field(default=None, max_length=40)
    alternate_phone: str | None = Field(default=None, max_length=40)
    price_min: int | None = Field(default=None, ge=0)
    price_max: int | None = Field(default=None, ge=0)
    rating: float | None = Field(default=None, ge=1.0, le=5.0)
    notes: str | None = Field(default=None, max_length=1000)
    google_maps_link: str | None = Field(default=None, max_length=500)
    is_verified: bool = False


class ExternalHotelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    address: str | None = None
    area: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    phone: str | None = None
    alternate_phone: str | None = None
    price_min: int | None = None
    price_max: int | None = None
    rating: float | None = None
    notes: str | None = None
    google_maps_link: str | None = None
    is_verified: bool | None = None
    is_active: bool | None = None


class CampusLocationUpdate(BaseModel):
    campus_name: str = Field(min_length=2, max_length=120)
    campus_latitude: float = Field(ge=-90.0, le=90.0)
    campus_longitude: float = Field(ge=-180.0, le=180.0)


class ParticipantTelegramUpdate(BaseModel):
    telegram_chat_id: str | None = Field(default=None, max_length=64)
    telegram_username: str | None = Field(default=None, max_length=64)


class SendSuggestionsRequest(BaseModel):
    participant_id: int
    hotel_ids: list[int] = Field(min_length=1, max_length=5)
    message_override: str | None = None
    confirm_unverified: bool = False


class LogFallbackCopiedRequest(BaseModel):
    participant_id: int
    hotel_ids: list[int] = Field(min_length=1, max_length=5)
    message_text: str


# ------------------ Helpers ------------------

def serialize_hotel(h: ExternalHotel, campus_lat: float, campus_lng: float) -> dict[str, Any]:
    dist = haversine_distance(campus_lat, campus_lng, h.latitude, h.longitude)
    travel_time = approx_travel_time_minutes(dist)
    if h.price_min and h.price_max:
        price_display = f"₹{h.price_min:,} - ₹{h.price_max:,} / night"
    elif h.price_min:
        price_display = f"From ₹{h.price_min:,} / night"
    else:
        price_display = "Price unconfirmed"

    return {
        "id": h.id,
        "name": h.name,
        "address": h.address,
        "area": h.area,
        "latitude": h.latitude,
        "longitude": h.longitude,
        "phone": h.phone,
        "alternate_phone": h.alternate_phone,
        "price_min": h.price_min,
        "price_max": h.price_max,
        "price_display": price_display,
        "rating": h.rating,
        "notes": h.notes,
        "google_maps_link": h.google_maps_link or f"https://maps.google.com/?q={h.latitude},{h.longitude}",
        "is_verified": h.is_verified,
        "verified_by": h.verified_by,
        "verified_at": h.verified_at,
        "is_active": h.is_active,
        "distance_km": dist,
        "travel_time_min": travel_time,
    }


def build_default_message(hotels: list[dict[str, Any]], campus_name: str) -> str:
    """Build the standard WhatsApp / Telegram stay suggestion message."""
    lines = [
        f"🏨 <b>Outside Accommodation Suggestions — {campus_name}</b>",
        "Here are recommended nearby hotels for your stay during Aarohan 2026:\n",
    ]
    for idx, h in enumerate(hotels, start=1):
        verified_mark = "✅ Verified" if h.get("is_verified") else "⚠️ Unverified (Confirm before booking)"
        phone_txt = h.get("phone") or "Phone not available"
        price_txt = h.get("price_display") or "Contact hotel for pricing"
        lines.append(f"<b>{idx}. {h['name']}</b> ({verified_mark})")
        lines.append(f"📍 Distance: {h.get('distance_km', 0):.1f} km from campus (~{h.get('travel_time_min', 10)} mins by car)")
        lines.append(f"💰 Price: {price_txt}")
        lines.append(f"📞 Contact: {phone_txt}")
        if h.get("google_maps_link"):
            lines.append(f"🗺️ Map: {h['google_maps_link']}")
        lines.append("")

    lines.append("<i>Note: Hotel stay is at your own cost and booking. Please confirm availability and rates directly with the hotel.</i>")
    return "\n".join(lines)


# ------------------ Endpoints ------------------

@router.get("/external-hotels", dependencies=[Depends(require_stay_access)])
def list_external_hotels(
    radius: float = Query(10.0, ge=1.0, le=50.0),
    q: str | None = Query(None, max_length=80),
    verified_only: bool = False,
    has_phone: bool = False,
    min_rating: float | None = Query(None, ge=1.0, le=5.0),
    price_min: int | None = None,
    price_max: int | None = None,
    include_inactive: bool = False,
    festival: Festival = Depends(festival_access),
    db: Session = Depends(get_db),
):
    """Retrieve external stay suggestions sorted by distance from campus within selected radius."""
    campus_lat = festival.campus_latitude or 22.5838
    campus_lng = festival.campus_longitude or 75.7656

    query = select(ExternalHotel).where(ExternalHotel.festival_id == festival.id)
    if not include_inactive:
        query = query.where(ExternalHotel.is_active.is_(True))
    if verified_only:
        query = query.where(ExternalHotel.is_verified.is_(True))
    if has_phone:
        query = query.where(ExternalHotel.phone.isnot(None), ExternalHotel.phone != "")
    if min_rating:
        query = query.where(ExternalHotel.rating >= min_rating)
    if price_max:
        query = query.where(or_(ExternalHotel.price_min <= price_max, ExternalHotel.price_min.is_(None)))
    if price_min:
        query = query.where(or_(ExternalHotel.price_max >= price_min, ExternalHotel.price_max.is_(None)))

    all_hotels = list(db.scalars(query))

    # Auto-seed if database is currently empty for this festival
    if not all_hotels and not q and not verified_only:
        seed_or_refresh_hotels(db, festival.id, radius_km=radius, use_remote=False)
        all_hotels = list(db.scalars(query))

    # Calculate distance and serialize
    serialized: list[dict[str, Any]] = []
    for h in all_hotels:
        row = serialize_hotel(h, campus_lat, campus_lng)
        # Apply radius filter
        if row["distance_km"] <= radius:
            # Apply search keyword filter
            if q:
                term = q.lower()
                name_match = term in (h.name or "").lower()
                area_match = term in (h.area or "").lower()
                address_match = term in (h.address or "").lower()
                if not (name_match or area_match or address_match):
                    continue
            serialized.append(row)

    # Sort strictly by distance from campus
    serialized.sort(key=lambda x: x["distance_km"])

    return {
        "campus": {
            "name": festival.campus_name or "IIM Indore",
            "latitude": campus_lat,
            "longitude": campus_lng,
        },
        "radius_km": radius,
        "total": len(serialized),
        "items": serialized,
    }


@router.post("/external-hotels", status_code=201, dependencies=[Depends(require_stay_access)])
def create_external_hotel(
    body: ExternalHotelIn,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Add a new external hotel property manually."""
    now = datetime.now(timezone.utc)
    hotel = ExternalHotel(
        festival_id=festival.id,
        name=body.name,
        address=body.address,
        area=body.area,
        latitude=body.latitude,
        longitude=body.longitude,
        phone=body.phone,
        alternate_phone=body.alternate_phone,
        price_min=body.price_min,
        price_max=body.price_max,
        rating=body.rating,
        notes=body.notes,
        google_maps_link=body.google_maps_link or f"https://maps.google.com/?q={body.latitude},{body.longitude}",
        is_verified=body.is_verified,
        verified_by=user.name if body.is_verified else None,
        verified_at=now if body.is_verified else None,
        is_active=True,
    )
    db.add(hotel)
    db.flush()
    record(db, user, festival.id, "external_hotel.create", "external_hotel", hotel.id, f"{user.name} added hotel suggestion: {hotel.name}")
    db.commit()
    return serialize_hotel(hotel, festival.campus_latitude or 22.5838, festival.campus_longitude or 75.7656)


@router.patch("/external-hotels/{hotel_id}", dependencies=[Depends(require_stay_access)])
def update_external_hotel(
    hotel_id: int,
    body: ExternalHotelUpdate,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Update hotel details, verify phone/pricing, or activate/deactivate."""
    hotel = db.get(ExternalHotel, hotel_id)
    if not hotel or hotel.festival_id != festival.id:
        raise HTTPException(404, "Hotel not found")

    data = body.model_dump(exclude_unset=True)
    if "is_verified" in data:
        if data["is_verified"] and not hotel.is_verified:
            hotel.verified_by = user.name
            hotel.verified_at = datetime.now(timezone.utc)
        elif not data["is_verified"]:
            hotel.verified_by = None
            hotel.verified_at = None

    for k, v in data.items():
        setattr(hotel, k, v)

    record(db, user, festival.id, "external_hotel.update", "external_hotel", hotel.id, f"{user.name} updated hotel: {hotel.name}")
    db.commit()
    return serialize_hotel(hotel, festival.campus_latitude or 22.5838, festival.campus_longitude or 75.7656)


@router.delete("/external-hotels/{hotel_id}", status_code=204, dependencies=[Depends(require_stay_access)])
def delete_external_hotel(
    hotel_id: int,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Delete a hotel from external suggestions."""
    hotel = db.get(ExternalHotel, hotel_id)
    if not hotel or hotel.festival_id != festival.id:
        raise HTTPException(404, "Hotel not found")

    db.delete(hotel)
    record(db, user, festival.id, "external_hotel.delete", "external_hotel", hotel.id, f"{user.name} deleted hotel: {hotel.name}")
    db.commit()


@router.post("/external-hotels/refresh", dependencies=[Depends(require_stay_access)])
def refresh_external_hotels(
    radius: float = Query(10.0, ge=1.0, le=50.0),
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Pull / refresh hotels around campus using Google Places / OpenStreetMap."""
    added = seed_or_refresh_hotels(db, festival.id, radius_km=radius, use_remote=True)
    record(db, user, festival.id, "external_hotel.refresh", "external_hotel", None, f"{user.name} refreshed hotel suggestions ({added} added)")
    return {"status": "ok", "added": added}


@router.patch("/campus-coordinates", dependencies=[Depends(require_stay_access)])
def update_campus_coordinates(
    body: CampusLocationUpdate,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Edit campus coordinates stored for distance calculations."""
    festival.campus_name = body.campus_name
    festival.campus_latitude = body.campus_latitude
    festival.campus_longitude = body.campus_longitude
    record(
        db,
        user,
        festival.id,
        "festival.update_campus",
        "festival",
        festival.id,
        f"{user.name} updated campus coordinates to ({body.campus_latitude}, {body.campus_longitude})",
    )
    db.commit()
    return {
        "campus_name": festival.campus_name,
        "campus_latitude": festival.campus_latitude,
        "campus_longitude": festival.campus_longitude,
    }


# ------------------ Participants & Telegram ------------------

@router.get("/external-stays/participants", dependencies=[Depends(require_stay_access)])
def search_participants_for_stays(
    q: str = Query("", max_length=80),
    festival: Festival = Depends(festival_access),
    db: Session = Depends(get_db),
):
    """Search participants by name, phone, or code for sending stay suggestions.
    
    SAFETY RULE: Does NOT return or expose participant gender!
    """
    query = select(Participant).where(
        Participant.festival_id == festival.id,
        Participant.deleted_at.is_(None),
    )
    if q.strip():
        term = f"%{q.strip()}%"
        query = query.where(
            or_(
                Participant.full_name.ilike(term),
                Participant.code.ilike(term),
                Participant.phone.ilike(term),
                Participant.college.ilike(term),
            )
        )
    query = query.order_by(Participant.full_name).limit(20)

    rows = []
    for p in db.scalars(query):
        has_tg = bool(p.telegram_chat_id and p.telegram_chat_id.strip())
        rows.append({
            "id": p.id,
            "code": p.code,
            "name": p.full_name,
            "college": p.college,
            "phone": p.phone,
            "arrival_at": p.arrival_at,
            "telegram_status": "linked" if has_tg else "not_started",
            "telegram_chat_id": p.telegram_chat_id,
            "telegram_username": p.telegram_username,
        })
    return {"items": rows}


@router.patch("/external-stays/participants/{participant_id}/telegram", dependencies=[Depends(require_stay_access)])
def update_participant_telegram(
    participant_id: int,
    body: ParticipantTelegramUpdate,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Allows volunteers/admins to link a participant's Telegram Chat ID for testing or live support."""
    p = db.get(Participant, participant_id)
    if not p or p.festival_id != festival.id:
        raise HTTPException(404, "Participant not found")

    p.telegram_chat_id = body.telegram_chat_id.strip() if body.telegram_chat_id else None
    p.telegram_username = body.telegram_username.strip() if body.telegram_username else None
    db.commit()
    return {
        "id": p.id,
        "code": p.code,
        "name": p.full_name,
        "telegram_status": "linked" if p.telegram_chat_id else "not_started",
        "telegram_chat_id": p.telegram_chat_id,
        "telegram_username": p.telegram_username,
    }


@router.post("/external-stays/preview", dependencies=[Depends(require_stay_access)])
def preview_stay_message(
    hotel_ids: list[int],
    festival: Festival = Depends(festival_access),
    db: Session = Depends(get_db),
):
    """Generate default message preview and check for unverified hotels."""
    campus_lat = festival.campus_latitude or 22.5838
    campus_lng = festival.campus_longitude or 75.7656
    hotels = list(db.scalars(select(ExternalHotel).where(ExternalHotel.id.in_(hotel_ids), ExternalHotel.festival_id == festival.id)))
    serialized = [serialize_hotel(h, campus_lat, campus_lng) for h in hotels]
    serialized.sort(key=lambda x: x["distance_km"])

    has_unverified = any(not h["is_verified"] for h in serialized)
    unverified_names = [h["name"] for h in serialized if not h["is_verified"]]

    message = build_default_message(serialized, festival.campus_name or "IIM Indore")
    return {
        "message": message,
        "hotels": serialized,
        "has_unverified": has_unverified,
        "unverified_names": unverified_names,
    }


@router.post("/external-stays/send", dependencies=[Depends(require_stay_access)])
def send_stay_suggestions(
    body: SendSuggestionsRequest,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Send hotel recommendations + venue location pins to a participant on Telegram."""
    # 1. Rate limiting check: max 10 sends per volunteer per minute
    one_minute_ago = datetime.now(timezone.utc) - timedelta(minutes=1)
    recent_sends = db.scalar(
        select(func.count(StaySuggestionLog.id)).where(
            StaySuggestionLog.sent_by_id == user.id,
            StaySuggestionLog.created_at >= one_minute_ago,
        )
    ) or 0
    if recent_sends >= 10:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit reached: Maximum 10 stay suggestions per minute. Please wait a few seconds before sending again.",
        )

    # 2. Fetch participant
    participant = db.get(Participant, body.participant_id)
    if not participant or participant.festival_id != festival.id or participant.deleted_at is not None:
        raise HTTPException(404, "Participant not found")

    # 3. Fetch hotels
    campus_lat = festival.campus_latitude or 22.5838
    campus_lng = festival.campus_longitude or 75.7656
    hotels = list(db.scalars(select(ExternalHotel).where(ExternalHotel.id.in_(body.hotel_ids), ExternalHotel.festival_id == festival.id)))
    if not hotels:
        raise HTTPException(400, "No valid hotels selected")

    serialized_hotels = [serialize_hotel(h, campus_lat, campus_lng) for h in hotels]
    serialized_hotels.sort(key=lambda x: x["distance_km"])

    # 4. Safety rule: Unverified hotel confirmation
    has_unverified = any(not h["is_verified"] for h in serialized_hotels)
    if has_unverified and not body.confirm_unverified:
        unverified_names = [h["name"] for h in serialized_hotels if not h["is_verified"]]
        return {
            "success": False,
            "needs_confirmation": True,
            "unverified_names": unverified_names,
            "message": "Some selected hotels are unverified (phone and pricing unconfirmed). Please confirm to proceed.",
        }

    # 5. Build message text
    message_text = body.message_override.strip() if body.message_override else build_default_message(serialized_hotels, festival.campus_name or "IIM Indore")

    # 6. Telegram delivery
    chat_id = participant.telegram_chat_id
    if not chat_id or not chat_id.strip():
        # Participant has not started bot: do NOT fail silently, provide fallback!
        log = StaySuggestionLog(
            festival_id=festival.id,
            sent_by_id=user.id,
            sent_by_name=user.name,
            participant_id=participant.id,
            participant_name=participant.full_name,
            participant_code=participant.code,
            hotel_ids=[h["id"] for h in serialized_hotels],
            hotel_names=[h["name"] for h in serialized_hotels],
            message_text=message_text,
            delivery_channel="clipboard",
            delivery_status="no_telegram",
            error_message="Participant has not started the Telegram bot.",
        )
        db.add(log)
        db.commit()
        return {
            "success": False,
            "delivery_status": "no_telegram",
            "telegram_status": "not_started",
            "message": "This participant has not started the Telegram bot.",
            "fallback_available": True,
            "preview_message": message_text,
            "log_id": log.id,
        }

    try:
        delivery_res = deliver_stay_suggestions_via_telegram(
            chat_id=chat_id,
            message_text=message_text,
            hotels=serialized_hotels,
        )

        log = StaySuggestionLog(
            festival_id=festival.id,
            sent_by_id=user.id,
            sent_by_name=user.name,
            participant_id=participant.id,
            participant_name=participant.full_name,
            participant_code=participant.code,
            hotel_ids=[h["id"] for h in serialized_hotels],
            hotel_names=[h["name"] for h in serialized_hotels],
            message_text=message_text,
            delivery_channel="telegram",
            delivery_status="delivered",
            error_message=None,
        )
        db.add(log)
        record(
            db,
            user,
            festival.id,
            "external_stay.send",
            "participant",
            participant.code,
            f"{user.name} sent {len(serialized_hotels)} hotel suggestions to {participant.full_name} ({participant.code}) via Telegram",
        )
        db.commit()

        return {
            "success": True,
            "delivery_status": "delivered",
            "message": f"Stay suggestions and {len(serialized_hotels)} location pin(s) sent successfully via Telegram!",
            "log_id": log.id,
            "details": delivery_res,
        }

    except TelegramDeliveryError as e:
        log = StaySuggestionLog(
            festival_id=festival.id,
            sent_by_id=user.id,
            sent_by_name=user.name,
            participant_id=participant.id,
            participant_name=participant.full_name,
            participant_code=participant.code,
            hotel_ids=[h["id"] for h in serialized_hotels],
            hotel_names=[h["name"] for h in serialized_hotels],
            message_text=message_text,
            delivery_channel="telegram",
            delivery_status="failed",
            error_message=e.message,
        )
        db.add(log)
        db.commit()
        return {
            "success": False,
            "delivery_status": "failed",
            "message": e.message,
            "allow_retry": True,
            "fallback_available": True,
            "preview_message": message_text,
            "log_id": log.id,
        }


@router.post("/external-stays/log-fallback-copied", dependencies=[Depends(require_stay_access)])
def log_fallback_copied(
    body: LogFallbackCopiedRequest,
    festival: Festival = Depends(festival_access),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Log when a volunteer copies the formatted message to clipboard as fallback."""
    participant = db.get(Participant, body.participant_id)
    if not participant or participant.festival_id != festival.id:
        raise HTTPException(404, "Participant not found")

    hotels = list(db.scalars(select(ExternalHotel.name).where(ExternalHotel.id.in_(body.hotel_ids), ExternalHotel.festival_id == festival.id)))

    log = StaySuggestionLog(
        festival_id=festival.id,
        sent_by_id=user.id,
        sent_by_name=user.name,
        participant_id=participant.id,
        participant_name=participant.full_name,
        participant_code=participant.code,
        hotel_ids=body.hotel_ids,
        hotel_names=hotels,
        message_text=body.message_text,
        delivery_channel="clipboard",
        delivery_status="copied",
        error_message=None,
    )
    db.add(log)
    record(
        db,
        user,
        festival.id,
        "external_stay.copy_fallback",
        "participant",
        participant.code,
        f"{user.name} copied {len(hotels)} stay suggestions for {participant.full_name} ({participant.code})",
    )
    db.commit()
    return {"status": "ok", "log_id": log.id}


@router.get("/external-stays/history", dependencies=[Depends(require_stay_access)])
def list_stay_suggestions_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=5, le=100),
    festival: Festival = Depends(festival_access),
    db: Session = Depends(get_db),
):
    """Retrieve full audit history of all sent stay suggestions."""
    total = db.scalar(
        select(func.count(StaySuggestionLog.id)).where(StaySuggestionLog.festival_id == festival.id)
    ) or 0

    offset = (page - 1) * page_size
    query = (
        select(StaySuggestionLog)
        .where(StaySuggestionLog.festival_id == festival.id)
        .order_by(StaySuggestionLog.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    items = []
    for row in db.scalars(query):
        items.append({
            "id": row.id,
            "sent_at": row.created_at,
            "sent_by": row.sent_by_name,
            "participant": {
                "id": row.participant_id,
                "name": row.participant_name,
                "code": row.participant_code,
            },
            "hotel_ids": row.hotel_ids,
            "hotel_names": row.hotel_names,
            "delivery_channel": row.delivery_channel,
            "delivery_status": row.delivery_status,
            "error_message": row.error_message,
            "message_text": row.message_text,
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "pages": max(1, (total + page_size - 1) // page_size),
        "page_size": page_size,
    }
