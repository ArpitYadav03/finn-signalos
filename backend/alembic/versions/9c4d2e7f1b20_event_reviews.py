"""explicit event reviews ("Since your last review")

Revision ID: 9c4d2e7f1b20
Revises: 7b2e1c9d4a10
Create Date: 2026-11-15 13:45:00
"""
import sqlalchemy as sa
from alembic import op

revision = "9c4d2e7f1b20"
down_revision = "7b2e1c9d4a10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_key", sa.String(64), nullable=False, unique=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), index=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("filings_seen", sa.JSON()),
        sa.Column("watch", sa.JSON()),
        sa.Column("reviewed_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("event_reviews")
