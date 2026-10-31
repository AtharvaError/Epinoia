"""external stay suggestions

Revision ID: e6a1b2c3d4f5
Revises: d5f9b02c3e14
"""
import sqlalchemy as sa
from alembic import op
from app.auth.permissions import ROLES
from sqlalchemy.dialects.postgresql import JSONB

revision = "e6a1b2c3d4f5"
down_revision = "d5f9b02c3e14"
branch_labels = None
depends_on = None

CURATED_HOTELS = [
    {
        "name": "Papaya Tree Hotel",
        "address": "A.B. Road, Rau Bypass, Indore, Madhya Pradesh 453331",
        "area": "Rau Bypass",
        "latitude": 22.6315,
        "longitude": 75.8242,
        "phone": "+91 731 475 0000",
        "alternate_phone": "+91 98260 00001",
        "price_min": 2800,
        "price_max": 4200,
        "rating": 4.2,
        "notes": "3-star business hotel. 24h room service, banquet halls. Very close to Rau circle.",
        "google_maps_link": "https://maps.google.com/?q=22.6315,75.8242",
        "is_verified": True,
        "verified_by": "Aditya Deshmukh (Accommodation Head)",
    },
    {
        "name": "The Red Maple Mashal",
        "address": "Jhabua Custom Road, Rau, Indore, Madhya Pradesh 453331",
        "area": "Rau",
        "latitude": 22.6240,
        "longitude": 75.8150,
        "phone": "+91 731 479 0000",
        "alternate_phone": "+91 98260 00002",
        "price_min": 3200,
        "price_max": 5500,
        "rating": 4.4,
        "notes": "Premium resort-style property with swimming pool and landscaped gardens. Group discounts available.",
        "google_maps_link": "https://maps.google.com/?q=22.6240,75.8150",
        "is_verified": True,
        "verified_by": "Aditya Deshmukh (Accommodation Head)",
    },
    {
        "name": "Hotel Solaris Pithampur",
        "address": "Sector 1, Pithampur Industrial Area, Madhya Pradesh 454775",
        "area": "Pithampur",
        "latitude": 22.6020,
        "longitude": 75.7150,
        "phone": "+91 729 240 4000",
        "alternate_phone": None,
        "price_min": 1800,
        "price_max": 2800,
        "rating": 3.9,
        "notes": "Standard industrial corridor hotel with clean AC rooms. Wi-Fi and breakfast included.",
        "google_maps_link": "https://maps.google.com/?q=22.6020,75.7150",
        "is_verified": False,
        "verified_by": None,
    },
    {
        "name": "Hotel Pride Rau",
        "address": "Opposite Rau Railway Station, Indore, Madhya Pradesh 453331",
        "area": "Rau",
        "latitude": 22.6185,
        "longitude": 75.8080,
        "phone": "+91 98260 12345",
        "alternate_phone": None,
        "price_min": 1500,
        "price_max": 2200,
        "rating": 3.8,
        "notes": "Budget option right next to Rau railway station. Suitable for quick transit.",
        "google_maps_link": "https://maps.google.com/?q=22.6185,75.8080",
        "is_verified": False,
        "verified_by": None,
    },
    {
        "name": "Hotel Highway King",
        "address": "Pithampur-Rau Main Road, Dongargaon, Madhya Pradesh 453331",
        "area": "Dongargaon",
        "latitude": 22.5950,
        "longitude": 75.7300,
        "phone": "+91 98930 54321",
        "alternate_phone": None,
        "price_min": 1200,
        "price_max": 2000,
        "rating": 3.7,
        "notes": "Closest roadside budget hotel towards Pithampur. Restaurant on ground floor.",
        "google_maps_link": "https://maps.google.com/?q=22.5950,75.7300",
        "is_verified": False,
        "verified_by": None,
    },
    {
        "name": "Hotel Rajshree",
        "address": "Near Silicon City, A.B. Road, Indore, Madhya Pradesh 452012",
        "area": "Silicon City",
        "latitude": 22.6450,
        "longitude": 75.8320,
        "phone": "+91 731 285 6600",
        "alternate_phone": None,
        "price_min": 2000,
        "price_max": 3200,
        "rating": 4.0,
        "notes": "Comfortable 3-star property near Silicon City entrance. Direct highway access.",
        "google_maps_link": "https://maps.google.com/?q=22.6450,75.8320",
        "is_verified": False,
        "verified_by": None,
    },
    {
        "name": "Hotel Lakeview Residency",
        "address": "Near Pipliyapala Regional Park, Ring Road, Indore, Madhya Pradesh 452020",
        "area": "Pipliyapala",
        "latitude": 22.6720,
        "longitude": 75.8450,
        "phone": "+91 97555 11223",
        "alternate_phone": None,
        "price_min": 1800,
        "price_max": 2600,
        "rating": 3.9,
        "notes": "Scenic location near Pipliyapala lake. Peaceful environment, in-house cafe.",
        "google_maps_link": "https://maps.google.com/?q=22.6720,75.8450",
        "is_verified": False,
        "verified_by": None,
    },
    {
        "name": "Treebo Trend Shivani",
        "address": "Bhawarkua Main Road, Near Rajiv Gandhi Square, Indore, Madhya Pradesh 452001",
        "area": "Bhawarkua",
        "latitude": 22.6880,
        "longitude": 75.8550,
        "phone": "+91 93228 00100",
        "alternate_phone": None,
        "price_min": 2200,
        "price_max": 3400,
        "rating": 4.1,
        "notes": "Standardized Treebo chain hospitality. Free high-speed Wi-Fi, elevator, parking.",
        "google_maps_link": "https://maps.google.com/?q=22.6880,75.8550",
        "is_verified": False,
        "verified_by": None,
    },
    {
        "name": "Hotel Apna Palace",
        "address": "Dhar Road, Near Gangwal Bus Stand, Indore, Madhya Pradesh 452002",
        "area": "Dhar Road",
        "latitude": 22.7050,
        "longitude": 75.8350,
        "phone": "+91 731 400 9999",
        "alternate_phone": None,
        "price_min": 2400,
        "price_max": 3800,
        "rating": 4.0,
        "notes": "Spacious family and group rooms. Multi-cuisine vegetarian dining hall.",
        "google_maps_link": "https://maps.google.com/?q=22.7050,75.8350",
        "is_verified": False,
        "verified_by": None,
    },
    {
        "name": "Effotel Hotel by Sayaji",
        "address": "Plot No. 10-C/C.A., Scheme No. 94, Sector C, Indore, Madhya Pradesh 452010",
        "area": "Vijay Nagar / Ring Road",
        "latitude": 22.7480,
        "longitude": 75.8920,
        "phone": "+91 731 474 4000",
        "alternate_phone": "+91 731 474 4040",
        "price_min": 4000,
        "price_max": 6500,
        "rating": 4.5,
        "notes": "4-star upscale business property. Valet parking, fitness center, corporate banquet halls.",
        "google_maps_link": "https://maps.google.com/?q=22.7480,75.8920",
        "is_verified": True,
        "verified_by": "Aditya Deshmukh (Accommodation Head)",
    },
]


