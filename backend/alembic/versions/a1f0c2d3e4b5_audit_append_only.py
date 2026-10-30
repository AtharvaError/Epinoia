"""audit_logs is append-only

Revision ID: a1f0c2d3e4b5
Revises: eb30ac28aa84
"""
from alembic import op

revision = "a1f0c2d3e4b5"
down_revision = "eb30ac28aa84"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION reject_audit_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER audit_logs_append_only
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION reject_audit_change()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_logs_append_only ON audit_logs")
    op.execute("DROP FUNCTION reject_audit_change()")
