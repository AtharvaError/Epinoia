"""Telegram Bot service for sending external hotel suggestions and location pins."""
from typing import Any
import httpx

from app.config import settings


class TelegramDeliveryError(Exception):
    def __init__(self, message: str, status_code: int = 400, details: Any = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> dict[str, Any]:
    """Send a text message to a Telegram chat."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(url, json=payload)
        data = resp.json()
        if not resp.is_success or not data.get("ok"):
            description = data.get("description", "Failed to send Telegram message")
            if "bot was blocked by the user" in description.lower():
                raise TelegramDeliveryError("The participant has blocked the Telegram bot.", 403, data)
            if "chat not found" in description.lower() or "user not found" in description.lower():
                raise TelegramDeliveryError("Telegram chat not found. The participant may not have started the bot.", 404, data)
            raise TelegramDeliveryError(f"Telegram error: {description}", resp.status_code, data)
        return data


def send_telegram_venue(bot_token: str, chat_id: str, title: str, address: str, latitude: float, longitude: float) -> dict[str, Any]:
    """Send a location pin/venue to a Telegram chat so the participant sees a tappable map pin."""
    url = f"https://api.telegram.org/bot{bot_token}/sendVenue"
    payload = {
        "chat_id": chat_id,
        "latitude": latitude,
        "longitude": longitude,
        "title": title[:120],
        "address": (address or "Indore, MP")[:120],
    }
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(url, json=payload)
        data = resp.json()
        if not resp.is_success or not data.get("ok"):
            description = data.get("description", "Failed to send hotel venue pin")
            raise TelegramDeliveryError(f"Telegram venue error: {description}", resp.status_code, data)
        return data


def deliver_stay_suggestions_via_telegram(
    chat_id: str,
    message_text: str,
    hotels: list[dict[str, Any]],
    bot_token: str | None = None,
) -> dict[str, Any]:
    """Sends text summary message followed by sendVenue pins for each hotel.
    
    If bot_token is empty or mock test, and environment is development,
    returns simulated success while noting token wasn't supplied.
    """
    token = bot_token or settings.telegram_bot_token
    if not token:
        # Development mode simulation when token has not been configured in .env yet
        return {
            "ok": True,
            "simulated": True,
            "messages_sent": 1 + len(hotels),
            "note": "Delivered in development simulation mode. Set TELEGRAM_BOT_TOKEN in .env to send to real Telegram accounts.",
        }

    # 1. Send the text message
    text_result = send_telegram_message(token, chat_id, message_text)

    # 2. Send venue pin for each hotel
    venue_results: list[dict[str, Any]] = []
    failed_venues: list[str] = []
    for h in hotels:
        try:
            vr = send_telegram_venue(
                bot_token=token,
                chat_id=chat_id,
                title=h["name"],
                address=h.get("address") or h.get("area") or "Indore",
                latitude=h["latitude"],
                longitude=h["longitude"],
            )
            venue_results.append(vr)
        except Exception as e:
            failed_venues.append(f"{h['name']}: {str(e)}")

    return {
        "ok": True,
        "simulated": False,
        "text_message_id": text_result.get("result", {}).get("message_id"),
        "venues_sent": len(venue_results),
        "failed_venues": failed_venues,
    }

