"""equipment request and allocation

Revision ID: e7a1c3d5f902
Revises: d5f9b02c3e14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from app.auth.permissions import ROLES

revision = "e7a1c3d5f902"
down_revision = "d5f9b02c3e14"
branch_labels = None
depends_on = None

PERMS = ("equipment.read", "equipment.request", "equipment.approve", "equipment.receive")


def _stamps():
    return [sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "equipment_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("festival_id", sa.Integer(), sa.ForeignKey("festivals.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("item", sa.String(60), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("destination", sa.String(120), nullable=False),
        sa.Column("needed_by", sa.DateTime(timezone=True), nullable=False),
        sa.Column("priority", sa.String(10), server_default="normal", nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("source", sa.String(20), server_default="event_csv", nullable=False),
        sa.Column("source_text", sa.String(160)),
        sa.Column("status", sa.String(24), server_default="submitted", nullable=False),
        sa.Column("availability_snapshot", JSONB, server_default="{}", nullable=False),
        sa.Column("requested_by_name", sa.String(120), nullable=False),
        sa.Column("decided_by_name", sa.String(120)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decision_reason", sa.Text()),
        *_stamps(),
        sa.UniqueConstraint("festival_id", "code"),
        sa.CheckConstraint("quantity > 0", name="quantity_valid"),
    )
    op.create_table(
        "equipment_transfers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("festival_id", sa.Integer(), sa.ForeignKey("festivals.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("request_id", sa.Integer(), sa.ForeignKey("equipment_requests.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("equipment_transfers.id")),
        sa.Column("resource_id", sa.Integer(), sa.ForeignKey("resources.id"), nullable=False, index=True),
        sa.Column("kind", sa.String(10), server_default="delivery", nullable=False),
        sa.Column("from_location", sa.String(120), nullable=False),
        sa.Column("to_location", sa.String(120), nullable=False),
        sa.Column("status", sa.String(12), server_default="REQUESTED", nullable=False),
        sa.Column("quantity_planned", sa.Integer(), nullable=False),
        sa.Column("quantity_approved", sa.Integer()),
        sa.Column("quantity_dispatched", sa.Integer()),
        sa.Column("quantity_received", sa.Integer()),
        sa.Column("quantity_damaged", sa.Integer()),
        sa.Column("reserved_quantity", sa.Integer(), server_default="0", nullable=False),
        sa.Column("verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("stages", JSONB, server_default="{}", nullable=False),
        sa.Column("discrepancy_note", sa.Text()),
        sa.Column("condition_note", sa.Text()),
        *_stamps(),
    )
    conn = op.get_bind()
    for key in PERMS:
        conn.execute(sa.text("INSERT INTO permissions (key) VALUES (:k) ON CONFLICT (key) DO NOTHING"), {"k": key})
    for role_key, (_, granted) in ROLES.items():
        for key in granted & set(PERMS):
            conn.execute(sa.text("""INSERT INTO role_permissions (role_id, permission_id)
                SELECT r.id, p.id FROM roles r, permissions p WHERE r.key=:r AND p.key=:p ON CONFLICT DO NOTHING"""),
                         {"r": role_key, "p": key})


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE key LIKE 'equipment.%')"))
    conn.execute(sa.text("DELETE FROM permissions WHERE key LIKE 'equipment.%'"))
    op.drop_table("equipment_transfers")
    op.drop_table("equipment_requests")

