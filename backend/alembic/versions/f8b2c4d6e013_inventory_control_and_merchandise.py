"""inventory control and merchandise modules

Revision ID: f8b2c4d6e013
Revises: e7a1c3d5f902
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.auth.permissions import ALL, ROLES
from app.models import Festival
from app.services.pack_import import ensure_pack_extensions

revision = "f8b2c4d6e013"
down_revision = "e7a1c3d5f902"
branch_labels = None
depends_on = None


def _stamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    # 1. Resources columns & check constraint
    op.add_column("resources", sa.Column("in_transit_quantity", sa.Integer(), server_default="0", nullable=False))
    op.add_column("resources", sa.Column("version", sa.Integer(), server_default="1", nullable=False))
    op.drop_constraint("quantities_valid", "resources", type_="check")
    op.create_check_constraint(
        "quantities_valid",
        "resources",
        "quantity >= 0 AND reserved_quantity >= 0 AND in_transit_quantity >= 0",
    )

    # 2. Inventory locations & aliases
    op.create_table(
        "inventory_locations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("festival_id", sa.Integer(), sa.ForeignKey("festivals.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(24), server_default="stock_only", nullable=False),
        sa.Column("venue_id", sa.Integer(), sa.ForeignKey("venues.id", ondelete="SET NULL"), index=True),
        sa.Column("venue_code", sa.String(20)),
        sa.Column("matched_by_name", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("venue_confirmed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("venue_confirmed_by", sa.String(120)),
        sa.Column("venue_confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("position_unverified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("position_note", sa.String(240)),
        sa.Column("position_updated_by", sa.String(120)),
        sa.Column("position_updated_at", sa.DateTime(timezone=True)),
        *_stamps(),
        sa.UniqueConstraint("festival_id", "name"),
    )

    op.create_table(
        "equipment_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("festival_id", sa.Integer(), sa.ForeignKey("festivals.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("token", sa.String(80), nullable=False),
        sa.Column("target_item", sa.String(80)),
        sa.Column("verified", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("verified_by", sa.String(120)),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("source", sa.String(20), server_default="pack", nullable=False),
        *_stamps(),
        sa.UniqueConstraint("festival_id", "token"),
    )

    # 3. EquipmentRequests & Lines & Allocations & Transfers & Ledger
    op.add_column("equipment_requests", sa.Column("destination_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("equipment_requests", sa.Column("has_shortage", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.alter_column("equipment_requests", "needed_by", existing_type=sa.DateTime(timezone=True), nullable=True)

    op.create_table(
        "equipment_request_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("festival_id", sa.Integer(), sa.ForeignKey("festivals.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("request_id", sa.Integer(), sa.ForeignKey("equipment_requests.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("raw_token", sa.String(120)),
        sa.Column("item", sa.String(60), nullable=False),
        sa.Column("matched", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("confirmed_qty", sa.Integer(), nullable=False),
        sa.Column("qty_source", sa.String(30), server_default="organizer", nullable=False),
        sa.Column("confirmed_by", sa.String(120)),
        sa.Column("approved_qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("shortage_qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("shortage_decision", sa.String(40)),
        sa.Column("shortage_decision_qty", sa.Integer()),
        sa.Column("shortage_decision_note", sa.Text()),
        sa.Column("shortage_decided_by", sa.String(120)),
        sa.Column("shortage_decided_at", sa.DateTime(timezone=True)),
        *_stamps(),
        sa.CheckConstraint("confirmed_qty > 0 AND approved_qty >= 0 AND shortage_qty >= 0", name="req_line_qty_valid"),
    )

    op.create_table(
        "equipment_allocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("festival_id", sa.Integer(), sa.ForeignKey("festivals.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("request_id", sa.Integer(), sa.ForeignKey("equipment_requests.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("request_line_id", sa.Integer(), sa.ForeignKey("equipment_request_lines.id", ondelete="CASCADE"), index=True),
        sa.Column("resource_id", sa.Integer(), sa.ForeignKey("resources.id"), nullable=False, index=True),
        sa.Column("item", sa.String(60), nullable=False),
        sa.Column("source_location", sa.String(120), nullable=False),
        sa.Column("destination", sa.String(120), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), server_default="allocated", nullable=False),
        sa.Column("is_in_place", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("needs_check_exception", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("exception_by", sa.String(120)),
        sa.Column("exception_reason", sa.Text()),
        sa.Column("commitment_scope", sa.String(40), server_default="held for entire fest", nullable=False),
        *_stamps(),
        sa.CheckConstraint("qty > 0", name="alloc_qty_valid"),
    )

    op.alter_column("equipment_transfers", "request_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column("equipment_transfers", "status", existing_type=sa.String(12), type_=sa.String(24), nullable=False)
    op.add_column("equipment_transfers", sa.Column("request_line_id", sa.Integer(), sa.ForeignKey("equipment_request_lines.id", ondelete="SET NULL"), index=True))
    op.add_column("equipment_transfers", sa.Column("allocation_id", sa.Integer(), sa.ForeignKey("equipment_allocations.id", ondelete="SET NULL"), index=True))
    op.add_column("equipment_transfers", sa.Column("quantity_accepted", sa.Integer()))
    op.add_column("equipment_transfers", sa.Column("quantity_missing", sa.Integer(), server_default="0"))
    op.add_column("equipment_transfers", sa.Column("quantity_good_returned", sa.Integer()))
    op.add_column("equipment_transfers", sa.Column("quantity_needs_check_returned", sa.Integer()))
    op.add_column("equipment_transfers", sa.Column("discrepancy_status", sa.String(24)))
    op.add_column("equipment_transfers", sa.Column("discrepancy_resolved_by", sa.String(120)))
    op.add_column("equipment_transfers", sa.Column("discrepancy_resolved_at", sa.DateTime(timezone=True)))
    op.add_column("equipment_transfers", sa.Column("discrepancy_resolution_note", sa.Text()))
    op.add_column("equipment_transfers", sa.Column("dispatch_idempotency_key", sa.String(80), unique=True))
    op.add_column("equipment_transfers", sa.Column("receive_idempotency_key", sa.String(80), unique=True))
    op.add_column("equipment_transfers", sa.Column("return_idempotency_key", sa.String(80), unique=True))

    op.create_table(
        "movement_ledger",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("festival_id", sa.Integer(), sa.ForeignKey("festivals.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("action", sa.String(40), nullable=False, index=True),
        sa.Column("item", sa.String(60), nullable=False, index=True),
        sa.Column("resource_code", sa.String(40)),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("from_location", sa.String(120)),
        sa.Column("to_location", sa.String(120)),
        sa.Column("from_condition", sa.String(30)),
        sa.Column("to_condition", sa.String(30)),
        sa.Column("event_code", sa.String(20), index=True),
        sa.Column("request_code", sa.String(20), index=True),
        sa.Column("transfer_id", sa.Integer()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("actor_name", sa.String(120), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("idempotency_key", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True),
    )

    op.execute("""
        CREATE TRIGGER movement_ledger_append_only
        BEFORE UPDATE OR DELETE ON movement_ledger
        FOR EACH ROW EXECUTE FUNCTION reject_audit_change()
    """)

    # Migrate any existing equipment_requests rows into equipment_request_lines
    conn = op.get_bind()
    conn.execute(sa.text("""
        INSERT INTO equipment_request_lines
            (festival_id, request_id, raw_token, item, matched, confirmed_qty, qty_source, confirmed_by, approved_qty, shortage_qty)
        SELECT
            r.festival_id,
            r.id,
            COALESCE(r.source_text, r.item),
            r.item,
            true,
            r.quantity,
            CASE WHEN r.source = 'event_csv' THEN 'events_csv' ELSE 'organizer' END,
            r.requested_by_name,
            0,
            0
        FROM equipment_requests r
        WHERE NOT EXISTS (
            SELECT 1 FROM equipment_request_lines l WHERE l.request_id = r.id
        )
    """))

    # 4. Merchandise & Distribution tables
    op.create_table(
        "merch_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("festival_id", sa.Integer(), sa.ForeignKey("festivals.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("item", sa.String(80), nullable=False),
        sa.Column("size", sa.String(16), nullable=False),
        sa.Column("on_hand", sa.Integer(), nullable=False),
        sa.Column("csv_reserved", sa.Integer(), server_default="0", nullable=False),
        sa.Column("csv_reserved_note", sa.String(240)),
        sa.Column("csv_reserved_annotated_by", sa.String(120)),
        sa.Column("csv_reserved_annotated_at", sa.DateTime(timezone=True)),
        sa.Column("plan_reserved", sa.Integer(), server_default="0", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("entitlement_rule", sa.String(40), server_default="manual_only", nullable=False),
        sa.Column("entitlement_confirmed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("entitlement_confirmed_by", sa.String(120)),
        sa.Column("entitlement_confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("max_per_person", sa.Integer(), server_default="1", nullable=False),
        *_stamps(),
        sa.UniqueConstraint("festival_id", "item", "size", name="uq_merch_item_size"),
        sa.UniqueConstraint("festival_id", "code", name="uq_merch_item_code"),
        sa.CheckConstraint(
            "on_hand >= 0 AND csv_reserved >= 0 AND plan_reserved >= 0 AND on_hand >= csv_reserved + plan_reserved",
            name="merch_quantities_valid",
        ),
    )

    op.create_table(
        "merch_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("festival_id", sa.Integer(), sa.ForeignKey("festivals.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("assumptions_confirmed", JSONB, server_default="{}", nullable=False),
        sa.Column("kit_templates", JSONB, server_default="{}", nullable=False),
        sa.Column("kit_confirmed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("kit_confirmed_by", sa.String(120)),
        sa.Column("kit_confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("staff_leader_quota", JSONB),
        sa.Column("staff_quota_set_by", sa.String(120)),
        sa.Column("staff_quota_set_at", sa.DateTime(timezone=True)),
        sa.Column("substitution_rule", JSONB, server_default='{"max_steps": 1, "direction": "up_preferred"}', nullable=False),
        sa.Column("active_policy", sa.String(20)),
        sa.Column("approved_plan", JSONB),
        sa.Column("plan_approved_by", sa.String(120)),
        sa.Column("plan_approved_at", sa.DateTime(timezone=True)),
        sa.Column("plan_stale", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("plan_stale_reasons", JSONB, server_default="[]", nullable=False),
        sa.Column("tray_prep_status", JSONB, server_default="{}", nullable=False),
        *_stamps(),
        sa.UniqueConstraint("festival_id"),
    )

    op.create_table(
        "merch_issues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("festival_id", sa.Integer(), sa.ForeignKey("festivals.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("participant_id", sa.Integer(), sa.ForeignKey("participants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("merch_item_id", sa.Integer(), sa.ForeignKey("merch_items.id"), nullable=False, index=True),
        sa.Column("item", sa.String(80), nullable=False, index=True),
        sa.Column("requested_size", sa.String(16), nullable=False),
        sa.Column("issued_size", sa.String(16), nullable=False),
        sa.Column("is_substitution", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("consent_recorded", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("qty", sa.Integer(), server_default="1", nullable=False),
        sa.Column("desk", sa.String(60)),
        sa.Column("issued_by", sa.String(120), nullable=False),
        sa.Column("issued_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("issued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True),
        sa.Column("verification_method", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), server_default="issued", nullable=False),
        sa.Column("voided_by", sa.String(120)),
        sa.Column("voided_at", sa.DateTime(timezone=True)),
        sa.Column("void_reason", sa.Text()),
        sa.Column("event_code", sa.String(20)),
        sa.Column("position", sa.String(40)),
        sa.Column("badge_category", sa.String(60)),
        sa.Column("override_reason", sa.Text()),
        sa.Column("override_approved_by", sa.String(120)),
        sa.Column("idempotency_key", sa.String(80), unique=True),
        *_stamps(),
        sa.CheckConstraint("qty > 0", name="merch_issue_qty_valid"),
    )
    op.create_index(
        "uq_merch_issue_once_per_person",
        "merch_issues",
        ["participant_id", "item"],
        unique=True,
        postgresql_where=sa.text("status = 'issued' AND item <> 'winner medal' AND override_reason IS NULL"),
    )
    op.create_index(
        "uq_merch_issue_medal",
        "merch_issues",
        ["participant_id", "item", "event_code", "position"],
        unique=True,
        postgresql_where=sa.text("status = 'issued' AND item = 'winner medal' AND override_reason IS NULL"),
    )

    op.create_table(
        "merch_owed",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("festival_id", sa.Integer(), sa.ForeignKey("festivals.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("participant_id", sa.Integer(), sa.ForeignKey("participants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("item", sa.String(80), nullable=False, index=True),
        sa.Column("requested_size", sa.String(16), nullable=False),
        sa.Column("proposed_substitute_size", sa.String(16)),
        sa.Column("status", sa.String(24), server_default="owed", nullable=False),
        sa.Column("reason", sa.String(40), server_default="size_short", nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("created_by", sa.String(120), nullable=False),
        *_stamps(),
    )
    op.create_index(
        "uq_merch_owed_active",
        "merch_owed",
        ["festival_id", "participant_id", "item"],
        unique=True,
        postgresql_where=sa.text("status <> 'collected'"),
    )

    op.create_table(
        "merch_incoming",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("festival_id", sa.Integer(), sa.ForeignKey("festivals.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("merch_item_id", sa.Integer(), sa.ForeignKey("merch_items.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("item", sa.String(80), nullable=False),
        sa.Column("size", sa.String(16), nullable=False),
        sa.Column("expected_qty", sa.Integer(), nullable=False),
        sa.Column("expected_date", sa.Date()),
        sa.Column("note", sa.Text()),
        sa.Column("status", sa.String(24), server_default="expected", nullable=False),
        sa.Column("qty_received", sa.Integer(), server_default="0", nullable=False),
        sa.Column("discrepancy_qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("discrepancy_note", sa.Text()),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("received_by", sa.String(120)),
        sa.Column("received_at", sa.DateTime(timezone=True)),
        *_stamps(),
        sa.CheckConstraint("expected_qty > 0 AND qty_received >= 0", name="merch_incoming_qty_valid"),
    )

    op.create_table(
        "merch_ledger",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("festival_id", sa.Integer(), sa.ForeignKey("festivals.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("merch_item_id", sa.Integer(), sa.ForeignKey("merch_items.id", ondelete="SET NULL"), index=True),
        sa.Column("action", sa.String(40), nullable=False, index=True),
        sa.Column("item", sa.String(80), nullable=False, index=True),
        sa.Column("size", sa.String(16), nullable=False),
        sa.Column("qty_delta", sa.Integer(), nullable=False),
        sa.Column("participant_code", sa.String(20), index=True),
        sa.Column("participant_name", sa.String(120)),
        sa.Column("desk", sa.String(60)),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("actor_name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("is_exception", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True),
    )

    op.execute("""
        CREATE TRIGGER merch_ledger_append_only
        BEFORE UPDATE OR DELETE ON merch_ledger
        FOR EACH ROW EXECUTE FUNCTION reject_audit_change()
    """)

    # 5. Sync all permissions and role_permissions
    for key in sorted(ALL):
        conn.execute(sa.text("INSERT INTO permissions (key) VALUES (:k) ON CONFLICT (key) DO NOTHING"), {"k": key})
    conn.execute(sa.text("DELETE FROM role_permissions"))
    for role_key, (_, granted) in ROLES.items():
        for key in sorted(granted):
            conn.execute(
                sa.text("""
                    INSERT INTO role_permissions (role_id, permission_id)
                    SELECT r.id, p.id FROM roles r, permissions p WHERE r.key=:r AND p.key=:p
                    ON CONFLICT DO NOTHING
                """),
                {"r": role_key, "p": key},
            )

    # 6. Populate pack extensions for any existing festival
    db = Session(bind=conn)
    for fest in db.scalars(sa.select(Festival)):
        ensure_pack_extensions(db, fest.id)
    db.flush()


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS merch_ledger_append_only ON merch_ledger")
    op.drop_table("merch_ledger")
    op.drop_table("merch_incoming")
    op.drop_index("uq_merch_owed_active", table_name="merch_owed")
    op.drop_table("merch_owed")
    op.drop_index("uq_merch_issue_medal", table_name="merch_issues")
    op.drop_index("uq_merch_issue_once_per_person", table_name="merch_issues")
    op.drop_table("merch_issues")
    op.drop_table("merch_configs")
    op.drop_table("merch_items")
    op.execute("DROP TRIGGER IF EXISTS movement_ledger_append_only ON movement_ledger")
    op.drop_table("movement_ledger")
    op.drop_column("equipment_transfers", "return_idempotency_key")
    op.drop_column("equipment_transfers", "receive_idempotency_key")
    op.drop_column("equipment_transfers", "dispatch_idempotency_key")
    op.drop_column("equipment_transfers", "discrepancy_resolution_note")
    op.drop_column("equipment_transfers", "discrepancy_resolved_at")
    op.drop_column("equipment_transfers", "discrepancy_resolved_by")
    op.drop_column("equipment_transfers", "discrepancy_status")
    op.drop_column("equipment_transfers", "quantity_needs_check_returned")
    op.drop_column("equipment_transfers", "quantity_good_returned")
    op.drop_column("equipment_transfers", "quantity_missing")
    op.drop_column("equipment_transfers", "quantity_accepted")
    op.drop_column("equipment_transfers", "allocation_id")
    op.drop_column("equipment_transfers", "request_line_id")
    op.drop_table("equipment_allocations")
    op.drop_table("equipment_request_lines")
    op.drop_column("equipment_requests", "has_shortage")
    op.drop_column("equipment_requests", "destination_verified")
    op.drop_table("equipment_aliases")
    op.drop_table("inventory_locations")
    op.drop_column("resources", "version")
    op.drop_column("resources", "in_transit_quantity")

