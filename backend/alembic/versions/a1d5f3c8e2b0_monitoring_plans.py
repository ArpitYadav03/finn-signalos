"""Investor Handover: monitoring plans, plan versions, idempotent plan evaluations

Revision ID: a1d5f3c8e2b0
Revises: 9c4d2e7f1b20
Create Date: 2026-11-15 18:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "a1d5f3c8e2b0"
down_revision = "9c4d2e7f1b20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monitoring_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_evaluated_at", sa.DateTime()),
    )
    op.create_table(
        "monitoring_plan_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("monitoring_plans.id", ondelete="CASCADE"), index=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("plan_id", "version"),
    )
    op.create_table(
        "plan_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("monitoring_plans.id", ondelete="CASCADE"), index=True),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(32), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("item_ids", sa.JSON(), nullable=False),
        sa.UniqueConstraint("plan_id", "plan_version", "fingerprint"),
    )


def downgrade() -> None:
    op.drop_table("plan_evaluations")
    op.drop_table("monitoring_plan_versions")
    op.drop_table("monitoring_plans")
