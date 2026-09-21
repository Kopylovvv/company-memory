"""SQLAlchemy ORM mapping for the `cases` table.

One flat row per case, mirroring `app.cases.models.Case`. Nested contract
objects (`Source`, `Equipment`, `Participant`) are stored as plain columns
rather than JSON so `equipment_id`/`status` stay indexable for search and
equipment history, and so a Postgres NOT NULL/UNIQUE constraint can enforce
the rules the contract already documents (e.g. one row per source event).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CaseRow(Base):
    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint("source_id", name="uq_cases_source_id"),
        Index(
            "uq_cases_source_external_event_id",
            "source_external_event_id",
            unique=True,
            postgresql_where="source_external_event_id IS NOT NULL",
        ),
        Index("ix_cases_equipment_id", "equipment_id"),
        Index("ix_cases_status", "status"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String, nullable=False)

    source_id: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    source_text: Mapped[str] = mapped_column(String, nullable=False)
    source_author_id: Mapped[str] = mapped_column(String, nullable=False)
    source_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_external_event_id: Mapped[str | None] = mapped_column(String, nullable=True)

    equipment_id: Mapped[str | None] = mapped_column(String, nullable=True)
    equipment_label: Mapped[str | None] = mapped_column(String, nullable=True)

    symptom: Mapped[str | None] = mapped_column(String, nullable=True)
    cause: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str | None] = mapped_column(String, nullable=True)
    result: Mapped[str | None] = mapped_column(String, nullable=True)

    participant_id: Mapped[str | None] = mapped_column(String, nullable=True)
    participant_display_name: Mapped[str | None] = mapped_column(String, nullable=True)

    confirmed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
