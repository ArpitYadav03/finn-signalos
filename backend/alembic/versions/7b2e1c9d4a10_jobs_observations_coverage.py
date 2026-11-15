"""durable ingestion jobs, versioned financial observations, source coverage

Revision ID: 7b2e1c9d4a10
Revises: 4cf3787d5aca
Create Date: 2026-11-15 12:40:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7b2e1c9d4a10"
down_revision: Union[str, Sequence[str], None] = "4cf3787d5aca"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("key", sa.String(120), nullable=False, unique=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), index=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE")),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("lease_owner", sa.String(80)),
        sa.Column("lease_expires_at", sa.DateTime()),
        sa.Column("error_class", sa.String(24)),
        sa.Column("error", sa.Text()),
        sa.Column("progress", sa.JSON()),
        sa.Column("result", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("finished_at", sa.DateTime()),
    )
    op.create_index("ix_jobs_due", "ingestion_jobs", ["status", "next_attempt_at"])
    op.create_table(
        "financial_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), index=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("extraction_version", sa.String(32), nullable=False),
        sa.Column("statement_basis", sa.String(16)),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("n_values", sa.Integer(), server_default="0"),
        sa.Column("changes", sa.JSON()),
        sa.Column("validation", sa.JSON()),
    )
    op.create_table(
        "financial_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE")),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("financial_snapshots.id", ondelete="CASCADE"), index=True),
        sa.Column("fiscal_period", sa.String(16), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("period_type", sa.String(8), nullable=False),
        sa.Column("statement_basis", sa.String(16), nullable=False),
        sa.Column("metric_name", sa.String(48), nullable=False),
        sa.Column("value", sa.Float()),
        sa.Column("unit", sa.String(12)),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("supersedes_id", sa.Integer()),
    )
    op.create_index("ix_obs_key", "financial_observations", ["company_id", "metric_name", "fiscal_period", "period_type", "statement_basis"])
    op.create_table(
        "source_coverage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE")),
        sa.Column("source", sa.String(48), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime()),
        sa.Column("last_success_at", sa.DateTime()),
        sa.Column("last_error", sa.Text()),
        sa.Column("items_last", sa.Integer(), server_default="0"),
        sa.Column("oldest_item_at", sa.DateTime()),
        sa.Column("newest_item_at", sa.DateTime()),
        sa.Column("continuous_since", sa.DateTime()),
        sa.Column("limited_window", sa.Boolean(), server_default=sa.true()),
        sa.UniqueConstraint("company_id", "source"),
    )


def downgrade() -> None:
    op.drop_table("source_coverage")
    op.drop_index("ix_obs_key", "financial_observations")
    op.drop_table("financial_observations")
    op.drop_table("financial_snapshots")
    op.drop_index("ix_jobs_due", "ingestion_jobs")
    op.drop_table("ingestion_jobs")
