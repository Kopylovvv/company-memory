"""create cases table

Revision ID: ecd92264fcbb
Revises:
Create Date: 2026-09-21 16:03:07.684843

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ecd92264fcbb"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_text", sa.String(), nullable=False),
        sa.Column("source_author_id", sa.String(), nullable=False),
        sa.Column("source_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_external_event_id", sa.String(), nullable=True),
        sa.Column("equipment_id", sa.String(), nullable=True),
        sa.Column("equipment_label", sa.String(), nullable=True),
        sa.Column("symptom", sa.String(), nullable=True),
        sa.Column("cause", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=True),
        sa.Column("result", sa.String(), nullable=True),
        sa.Column("participant_id", sa.String(), nullable=True),
        sa.Column("participant_display_name", sa.String(), nullable=True),
        sa.Column("confirmed_by", sa.String(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("source_id", name="uq_cases_source_id"),
    )
    op.create_index(
        "uq_cases_source_external_event_id",
        "cases",
        ["source_external_event_id"],
        unique=True,
        postgresql_where=sa.text("source_external_event_id IS NOT NULL"),
    )
    op.create_index("ix_cases_equipment_id", "cases", ["equipment_id"])
    op.create_index("ix_cases_status", "cases", ["status"])


def downgrade() -> None:
    op.drop_table("cases")
