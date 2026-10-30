"""accommodation operational fields

Revision ID: c4e8a91b2d03
Revises: 09a583a47481
"""
from alembic import op
import sqlalchemy as sa


revision = "c4e8a91b2d03"
down_revision = "09a583a47481"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("festivals", sa.Column("minor_housing_policy", sa.String(length=40), nullable=True))
    op.add_column("festivals", sa.Column("minor_policy_set_by", sa.String(length=120), nullable=True))

    op.add_column("participants", sa.Column("housing_preference", sa.String(length=40), nullable=True))
    op.add_column("participants", sa.Column("housing_outreach_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("participants", sa.Column("minor_supervision_status", sa.String(length=30), nullable=True))
    op.add_column("participants", sa.Column("minor_supervision_note", sa.String(length=240), nullable=True))

    op.add_column("hostels", sa.Column("accessibility_status", sa.String(length=20), server_default="unknown", nullable=False))
    op.add_column("hostels", sa.Column("step_free_entry", sa.Boolean(), nullable=True))
    op.add_column("hostels", sa.Column("ground_floor_rooms", sa.Integer(), nullable=True))
    op.add_column("hostels", sa.Column("accessible_bathrooms", sa.Integer(), nullable=True))
    op.add_column("hostels", sa.Column("has_lift", sa.Boolean(), nullable=True))
    op.add_column("hostels", sa.Column("accessibility_confirmed_by", sa.String(length=120), nullable=True))
    op.add_column("hostels", sa.Column("accessibility_confirmed_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("rooms", sa.Column("policy_confirmed_by", sa.String(length=120), nullable=True))
    op.add_column("rooms", sa.Column("near_washroom", sa.Boolean(), nullable=True))
    op.add_column("rooms", sa.Column("visual_assistance", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("rooms", "visual_assistance")
    op.drop_column("rooms", "near_washroom")
    op.drop_column("rooms", "policy_confirmed_by")

    op.drop_column("hostels", "accessibility_confirmed_at")
    op.drop_column("hostels", "accessibility_confirmed_by")
    op.drop_column("hostels", "has_lift")
    op.drop_column("hostels", "accessible_bathrooms")
    op.drop_column("hostels", "ground_floor_rooms")
    op.drop_column("hostels", "step_free_entry")
    op.drop_column("hostels", "accessibility_status")

    op.drop_column("participants", "minor_supervision_note")
    op.drop_column("participants", "minor_supervision_status")
    op.drop_column("participants", "housing_outreach_sent_at")
    op.drop_column("participants", "housing_preference")

    op.drop_column("festivals", "minor_policy_set_by")
    op.drop_column("festivals", "minor_housing_policy")

