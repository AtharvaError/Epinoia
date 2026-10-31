"""CLI script to refresh external stay suggestions from Google Places or OpenStreetMap.

Usage:
    python -m scripts.refresh_hotels
    python -m scripts.refresh_hotels --radius 15 --remote
    python -m scripts.refresh_hotels --verify-all
"""
import argparse
import sys
from sqlalchemy import select

from app.database import SessionLocal
from app.models import ExternalHotel, Festival
from app.services.external_stay_service import seed_or_refresh_hotels


def main():
    parser = argparse.ArgumentParser(description="Refresh external hotels around campus")
    parser.add_argument("--radius", type=float, default=10.0, help="Search radius in kilometers (default: 10.0)")
    parser.add_argument("--remote", action="store_true", help="Fetch remote data from Google Places / OpenStreetMap")
    parser.add_argument("--verify-all", action="store_true", help="Mark all existing hotels as verified")
    args = parser.parse_args()

    with SessionLocal() as db:
        festivals = list(db.scalars(select(Festival)))
        if not festivals:
            print("No festivals found in database. Run python -m scripts.seed first.")
            sys.exit(1)

        for fest in festivals:
            print(f"Processing festival: {fest.name} (ID: {fest.id})")
            if args.verify_all:
                hotels = list(db.scalars(select(ExternalHotel).where(ExternalHotel.festival_id == fest.id)))
                for h in hotels:
                    h.is_verified = True
                    h.verified_by = "Admin (CLI)"
                db.commit()
                print(f"Marked {len(hotels)} hotels as verified.")
            else:
                added = seed_or_refresh_hotels(db, fest.id, radius_km=args.radius, use_remote=args.remote)
                total = len(list(db.scalars(select(ExternalHotel).where(ExternalHotel.festival_id == fest.id))))
                print(f"Added {added} new hotels. Total available: {total}")


if __name__ == "__main__":
    main()

