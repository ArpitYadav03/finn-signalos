"""Historical Alert Replay runs

Revision ID: c3f7b5e0a4d2
Revises: b2e6a4d9f3c1
Create Date: 2026-11-15 20:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "c3f7b5e0a4d2"
down_revision = "b2e6a4d9f3c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "replay_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("monitoring_plans.id", ondelete="CASCADE"), index=True),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("progress", sa.JSON()),
        sa.Column("coverage", sa.JSON()),
        sa.Column("results", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("heartbeat_at", sa.DateTime()),
        sa.Column("finished_at", sa.DateTime()),
    )


def downgrade() -> None:
    op.drop_table("replay_runs")
