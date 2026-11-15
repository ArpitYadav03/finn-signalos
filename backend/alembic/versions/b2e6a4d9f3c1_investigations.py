"""Competing Hypotheses: investigations and evidence versions

Revision ID: b2e6a4d9f3c1
Revises: a1d5f3c8e2b0
Create Date: 2026-11-15 19:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "b2e6a4d9f3c1"
down_revision = "a1d5f3c8e2b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "investigations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), index=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("hypotheses", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("reviewed_version", sa.Integer(), nullable=False),
        sa.Column("judgement_cache", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime()),
    )
    op.create_table(
        "investigation_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("investigation_id", sa.Integer(), sa.ForeignKey("investigations.id", ondelete="CASCADE"), index=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(32), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("investigation_id", "version"),
    )


def downgrade() -> None:
    op.drop_table("investigation_versions")
    op.drop_table("investigations")
