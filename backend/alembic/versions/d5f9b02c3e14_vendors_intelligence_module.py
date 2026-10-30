"""vendors intelligence module

Revision ID: d5f9b02c3e14
Revises: c4e8a91b2d03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from app.auth.permissions import ROLES


revision = "d5f9b02c3e14"
down_revision = "c4e8a91b2d03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vendors", sa.Column("source", sa.String(length=20), server_default="pack", nullable=False))
    op.add_column("vendors", sa.Column("original_pack_data", JSONB, nullable=True))
    op.add_column("vendors", sa.Column("operational_status", sa.String(length=24), server_default="unconfirmed", nullable=False))
    op.add_column("vendors", sa.Column("capacity_unit", sa.String(length=40), nullable=True))
    op.add_column("vendors", sa.Column("capacity_verified_by", sa.String(length=120), nullable=True))
    op.add_column("vendors", sa.Column("capacity_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("vendors", sa.Column("verified_dietary_options", JSONB, nullable=True))
    op.add_column("vendors", sa.Column("nut_free_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("vendors", sa.Column("allergen_controls_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("vendors", sa.Column("cross_contamination_note", sa.String(length=240), nullable=True))
    op.add_column("vendors", sa.Column("certificate_verified_by", sa.String(length=120), nullable=True))
    op.add_column("vendors", sa.Column("certificate_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("vendors", sa.Column("certificate_verification_note", sa.String(length=240), nullable=True))
    op.add_column("vendors", sa.Column("contact_name", sa.String(length=120), nullable=True))
    op.add_column("vendors", sa.Column("contact_phone", sa.String(length=40), nullable=True))
    op.add_column("vendors", sa.Column("verification_notes", sa.Text(), nullable=True))
    op.add_column("vendors", sa.Column("verified_by", sa.String(length=120), nullable=True))
    op.add_column("vendors", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("vendors", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "vendor_interruptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("festival_id", sa.Integer(), sa.ForeignKey("festivals.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("vendor_id", sa.Integer(), sa.ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("vendor_code", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("simulation_report", JSONB, server_default="{}", nullable=False),
        sa.Column("replacement_codes", JSONB, server_default="[]", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="simulated", nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_by_name", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("decided_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_by_name", sa.String(length=120), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
    )

    conn = op.get_bind()
    # Populate original_pack_data for existing pack vendor rows so source provenance is preserved
    conn.execute(sa.text("""
        UPDATE vendors
        SET original_pack_data = jsonb_build_object(
            'vendor_id', code,
            'vendor_name', name,
            'menu', COALESCE(menu, ''),
            'dietary_options', COALESCE((SELECT string_agg(elem, ';') FROM jsonb_array_elements_text(dietary_options) AS elem), ''),
            'hygiene_certificate_expiry', COALESCE(hygiene_certificate_expiry::text, ''),
            'hours', COALESCE(hours, ''),
            'capacity', COALESCE(capacity::text, ''),
            'payment_terms', COALESCE(payment_terms, '')
        )
        WHERE original_pack_data IS NULL
    """))

    # Sync new permissions into permissions and role_permissions tables
    for perm_key in ("vendors.read", "vendors.write", "vendors.approve"):
        conn.execute(
            sa.text("INSERT INTO permissions (key) VALUES (:k) ON CONFLICT (key) DO NOTHING"),
            {"k": perm_key},
        )
    for role_key, (_, granted) in ROLES.items():
        for perm_key in granted:
            if perm_key.startswith("vendors."):
                conn.execute(
                    sa.text("""
                        INSERT INTO role_permissions (role_id, permission_id)
                        SELECT r.id, p.id FROM roles r, permissions p
                        WHERE r.key = :rk AND p.key = :pk
                        ON CONFLICT DO NOTHING
                    """),
                    {"rk": role_key, "pk": perm_key},
                )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("""
            DELETE FROM role_permissions
            WHERE permission_id IN (SELECT id FROM permissions WHERE key IN ('vendors.read', 'vendors.write', 'vendors.approve'))
        """)
    )
    conn.execute(sa.text("DELETE FROM permissions WHERE key IN ('vendors.read', 'vendors.write', 'vendors.approve')"))

    op.drop_table("vendor_interruptions")

    for col in (
        "archived_at", "verified_at", "verified_by", "verification_notes", "contact_phone", "contact_name",
        "certificate_verification_note", "certificate_verified_at", "certificate_verified_by",
        "cross_contamination_note", "allergen_controls_verified", "nut_free_verified", "verified_dietary_options",
        "capacity_verified_at", "capacity_verified_by", "capacity_unit", "operational_status", "original_pack_data", "source"
    ):
        op.drop_column("vendors", col)