def upgrade() -> None:
    # 1. Add campus coordinates to festivals
    op.add_column("festivals", sa.Column("campus_latitude", sa.Float(), server_default="22.5838", nullable=False))
    op.add_column("festivals", sa.Column("campus_longitude", sa.Float(), server_default="75.7656", nullable=False))
    op.add_column("festivals", sa.Column("campus_name", sa.String(length=120), server_default="IIM Indore", nullable=False))

    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE festivals
        SET campus_latitude = 22.5838, campus_longitude = 75.7656, campus_name = 'IIM Indore'
        WHERE campus_latitude IS NULL OR campus_longitude IS NULL OR campus_name IS NULL
    """))

    # 2. Add Telegram fields to participants
    op.add_column("participants", sa.Column("telegram_chat_id", sa.String(length=64), nullable=True))
    op.add_column("participants", sa.Column("telegram_username", sa.String(length=64), nullable=True))

    # 3. Create external_hotels table
    op.create_table(
        "external_hotels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("festival_id", sa.Integer(), sa.ForeignKey("festivals.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(length=160), nullable=False, index=True),
        sa.Column("address", sa.String(length=300), nullable=True),
        sa.Column("area", sa.String(length=120), nullable=True, index=True),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("alternate_phone", sa.String(length=40), nullable=True),
        sa.Column("price_min", sa.Integer(), nullable=True),
        sa.Column("price_max", sa.Integer(), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("google_maps_link", sa.String(length=500), nullable=True),
        sa.Column("is_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("verified_by", sa.String(length=120), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 4. Create stay_suggestion_logs table
    op.create_table(
        "stay_suggestion_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("festival_id", sa.Integer(), sa.ForeignKey("festivals.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("sent_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True, index=True),
        sa.Column("sent_by_name", sa.String(length=120), nullable=False),
        sa.Column("participant_id", sa.Integer(), sa.ForeignKey("participants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("participant_name", sa.String(length=120), nullable=False),
        sa.Column("participant_code", sa.String(length=20), nullable=False),
        sa.Column("hotel_ids", JSONB, server_default="[]", nullable=False),
        sa.Column("hotel_names", JSONB, server_default="[]", nullable=False),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column("delivery_channel", sa.String(length=30), nullable=False),
        sa.Column("delivery_status", sa.String(length=30), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 5. Sync permission
    conn.execute(
        sa.text("INSERT INTO permissions (key) VALUES (:k) ON CONFLICT (key) DO NOTHING"),
        {"k": "can_send_stay_suggestions"},
    )
    for role_key, (_, granted) in ROLES.items():
        if "can_send_stay_suggestions" in granted:
            conn.execute(
                sa.text("""
                    INSERT INTO role_permissions (role_id, permission_id)
                    SELECT r.id, p.id FROM roles r, permissions p
                    WHERE r.key = :rk AND p.key = 'can_send_stay_suggestions'
                    ON CONFLICT DO NOTHING
                """),
                {"rk": role_key},
            )

    # 6. Seed curated Indore hotels for existing festivals
    festivals = conn.execute(sa.text("SELECT id FROM festivals")).fetchall()
    for (fest_id,) in festivals:
        for h in CURATED_HOTELS:
            conn.execute(
                sa.text("""
                    INSERT INTO external_hotels (
                        festival_id, name, address, area, latitude, longitude,
                        phone, alternate_phone, price_min, price_max, rating, notes,
                        google_maps_link, is_verified, verified_by, is_active
                    ) VALUES (
                        :fest_id, :name, :address, :area, :latitude, :longitude,
                        :phone, :alternate_phone, :price_min, :price_max, :rating, :notes,
                        :google_maps_link, :is_verified, :verified_by, true
                    ) ON CONFLICT DO NOTHING
                """),
                {
                    "fest_id": fest_id,
                    "name": h["name"],
                    "address": h["address"],
                    "area": h["area"],
                    "latitude": h["latitude"],
                    "longitude": h["longitude"],
                    "phone": h["phone"],
                    "alternate_phone": h["alternate_phone"],
                    "price_min": h["price_min"],
                    "price_max": h["price_max"],
                    "rating": h["rating"],
                    "notes": h["notes"],
                    "google_maps_link": h["google_maps_link"],
                    "is_verified": h["is_verified"],
                    "verified_by": h["verified_by"],
                },
            )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("""
            DELETE FROM role_permissions
            WHERE permission_id IN (SELECT id FROM permissions WHERE key = 'can_send_stay_suggestions')
        """)
    )
    conn.execute(sa.text("DELETE FROM permissions WHERE key = 'can_send_stay_suggestions'"))

    op.drop_table("stay_suggestion_logs")
    op.drop_table("external_hotels")
    op.drop_column("participants", "telegram_username")
    op.drop_column("participants", "telegram_chat_id")
    op.drop_column("festivals", "campus_name")
    op.drop_column("festivals", "campus_longitude")
    op.drop_column("festivals", "campus_latitude")
